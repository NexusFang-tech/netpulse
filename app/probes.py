"""Local network probes run from the host machine.

Uses only stdlib + subprocess to stay dependency-light.
Works on Windows (primary target) and Linux.
"""
import asyncio
import logging
import platform
import re
import socket
import subprocess
import time
from typing import Any


log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


async def ping(target: str, timeout_ms: int = 2000) -> tuple[bool, float | None, str | None]:
    """Async-friendly ping using the OS ping command.

    Returns (success, latency_ms, error).
    """
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), target]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=(timeout_ms / 1000) + 2
        )
    except asyncio.TimeoutError:
        return False, None, "timeout"
    except FileNotFoundError:
        return False, None, "ping command not found"

    output = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return False, None, output.strip().splitlines()[-1] if output else "ping failed"

    # Parse latency
    # Windows: "time=1ms" or "time<1ms"
    # Linux:   "time=1.23 ms"
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", output)
    if m:
        return True, float(m.group(1)), None
    return True, None, None


async def dns_lookup(domain: str, dns_server: str | None = None,
                     timeout_ms: int = 2000) -> tuple[bool, float | None, str | None]:
    """Resolve a domain and measure latency.

    If dns_server is provided on Windows, we use nslookup to force the server.
    Otherwise uses system resolver.
    """
    if dns_server:
        cmd = ["nslookup", domain, dns_server] if IS_WINDOWS else ["nslookup", domain, dns_server]
        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=(timeout_ms / 1000) + 2
            )
        except asyncio.TimeoutError:
            return False, None, "timeout"
        except FileNotFoundError:
            return False, None, "nslookup not found"

        latency = (time.perf_counter() - start) * 1000
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 or "can't find" in output.lower() or "timed out" in output.lower():
            return False, latency, "resolution failed"
        return True, latency, None

    # System resolver path
    loop = asyncio.get_running_loop()
    start = time.perf_counter()
    try:
        await loop.run_in_executor(None, socket.gethostbyname, domain)
        return True, (time.perf_counter() - start) * 1000, None
    except socket.gaierror as e:
        return False, (time.perf_counter() - start) * 1000, str(e)


def get_dhcp_state() -> dict[str, Any]:
    """Read DHCP lease state from the OS (Windows ipconfig /all or Linux ip)."""
    if IS_WINDOWS:
        try:
            out = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"error": str(e)}
        return _parse_ipconfig(out)
    else:
        try:
            out = subprocess.run(
                ["ip", "-4", "addr"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"error": str(e)}
        return {"raw": out[:500], "adapters": []}


def _parse_ipconfig(text: str) -> dict[str, Any]:
    """Parse Windows ipconfig /all output to extract lease info."""
    adapters: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # New adapter section (starts at column 0 and ends with colon)
        if line and line[0] not in (" ", "\t") and stripped.endswith(":"):
            if current:
                adapters.append(current)
            current = {"name": stripped.rstrip(":"), "properties": {}}
            continue
        if current is None:
            continue
        m = re.match(r"\s*(.+?)\s*\.\s*:\s*(.+)$", line)
        if m:
            key = m.group(1).strip().replace(".", "").strip()
            val = m.group(2).strip()
            current["properties"][key] = val
    if current:
        adapters.append(current)

    # Find an adapter with a DHCP lease + IPv4
    active = None
    for a in adapters:
        p = a.get("properties", {})
        if "Lease Obtained" in p and any(k.startswith("IPv4 Address") for k in p):
            active = a
            break

    return {"adapters": adapters, "active": active}


def dhcp_lease_age_seconds(dhcp_state: dict) -> int | None:
    """Rough calculation of how old the current DHCP lease is."""
    active = dhcp_state.get("active")
    if not active:
        return None
    lease_str = active.get("properties", {}).get("Lease Obtained")
    if not lease_str:
        return None
    # Windows format varies by locale; try a few
    from datetime import datetime
    for fmt in (
        "%A, %B %d, %Y %I:%M:%S %p",
        "%A, %B %d, %Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(lease_str, fmt)
            return int(time.time() - dt.timestamp())
        except ValueError:
            continue
    return None
