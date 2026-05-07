"""Meraki Dashboard API client.

Read-only endpoints for device-level monitoring. Polls:
- per-SSID client counts (clientCountHistory)
- full client roster with names/hostnames/AP attribution
- per-AP client counts
- network events
- assurance alerts
"""
import logging
from typing import Any

import httpx


log = logging.getLogger(__name__)

BASE_URL = "https://api.meraki.com/api/v1"


class MerakiClient:
    def __init__(self, api_key: str, organization_id: str, network_id: str):
        self.api_key = api_key
        self.organization_id = organization_id
        self.network_id = network_id
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "X-Cisco-Meraki-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "NetPulse/1.2",
            },
            timeout=20.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = await self._client.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            log.warning("Meraki API %s returned %s: %s",
                        path, e.response.status_code, e.response.text[:200])
            raise
        except httpx.RequestError as e:
            log.warning("Meraki API %s request error: %s", path, e)
            raise

    # ------------------------------------------------------------------
    async def get_ssid_clients(self, ssid_numbers: list[int] | None = None,
                               ssid_name_map: dict[int, str] | None = None,
                               timespan_seconds: int = 600) -> list[dict]:
        """Per-SSID client counts via clientCountHistory.

        See get_clients() for the full per-device roster.
        """
        if not ssid_numbers:
            return []
        name_map = ssid_name_map or {}
        resolution = 300
        if timespan_seconds < resolution * 2:
            timespan_seconds = resolution * 2

        results: list[dict] = []
        for num in ssid_numbers:
            try:
                data = await self._get(
                    f"/networks/{self.network_id}/wireless/clientCountHistory",
                    params={
                        "timespan": timespan_seconds,
                        "resolution": resolution,
                        "ssid": num,
                    },
                )
            except httpx.HTTPStatusError:
                continue
            if not data:
                continue
            latest_count = 0
            for bucket in reversed(data):
                cc = bucket.get("clientCount")
                if cc is not None:
                    latest_count = int(cc)
                    break
            results.append({
                "ssidName": name_map.get(num, f"ssid-{num}"),
                "ssidNumber": num,
                "clientCount": latest_count,
                "usageMb": 0.0,
            })
        return results

    async def get_clients(self, timespan_seconds: int = 300, per_page: int = 1000) -> list[dict]:
        """Full client roster -- the rich per-device data.

        Returns list of dicts with: mac, description, dhcpHostname, manufacturer,
        os, ip, ssid, recentDeviceName (AP), vlan, status, usage{sent,recv}, lastSeen.
        """
        try:
            data = await self._get(
                f"/networks/{self.network_id}/clients",
                params={"timespan": timespan_seconds, "perPage": per_page},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError as e:
            log.warning("get_clients failed: %s", e)
            return []

    async def get_devices(self) -> list[dict]:
        """All Meraki devices (APs, switches, MX) in the network. Returns list with
        name, serial, model, lanIp, mac, etc."""
        try:
            return await self._get(f"/networks/{self.network_id}/devices") or []
        except httpx.HTTPStatusError:
            return []

    async def get_ap_client_counts(self) -> list[dict]:
        """Per-AP client counts. Strictly limited to wireless APs (MR/CW models)
        -- switches, MX firewalls, and cameras are excluded so the AP grid only
        shows actual access points.

        Returns list of {serial, name, clientCount}.
        """
        clients = await self.get_clients(timespan_seconds=300)
        devices = await self.get_devices()

        # Build serial -> name map for APs ONLY (model starts with 'CW' or 'MR').
        # This is the canonical list of APs -- nothing outside this map is an AP.
        ap_serial_to_name: dict[str, str] = {}
        for d in devices:
            model = (d.get("model") or "")
            if model.startswith("MR") or model.startswith("CW"):
                serial = d.get("serial", "")
                if serial:
                    ap_serial_to_name[serial] = d.get("name") or "Unknown AP"

        # Initialize all known APs with zero clients. Only these will be reported.
        counts: dict[str, dict] = {
            name: {"name": name, "serial": serial, "clientCount": 0}
            for serial, name in ap_serial_to_name.items()
        }

        # Count wireless clients per AP. Skip clients whose recentDeviceSerial
        # isn't a known AP serial -- those are wired clients on switches/MX,
        # not wireless clients on an AP.
        for c in clients:
            client_serial = c.get("recentDeviceSerial") or ""
            if client_serial not in ap_serial_to_name:
                continue
            ap_name = ap_serial_to_name[client_serial]
            counts[ap_name]["clientCount"] += 1

        return list(counts.values())

    async def get_network_events(self, product_type: str = "wireless",
                                 included_types: list[str] | None = None,
                                 per_page: int = 100) -> list[dict]:
        params: dict[str, Any] = {"productType": product_type, "perPage": per_page}
        if included_types:
            params["includedEventTypes[]"] = included_types
        try:
            data = await self._get(f"/networks/{self.network_id}/events", params=params)
            if isinstance(data, dict):
                return data.get("events", [])
            return data or []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and product_type != "appliance":
                return await self.get_network_events("appliance", included_types, per_page)
            return []

    async def get_assurance_alerts(self) -> list[dict]:
        try:
            data = await self._get(
                f"/organizations/{self.organization_id}/assurance/alerts",
                params={"active": "true", "perPage": 100},
            )
            if isinstance(data, list):
                return data
            return data.get("items", []) if isinstance(data, dict) else []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return []
            return []

    async def get_devices_statuses(self) -> list[dict]:
        try:
            data = await self._get(
                f"/organizations/{self.organization_id}/devices/statuses",
                params={"networkIds[]": self.network_id, "perPage": 100},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError:
            return []
