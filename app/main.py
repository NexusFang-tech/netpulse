"""NetPulse FastAPI app -- dashboard + JSON API."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .collector import Collector
from .database import Database
from .detection import DetectionEngine
from .meraki_client import MerakiClient
from .notifier import Notifier
from . import settings as settings_mod


log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def load_config(path: str = "config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Config file {p} not found. Copy config.example.yaml to config.yaml and fill in values."
        )
    with p.open() as f:
        return yaml.safe_load(f)


def build_app(config_path: str = "config.yaml") -> FastAPI:
    config_path_obj = Path(config_path)
    state = {"config": load_config(config_path)}
    config = state["config"]

    db = Database(config["database"]["path"])

    meraki_cfg = config.get("meraki", {})
    meraki: MerakiClient | None = None
    if meraki_cfg.get("api_key") and meraki_cfg["api_key"] != "YOUR_MERAKI_API_KEY_HERE":
        meraki = MerakiClient(
            api_key=meraki_cfg["api_key"],
            organization_id=meraki_cfg["organization_id"],
            network_id=meraki_cfg["network_id"],
        )
    else:
        log.warning("Meraki API key not configured -- running in probe-only mode")

    notifier = Notifier(config)
    detector = DetectionEngine(db, config)
    collector = Collector(config, db, meraki, notifier, detector)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        collector.start()
        yield
        await collector.shutdown()

    app = FastAPI(title="NetPulse", lifespan=lifespan)

    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def dashboard(request: Request):
        return TEMPLATES.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "refresh_ms": state["config"].get("web", {}).get("refresh_interval_ms", 5000),
                "ssids": [s["name"] for s in state["config"].get("ssids", [])],
            },
        )

    @app.get("/api/status")
    async def api_status():
        verdict = detector.evaluate()
        return {
            "ts": int(time.time()),
            "overall": verdict.overall,
            "pattern": verdict.pattern,
            "summary": verdict.summary,
            "recommendations": verdict.recommendations,
            "ssids": [
                {
                    "name": s.name, "critical": s.critical,
                    "current_clients": s.current_clients,
                    "baseline_clients": s.baseline_clients,
                    "drop_percent": s.drop_percent, "healthy": s.healthy,
                }
                for s in verdict.ssid_statuses
            ],
            "affected_aps": [
                {"name": a.name, "current_clients": a.current_clients,
                 "baseline_clients": a.baseline_clients, "dropped": a.dropped}
                for a in verdict.affected_aps
            ],
            "dropped_device_count": verdict.dropped_device_count,
            "dropped_device_sample": verdict.dropped_device_sample,
        }

    @app.get("/api/probes/latest")
    async def api_probes_latest():
        types = ["ping_gateway", "ping_external", "dns", "dhcp_state"]
        return {t: db.get_latest_probe(t) for t in types}

    @app.get("/api/probes/history")
    async def api_probes_history(type: str, minutes: int = 30):
        since = int(time.time()) - (minutes * 60)
        return db.get_probe_history(type, since)

    @app.get("/api/ssid/history")
    async def api_ssid_history(name: str, minutes: int = 60):
        since = int(time.time()) - (minutes * 60)
        return db.get_ssid_history(name, since)

    @app.get("/api/events")
    async def api_events(limit: int = 50):
        return db.get_recent_events(limit=limit)

    @app.get("/api/incidents")
    async def api_incidents(limit: int = 25):
        return db.get_recent_incidents(limit=limit)

    @app.post("/api/incidents/{incident_id}/acknowledge")
    async def ack_incident(incident_id: int):
        db.acknowledge_incident(incident_id)
        return {"ok": True}

    # ---- v1.2 device + AP endpoints --------------------------------------
    @app.get("/api/devices")
    async def api_devices(status: str = "online", limit: int = 500):
        """List devices. status=online|all|offline. Returns sortable table data."""
        if status == "online":
            devices = db.get_online_devices(limit=limit)
        elif status == "offline":
            since = int(time.time()) - 3600
            devices = db.get_recently_dropped_devices(since, limit=limit)
        else:
            devices = db.get_all_devices(limit=limit)
        # Add a friendly display name
        for d in devices:
            d["display_name"] = (
                d.get("description") or d.get("hostname") or
                d.get("manufacturer") or d["mac"]
            )
        return devices

    @app.get("/api/devices/recent_drops")
    async def api_devices_recent_drops(minutes: int = 30, limit: int = 100):
        since = int(time.time()) - (minutes * 60)
        devices = db.get_recently_dropped_devices(since, limit=limit)
        for d in devices:
            d["display_name"] = (
                d.get("description") or d.get("hostname") or
                d.get("manufacturer") or d["mac"]
            )
        return devices

    @app.get("/api/aps")
    async def api_aps():
        """Latest client count per AP, with baseline and drop status."""
        latest = db.get_latest_ap_metrics()
        thresholds = state["config"].get("detection", {})
        baseline_window = thresholds.get("baseline_window_seconds", 1800)
        baseline_percentile = thresholds.get("baseline_percentile", 90)
        min_samples = thresholds.get("baseline_min_samples", 5)
        drop_threshold = thresholds.get("client_drop_percent", 30)
        now = int(time.time())
        out = []
        for ap in latest:
            ap_name = ap["ap_name"]
            history = db.get_ap_history(ap_name, now - baseline_window)
            counts = [h["client_count"] for h in history]
            current = ap["client_count"]
            baseline = current
            healthy = True
            drop_pct = 0.0
            if len(counts) >= min_samples:
                sorted_counts = sorted(counts)
                idx = int(len(sorted_counts) * baseline_percentile / 100.0)
                if idx >= len(sorted_counts):
                    idx = len(sorted_counts) - 1
                baseline = max(sorted_counts[idx], current)
                if baseline > 0:
                    drop_pct = max(0.0, (baseline - current) / baseline * 100.0)
                healthy = drop_pct < drop_threshold or baseline < 3
            out.append({
                "name": ap_name, "serial": ap.get("serial"),
                "current_clients": current, "baseline_clients": baseline,
                "drop_percent": round(drop_pct, 1), "healthy": healthy,
                "ts": ap.get("ts"),
            })
        return out

    @app.get("/api/aps/{ap_name}/history")
    async def api_ap_history(ap_name: str, minutes: int = 60):
        since = int(time.time()) - (minutes * 60)
        return db.get_ap_history(ap_name, since)

    # ---- v1.3 DHCP Health endpoint ---------------------------------------
    @app.get("/api/dhcp/health")
    async def api_dhcp_health():
        """Returns everything we know about DHCP server health right now."""
        cfg = state["config"].get("dhcp_server", {})
        enabled = cfg.get("enabled", False)
        server_ip = cfg.get("server_ip")
        ad_domain = cfg.get("ad_domain")

        now = int(time.time())
        window = now - (15 * 60)  # last 15 min

        def probe_summary(probe_type: str) -> dict | None:
            history = db.get_probe_history(probe_type, window)
            latest = db.get_latest_probe(probe_type)
            if not latest:
                return None
            total = len(history)
            failures = sum(1 for h in history if h["success"] == 0)
            success_rate = (1 - failures / total) * 100 if total else 0
            avg_latency = None
            latencies = [h["latency_ms"] for h in history
                         if h["success"] == 1 and h["latency_ms"] is not None]
            if latencies:
                avg_latency = round(sum(latencies) / len(latencies), 1)
            return {
                "latest": latest,
                "samples_15min": total,
                "failures_15min": failures,
                "success_rate": round(success_rate, 1),
                "avg_latency_ms": avg_latency,
            }

        # Pull recent Meraki DHCP-related events
        all_events = db.get_recent_events(limit=200)
        dhcp_events = [
            e for e in all_events
            if "dhcp" in (e.get("category", "") + e.get("title", "")).lower()
            and e["ts"] >= now - 3600  # last hour
        ][:20]

        # Local lease info
        try:
            from . import probes as probes_mod
            dhcp_state = probes_mod.get_dhcp_state()
            lease_age = probes_mod.dhcp_lease_age_seconds(dhcp_state)
        except Exception:  # noqa: BLE001
            dhcp_state = {"active": None}
            lease_age = None

        # Determine if the INFORM probe is failing in isolation (advisory) or
        # if it's failing alongside other DC probes (real concern).
        def is_failing(p: dict | None) -> bool:
            if not p or not p.get("samples_15min"):
                return False
            # Failing = success rate < 50% over the last 15 min
            return p.get("success_rate", 100) < 50

        probes_data = {
            "dc_ping": probe_summary("dhcp_dc_ping"),
            "dc_dns": probe_summary("dhcp_dc_dns"),
            "dc_ldap": probe_summary("dhcp_dc_ldap"),
            "dhcp_inform": probe_summary("dhcp_inform"),
        }
        inform_failing_now = is_failing(probes_data["dhcp_inform"])
        others_healthy = (not is_failing(probes_data["dc_ping"]) and
                          not is_failing(probes_data["dc_dns"]) and
                          not is_failing(probes_data["dc_ldap"]))
        # Advisory: INFORM is failing but every other DC service is healthy.
        # Most likely environmental (firewall/relay filtering DHCPINFORM) and
        # not a real DHCP outage. The dashboard renders this in yellow rather
        # than red.
        inform_advisory = inform_failing_now and others_healthy

        return {
            "ts": now,
            "enabled": enabled,
            "server_ip": server_ip,
            "ad_domain": ad_domain,
            "probes": probes_data,
            "inform_advisory": inform_advisory,
            "local_lease": {
                "active": dhcp_state.get("active"),
                "age_seconds": lease_age,
            },
            "recent_dhcp_events": dhcp_events,
        }

    @app.get("/api/dhcp/probe_history")
    async def api_dhcp_probe_history(probe_type: str = "dhcp_inform", minutes: int = 60):
        """Time-series for a DHCP probe -- used for sparklines."""
        valid_types = {"dhcp_dc_ping", "dhcp_dc_dns", "dhcp_dc_ldap", "dhcp_inform"}
        if probe_type not in valid_types:
            raise HTTPException(status_code=400, detail=f"probe_type must be one of {valid_types}")
        since = int(time.time()) - (minutes * 60)
        return db.get_probe_history(probe_type, since)

    # ---- Settings endpoints (v1.1) ---------------------------------------
    @app.get("/api/settings")
    async def api_settings_get():
        return {
            "settings": settings_mod.get_current_settings(state["config"]),
            "config_path": str(config_path_obj.resolve()),
        }

    @app.post("/api/settings")
    async def api_settings_post(payload: dict):
        if not isinstance(payload, dict) or "updates" not in payload:
            raise HTTPException(status_code=400, detail="Expected {updates: {...}}")
        updates = payload["updates"]
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates must be an object")
        validated, errors = settings_mod.validate_updates(updates)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
        new_config = settings_mod.apply_updates_to_config(state["config"], validated)
        try:
            settings_mod.write_config(new_config, config_path_obj)
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to write config")
            raise HTTPException(status_code=500, detail=f"Write failed: {e}")
        state["config"] = new_config
        detector.thresholds = new_config.get("detection", {})
        detector.config = new_config
        alerts_cfg = new_config.get("alerts", {})
        notifier.enabled = alerts_cfg.get("desktop_notifications", True)
        notifier.sound_enabled = alerts_cfg.get("sound_enabled", True)
        notifier.dedupe_seconds = alerts_cfg.get("dedupe_seconds", 300)
        log.info("Settings updated and hot-reloaded: %s", list(validated.keys()))
        return {
            "ok": True,
            "applied": validated,
            "settings": settings_mod.get_current_settings(new_config),
        }

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "ts": int(time.time())}

    return app


app = None
try:
    app = build_app()
except FileNotFoundError as e:
    log.error(str(e))
    app = FastAPI()

    @app.get("/")
    async def _missing_config():
        return JSONResponse(
            status_code=500,
            content={"error": "config.yaml missing. Copy config.example.yaml and edit."},
        )