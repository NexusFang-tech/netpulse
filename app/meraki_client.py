"""Meraki Dashboard API client.

Uses read-only endpoints to pull client counts, events, and assurance alerts.
Only the minimum surface area needed for DHCP outage detection.
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
                "User-Agent": "NetPulse/1.0",
            },
            timeout=15.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = await self._client.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            log.warning("Meraki API %s returned %s: %s", path, e.response.status_code, e.response.text[:200])
            raise
        except httpx.RequestError as e:
            log.warning("Meraki API %s request error: %s", path, e)
            raise

    # ------------------------------------------------------------------
    async def get_ssid_clients(self, ssid_numbers: list[int] | None = None,
                               ssid_name_map: dict[int, str] | None = None,
                               timespan_seconds: int = 600) -> list[dict]:
        """Client counts per SSID using clientCountHistory (the endpoint that actually works).

        The /clients endpoint drops SSID labels for MAC-randomized devices (most
        modern phones), so it's unreliable. clientCountHistory aggregates counts
        server-side per SSID number and always returns data.

        Args:
          ssid_numbers: list of SSID numbers to query (e.g. [0, 3, 2] for
                        e.g. corp-primary, corp-mobile, corp-guest). Required.
          ssid_name_map: {ssid_number: ssid_name} to label results.
          timespan_seconds: lookback window; min 600 (10 min) per Meraki API.

        Returns list of {ssidName, clientCount, usageMb} entries -- one per SSID
        queried. clientCount is the latest bucket in the time series.
        """
        if not ssid_numbers:
            return []
        name_map = ssid_name_map or {}
        # API requires resolution to divide evenly into timespan; 300s buckets
        # with 600s timespan gives 2 buckets which is enough for "latest"
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
            except httpx.HTTPStatusError as e:
                log.warning("clientCountHistory failed for ssid=%s: %s", num, e)
                continue

            if not data:
                continue

            # Latest non-null bucket is our "current" count
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
                "usageMb": 0.0,  # not available from this endpoint; usage history is separate
            })
        return results

    async def get_network_events(self, product_type: str = "wireless",
                                 included_types: list[str] | None = None,
                                 per_page: int = 100) -> list[dict]:
        """Recent network events. For wireless, this includes DHCP events.

        Meraki event types we care about:
          - dhcp_no_leases, dhcp_nack, dhcp_alert
          - association, disassociation (mass disassoc is a signal)
          - device_packet_flood
        """
        params: dict[str, Any] = {
            "productType": product_type,
            "perPage": per_page,
        }
        if included_types:
            params["includedEventTypes[]"] = included_types
        try:
            data = await self._get(f"/networks/{self.network_id}/events", params=params)
            # Response shape: {"events": [...], "pageStartAt": ..., "pageEndAt": ...}
            if isinstance(data, dict):
                return data.get("events", [])
            return data or []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                # Fallback: some networks require different productType
                log.info("Events endpoint rejected productType=%s, trying appliance", product_type)
                if product_type != "appliance":
                    return await self.get_network_events("appliance", included_types, per_page)
            return []

    async def get_assurance_alerts(self) -> list[dict]:
        """Org-level assurance alerts (active). Includes DHCP issues, gateway unreachable, etc."""
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
                log.info("Assurance alerts endpoint unavailable (may need license)")
                return []
            return []

    async def get_devices_statuses(self) -> list[dict]:
        """Per-device online/offline status for the org, filtered to this network."""
        try:
            data = await self._get(
                f"/organizations/{self.organization_id}/devices/statuses",
                params={"networkIds[]": self.network_id, "perPage": 100},
            )
            return data if isinstance(data, list) else []
        except httpx.HTTPStatusError:
            return []
