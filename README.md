# NetPulse

**Real-time network outage monitor for Meraki + Fortigate environments.**

A desktop dashboard that surfaces specific outage patterns before email alerts arrive. Built to solve a recurring DHCP tunnel issue where employee SSIDs would degrade while guest networks stayed healthy — a signature that pointed directly to an IPsec Phase 2 problem.

Instead of waiting 15 minutes for Meraki''s email notification, NetPulse detects the pattern in about 30 seconds by correlating:

- Per-SSID client counts from the Meraki Dashboard API
- Local ICMP probes to the gateway and external targets
- DNS resolution latency
- Local DHCP lease state from `ipconfig`
- Meraki wireless events (DHCP NACKs, mass disassociations) and assurance alerts

When critical SSIDs degrade while the control SSID stays healthy, NetPulse fires a desktop notification with a specific remediation recommendation.

![Dashboard](docs/dashboard.png)

## Detection patterns

| Pattern | Meaning | Action |
|---|---|---|
| `PHASE_2_LIKELY` | Critical SSIDs dropping + control SSID healthy | Bounce Phase 2 selectors on Fortigate |
| `MERAKI_DHCP_ALERT` | Meraki reporting DHCP events network-wide | Check DHCP server + scope exhaustion |
| `UPSTREAM_DOWN` | Gateway OK, external ping failing | ISP / WAN issue, not internal |
| `LOCAL_GATEWAY_DOWN` | Gateway unreachable from this host | Likely local to the monitoring host |
| `GENERIC_DEGRADATION` | Drops that don''t match a known pattern | Monitor |

## Architecture

- **FastAPI** backend with APScheduler for polling (Meraki every 30s, local probes every 15s)
- **SQLite** storage with configurable retention (default 30 days)
- **Retrowave dashboard** served locally, designed to live pinned on a spare monitor
- **System tray launcher** via pystray — runs in the background, left-click to open dashboard
- **Desktop notifications** via win10toast on Windows
- All Meraki API calls are read-only GET requests — the tool cannot modify your network

## Tech stack

Python 3.12+ · FastAPI · APScheduler · SQLite · Jinja2 · pystray · win10toast · Plain ES5 JS · CSS (no frameworks)

## Setup

1. Install Python 3.12+ and make sure it''s on PATH
2. Clone the repo, `cd` into it
3. `python -m venv .venv && .\.venv\Scripts\Activate.ps1`
4. `pip install -r requirements.txt`
5. `copy config.example.yaml config.yaml` and fill in your Meraki API key, org ID, network ID, and SSID numbers
6. `python tray.py`

Dashboard opens automatically at `http://localhost:8787`. Tray icon provides quick access and clean shutdown.

### Finding your SSID numbers

SSIDs in Meraki are referenced by number, not name. To find yours:

```powershell
$headers = @{ "X-Cisco-Meraki-API-Key" = "YOUR_KEY"; "Accept" = "application/json" }
Invoke-RestMethod -Uri "https://api.meraki.com/api/v1/networks/YOUR_NETWORK_ID/wireless/ssids" -Headers $headers | Where-Object { $_.enabled } | Select-Object number, name
```

## Why `clientCountHistory` instead of `/clients`

Modern phones use MAC randomization, which causes Meraki''s `/networks/{id}/clients` endpoint to omit the SSID field for affected clients. The `clientCountHistory` endpoint aggregates server-side per SSID number and always returns accurate counts regardless of MAC randomization.

## Privacy

NetPulse is a local tool. No data leaves your network except read-only Meraki API calls to your own dashboard. All historical data is stored in a local SQLite file.

## License

MIT