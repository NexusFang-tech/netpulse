"""NetPulse tray launcher.

Runs the FastAPI server and installs a system tray icon so NetPulse stays
minimized and accessible. Left-click the icon to open the dashboard.
"""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
import yaml


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/netpulse.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

log = logging.getLogger("tray")


def load_web_config() -> tuple[str, int]:
    p = Path("config.yaml")
    if not p.exists():
        return "127.0.0.1", 8787
    with p.open() as f:
        cfg = yaml.safe_load(f)
    web = cfg.get("web", {})
    return web.get("host", "127.0.0.1"), web.get("port", 8787)


def make_icon():
    """Create a simple neon-pink/cyan NetPulse tray icon."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (10, 4, 21, 255))
    d = ImageDraw.Draw(img)
    # Concentric rings - a "pulse"
    d.ellipse((4, 4, 60, 60), outline=(0, 229, 255, 255), width=3)
    d.ellipse((16, 16, 48, 48), outline=(255, 46, 151, 255), width=3)
    d.ellipse((26, 26, 38, 38), fill=(255, 46, 151, 255))
    return img


def main() -> None:
    host, port = load_web_config()
    url = f"http://{host}:{port}/"

    # Start uvicorn in a thread
    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait briefly for server start
    time.sleep(1.5)

    # Try to bring up the tray icon
    try:
        import pystray
        from pystray import MenuItem as Item, Menu

        def on_open(icon, item):
            webbrowser.open(url)

        def on_quit(icon, item):
            log.info("Shutting down NetPulse")
            server.should_exit = True
            icon.stop()

        icon = pystray.Icon(
            "netpulse",
            make_icon(),
            "NetPulse",
            menu=Menu(
                Item("Open Dashboard", on_open, default=True),
                Item("Quit", on_quit),
            ),
        )
        webbrowser.open(url)
        icon.run()
    except Exception as e:
        log.warning("Tray icon unavailable (%s). Running headless. Dashboard: %s", e, url)
        webbrowser.open(url)
        # Keep process alive on signal
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        stop.wait()
        server.should_exit = True


if __name__ == "__main__":
    main()
