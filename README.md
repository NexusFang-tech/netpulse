# NetPulse

**Real-time network outage monitor for Meraki + Fortigate environments.**

A desktop dashboard that surfaces specific outage patterns before email alerts arrive. Built to solve a recurring DHCP tunnel issue where employee SSIDs degrade while guest networks stay healthy — a signature that points directly to an IPsec Phase 2 problem.

Instead of waiting 15 minutes for Meraki's email notification, NetPulse detects the pattern in about 30 seconds by correlating:

- Per-SSID client counts from the Meraki Dashboard API
- Local ICMP probes to the gateway and external targets
- DNS resolution latency
- Local DHCP lease state from `ipconfig`
- Meraki wireless events (DHCP NACKs, mass disassociations) and assurance alerts

When critical SSIDs degrade while the control SSID stays healthy, NetPulse fires a desktop notification with a specific remediation recommendation. Detection thresholds can be tuned live from the dashboard with no restart required.

## Detection patterns

| Pattern | Meaning | Action |
|---|---|---|
| `PHASE_2_LIKELY` | Critical SSIDs dropping + control SSID healthy | Bounce Phase 2 selectors on Fortigate |
| `MERAKI_DHCP_ALERT` | Meraki reporting DHCP events network-wide | Check DHCP server + scope exhaustion |
| `UPSTREAM_DOWN` | Gateway OK, external ping failing | ISP / WAN issue, not internal |
| `LOCAL_GATEWAY_DOWN` | Gateway unreachable from this host | Likely local to the monitoring host |
| `GENERIC_DEGRADATION` | Drops that don't match a known pattern | Monitor |

## Architecture

- **FastAPI** backend with APScheduler for polling (Meraki every 30s, local probes every 15s)
- **SQLite** storage with configurable retention (default 30 days)
- **Retrowave dashboard** served locally on `127.0.0.1:8787`, designed to live pinned on a spare monitor
- **Live configuration panel** for adjusting detection thresholds without restarting
- **System tray launcher** via pystray — runs in the background, left-click to open dashboard
- **Desktop notifications** via win10toast on Windows
- All Meraki API calls are **read-only GET requests** — the tool cannot modify your network

## Tech stack

Python 3.12+ · FastAPI · APScheduler · SQLite · Jinja2 · pystray · win10toast · Plain ES5 JS · CSS (no frameworks)

---

# Setup Guide

This guide walks through a complete first-time install on a Windows machine with no Python installed. Total time: ~20 minutes including grabbing your Meraki IDs.

## Step 1 — Install Python

1. Go to **https://www.python.org/downloads/windows/**
2. Download the latest **Python 3.12.x** Windows installer (64-bit recommended). Python 3.13 and 3.14 also work but 3.12 has the broadest wheel coverage.
3. Run the installer. **Critical**: on the first screen, check the box that says **"Add python.exe to PATH"** at the bottom before clicking Install.
4. Click **Install Now** (the default options are fine).
5. When it finishes, you may see a **"Disable path length limit"** button — click it. Saves headaches later.

### Disable Microsoft Store's Python alias

Windows ships an "App Execution Alias" for `python` that redirects to the Microsoft Store, which interferes with real Python. Disable it:

1. Press **Win** key, type `Manage app execution aliases`, press Enter
2. Find the entries for **python.exe** and **python3.exe** that point to "App Installer"
3. Toggle both **Off**

If you see entries pointing to "Python 3.12" (the real Python), leave those **On**.

### Verify Python works

