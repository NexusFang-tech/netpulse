"""Detection engine.

Correlates Meraki events, probe results, and SSID metrics into actionable
incidents. The key pattern we care about:

    PHASE_2_LIKELY:
      - Critical SSIDs (employee, employee-mobile) show degraded DHCP / client drops
      - Non-critical SSID (guest) is healthy
      - Gateway pings from this host may or may not be failing
    => Bounce Phase 2 selectors on Fortigate.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .database import Database


log = logging.getLogger(__name__)


@dataclass
class SsidStatus:
    name: str
    critical: bool
    current_clients: int
    baseline_clients: int
    drop_percent: float
    healthy: bool  # True if not dropping significantly


@dataclass
class Verdict:
    overall: str  # "healthy" | "degraded" | "outage"
    pattern: str | None  # e.g. "PHASE_2_LIKELY", "UPSTREAM_DOWN", "MERAKI_DHCP_ALERT"
    summary: str
    ssid_statuses: list[SsidStatus]
    recommendations: list[str]


class DetectionEngine:
    def __init__(self, db: Database, config: dict):
        self.db = db
        self.config = config
        self.thresholds = config.get("detection", {})
        self.ssid_configs = {s["name"]: s for s in config.get("ssids", [])}

    def evaluate(self) -> Verdict:
        """Run the full evaluation and return a current-state verdict."""
        ssid_statuses = self._evaluate_ssids()
        probe_snapshot = self._probe_snapshot()
        recent_meraki_events = self._recent_meraki_events(window_seconds=180)

        # --- Pattern matching ------------------------------------------------
        critical_unhealthy = [s for s in ssid_statuses if s.critical and not s.healthy]
        non_critical_healthy = [s for s in ssid_statuses if not s.critical and s.healthy]
        non_critical_present = [s for s in ssid_statuses if not s.critical]

        recommendations: list[str] = []

        # Pattern 1: Meraki DHCP event firing
        dhcp_events = [e for e in recent_meraki_events
                       if "dhcp" in (e.get("category", "") + e.get("title", "")).lower()]
        if dhcp_events:
            event_titles = ", ".join(sorted({e.get("title", "?") for e in dhcp_events[:5]}))
            summary = f"Meraki is reporting DHCP problems: {event_titles}"
            recommendations.append("Meraki DHCP events active -- check scope utilization and DHCP server/relay.")
            if critical_unhealthy and non_critical_present and all(s.healthy for s in non_critical_present):
                recommendations.append("Guest SSID healthy while employee SSIDs degraded -> likely Phase 2 tunnel issue. Bounce selectors.")
                return Verdict("outage", "PHASE_2_LIKELY", summary, ssid_statuses, recommendations)
            return Verdict("degraded", "MERAKI_DHCP_ALERT", summary, ssid_statuses, recommendations)

        # Pattern 2: Phase 2 pattern without explicit DHCP events (client-count signal alone)
        phase2_detection_on = self.thresholds.get("phase2_pattern_detection", True)
        if phase2_detection_on and critical_unhealthy and non_critical_present and all(s.healthy for s in non_critical_present):
            names = ", ".join(s.name for s in critical_unhealthy)
            summary = f"Client count dropped on {names} while guest SSID is stable."
            recommendations.append("Client drop on employee SSIDs but guest is fine -- classic Phase 2 pattern. Bounce tunnel selectors.")
            recommendations.append("Check Fortigate: diagnose vpn tunnel list | grep <phase2-name>")
            return Verdict("outage", "PHASE_2_LIKELY", summary, ssid_statuses, recommendations)

        # Pattern 3: Upstream down (external ping failing but gateway reachable)
        gateway_down = probe_snapshot.get("gateway_down", False)
        external_down = probe_snapshot.get("external_down", False)
        if external_down and not gateway_down:
            summary = "Gateway reachable but external target unreachable."
            recommendations.append("Internal LAN OK, upstream/WAN path failing. Check ISP or Fortigate WAN interface.")
            return Verdict("degraded", "UPSTREAM_DOWN", summary, ssid_statuses, recommendations)

        # Pattern 4: Gateway unreachable from this host
        if gateway_down:
            summary = "Gateway unreachable from this host."
            recommendations.append("Verify local network connectivity; if only this host is affected it may be a client-side issue.")
            return Verdict("outage", "LOCAL_GATEWAY_DOWN", summary, ssid_statuses, recommendations)

        # Pattern 5: General degradation (some critical SSID dropping but guest also weird)
        if critical_unhealthy:
            names = ", ".join(s.name for s in critical_unhealthy)
            summary = f"Degraded activity on {names}."
            recommendations.append("Monitor -- drop detected but not matching a known remediation pattern yet.")
            return Verdict("degraded", "GENERIC_DEGRADATION", summary, ssid_statuses, recommendations)

        # All clear
        return Verdict("healthy", None, "All monitored networks within normal range.", ssid_statuses, [])

    # ------------------------------------------------------------------
    def _evaluate_ssids(self) -> list[SsidStatus]:
        lookback = self.thresholds.get("client_drop_lookback_seconds", 120)
        drop_pct_threshold = self.thresholds.get("client_drop_percent", 30)
        now = int(time.time())
        statuses: list[SsidStatus] = []

        for name, cfg in self.ssid_configs.items():
            latest = self.db.get_latest_ssid_metric(name)
            if not latest:
                statuses.append(SsidStatus(name, cfg.get("critical", False), 0, 0, 0.0, True))
                continue

            # Baseline = max client count in lookback window (catches the drop from peak)
            history = self.db.get_ssid_history(name, now - lookback)
            baseline = max((h["client_count"] for h in history), default=latest["client_count"])
            current = latest["client_count"]

            if baseline == 0:
                drop_pct = 0.0
            else:
                drop_pct = max(0.0, (baseline - current) / baseline * 100.0)

            # "Healthy" means either we haven't seen a big drop OR the absolute count
            # is tiny anyway (early morning, after hours -- not actionable).
            healthy = drop_pct < drop_pct_threshold or baseline < 5

            statuses.append(SsidStatus(
                name=name,
                critical=cfg.get("critical", False),
                current_clients=current,
                baseline_clients=baseline,
                drop_percent=round(drop_pct, 1),
                healthy=healthy,
            ))
        return statuses

    def _probe_snapshot(self) -> dict[str, bool]:
        threshold = self.thresholds.get("ping_failure_threshold", 3)
        now = int(time.time())
        window = now - 90  # last 90 seconds

        def last_n_failed(probe_type: str) -> bool:
            history = self.db.get_probe_history(probe_type, window)
            if len(history) < threshold:
                return False
            recent = history[-threshold:]
            return all(h["success"] == 0 for h in recent)

        return {
            "gateway_down": last_n_failed("ping_gateway"),
            "external_down": last_n_failed("ping_external"),
            "dns_down": last_n_failed("dns"),
        }

    def _recent_meraki_events(self, window_seconds: int) -> list[dict]:
        cutoff = int(time.time()) - window_seconds
        all_events = self.db.get_recent_events(limit=200)
        return [e for e in all_events if e["ts"] >= cutoff and e["source"] == "meraki"]

    # ------------------------------------------------------------------
    def manage_incident_lifecycle(self, verdict: Verdict) -> tuple[str, int | None]:
        """Open / close incidents based on verdict.

        Returns (action, incident_id) where action is one of:
          'opened', 'closed', 'unchanged'
        """
        if verdict.pattern is None:
            # Close any open incidents
            open_incidents = [
                i for i in self.db.get_recent_incidents(limit=10)
                if i["ended_at"] is None
            ]
            for inc in open_incidents:
                self.db.close_incident(inc["id"])
                return "closed", inc["id"]
            return "unchanged", None

        existing = self.db.get_open_incident(verdict.pattern)
        if existing:
            return "unchanged", existing["id"]

        severity = "critical" if verdict.overall == "outage" else "warning"
        incident_id = self.db.open_incident(verdict.pattern, severity, verdict.summary)
        return "opened", incident_id
