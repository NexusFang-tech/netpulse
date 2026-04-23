"""Scheduler that orchestrates Meraki polling, local probes, and detection."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .database import Database
from .detection import DetectionEngine
from .meraki_client import MerakiClient
from .notifier import Notifier
from . import probes


log = logging.getLogger(__name__)


class Collector:
    def __init__(self, config: dict, db: Database,
                 meraki: MerakiClient | None, notifier: Notifier,
                 detector: DetectionEngine):
        self.config = config
        self.db = db
        self.meraki = meraki
        self.notifier = notifier
        self.detector = detector
        self.scheduler = AsyncIOScheduler()
        self._last_verdict_pattern: str | None = None

    def start(self) -> None:
        meraki_cfg = self.config.get("meraki", {})
        if self.meraki is not None:
            self.scheduler.add_job(
                self.poll_meraki,
                "interval",
                seconds=meraki_cfg.get("poll_interval_seconds", 30),
                id="meraki_poll",
                max_instances=1,
                coalesce=True,
            )

        probe_cfg = self.config.get("probes", {})
        if probe_cfg.get("enabled", True):
            self.scheduler.add_job(
                self.run_probes,
                "interval",
                seconds=probe_cfg.get("interval_seconds", 15),
                id="probes",
                max_instances=1,
                coalesce=True,
            )

        # Detection runs often -- cheap (all SQLite reads)
        self.scheduler.add_job(
            self.run_detection,
            "interval",
            seconds=10,
            id="detect",
            max_instances=1,
            coalesce=True,
        )

        # Nightly prune at 03:00 local
        self.scheduler.add_job(
            self.prune_old_data,
            "cron",
            hour=3,
            minute=0,
            id="prune",
        )

        self.scheduler.start()
        log.info("Scheduler started")

    async def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        if self.meraki is not None:
            await self.meraki.close()

    # ------------------------------------------------------------------
    async def poll_meraki(self) -> None:
        if self.meraki is None:
            return
        try:
            # Build {number: name} map from config. SSID config entries need
            # a 'number' field for this to work with the new API approach.
            ssid_name_map: dict[int, str] = {}
            for s in self.config.get("ssids", []):
                if "number" in s:
                    ssid_name_map[int(s["number"])] = s["name"]
            ssid_numbers = list(ssid_name_map.keys())

            ssid_data = await self.meraki.get_ssid_clients(
                ssid_numbers=ssid_numbers,
                ssid_name_map=ssid_name_map,
            )
            for entry in ssid_data:
                self.db.insert_ssid_metric(
                    ssid_name=entry["ssidName"],
                    client_count=entry["clientCount"],
                    usage_mb=entry["usageMb"],
                )

            # DHCP-relevant wireless events
            wireless_events = await self.meraki.get_network_events(
                product_type="wireless",
                included_types=["dhcp_no_leases", "dhcp_nack", "dhcp_alert",
                                "association", "disassociation"],
                per_page=50,
            )
            for ev in wireless_events:
                self._store_meraki_event(ev, category="wireless")

            # Assurance alerts
            alerts = await self.meraki.get_assurance_alerts()
            for a in alerts:
                ext_id = a.get("id") or self._hash_event(a)
                self.db.insert_event(
                    source="meraki",
                    severity=a.get("severity", "warning"),
                    category=a.get("type", "assurance"),
                    title=a.get("title") or a.get("type", "Assurance alert"),
                    detail=str(a)[:500],
                    external_id=f"assurance:{ext_id}",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("Meraki poll failed: %s", e)
            self.db.insert_event(
                source="netpulse",
                severity="warning",
                category="meraki_poll_error",
                title="Meraki poll failed",
                detail=str(e)[:500],
            )

    def _store_meraki_event(self, ev: dict, category: str) -> None:
        ts = ev.get("occurredAt") or ev.get("ts")
        etype = ev.get("type", "event")
        device = ev.get("deviceName") or ev.get("deviceSerial", "")
        client = ev.get("clientDescription") or ev.get("clientMac", "")
        ssid = ev.get("ssidName", "")
        title_parts = [etype]
        if ssid:
            title_parts.append(ssid)
        if device:
            title_parts.append(device)
        ext_id = f"mk:{ts}:{etype}:{device}:{client}"
        severity = "critical" if "dhcp" in etype.lower() else "info"
        self.db.insert_event(
            source="meraki",
            severity=severity,
            category=f"{category}:{etype}",
            title=" | ".join(p for p in title_parts if p),
            detail=str(ev)[:500],
            external_id=ext_id,
        )

    @staticmethod
    def _hash_event(ev: dict) -> str:
        return hashlib.sha1(str(sorted(ev.items())).encode()).hexdigest()[:12]

    # ------------------------------------------------------------------
    async def run_probes(self) -> None:
        cfg = self.config.get("probes", {})
        timeout_ms = cfg.get("ping_timeout_ms", 2000)

        # Gateway ping
        gw = cfg.get("gateway_ip")
        if gw:
            ok, latency, err = await probes.ping(gw, timeout_ms)
            self.db.insert_probe("ping_gateway", gw, ok, latency, err)

        # External ping
        ext = cfg.get("external_target")
        if ext:
            ok, latency, err = await probes.ping(ext, timeout_ms)
            self.db.insert_probe("ping_external", ext, ok, latency, err)

        # DNS lookup
        domain = cfg.get("dns_test_domain")
        dns = cfg.get("dns_server")
        if domain:
            ok, latency, err = await probes.dns_lookup(domain, dns, timeout_ms)
            self.db.insert_probe("dns", domain, ok, latency, err)

        # DHCP state (read ipconfig, log lease age)
        try:
            state = probes.get_dhcp_state()
            age = probes.dhcp_lease_age_seconds(state)
            active = state.get("active")
            target = active["name"] if active else "unknown"
            self.db.insert_probe(
                "dhcp_state",
                target,
                success=bool(active),
                latency_ms=float(age) if age is not None else None,
                error=None if active else "no active lease",
            )
        except Exception as e:  # noqa: BLE001
            self.db.insert_probe("dhcp_state", "local", False, None, str(e)[:200])

    # ------------------------------------------------------------------
    async def run_detection(self) -> None:
        verdict = self.detector.evaluate()
        action, incident_id = self.detector.manage_incident_lifecycle(verdict)

        if action == "opened" and verdict.pattern:
            self.notifier.alert(
                pattern=verdict.pattern,
                title=f"{verdict.overall.upper()}: {verdict.pattern}",
                message=verdict.summary,
                severity="critical" if verdict.overall == "outage" else "warning",
            )
        elif action == "closed":
            self.notifier.resolved(
                pattern=self._last_verdict_pattern or "incident",
                title="Resolved",
                message="Monitored networks are back to normal.",
            )

        self._last_verdict_pattern = verdict.pattern

    # ------------------------------------------------------------------
    async def prune_old_data(self) -> None:
        retention = self.config.get("database", {}).get("retention_days", 30)
        stats = self.db.prune(retention)
        log.info("Pruned old data: %s", stats)