Open a **fresh PowerShell window** (PATH changes don't apply to already-open shells). Then:

```powershell
python --version
```

You should see `Python 3.12.x` (or whatever version you installed). If you see "Python was not found" or get pushed to the Microsoft Store, the alias toggle didn't take effect — go back and try again.

## Step 2 — Install Git

If you don't already have Git, download from **https://git-scm.com/download/win**. Use the default options during install. Verify in a fresh PowerShell:

```powershell
git --version
```

## Step 3 — Clone NetPulse and install dependencies

Pick an install location. The convention is `C:\Apps\netpulse`, but anywhere your user account can write to is fine.

```powershell
mkdir C:\Apps -ErrorAction SilentlyContinue
cd C:\Apps
git clone https://github.com/NexusFang-tech/netpulse.git
cd netpulse
```

Create a Python virtual environment and install dependencies into it:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Your prompt should now show `(.venv)` at the start. **Keep this PowerShell window open** for the next steps.

### If PowerShell blocks the activate script

If you get an error like *"running scripts is disabled on this system"*, run this **once** in an elevated (admin) PowerShell window, then close and reopen your regular PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

This is a one-time per-user change and doesn't affect any other software.

## Step 4 — Generate a Meraki API key

1. Log into **https://dashboard.meraki.com**
2. Click your name (top right) → **My profile**
3. Scroll to the **API access** section → click **Generate new API key**
4. **Copy the key immediately** — it's only displayed once. Paste it into Notepad temporarily.

A read-only Meraki user can generate a key. The key inherits your Meraki permissions, so NetPulse can read but cannot write.

If your organization hasn't enabled API access at the org level, you may need an admin to flip it on:
- Meraki Dashboard → **Organization → Configure → API & Webhooks** → enable Dashboard API access

## Step 5 — Find your org ID, network ID, and SSID numbers

In your PowerShell window with `(.venv)` still active, paste your API key into `$key` and run:

```powershell
$key = "PASTE_YOUR_API_KEY_HERE"
$headers = @{ "X-Cisco-Meraki-API-Key" = $key; "Accept" = "application/json" }

# 1. Find your organization ID
Invoke-WebRequest -Uri "https://api.meraki.com/api/v1/organizations" -Headers $headers -UseBasicParsing | Select-Object -ExpandProperty Content

# 2. Find your network ID -- look for one with 'wireless' in productTypes
$orgId = "PASTE_ORG_ID_HERE"
Invoke-RestMethod -Uri "https://api.meraki.com/api/v1/organizations/$orgId/networks" -Headers $headers | Select-Object id, name, productTypes | Format-Table -AutoSize

# 3. Find your SSID numbers and names
$netId = "PASTE_NETWORK_ID_HERE"
Invoke-RestMethod -Uri "https://api.meraki.com/api/v1/networks/$netId/wireless/ssids" -Headers $headers | Where-Object { $_.enabled } | Select-Object number, name | Format-Table -AutoSize
```

Write down: org ID (numeric, e.g. `84824`), network ID (e.g. `L_571394202722633529`), and the **number + name** of each enabled SSID.

## Step 6 — Find your gateway IP

```powershell
ipconfig | findstr "Default Gateway"
```

Note the IPv4 address (something like `10.x.x.1` or `192.168.x.1`). If you see multiple, pick the one on the adapter you're actually using (Wireless LAN adapter Wi-Fi for laptops on WiFi, Ethernet for wired).

## Step 7 — Create config.yaml

```powershell
copy config.example.yaml config.yaml
notepad config.yaml
```

Fill in the placeholders with your real values. Pay particular attention to:

- `meraki.api_key` — your API key from Step 4
- `meraki.organization_id` — from Step 5
- `meraki.network_id` — from Step 5
- `ssids[].name` and `ssids[].number` — for each SSID. **Names are free-form labels; numbers must match Meraki's SSID numbers exactly.**
- `ssids[].critical` — `true` for employee SSIDs, `false` for your "control" network (typically guest). The detection engine needs at least one non-critical SSID to identify the Phase 2 pattern.
- `probes.gateway_ip` — your local default gateway from Step 6

**Save as UTF-8.** Modern Notepad defaults to UTF-8 (without BOM) which is correct. If you use a different editor, make sure to save without a BOM — older Windows tools sometimes add a `\ufeff` prefix that breaks YAML parsing.

## Step 8 — First run

In your PowerShell window (still with `(.venv)` active):

```powershell
python tray.py
```

Within 30 seconds you should see:

- A **NetPulse icon** in the system tray (click `^` in the taskbar to expand if hidden)
- Your **browser opens** to http://localhost:8787
- The dashboard shows **HEALTHY** in the top-right
- **SSID tiles populate** with current client counts
- **Local probes** show green checkmarks with latency in milliseconds

If the SSID tiles show 0 clients indefinitely, your SSID `number` fields don't match Meraki — re-run the SSID lookup from Step 5 and verify they're identical.

To stop NetPulse: **right-click the tray icon → Quit**, or press Ctrl+C in the PowerShell window where it's running.

---

# Live Settings Panel

Click the **⚙ CONFIG** button in the top-right of the dashboard to open the settings panel. Adjustable values:

| Setting | Default | Range | What it does |
|---|---|---|---|
| Baseline Window | 1800 (30 min) | 300–7200 sec | History window that defines "normal." Longer resists false resets but adapts slower. |
| Baseline Percentile | 90 | 50–100 | Which percentile of that window counts as peak. 90 = typical peak, 100 = absolute max. |
| Drop Threshold | 30% | 10–80% | Percentage drop from baseline that triggers an alert. Lower = more sensitive. |
| Minimum Samples | 5 | 2–30 | Samples needed before the baseline is trusted. Prevents startup false alarms. |
| Ping Failure Threshold | 3 | 1–10 | Consecutive failed pings before marking gateway/external down. |
| Alert Dedupe Window | 300 (5 min) | 30–1800 sec | Minimum seconds between duplicate notifications. |
| Desktop Notifications | On | toggle | Fire toast notifications on alerts. |
| Alert Sounds | On | toggle | Play sound when alerts fire. |
| Phase 2 Pattern Detection | On | toggle | Flag the specific "critical SSIDs drop, guest stable" pattern. |

Changes save to `config.yaml` (a `.bak` is created automatically before overwriting) and **hot-reload** the detection engine immediately — no restart needed. Values outside valid ranges are rejected by the API.

The following settings still require editing `config.yaml` manually + restart:

- Meraki API key, org ID, network ID
- SSID definitions (names, numbers, critical flags)
- Probe targets (gateway, DNS server, external IP)
- Database path + retention
- Web host + port

---

# Optional: Autostart on Login

To have NetPulse start automatically when you log in:

1. Press **Win+R**, type `shell:startup`, press Enter
2. Right-click in the folder that opens → **New → Shortcut**
3. **Browse** to `C:\Apps\netpulse\run.bat`
4. Click **Next**, name it "NetPulse", click **Finish**

Next login, NetPulse starts itself. The tray icon appears in your taskbar.

---

# File Layout

```
netpulse/
├── app/
│   ├── main.py              # FastAPI app + API endpoints
│   ├── collector.py         # Scheduler orchestrating Meraki polls + local probes
│   ├── meraki_client.py     # Meraki API wrapper (read-only)
│   ├── probes.py            # Ping / DNS / DHCP lease probes
│   ├── detection.py         # Pattern matching engine
│   ├── notifier.py          # Desktop toasts + sound
│   ├── database.py          # SQLite storage
│   ├── settings.py          # Settings schema, validation, config writer
│   ├── static/              # Dashboard CSS + JS
│   └── templates/           # Dashboard HTML
├── data/                    # SQLite DB (created on first run, gitignored)
├── logs/                    # Runtime logs (gitignored)
├── config.example.yaml      # Template config
├── config.yaml              # Your config (gitignored, you create from example)
├── config.yaml.bak          # Created automatically on each settings save
├── requirements.txt
├── run.bat                  # Windows launcher (auto-creates venv)
└── tray.py                  # System-tray entry point
```

# API Endpoints

All endpoints are bound to `127.0.0.1` and not exposed to the network.

- `GET /api/status` — current verdict + all SSID statuses
- `GET /api/probes/latest` — last result for each probe type
- `GET /api/probes/history?type=ping_gateway&minutes=30`
- `GET /api/ssid/history?name=employee&minutes=60`
- `GET /api/events?limit=50` — recent Meraki events
- `GET /api/incidents?limit=25` — recent detected incidents
- `POST /api/incidents/{id}/acknowledge`
- `GET /api/settings` — schema + current values of adjustable settings
- `POST /api/settings` — body `{updates: {key: value, ...}}`, validates and hot-reloads

# Why `clientCountHistory` instead of `/clients`

Modern phones use MAC randomization, which causes Meraki's `/networks/{id}/clients` endpoint to omit the SSID field for affected clients. The `clientCountHistory` endpoint aggregates server-side per SSID number and always returns accurate counts regardless of MAC randomization.

# Privacy

NetPulse is a local tool. No data leaves your network except read-only Meraki API calls to your own dashboard. All historical data is stored in a local SQLite file. The dashboard binds to `127.0.0.1` only — not accessible from other machines on the network.

---

# Troubleshooting

### "Python was not found" when running commands
Either Python isn't installed, or the Microsoft Store alias is hijacking the command. See **Step 1** — install from python.org and disable the App Execution Aliases.

### `pip install` fails on Pillow with "zlib not found" or compilation errors
You're on Python 3.14 trying to install an older Pillow that doesn't have a 3.14 wheel. The current `requirements.txt` uses `Pillow>=11.0.0` which has full 3.14 wheel coverage. If you see this error, you likely have an outdated copy of `requirements.txt` — pull the latest from the repo.

### `pip install` fails with "Could not find version that satisfies win10toast-click==0.1.3"
Same as above — outdated requirements.txt. The correct pin is `win10toast-click==0.1.2` (the only version published to PyPI).

### Activate script blocked: "running scripts is disabled on this system"
Run this once in an admin PowerShell, then reopen your regular PowerShell:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### "Meraki API key not configured -- running in probe-only mode" in logs
Your `config.yaml` still has the placeholder key, or the file has a UTF-8 BOM that's breaking YAML parsing. Fix the key first; if that's already set, check encoding:
```powershell
python -c "data = open('config.yaml', 'rb').read(); print('Has BOM:', data.startswith(b'\xef\xbb\xbf'))"
```
If `Has BOM: True`, rewrite the file:
```powershell
$content = Get-Content config.yaml -Raw
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$PWD\config.yaml", $content, $utf8NoBom)
```

### Meraki API returns empty array for `/organizations`
Your account has dashboard access but no organization-level API access. Ask your Meraki admin to add your user as an organization administrator (read-only is fine) under **Organization → Administrators**.

### Dashboard loads but SSID tiles show 0 clients forever
SSID `number` fields in `config.yaml` don't match Meraki. Re-run the SSID lookup from **Step 5** and verify numbers match exactly. Note that SSID numbers are 0-indexed.

### Settings panel doesn't open when clicking ⚙ CONFIG
Browser cache. Press **Ctrl+F5** to force a full reload. If that doesn't work, fully quit NetPulse (right-click tray → Quit) and restart with `python tray.py` to confirm the latest static files are being served.

### Can't stop the tray app
Right-click the tray icon → Quit. If the icon is unresponsive: **Task Manager → Details tab → end `python.exe`** processes.

### Need to revert a settings change
Each settings save creates `config.yaml.bak`. If you want to revert: stop NetPulse, copy the .bak over `config.yaml`, restart.

---

# License

MIT

---

*Built by Matt Keith ([NexusFang-tech](https://github.com/NexusFang-tech)) — 2026*
