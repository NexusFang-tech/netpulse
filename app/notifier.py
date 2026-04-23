"""Desktop notifications and sound alerts.

Platform-specific: uses win10toast on Windows, falls back to plyer/silent on others.
"""
import logging
import platform
import time
from typing import Any


log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

# Try to load Windows notification libraries lazily
_toast = None
_winsound = None


def _init_windows():
    global _toast, _winsound
    if not IS_WINDOWS:
        return
    try:
        from win10toast_click import ToastNotifier
        _toast = ToastNotifier()
    except Exception as e:  # noqa: BLE001
        log.warning("win10toast_click not available: %s", e)
    try:
        import winsound as ws
        _winsound = ws
    except Exception as e:  # noqa: BLE001
        log.warning("winsound not available: %s", e)


_init_windows()


class Notifier:
    def __init__(self, config: dict):
        alerts = config.get("alerts", {})
        self.enabled = alerts.get("desktop_notifications", True)
        self.sound_enabled = alerts.get("sound_enabled", True)
        self.sound_file = alerts.get("sound_file") or ""
        self.dedupe_seconds = alerts.get("dedupe_seconds", 300)
        self._last_alert: dict[str, float] = {}

    def alert(self, pattern: str, title: str, message: str, severity: str = "warning") -> bool:
        """Fire a desktop alert for a given pattern. Deduplicated."""
        now = time.time()
        last = self._last_alert.get(pattern, 0)
        if now - last < self.dedupe_seconds:
            return False
        self._last_alert[pattern] = now

        if not self.enabled:
            log.info("[ALERT:%s] %s -- %s", severity, title, message)
            return True

        full_title = f"NetPulse: {title}"

        if IS_WINDOWS and _toast is not None:
            try:
                _toast.show_toast(
                    full_title,
                    message,
                    duration=10,
                    threaded=True,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Toast failed: %s", e)

        if self.sound_enabled:
            self._play_sound(severity)

        log.info("[ALERT:%s] %s -- %s", severity, title, message)
        return True

    def _play_sound(self, severity: str) -> None:
        if not IS_WINDOWS or _winsound is None:
            return
        try:
            if self.sound_file:
                _winsound.PlaySound(self.sound_file, _winsound.SND_FILENAME | _winsound.SND_ASYNC)
            else:
                # System alarm sound for critical, exclamation for warning
                alias = "SystemHand" if severity == "critical" else "SystemExclamation"
                _winsound.PlaySound(alias, _winsound.SND_ALIAS | _winsound.SND_ASYNC)
        except Exception as e:  # noqa: BLE001
            log.warning("Sound playback failed: %s", e)

    def resolved(self, pattern: str, title: str, message: str) -> None:
        """Fire a resolution notification (not deduped — these are rare)."""
        if not self.enabled:
            log.info("[RESOLVED] %s -- %s", title, message)
            return
        if IS_WINDOWS and _toast is not None:
            try:
                _toast.show_toast(
                    f"NetPulse: {title}",
                    message,
                    duration=5,
                    threaded=True,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Toast failed: %s", e)
        log.info("[RESOLVED] %s -- %s", title, message)
