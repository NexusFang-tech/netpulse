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
    # Mutable holder so hot-reload can update these in place from endpoints
    state = {
        "config": load_config(config_path),
    }
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

    # ---- Routes -----------------------------------------------------------
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
                    "name": s.name,
                    "critical": s.critical,
                    "current_clients": s.current_clients,
                    "baseline_clients": s.baseline_clients,
                    "drop_percent": s.drop_percent,
                    "healthy": s.healthy,
                }
                for s in verdict.ssid_statuses
            ],
        }

    @app.get("/api/probes/latest")
    async def api_probes_latest():
        types = ["ping_gateway", "ping_external", "dns", "dhcp_state"]
        out = {}
        for t in types:
            latest = db.get_latest_probe(t)
            out[t] = latest
        return out

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

    # ---- Settings endpoints -----------------------------------------------
    @app.get("/api/settings")
    async def api_settings_get():
        """Return full settings schema with current values."""
        return {
            "settings": settings_mod.get_current_settings(state["config"]),
            "config_path": str(config_path_obj.resolve()),
        }

    @app.post("/api/settings")
    async def api_settings_post(payload: dict):
        """Update settings. Hot-reloads affected components without restart."""
        if not isinstance(payload, dict) or "updates" not in payload:
            raise HTTPException(status_code=400, detail="Expected {updates: {...}}")
        updates = payload["updates"]
        if not isinstance(updates, dict):
            raise HTTPException(status_code=400, detail="updates must be an object")

        validated, errors = settings_mod.validate_updates(updates)
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

        # Apply to in-memory config
        new_config = settings_mod.apply_updates_to_config(state["config"], validated)

        # Write to disk
        try:
            settings_mod.write_config(new_config, config_path_obj)
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to write config")
            raise HTTPException(status_code=500, detail=f"Write failed: {e}")

        # Hot-reload: rebuild detector with new thresholds + update notifier dedupe
        state["config"] = new_config
        detector.thresholds = new_config.get("detection", {})
        detector.config = new_config
        # Notifier pulls from config each call, but update the instance fields directly
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


# For `uvicorn app.main:app`
app = None
try:
    app = build_app()
except FileNotFoundError as e:
    # Allow `uvicorn app.main:app` to still import and show a clear error
    log.error(str(e))
    app = FastAPI()

    @app.get("/")
    async def _missing_config():
        return JSONResponse(
            status_code=500,
            content={"error": "config.yaml missing. Copy config.example.yaml and edit."},
        )