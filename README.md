# NetPulse

**Real-time network outage monitor and DHCP diagnostic dashboard for Meraki + Fortigate environments.**

NetPulse runs as a system tray app on a Windows machine and surfaces network problems before email alerts arrive. It correlates four data sources — the Meraki Dashboard API, local network probes, active DHCP server probes, and Meraki wireless events — to detect specific outage patterns and tell you exactly what to do about each one.

The tool was built around a recurring near-daily DHCP outage on a hybrid Fortigate + Meraki + Azure DC environment, but it works for any Meraki-based network where DHCP lives upstream of a tunnel.

![v1.3 Dashboard](https://raw.githubusercontent.com/NexusFang-tech/netpulse/main/docs/dashboard.png)

---

## What it monitors

### Detection patterns

When NetPulse fires an alert, it identifies the specific failure mode:

| Pattern | What triggered it | Recommended action |
|---|---|---|
| `DHCP_SERVICE_DOWN` | DHCP probes failing alongside other DC services | RDP the DC, restart DHCP service |
| `DHCP_SERVER_UNREACHABLE` | Domain Controller unreachable from this host | Check S2S VPN tunnel, verify Azure VM is running |
| `PHASE_2_LIKELY` | Critical SSIDs dropping while guest SSID stays healthy | Bounce Phase 2 selectors on the Fortigate |
| `MERAKI_DHCP_ALERT` | Meraki reporting DHCP events network-wide | Check DHCP scope utilization |
| `UPSTREAM_DOWN` | Local gateway OK but external pings failing | ISP / WAN-side issue |
| `LOCAL_GATEWAY_DOWN` | Gateway unreachable from this host | Local network or client issue |
| `GENERIC_DEGRADATION` | Drops that don't match a known pattern | Investigate manually |

Each verdict on the dashboard includes specific remediation steps, AP-level attribution ("most affected APs: Albemarle (-5), Coates (-3)"), and a sample of which devices recently dropped.

### Live data sources

- **Per-SSID client counts** from Meraki's `clientCountHistory` endpoint (every 30s)
- **Full client roster** with names, manufacturers, OS, IPs, AP attribution (every 30s)
- **Per-AP client counts** for spatial outage attribution (every 30s)
- **Local probes** — gateway ping, external ping, DNS lookup, DHCP lease state (every 15s)
- **DHCP server probes (v1.3)** — ICMP, DNS, LDAP, and active DHCPINFORM packet against the DC (every 15s)
- **Meraki wireless events** — DHCP NACKs, mass disassociations, association events (every 30s)
- **Meraki assurance alerts** (every 30s)

### Dashboard panels

1. **Verdict card** — overall status with pattern, summary, and remediation steps
2. **DHCP Health (v1.3)** — four probe tiles (DHCP INFORM, DC ICMP, DC DNS, DC LDAP) with live latency, 15-min reliability, and sparklines. Recent DHCP events bar at the bottom.
3. **SSID Telemetry** — live client count per SSID with peak/baseline tracking
4. **Local Probes** — gateway, external, DNS, DHCP lease state
5. **Access Point Activity** — every wireless AP with current vs. baseline client count, click for device list
6. **Devices Online** — full searchable, sortable device roster: name, manufacturer, SSID, AP, IP, VLAN, last seen
7. **Recent Disconnects** — who just dropped, with AP attribution
8. **Event Feed** — Meraki wireless events
9. **Incident Log** — auto-detected incidents with pattern, duration, status

---

## Architecture

- **FastAPI** backend with **APScheduler** for polling
- **SQLite** storage with WAL mode and configurable retention (default 30 days)
- **Retrowave dashboard** at `127.0.0.1:8787` — designed to live pinned on a spare monitor
- **Live configuration panel** for tuning detection thresholds without restarting
- **System tray launcher** via pystray — left-click to open dashboard, right-click to quit
- **Desktop notifications** via win10toast on Windows
- All Meraki API calls are **read-only GET requests** — the tool cannot modify your network

### Tech stack

Python 3.12+ · FastAPI · APScheduler · SQLite · Jinja2 · pystray · win10toast · Plain ES5 JS · CSS (no frameworks)

---

# Setup Guide

This guide walks through a complete first-time install on a Windows machine with no Python installed. Total time: **~25 minutes** including grabbing your Meraki IDs.

## Step 1 — Install Python

1. Go to **https://www.python.org/downloads/windows/**
2. Download the latest **Python 3.12.x** Windows installer (64-bit recommended). Python 3.13 and 3.14 also work.
3. Run the installer. **Critical**: on the first screen, check the box that says **"Add python.exe to PATH"** at the bottom before clicking Install.
4. Click **Install Now** (the default options are fine).
5. When it finishes, click **"Disable path length limit"** if it appears.

### Disable Microsoft Store's Python alias

Windows ships an "App Execution Alias" for `python` that redirects to the Microsoft Store and interferes with real Python:

1. Press **Win**, type `Manage app execution aliases`, press Enter
2. Find the entries for **python.exe** and **python3.exe** that point to "App Installer"
3. Toggle both **Off**

Verify in a fresh PowerShell window:

```powershell
python --version
```

You should see `Python 3.12.x`. If you see "Python was not found" or get pushed to the Microsoft Store, the alias toggle didn't take effect.

## Step 2 — Install Git

If you don't already have Git, download from **https://git-scm.com/download/win**. Use the default options. Verify in a fresh PowerShell:

```powershell
git --version
```

## Step 3 — Clone NetPulse and install dependencies

```powershell
mkdir C:\Apps -ErrorAction SilentlyContinue
cd C:\Apps
git clone https://github.com/NexusFang-tech/netpulse.git
cd netpulse
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Your prompt should now show `(.venv)` at the start. Keep this PowerShell window open.

### If PowerShell blocks the activate script

Run this once in an admin PowerShell, then reopen your regular shell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Step 4 — Generate a Meraki API key

1. Log into **https://dashboard.meraki.com**
2. Click your name (top right) → **My profile**
3. Scroll to **API access** → **Generate new API key**
4. **Copy the key immediately** — it's only displayed once.

A read-only Meraki user can generate a key. The key inherits your permissions, so NetPulse can read but cannot write.

If your organization hasn't enabled API access at the org level, an admin needs to flip it on under **Organization → Configure → API & Webhooks → Dashboard API access**.

## Step 5 — Find your Meraki IDs

In your PowerShell window with `(.venv)` active:

```powershell
$key = "PASTE_YOUR_API_KEY_HERE"
$headers = @{ "X-Cisco-Meraki-API-Key" = $key; "Accept" = "application/json" }

# 1. Organization ID
Invoke-WebRequest -Uri "https://api.meraki.com/api/v1/organizations" -Headers $headers -UseBasicParsing | Select-Object -ExpandProperty Content

# 2. Network ID -- look for one with 'wireless' in productTypes
$orgId = "PASTE_ORG_ID_HERE"
Invoke-RestMethod -Uri "https://api.meraki.com/api/v1/organizations/$orgId/networks" -Headers $headers | Select-Object id, name, productTypes | Format-Table -AutoSize

# 3. SSID numbers + names
$netId = "PASTE_NETWORK_ID_HERE"
Invoke-RestMethod -Uri "https://api.meraki.com/api/v1/networks/$netId/wireless/ssids" -Headers $headers | Where-Object { $_.enabled } | Select-Object number, name | Format-Table -AutoSize
```

Note: org ID, network ID, and the **number + name** of each enabled SSID.

## Step 6 — Find your gateway and DHCP server IPs

```powershell
ipconfig /all | Select-String "Default Gateway|DHCP Server"
```

Note both:
- **Default Gateway** — typically your local Fortigate or Meraki MX (e.g., `192.168.X.1`)
- **DHCP Server** — the actual server handing out leases (often a Domain Controller, e.g., `10.X.X.X` if it's hosted in Azure over a S2S tunnel)

If your DHCP server is on a different IP from your gateway, that's the typical Fortigate-relay-to-DC topology that NetPulse v1.3's DHCP probes are designed for.

## Step 7 — Find your AD domain (for DHCP probing)

If you're on a domain-joined machine:

```powershell
$env:USERDNSDOMAIN
```

That returns your AD domain (e.g., `your-domain.org`). Lowercase it for the config.

If you're not domain-joined or don't have an AD setup, you can skip this and disable the DC DNS probe later.

## Step 8 — Create config.yaml

```powershell
copy config.example.yaml config.yaml
notepad config.yaml
```

Fill in the placeholders. The required sections are commented inline in the file. Critical fields:

- **`meraki.api_key`** — from Step 4
- **`meraki.organization_id`** — from Step 5
- **`meraki.network_id`** — from Step 5
- **`ssids[]`** — for each SSID. Names are free-form; numbers must match Meraki exactly. Mark employee SSIDs `critical: true`, guest SSIDs `critical: false`.
- **`probes.gateway_ip`** — your local default gateway from Step 6
- **`dhcp_server.server_ip`** — your DHCP server IP from Step 6
- **`dhcp_server.ad_domain`** — your AD domain from Step 7

Save as UTF-8 (Notepad's default is correct — without BOM).

### Example config for an Azure-hosted DC

```yaml
meraki:
  api_key: "abc123..."
  organization_id: "12345"
  network_id: "L_98765"
  poll_interval_seconds: 30
  device_poll_interval_seconds: 30
  device_offline_threshold_seconds: 90

ssids:
  - { name: "employee", number: 0, critical: true }
  - { name: "employee-mobile", number: 3, critical: true }
  - { name: "guest", number: 2, critical: false }

probes:
  enabled: true
  interval_seconds: 15
  gateway_ip: "192.168.X.1"
  external_target: "1.1.1.1"
  dns_test_domain: "your-domain.org"
  dns_server: ""
  ping_timeout_ms: 2000

dhcp_server:
  enabled: true
  server_ip: "10.X.X.X"          # Domain Controller IP
  ad_domain: "your-domain.org"
  inform_probe_enabled: true
  inform_timeout_ms: 3000
  server_timeout_ms: 3000

detection:
  baseline_window_seconds: 1800
  baseline_percentile: 90
  baseline_min_samples: 5
  client_drop_percent: 30
  ping_failure_threshold: 3
  dhcp_failure_threshold: 2
  dns_slow_threshold_ms: 500
  phase2_pattern_detection: true

alerts:
  desktop_notifications: true
  sound_enabled: true
  sound_file: ""
  dedupe_seconds: 300

database:
  path: "data/netpulse.db"
  retention_days: 30

web:
  host: "127.0.0.1"
  port: 8787
  refresh_interval_ms: 5000
```

## Step 9 — First run

```powershell
python tray.py
```

Within 30 seconds:
- A NetPulse icon appears in your system tray
- Your browser opens to **http://localhost:8787**
- The verdict shows **HEALTHY**
- All panels begin populating

To stop NetPulse: right-click the tray icon → **Quit**.

---

# DHCP Diagnostics (v1.3 deep dive)

The DHCP Health panel is the headline feature of v1.3. It runs four probes against your DHCP server every 15 seconds and tells you exactly which layer is failing when DHCP misbehaves.

## What each probe tests

| Probe | Mechanism | What it tells you |
|---|---|---|
| **DC ICMP** | `ping <server_ip>` | The VM is reachable and the network path is up (S2S tunnel, routing, etc.) |
| **DC DNS** | `nslookup <ad_domain> <server_ip>` | DNS service is responding (most DCs run DNS) |
| **DC LDAP** | TCP connect to `<server_ip>:389` | AD/LDAP service is alive |
| **DHCP INFORM** | Crafted UDP DHCPINFORM packet → `<server_ip>:67` | Direct test of the DHCP service itself |

All four probes are read-only and harmless. The DHCP INFORM probe queries the server for configuration without requesting a lease (per RFC 2131).

## Reading the dashboard during an outage

The probes lit up in different combinations tell you different things:

### All four probes red
**Cause:** Either the S2S VPN tunnel is down, the Azure VM is stopped, or there's a routing failure between you and the DC.

**Action:** Check Fortigate VPN tunnel state:
```
diagnose vpn tunnel list | grep <azure-tunnel-name>
```
Check Azure portal — is the VM still running?

### DC ICMP red, others red as well
Same as above — the network path is broken.

### DC ICMP green, but DC DNS or LDAP red
**Cause:** The VM is up but services are misbehaving. Could be VM resource pressure, mid-update, or service crash.

**Action:** Azure portal → VM → Metrics. Look for CPU/memory spikes. Check the VM's activity log for recent reboots or maintenance.

### Three probes green, only DHCP INFORM red
**Cause:** Most likely **environmental**, not a real DHCP outage. Many enterprise firewalls (including most Fortigates with default config) block unicast UDP/67 traffic between subnets as anti-rogue-DHCP-server hardening. The DHCP service is fine, but the INFORM probe can't reach it directly.

**Behavior:** NetPulse renders this as a **yellow ADVISORY** state instead of a red FAIL. The overall verdict stays HEALTHY because INFORM-alone is no longer trusted as a smoking gun (see "Detection corroboration" below).

**Action:** None usually needed. If you want true direct DHCP probing, either:
1. Add a Fortigate firewall rule allowing your monitoring host to send UDP/67 to the DC, or
2. Run NetPulse from a machine on the same VLAN as the DC (where it qualifies as a legitimate relay agent)

### DHCP INFORM red AND another probe red
**Cause:** This is real. Two corroborating signals = NetPulse fires `DHCP_SERVICE_DOWN`.

**Action:** RDP the DC and restart the DHCP service:
```powershell
Get-Service DHCPServer
Restart-Service DHCPServer
```
Check the DHCP event log: Event Viewer → Applications and Services Logs → Microsoft → Windows → DHCP-Server.

## Detection corroboration logic

NetPulse v1.3.1 treats the four probes hierarchically:

| Combination | Verdict | Reasoning |
|---|---|---|
| All probes green | `HEALTHY` | Everything's fine |
| INFORM red, others green | `HEALTHY` (advisory shown) | INFORM is environmental, not trustworthy alone |
| DC ICMP red | `DHCP_SERVER_UNREACHABLE` | VM/tunnel issue |
| DNS + LDAP both red | `DHCP_SERVICE_DOWN` | VM is degraded |
| INFORM + (DNS or LDAP) red | `DHCP_SERVICE_DOWN` | Corroborated DHCP failure |

This avoids the false-alarm trap where INFORM is permanently blocked by a firewall but everything is actually fine.

## DHCP probe history

Each probe stores 15 minutes of rolling history in SQLite. The dashboard sparklines show the last 30 samples (~7.5 min) of latency per probe. Red bars in the sparkline indicate failed samples — you can see at a glance whether a probe has been flaky over time even when "currently healthy."

For longer historical analysis, hit:

```
GET /api/dhcp/probe_history?probe_type=dhcp_inform&minutes=120
```

Returns the raw samples as JSON for any of the four probe types.

---

# Live Settings Panel

Click **⚙ CONFIG** in the top-right of the dashboard to open the settings modal. Adjustable values:

| Setting | Default | Range | What it does |
|---|---|---|---|
| Baseline Window | 1800 (30m) | 300–7200 | History window that defines "normal" SSID/AP client counts |
| Baseline Percentile | 90 | 50–100 | Percentile of that window treated as "peak" |
| Drop Threshold | 30% | 10–80% | % drop from baseline that triggers an alert |
| Minimum Samples | 5 | 2–30 | Samples needed before baseline is trusted |
| Ping Failure Threshold | 3 | 1–10 | Consecutive failed pings before marking down |
| **DHCP Failure Threshold** | **2** | **1–5** | **Consecutive failed DHCP probes before firing DHCP_SERVER_DOWN. Lower = faster detection.** |
| Device Offline Timeout | 90 | 30–600 sec | How long a device must be quiet before marked offline |
| Alert Dedupe Window | 300 (5m) | 30–1800 | Min seconds between duplicate notifications |
| Desktop Notifications | On | toggle | Toast notifications on alerts |
| Alert Sounds | On | toggle | Sound when alerts fire |
| **DHCP INFORM Probe** | **On** | **toggle** | **Disable if security tools flag the unicast DHCP packets** |
| Phase 2 Pattern Detection | On | toggle | Flag the "critical SSIDs drop, guest stable" pattern |

Changes save to `config.yaml` (a `.bak` is created automatically) and **hot-reload** the detection engine immediately — no restart needed.

The following settings still require editing `config.yaml` manually + restart:

- Meraki API key, org ID, network ID
- SSID definitions
- Probe targets (gateway, DNS server, external IP)
- **DHCP server IP and AD domain**
- Database path + retention
- Web host + port

---

# Optional: Autostart on Login

1. Press **Win+R**, type `shell:startup`, press Enter
2. Right-click in the folder → **New → Shortcut**
3. Browse to `C:\Apps\netpulse\run.bat`
4. Name it "NetPulse"

Next login, NetPulse starts itself.

---

# File Layout

```
netpulse/
├── app/
│   ├── main.py              # FastAPI app + API endpoints
│   ├── collector.py         # Scheduler -- Meraki polls, device roster, local + DHCP probes
│   ├── meraki_client.py     # Meraki API wrapper (read-only)
│   ├── probes.py            # ping / DNS / DHCP lease state / DHCP server probes (DHCPINFORM)
│   ├── detection.py         # Pattern matching + AP attribution + DHCP corroboration logic
│   ├── notifier.py          # Desktop toasts + sound
│   ├── database.py          # SQLite -- ssid_metrics, devices, sessions, ap_metrics, probes
│   ├── settings.py          # Settings schema, validation, config writer
│   ├── static/              # Dashboard CSS + JS
│   └── templates/           # Dashboard HTML
├── data/                    # SQLite DB (gitignored)
├── logs/                    # Runtime logs (gitignored)
├── config.example.yaml
├── config.yaml              # Your config (gitignored)
├── config.yaml.bak          # Auto-created on each settings save
├── requirements.txt
├── run.bat                  # Windows launcher
└── tray.py                  # System-tray entry point
```

# API Reference

All endpoints bound to `127.0.0.1` only — not accessible from other machines.

### Status & verdict
- `GET /api/status` — current verdict, SSID statuses, affected APs, dropped device sample
- `GET /healthz` — process liveness check

### Probes
- `GET /api/probes/latest` — last result for each local probe type
- `GET /api/probes/history?type=ping_gateway&minutes=30` — local probe time series
- `GET /api/ssid/history?name=employee&minutes=60` — per-SSID client count history

### DHCP
- `GET /api/dhcp/health` — full DHCP Health panel data: 4 probe summaries, advisory flag, recent DHCP events, local lease info
- `GET /api/dhcp/probe_history?probe_type=dhcp_inform&minutes=60` — raw samples for any DHCP probe (`dhcp_dc_ping`, `dhcp_dc_dns`, `dhcp_dc_ldap`, `dhcp_inform`)

### Devices & APs
- `GET /api/devices?status=online|all|offline&limit=500`
- `GET /api/devices/recent_drops?minutes=30&limit=100`
- `GET /api/aps` — current per-AP client counts with health status
- `GET /api/aps/{ap_name}/history?minutes=60`

### Events & incidents
- `GET /api/events?limit=50` — Meraki wireless events
- `GET /api/incidents?limit=25` — auto-detected incidents
- `POST /api/incidents/{id}/acknowledge`

### Settings
- `GET /api/settings` — schema + current values
- `POST /api/settings` — body `{updates: {key: value, ...}}`, validates and hot-reloads

---

# Troubleshooting

### "Python was not found" when running commands
Either Python isn't installed, or the Microsoft Store alias is hijacking the command. See **Step 1** — install from python.org and disable the App Execution Aliases.

### `pip install` fails on Pillow with compilation errors
Outdated `requirements.txt`. Pull the latest from the repo — current pin is `Pillow>=11.0.0` which has full Python 3.14 wheel coverage.

### Activate script blocked
Run once in admin PowerShell:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### "Meraki API key not configured -- running in probe-only mode"
`config.yaml` has the placeholder key, or has a UTF-8 BOM breaking YAML parsing. Check encoding:
```powershell
python -c "data = open('config.yaml', 'rb').read(); print('Has BOM:', data.startswith(b'\xef\xbb\xbf'))"
```
If `True`, rewrite without BOM:
```powershell
$content = Get-Content config.yaml -Raw
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$PWD\config.yaml", $content, $utf8NoBom)
```

### Meraki API returns empty array for `/organizations`
Your account has dashboard access but no organization-level API access. Ask your Meraki admin to add your user as an organization administrator (read-only is fine).

### Dashboard loads but SSID/AP/Device panels stay empty
Check `logs/netpulse.log` for Meraki API errors. Common causes: wrong API key, wrong network ID, network has no wireless, or API key user lacks permissions for that org/network.

### DHCP HEALTH panel shows "no data yet" indefinitely
The `dhcp_server` config block isn't present or `enabled: false`. Verify:
```powershell
Select-String -Path C:\Apps\netpulse\config.yaml -Pattern "dhcp_server|server_ip|ad_domain"
```
Should return at least 4 matches. If empty, you need to add the `dhcp_server:` section (see Step 8 example).

### DHCP INFORM probe permanently fails (yellow ADVISORY)
Expected in many environments. The DHCPINFORM packet is unicast UDP/67 to the DC and most enterprise firewalls drop this traffic between subnets. The other three probes (ICMP, DNS, LDAP) cover ~80% of the diagnostic space. The dashboard correctly displays this as advisory rather than firing false outage alerts.

To enable INFORM end-to-end:
1. Add a Fortigate firewall rule allowing the monitoring host's IP to send UDP/67 to the DC, or
2. Run NetPulse from a host on the same VLAN as the DC

Or just disable the probe in the Settings panel if you don't want to see the advisory state.

### AP grid showing switches or MX firewall as "APs"
This was a bug fixed in v1.2.1. If you're seeing it on a fresh install, pull the latest. If you're upgrading and the issue persists, the SQLite database has stale entries that need cleanup:
```powershell
cd C:\Apps\netpulse
.\.venv\Scripts\Activate.ps1
python -c "
import sqlite3
con = sqlite3.connect('data/netpulse.db')
con.execute('DELETE FROM ap_metrics WHERE ap_name LIKE \"%MS350%\" OR ap_name LIKE \"%MS42%\" OR ap_name LIKE \"%MS250%\" OR ap_name LIKE \"MX%\"')
con.commit()
con.close()
print('Cleaned')
"
```

### Settings panel doesn't open when clicking ⚙ CONFIG
Browser cache. Press **Ctrl+F5** to force a full reload. If that doesn't work, fully quit NetPulse (right-click tray → Quit) and restart with `python tray.py`.

### Device names show as MAC addresses
Meraki only knows a "description" if someone has named the device in the Meraki dashboard, or the device's DHCP hostname is set. For unnamed devices, NetPulse shows manufacturer + MAC. To set friendly names, edit each client in the Meraki dashboard.

### Need to revert a settings change
`config.yaml.bak` is created on every save. Stop NetPulse, copy the backup over `config.yaml`, restart.

---

# Privacy & Security

NetPulse is a local internal tool intended for IT/network administrators monitoring their own infrastructure.

- **No outbound data transmission** except read-only Meraki API calls to your own dashboard
- **All historical data stored locally** in a SQLite file on the monitoring host
- **Dashboard binds to `127.0.0.1` only** — not accessible from other machines on the network
- **Read-only Meraki access** — the tool cannot modify your network configuration
- **DHCP probes are read-only** — DHCPINFORM queries server config without requesting leases
- **Device tracking is internal** — MAC addresses, hostnames, and device descriptions are stored locally for diagnostic purposes. Use accordingly per your organization's privacy policies.

---

# Version History

- **v1.3.1** — DHCPINFORM corroboration logic. INFORM-only failure now treated as advisory (likely firewall filtering, not real outage). Detection requires INFORM + (DNS or LDAP) failing together to fire DHCP_SERVICE_DOWN.
- **v1.3** — DHCP Diagnostic Suite. Four active probes against the DC (ICMP, DNS, LDAP, DHCPINFORM). New DHCP Health panel with sparklines. New detection patterns: `DHCP_SERVICE_DOWN` and `DHCP_SERVER_UNREACHABLE`. Two new settings panel knobs.
- **v1.2.1** — AP grid filter fix. Switches and MX firewalls no longer appear in the Access Point Activity panel. Layout regrid: Devices Online on left, log panels stacked on right.
- **v1.2** — Device-level tracking. Per-MAC device records with descriptions, manufacturers, OS, IPs. AP attribution for outages. Recent Disconnects panel. Three new dashboard panels.
- **v1.1** — Live Settings Panel with hot-reload. Smarter baseline math (90th percentile over 30-min window instead of 2-min raw max).
- **v1.0** — Initial release. Per-SSID monitoring, local probes, Phase 2 pattern detection, system tray app.

---

# License

MIT

---

*Built by Matt Keith ([NexusFang-tech](https://github.com/NexusFang-tech)) — 2026*
