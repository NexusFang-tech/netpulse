"""Detection engine.

Correlates Meraki events, probe results, SSID metrics, and AP metrics into
actionable incidents. Now enriches PHASE_2_LIKELY verdicts with AP-level
attribution -- "10 devices dropped on AP-Albemarle and AP-Coates."
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
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
    healthy: bool


@dataclass
class ApAttribution:
    name: str
    current_clients: int
    baseline_clients: int
    dropped: int


@dataclass
class Verdict:
    overall: str
    pattern: str | None
    summary: str
    ssid_statuses: list[SsidStatus]
    recommendations: list[str]
    affected_aps: list[ApAttribution] = field(default_factory=list)
    dropped_device_count: int = 0
    dropped_device_sample: list[dict] = field(default_factory=list)


class DetectionEngine:
    def __init__(self, db: Database, config: dict):
        self.db = db
        self.config = config
        self.thresholds = config.get("detection", {})
        self.ssid_configs = {s["name"]: s for s in config.get("ssids", [])}

    def evaluate(self) -> Verdict:
        ssid_statuses = self._evaluate_ssids()
        probe_snapshot = self._probe_snapshot()
        recent_meraki_events = self._recent_meraki_events(window_seconds=180)

        critical_unhealthy = [s for s in ssid_statuses if s.critical and not s.healthy]
        non_critical_present = [s for s in ssid_statuses if not s.critical]

        recommendations: list[str] = []
        affected_aps = self._compute_ap_attribution() if critical_unhealthy else []
        dropped = self._recent_dropped_devices()

        # ----- DHCP-FOCUSED PATTERNS (v1.3) -----
        # These fire BEFORE downstream client-count patterns because they
        # represent the root cause we'd otherwise have to infer from symptoms.

        # Pattern: DHCP INFORM probe failing.
        #
        # IMPORTANT: DHCPINFORM is a unicast probe and may be permanently
        # unreachable from the monitoring host's network position even when the
        # DHCP service is healthy (Fortigate filtering, Windows Firewall on the
        # DC, DHCP service binding only to the relay-facing interface, etc.).
        # So we don't trust INFORM alone -- we require it to be CORROBORATED by
        # another failing signal before declaring DHCP down. INFORM-alone gets
        # surfaced as advisory info on the dashboard, not an outage verdict.
        inform_failing = probe_snapshot.get("dhcp_inform_failing")
        dc_reachable = not probe_snapshot.get("dhcp_dc_unreachable")
        ldap_up = not probe_snapshot.get("dhcp_dc_ldap_down")
        dns_up = not probe_snapshot.get("dhcp_dc_dns_down")

        # Strong signal: DC is unreachable. This trumps everything.
        if not dc_reachable:
            summary = "Domain Controller (DHCP server host) unreachable from this host."
            recommendations.append("DC unreachable -- check S2S VPN tunnel state on the Fortigate.")
            recommendations.append("If tunnel is up, check the Azure VM is running (portal → Virtual Machines).")
            recommendations.append("Fortigate CLI: diagnose vpn tunnel list | grep <azure-tunnel>")
            recommendations.append("Once the tunnel/VM is back, DHCP should resume automatically.")
            return Verdict("outage", "DHCP_SERVER_UNREACHABLE", summary, ssid_statuses,
                           recommendations, affected_aps, len(dropped), dropped[:10])

        # Strong signal: DC reachable but BOTH LDAP and DNS down. The VM itself
        # is in trouble even if it pings -- definitely an outage.
        if not ldap_up and not dns_up:
            summary = "DC reachable but multiple core services (AD, DNS) failing -- VM is degraded."
            recommendations.append("DC pings but AD/DNS aren't responding. Check VM resource pressure.")
            recommendations.append("Azure portal → vm-dc-prod-eastus-001 → Metrics: CPU/memory/disk.")
            recommendations.append("If the VM was recently rebooted or is mid-update, services may not be back up yet.")
            return Verdict("outage", "DHCP_SERVICE_DOWN", summary, ssid_statuses,
                           recommendations, affected_aps, len(dropped), dropped[:10])

        # Corroborated DHCP service failure: INFORM failing AND something else
        # on the DC is also failing. INFORM alone is not trusted (see note above)
        # but INFORM + LDAP-down or INFORM + DNS-down is meaningful.
        if inform_failing and (not ldap_up or not dns_up):
            failed_others = []
            if not ldap_up:
                failed_others.append("LDAP")
            if not dns_up:
                failed_others.append("DNS")
            others_str = " and ".join(failed_others)
            summary = (f"DHCP INFORM probe failing AND DC {others_str} probe(s) also failing. "
                       f"DC services are degraded.")
            recommendations.append("Multiple DC services failing -- VM may be under resource pressure.")
            recommendations.append("Check Azure portal for VM metrics and recent activity log.")
            recommendations.append("If DHCP issues match the typical pattern, RDP and restart DHCP service.")
            return Verdict("outage", "DHCP_SERVICE_DOWN", summary, ssid_statuses,
                           recommendations, affected_aps, len(dropped), dropped[:10])

        # INFORM-only failure: ADVISORY only. Most likely cause is environmental
        # (firewall filtering INFORM packets) rather than a real DHCP outage.
        # We surface this in the dashboard but don't treat it as an outage.
        # The downstream client-symptom patterns (PHASE_2_LIKELY) will catch
        # actual DHCP failures via their effect on client counts.

        # Pattern 1: Meraki DHCP event
        dhcp_events = [e for e in recent_meraki_events
                       if "dhcp" in (e.get("category", "") + e.get("title", "")).lower()]
        if dhcp_events:
            event_titles = ", ".join(sorted({e.get("title", "?") for e in dhcp_events[:5]}))
            summary = f"Meraki is reporting DHCP problems: {event_titles}"
            recommendations.append("Meraki DHCP events active — check scope utilization and DHCP server/relay.")
            if critical_unhealthy and non_critical_present and all(s.healthy for s in non_critical_present):
                summary = self._enrich_summary(summary, affected_aps, dropped)
                recommendations.append("Guest SSID healthy while employee SSIDs degraded → likely Phase 2 tunnel issue. Bounce selectors.")
                return Verdict("outage", "PHASE_2_LIKELY", summary, ssid_statuses, recommendations,
                               affected_aps, len(dropped), dropped[:10])
            return Verdict("degraded", "MERAKI_DHCP_ALERT", summary, ssid_statuses, recommendations,
                           affected_aps, len(dropped), dropped[:10])

        # Pattern 2: Phase 2 pattern via client-count signal alone
        phase2_on = self.thresholds.get("phase2_pattern_detection", True)
        if phase2_on and critical_unhealthy and non_critical_present and all(s.healthy for s in non_critical_present):
            names = ", ".join(s.name for s in critical_unhealthy)
            summary = f"Client count dropped on {names} while guest SSID is stable."
            summary = self._enrich_summary(summary, affected_aps, dropped)
            recommendations.append("Client drop on employee SSIDs but guest is fine — classic Phase 2 pattern. Bounce tunnel selectors.")
            recommendations.append("Check Fortigate: diagnose vpn tunnel list | grep <phase2-name>")
            return Verdict("outage", "PHASE_2_LIKELY", summary, ssid_statuses, recommendations,
                           affected_aps, len(dropped), dropped[:10])

        # Pattern 3: Upstream down
        if probe_snapshot.get("external_down") and not probe_snapshot.get("gateway_down"):
            summary = "Gateway reachable but external target unreachable."
            recommendations.append("Internal LAN OK, upstream/WAN path failing. Check ISP or Fortigate WAN interface.")
            return Verdict("degraded", "UPSTREAM_DOWN", summary, ssid_statuses, recommendations)

        # Pattern 4: Local gateway down
        if probe_snapshot.get("gateway_down"):
            summary = "Gateway unreachable from this host."
            recommendations.append("Verify local network connectivity; if only this host is affected it may be a client-side issue.")
            return Verdict("outage", "LOCAL_GATEWAY_DOWN", summary, ssid_statuses, recommendations)

        # Pattern 5: Generic degradation
        if critical_unhealthy:
            names = ", ".join(s.name for s in critical_unhealthy)
            summary = f"Degraded activity on {names}."
            summary = self._enrich_summary(summary, affected_aps, dropped)
            recommendations.append("Monitor — drop detected but not matching a known remediation pattern yet.")
            return Verdict("degraded", "GENERIC_DEGRADATION", summary, ssid_statuses, recommendations,
                           affected_aps, len(dropped), dropped[:10])

        return Verdict("healthy", None, "All monitored networks within normal range.", ssid_statuses, [])

    # ------------------------------------------------------------------
    def _evaluate_ssids(self) -> list[SsidStatus]:
        baseline_window = self.thresholds.get("baseline_window_seconds", 1800)
        drop_pct_threshold = self.thresholds.get("client_drop_percent", 30)
        baseline_percentile = self.thresholds.get("baseline_percentile", 90)
        min_samples = self.thresholds.get("baseline_min_samples", 5)
        now = int(time.time())
        statuses: list[SsidStatus] = []

        for name, cfg in self.ssid_configs.items():
            latest = self.db.get_latest_ssid_metric(name)
            if not latest:
                statuses.append(SsidStatus(name, cfg.get("critical", False), 0, 0, 0.0, True))
                continue
            history = self.db.get_ssid_history(name, now - baseline_window)
            counts = [h["client_count"] for h in history]
            current = latest["client_count"]
            if len(counts) < min_samples:
                baseline = max(counts, default=current)
                drop_pct = 0.0
                healthy = True
            else:
                sorted_counts = sorted(counts)
                idx = int(len(sorted_counts) * baseline_percentile / 100.0)
                if idx >= len(sorted_counts):
                    idx = len(sorted_counts) - 1
                baseline = sorted_counts[idx]
                if baseline < current:
                    baseline = current
                if baseline == 0:
                    drop_pct = 0.0
                else:
                    drop_pct = max(0.0, (baseline - current) / baseline * 100.0)
                healthy = drop_pct < drop_pct_threshold or baseline < 5
            statuses.append(SsidStatus(
                name=name, critical=cfg.get("critical", False),
                current_clients=current, baseline_clients=baseline,
                drop_percent=round(drop_pct, 1), healthy=healthy,
            ))
        return statuses

    def _probe_snapshot(self) -> dict[str, bool]:
        threshold = self.thresholds.get("ping_failure_threshold", 3)
        # DHCP probes have their own threshold -- typically lower so we catch
        # the failure faster (DHCP server outages are time-sensitive)
        dhcp_threshold = self.thresholds.get("dhcp_failure_threshold", 2)
        now = int(time.time())
        window = now - 180  # wider window to ensure we have enough samples

        def last_n_failed(probe_type: str, n: int) -> bool:
            history = self.db.get_probe_history(probe_type, window)
            if len(history) < n:
                return False
            recent = history[-n:]
            return all(h["success"] == 0 for h in recent)

        return {
            "gateway_down": last_n_failed("ping_gateway", threshold),
            "external_down": last_n_failed("ping_external", threshold),
            "dns_down": last_n_failed("dns", threshold),
            "dhcp_dc_unreachable": last_n_failed("dhcp_dc_ping", dhcp_threshold),
            "dhcp_dc_dns_down": last_n_failed("dhcp_dc_dns", dhcp_threshold),
            "dhcp_dc_ldap_down": last_n_failed("dhcp_dc_ldap", dhcp_threshold),
            "dhcp_inform_failing": last_n_failed("dhcp_inform", dhcp_threshold),
        }

    def _recent_meraki_events(self, window_seconds: int) -> list[dict]:
        cutoff = int(time.time()) - window_seconds
        all_events = self.db.get_recent_events(limit=200)
        return [e for e in all_events if e["ts"] >= cutoff and e["source"] == "meraki"]

    def _compute_ap_attribution(self) -> list[ApAttribution]:
        """For each AP, compare current count to its baseline percentile.
        Returns APs where the drop percentage exceeds the threshold, sorted by
        absolute number dropped (worst first)."""
        baseline_window = self.thresholds.get("baseline_window_seconds", 1800)
        baseline_percentile = self.thresholds.get("baseline_percentile", 90)
        drop_pct_threshold = self.thresholds.get("client_drop_percent", 30)
        min_samples = self.thresholds.get("baseline_min_samples", 5)
        now = int(time.time())

        latest = self.db.get_latest_ap_metrics()
        results: list[ApAttribution] = []
        for ap in latest:
            ap_name = ap["ap_name"]
            current = ap["client_count"]
            history = self.db.get_ap_history(ap_name, now - baseline_window)
            counts = [h["client_count"] for h in history]
            if len(counts) < min_samples:
                continue
            sorted_counts = sorted(counts)
            idx = int(len(sorted_counts) * baseline_percentile / 100.0)
            if idx >= len(sorted_counts):
                idx = len(sorted_counts) - 1
            baseline = sorted_counts[idx]
            if baseline < 3:  # ignore APs that never had many clients
                continue
            dropped = max(0, baseline - current)
            drop_pct = (dropped / baseline * 100.0) if baseline else 0
            if drop_pct >= drop_pct_threshold:
                results.append(ApAttribution(
                    name=ap_name, current_clients=current,
                    baseline_clients=baseline, dropped=dropped,
                ))
        results.sort(key=lambda x: x.dropped, reverse=True)
        return results

    def _recent_dropped_devices(self, window_seconds: int = 300) -> list[dict]:
        since = int(time.time()) - window_seconds
        return self.db.get_recently_dropped_devices(since, limit=50)

    @staticmethod
    def _enrich_summary(base: str, aps: list[ApAttribution], dropped: list[dict]) -> str:
        parts = [base]
        if aps:
            ap_str = ", ".join(f"{a.name} (-{a.dropped})" for a in aps[:3])
            parts.append(f"Most affected APs: {ap_str}.")
        if dropped:
            parts.append(f"{len(dropped)} device(s) recently dropped.")
        return " ".join(parts)

    # ------------------------------------------------------------------
    def manage_incident_lifecycle(self, verdict: Verdict) -> tuple[str, int | None]:
        if verdict.pattern is None:
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