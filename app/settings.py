"""Settings module - safely read/write tunable config values and hot-reload components.

Only a whitelisted set of config paths are editable through the API. All other
config (API keys, SSID numbers, database paths, etc.) requires a full restart
and manual editing of config.yaml.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

import yaml


log = logging.getLogger(__name__)


# Whitelist of adjustable settings. Each entry defines:
#   path: dot-separated path into the YAML tree
#   type: "int" | "float" | "bool"
#   min/max: validation bounds (inclusive)
#   label/description: UI strings
#
# Only these can be changed via the settings API. Anything else requires
# editing config.yaml by hand + restart.
ADJUSTABLE_SETTINGS = [
    {
        "key": "baseline_window_seconds",
        "path": "detection.baseline_window_seconds",
        "type": "int",
        "min": 300,
        "max": 7200,
        "step": 60,
        "default": 1800,
        "label": "Baseline Window",
        "unit": "seconds",
        "description": "How long a window of history to treat as 'normal'. Longer windows resist false resets but adapt slower.",
    },
    {
        "key": "baseline_percentile",
        "path": "detection.baseline_percentile",
        "type": "int",
        "min": 50,
        "max": 100,
        "step": 5,
        "default": 90,
        "label": "Baseline Percentile",
        "unit": "%",
        "description": "Which percentile of the baseline window to treat as peak. 90 = typical peak, 100 = absolute max.",
    },
    {
        "key": "client_drop_percent",
        "path": "detection.client_drop_percent",
        "type": "int",
        "min": 10,
        "max": 80,
        "step": 5,
        "default": 30,
        "label": "Drop Threshold",
        "unit": "%",
        "description": "Percentage drop from baseline that triggers an alert. Lower = more sensitive.",
    },
    {
        "key": "baseline_min_samples",
        "path": "detection.baseline_min_samples",
        "type": "int",
        "min": 2,
        "max": 30,
        "step": 1,
        "default": 5,
        "label": "Minimum Samples",
        "unit": "samples",
        "description": "Minimum history samples required before the baseline is trusted. Prevents startup false alarms.",
    },
    {
        "key": "ping_failure_threshold",
        "path": "detection.ping_failure_threshold",
        "type": "int",
        "min": 1,
        "max": 10,
        "step": 1,
        "default": 3,
        "label": "Ping Failure Threshold",
        "unit": "failures",
        "description": "Consecutive failed pings before gateway/external is marked down.",
    },
    {
        "key": "dedupe_seconds",
        "path": "alerts.dedupe_seconds",
        "type": "int",
        "min": 30,
        "max": 1800,
        "step": 30,
        "default": 300,
        "label": "Alert Dedupe Window",
        "unit": "seconds",
        "description": "Minimum seconds between duplicate notifications for the same pattern.",
    },
    {
        "key": "desktop_notifications",
        "path": "alerts.desktop_notifications",
        "type": "bool",
        "default": True,
        "label": "Desktop Notifications",
        "description": "Fire toast notifications on detection events.",
    },
    {
        "key": "sound_enabled",
        "path": "alerts.sound_enabled",
        "type": "bool",
        "default": True,
        "label": "Alert Sounds",
        "description": "Play sound when alerts fire.",
    },
    {
        "key": "phase2_pattern_detection",
        "path": "detection.phase2_pattern_detection",
        "type": "bool",
        "default": True,
        "label": "Phase 2 Pattern Detection",
        "description": "Flag the specific 'critical SSIDs drop, guest stable' pattern as PHASE_2_LIKELY.",
    },
]


def _get_path(d: dict, path: str) -> Any:
    """Get a nested value by dot-separated path. Returns None if missing."""
    parts = path.split(".")
    cur: Any = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set_path(d: dict, path: str, value: Any) -> None:
    """Set a nested value by dot-separated path. Creates intermediate dicts."""
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def get_current_settings(config: dict) -> list[dict]:
    """Return the full settings schema with current values from config."""
    out: list[dict] = []
    for s in ADJUSTABLE_SETTINGS:
        current = _get_path(config, s["path"])
        if current is None:
            current = s.get("default")
        entry = dict(s)
        entry["current"] = current
        out.append(entry)
    return out


def validate_updates(updates: dict) -> tuple[dict, list[str]]:
    """Validate incoming settings updates against the whitelist.

    Returns (validated_updates, errors). Only whitelisted keys pass through
    and types/bounds are enforced.
    """
    schema = {s["key"]: s for s in ADJUSTABLE_SETTINGS}
    validated: dict = {}
    errors: list[str] = []

    for key, raw_val in updates.items():
        if key not in schema:
            errors.append(f"Unknown setting: {key}")
            continue
        spec = schema[key]
        t = spec["type"]

        try:
            if t == "int":
                val = int(raw_val)
                if val < spec["min"] or val > spec["max"]:
                    errors.append(f"{key}: {val} out of range [{spec['min']}, {spec['max']}]")
                    continue
            elif t == "float":
                val = float(raw_val)
                if val < spec["min"] or val > spec["max"]:
                    errors.append(f"{key}: {val} out of range [{spec['min']}, {spec['max']}]")
                    continue
            elif t == "bool":
                if isinstance(raw_val, bool):
                    val = raw_val
                elif isinstance(raw_val, str):
                    val = raw_val.lower() in ("true", "1", "yes", "on")
                else:
                    val = bool(raw_val)
            else:
                errors.append(f"{key}: unknown type {t}")
                continue
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: {e}")
            continue

        validated[key] = val

    return validated, errors


def apply_updates_to_config(config: dict, validated: dict) -> dict:
    """Apply validated updates to an in-memory config dict. Returns modified copy."""
    import copy
    new_config = copy.deepcopy(config)
    schema = {s["key"]: s for s in ADJUSTABLE_SETTINGS}
    for key, val in validated.items():
        spec = schema[key]
        _set_path(new_config, spec["path"], val)
    return new_config


def write_config(config: dict, config_path: Path) -> None:
    """Write config back to YAML with a backup of the previous version.

    Creates config.yaml.bak before overwriting. Uses safe_dump to avoid
    arbitrary tag emission. Comments in the original file are NOT preserved
    (PyYAML limitation) but structure is preserved.
    """
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    if config_path.exists():
        shutil.copy2(config_path, backup_path)

    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        f.write("# NetPulse Configuration\n")
        f.write(f"# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
        f.write("# Editable fields can be changed via the Settings panel.\n")
        f.write("# Non-editable fields (API keys, SSID numbers) must be edited manually.\n\n")
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False, indent=2)

    # Atomic replace
    tmp_path.replace(config_path)
    log.info("Config written to %s (backup at %s)", config_path, backup_path)