"""High-level Sigenergy Cloud client used by Home Assistant integrations."""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp

from .auth import OAuthSession, encrypt_password
from .errors import SigenergyCloudAPIError, SigenergyCloudRateLimitError
from .models import (
    BatteryLevelSettings,
    InstantManualControl,
    InstantManualMode,
    PeakShavingSchedule,
    PeakShavingSlot,
)
from .regions import base_url_for_region
from .transport import CloudTransport

_INSTANT_MANUAL_UNLIMITED_POWER_KW = 4_294_967.295
_INSTANT_MANUAL_POWER_MODES = {
    InstantManualMode.CHARGING,
    InstantManualMode.DISCHARGING,
}


class SigenergyCloudClient:
    """Focused async client for the Sigenergy Cloud app API.

    The class intentionally exposes domain operations, not a generic REST
    interface. Raw dictionaries are returned for endpoints that Home Assistant
    currently displays directly or whose shape is still under observation.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        region: str = "eu",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.username = username
        self.region = region
        self.base_url = base_url_for_region(region)
        self._encrypted_password = encrypt_password(password)
        self._owned_session: aiohttp.ClientSession | None = None
        self._session = session
        self._auth = OAuthSession()
        self._transport = CloudTransport(self.base_url, self._auth)

        self.station_id: str | None = None
        self.ac_sns: tuple[str, ...] = ()
        self.dc_sns: tuple[str, ...] = ()
        self._operational_modes: dict[str, Any] | None = None

    @property
    def dc_sn(self) -> str | None:
        """Return the first DC charger serial number, for older single-device flows."""
        return self.dc_sns[0] if self.dc_sns else None

    async def connect(self) -> None:
        """Authenticate and load the station/device identifiers."""
        session = await self._http_session()
        await self._auth.authenticate(
            session,
            self.base_url,
            self.username,
            self._encrypted_password,
        )
        await self.refresh_station()

    async def close(self) -> None:
        """Close the owned HTTP session, if this client created one."""
        if self._owned_session is not None:
            await self._owned_session.close()
            self._owned_session = None

    async def refresh_station(self) -> dict[str, Any]:
        """Fetch station home data and cache station and charger serials."""
        data = await self._data("GET", "device/owner/station/home")
        self.station_id = str(data["stationId"])
        self.ac_sns = tuple(str(sn) for sn in data.get("acSnList") or ())
        self.dc_sns = tuple(str(sn) for sn in data.get("dcSnList") or ())
        return data

    async def energy_flow(self) -> dict[str, Any]:
        """Return real-time station energy flow."""
        try:
            return await self._station_data(
                "GET",
                "device/sigen/station/energyflow/async",
                params={"refreshFlag": "false"},
            )
        except SigenergyCloudRateLimitError:
            raise
        except SigenergyCloudAPIError:
            return await self._data(
                "GET",
                "device/sigen/station/energyflow",
                params={"id": self._station_id()},
            )

    async def prediction_data(self) -> dict[str, Any]:
        """Return Sigenergy AI forecast and plan series for the station."""
        return await self._station_data(
            "GET",
            "prediction/predictData/get/predictData/{station_id}",
        )

    async def available_operational_modes(self) -> dict[str, Any]:
        """Return available energy-profile modes."""
        data = await self._station_data("GET", "device/energy-profile/mode/all/{station_id}")
        self._operational_modes = data
        return data

    async def current_operational_mode(self) -> str:
        """Return the current energy-profile mode label."""
        if self._operational_modes is None:
            await self.available_operational_modes()
        data = await self._station_data(
            "GET", "device/energy-profile/mode/current/{station_id}"
        )
        mode = data["currentMode"]
        profile_id = data["currentProfileId"]
        modes = self._operational_modes or {}
        if mode != 9:
            for item in modes.get("defaultWorkingModes", ()):
                if item.get("value") == str(mode):
                    return str(item["label"])
        else:
            for item in modes.get("energyProfileItems", ()):
                if item.get("profileId") == profile_id:
                    return str(item["name"])
        return "Unknown mode"

    async def set_operational_mode(self, mode: int, profile_id: int = -1) -> dict[str, Any]:
        """Set the station energy-profile mode."""
        return await self._envelope(
            "PUT",
            "device/energy-profile/mode",
            json={
                "stationId": self._station_id_int(),
                "operationMode": mode,
                "profileId": profile_id,
            },
        )

    async def battery_levels(self) -> BatteryLevelSettings:
        """Return battery SOC threshold settings."""
        data = await self._station_data(
            "GET", "device/energy-profile/battery/level/{station_id}"
        )
        return BatteryLevelSettings.from_api(data)

    async def set_battery_levels(self, settings: BatteryLevelSettings) -> dict[str, Any]:
        """Replace battery SOC threshold settings."""
        return await self._envelope(
            "PUT",
            "device/energy-profile/battery/level",
            json=settings.to_api(self._station_id_int()),
        )

    async def grid_export_limit(self) -> dict[str, Any]:
        """Return owner grid-export limit settings."""
        return await self._station_data(
            "GET", "device/energy-profile/grid/limitation/export/{station_id}"
        )

    async def set_grid_export_limit(
        self, limit_kw: float, *, enabled: bool = True
    ) -> dict[str, Any]:
        """Set owner grid-export limit settings."""
        return await self._set_grid_limit("export", limit_kw, enabled)

    async def grid_import_limit(self) -> dict[str, Any]:
        """Return owner grid-import limit settings."""
        return await self._station_data(
            "GET", "device/energy-profile/grid/limitation/import/{station_id}"
        )

    async def set_grid_import_limit(
        self, limit_kw: float, *, enabled: bool = True
    ) -> dict[str, Any]:
        """Set owner grid-import limit settings."""
        return await self._set_grid_limit("import", limit_kw, enabled)

    async def battery_export_limitation(self) -> dict[str, Any]:
        """Return whether the battery may export to the grid."""
        return await self._station_data(
            "GET", "device/energy-profile/battery/export/limitation/{station_id}"
        )

    async def set_battery_export_limitation(self, enabled: bool) -> dict[str, Any]:
        """Enable or disable battery export to the grid."""
        return await self._envelope(
            "PUT",
            "device/energy-profile/battery/export/limitation",
            json={
                "stationId": self._station_id_int(),
                "installerSetEnable": None,
                "ownerSetEnable": enabled,
            },
        )

    async def peak_shaving_schedule(self) -> PeakShavingSchedule:
        """Return the full peak-shaving schedule."""
        data = await self._station_data(
            "GET", "device/dischargesetting/peak/shaving/{station_id}"
        )
        return PeakShavingSchedule.from_api(data)

    async def set_peak_shaving_schedule(
        self, schedule: PeakShavingSchedule
    ) -> dict[str, Any]:
        """Replace the full peak-shaving schedule."""
        return await self._envelope(
            "POST",
            "device/dischargesetting/peak/shaving",
            json=schedule.to_api(self._station_id_int()),
        )

    async def set_peak_shaving_slot(self, slot: PeakShavingSlot) -> dict[str, Any]:
        """Update one peak-shaving slot via read-modify-write."""
        schedule = (await self.peak_shaving_schedule()).with_slot(slot)
        return await self.set_peak_shaving_schedule(schedule)

    async def instant_manual_control(self) -> InstantManualControl:
        """Return current Instant Manual Control state."""
        data = await self._station_data(
            "GET", "device/energy-profile/instant/manunal/{station_id}"
        )
        return InstantManualControl.from_api(data)

    async def instant_manual_display(self) -> dict[str, Any]:
        """Return Instant Manual Control display values such as battery power and SOC."""
        return await self._station_data(
            "GET", "device/energy-profile/instant/manunal/display/{station_id}"
        )

    async def set_instant_manual_control(
        self,
        mode: InstantManualMode | str,
        *,
        duration_minutes: int,
        power_limitation_kw: float | None = None,
    ) -> dict[str, Any]:
        """Enable Instant Manual Control for a bounded duration.

        Sigenergy's app labels the modes as Charging, Discharging, Hold Battery,
        and Self-Consumption. For Charging and Discharging, a missing power
        limit follows the app's captured behavior and sends Sigenergy's
        unlimited-power sentinel.
        """
        mode = InstantManualMode(mode)
        if duration_minutes < 30 or duration_minutes > 120:
            raise ValueError("Instant Manual Control duration must be 30-120 minutes")
        if power_limitation_kw is None:
            power_limitation = (
                f"{_INSTANT_MANUAL_UNLIMITED_POWER_KW:.3f}"
                if mode in _INSTANT_MANUAL_POWER_MODES
                else ""
            )
        else:
            power_limitation = f"{power_limitation_kw:.3f}"

        return await self._envelope(
            "PUT",
            "device/energy-profile/instant/manunal",
            json={
                "enable": True,
                "stationId": self._station_id_int(),
                "mode": mode.value,
                "duration": str(duration_minutes),
                "powerLimitation": power_limitation,
            },
        )

    async def disable_instant_manual_control(self) -> dict[str, Any]:
        """Disable Instant Manual Control and return control to the active strategy."""
        return await self._envelope(
            "PUT",
            "device/energy-profile/instant/manunal",
            json={
                "enable": False,
                "stationId": self._station_id_int(),
                "mode": "",
                "duration": "",
                "powerLimitation": "",
            },
        )

    async def dc_charge_mode_soc_range(self) -> dict[str, Any]:
        """Return allowed SOC ranges for DC charger mode settings."""
        return await self._station_data(
            "GET", "device/charge/mode/soc/range", station_query=True
        )

    async def dc_charge_mode(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return the current DC charger mode and mode settings."""
        return await self._dc_data("GET", "device/charge/mode/dc", dc_sn)

    async def set_dc_charge_mode(
        self,
        charge_mode: int,
        *,
        dc_sn: str | None = None,
        enable_from_pack: bool | None = None,
        cutoff_soc_from_pack: float | None = None,
        allows_discharge_power: float | None = None,
        vehicle_discharge_cutoff_soc: int | None = None,
    ) -> dict[str, Any]:
        """Set the DC charger mode and optional mode-specific settings."""
        payload: dict[str, Any] = {
            "stationId": self._station_id_int(),
            "snCode": self._dc_sn(dc_sn),
            "chargeMode": charge_mode,
        }
        optional_fields = {
            "enableFromPack": enable_from_pack,
            "cutoffSocFromPack": cutoff_soc_from_pack,
            "allowsDischargePower": allows_discharge_power,
            "vehicleDischargeCutoffSoc": vehicle_discharge_cutoff_soc,
        }
        payload.update({key: value for key, value in optional_fields.items() if value is not None})
        return await self._envelope("POST", "device/charge/mode/dc", json=payload)

    async def dc_charge_setting(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return DC charger charge-power and stop-SOC settings."""
        return await self._dc_data("GET", "device/dcevse/charge/setting", dc_sn)

    async def set_dc_charge_setting(
        self,
        *,
        allowed_charge_power: float,
        vehicle_charging_cutoff_soc: int,
        dc_sn: str | None = None,
    ) -> dict[str, Any]:
        """Set DC charger charge-power and stop-SOC settings."""
        return await self._envelope(
            "POST",
            "device/dcevse/charge/setting",
            json={
                "stationId": self._station_id_int(),
                "snCode": self._dc_sn(dc_sn),
                "allowedChargePower": allowed_charge_power,
                "vehicleChargingCutoffSoc": vehicle_charging_cutoff_soc,
            },
        )

    async def set_dc_charge_enabled(
        self, enabled: bool, *, dc_sn: str | None = None
    ) -> dict[str, Any]:
        """Start or stop DC charging.

        Sigenergy's app API uses enable=0 to start and enable=1 to stop.
        """
        return await self._envelope(
            "PUT",
            "device/dcevse/charge/start",
            params={
                "enable": 0 if enabled else 1,
                "stationId": self._station_id(),
                "snCode": self._dc_sn(dc_sn),
            },
        )

    async def station_is_charging(self) -> bool:
        """Return true when the station reports active DC charging."""
        data = await self._station_data(
            "POST", "device/charge/check/charge", station_query=True
        )
        return bool(data)

    async def dc_status(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return the overall DC charger status."""
        return await self._dc_data("GET", "device/dcevse/status", dc_sn)

    async def dc_plug_status(self, dc_sn: str | None = None) -> bool | None:
        """Return whether a vehicle is plugged into the DC charger."""
        data = await self._dc_data("GET", "device/dcevse/plug/status", dc_sn)
        if isinstance(data, bool) or data is None:
            return data
        if isinstance(data, dict):
            for key in ("plugged", "pluggedIn", "isPlugged", "plugStatus", "status"):
                if key in data:
                    return bool(data[key])
        return bool(data)

    async def dc_charge_realtime(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return real-time DC charging values."""
        return await self._dc_data("GET", "device/dcevse/charge/realtime", dc_sn)

    async def dc_discharge_realtime(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return real-time DC discharge/V2X values."""
        return await self._dc_data("GET", "device/dcevse/discharge/realtime", dc_sn)

    async def dc_energy_totals(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return period energy totals for a DC charger."""
        return await self._dc_data("GET", "data-process/dcevse/energy", dc_sn)

    async def dc_lifetime_totals(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return lifetime charged/discharged totals for a DC charger."""
        return await self._data(
            "GET", f"data-process/dcevse/total/{self._station_id()}/{self._dc_sn(dc_sn)}"
        )

    async def dc_ocpp_status(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return DC charger OCPP status."""
        return await self._dc_data("GET", "device/dcevse/ocpp/status", dc_sn)

    async def dc_session_records(
        self,
        *,
        dc_sn: str | None = None,
        start_date: date,
        end_date: date,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Return paginated DC charge/discharge session history."""
        return await self._dc_data(
            "GET",
            "data-process/dcevse/record/page",
            dc_sn,
            params={
                "current": page,
                "size": page_size,
                "startTime": start_date.strftime("%Y%m%d"),
                "endTime": end_date.strftime("%Y%m%d"),
            },
        )

    async def v2x_discharge_info(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return current V2X discharge-session information."""
        return await self._dc_data(
            "GET",
            "device/station-v2x/discharge/info",
            dc_sn,
            serial_key="dcSnCode",
        )

    async def v2x_discharge_settings(self, dc_sn: str | None = None) -> dict[str, Any]:
        """Return V2X discharge enablement and capability flags."""
        return await self._dc_data(
            "GET",
            "device/station-v2x/select",
            dc_sn,
        )

    async def set_v2x_discharge_enabled(
        self,
        enabled: bool,
        *,
        dc_sn: str | None = None,
    ) -> dict[str, Any]:
        """Enable or disable V2X discharge for a DC charger."""
        return await self._envelope(
            "POST",
            "device/station-v2x/open-close",
            json={
                "snCode": self._dc_sn(dc_sn),
                "stationId": self._station_id_int(),
                "dischargeEnable": 1 if enabled else 0,
            },
        )

    async def start_v2x_discharge(
        self,
        *,
        dc_sn: str | None = None,
        duration_minutes: int = 600,
        power_cap_kw: float | None = None,
    ) -> dict[str, Any]:
        """Start a manual V2X discharge session."""
        return await self._envelope(
            "POST",
            "device/station-v2x/start/discharge",
            json={
                "snCode": self._dc_sn(dc_sn),
                "stationId": self._station_id_int(),
                "duration": duration_minutes,
                "powerCap": power_cap_kw,
            },
        )

    async def stop_v2x_discharge(self, *, dc_sn: str | None = None) -> dict[str, Any]:
        """Stop the current V2X discharge session."""
        return await self._envelope(
            "POST",
            "device/station-v2x/stop/discharge",
            json={"snCode": self._dc_sn(dc_sn), "stationId": self._station_id_int()},
        )

    async def active_alarms(self, *, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        """Return paginated active station alarms."""
        return await self._station_data(
            "GET",
            "device/alarm/page",
            station_query=True,
            params={"current": page, "size": page_size},
        )

    async def aio_serials(self) -> list[str]:
        """Return all-in-one (SigenStor stack) serial numbers for the station."""
        data = await self._station_data(
            "GET",
            "device/aio/by/station",
            station_query=True,
        )
        if isinstance(data, list):
            return [str(sn) for sn in data if sn]
        if isinstance(data, dict):
            for key in ("snList", "aioSnList", "list", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [str(sn) for sn in value if sn]
        return []

    # Topology deviceType values from device/devicetreepanel/topology (mySigen).
    TOPO_DEVICE_TYPE_AIO = 2
    TOPO_DEVICE_TYPE_INVERTER = 3
    TOPO_DEVICE_TYPE_BATTERY = 4
    TOPO_DEVICE_TYPE_DC_CHARGER = 5
    TOPO_DEVICE_TYPE_GATEWAY = 8

    # Flutter UI maps (HAR): communicateStatus 1=offline, 2=online;
    # deviceStatus 4=offline, 5=powerOff, 44=offline. 0/3 observed during AIO off.
    TOPO_DEVICE_STATUS_OFFLINE = frozenset({0, 3, 4, 5, 44})
    TOPO_COMMUNICATE_STATUS_OFFLINE = 1
    TOPO_COMMUNICATE_STATUS_ONLINE = 2

    async def device_topology(self) -> dict[str, Any]:
        """Return SigenStor device tree (AIO / inverter / EVDC / batteries).

        Source for the mySigen SigenStor detail panel status rows.
        """
        data = await self._data(
            "GET",
            "device/devicetreepanel/topology",
            params={"stationId": self._station_id()},
        )
        return data if isinstance(data, dict) else {"raw": data}

    @classmethod
    def iter_topology_nodes(cls, topology: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Flatten topology nodeList tree into a list of device dicts."""
        if not isinstance(topology, dict):
            return []
        nodes: list[dict[str, Any]] = []

        def _walk(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                nodes.append(item)
                _walk(item.get("nodeList"))

        _walk(topology.get("nodeList"))
        return nodes

    @classmethod
    def topology_node_is_offline(cls, node: dict[str, Any] | None) -> bool | None:
        """Return True when topology node is offline/power-off per app status maps."""
        if not isinstance(node, dict):
            return None
        try:
            communicate = (
                int(node["communicateStatus"])
                if node.get("communicateStatus") is not None
                else None
            )
        except (TypeError, ValueError):
            communicate = None
        try:
            device_status = (
                int(node["deviceStatus"]) if node.get("deviceStatus") is not None else None
            )
        except (TypeError, ValueError):
            device_status = None
        if communicate == cls.TOPO_COMMUNICATE_STATUS_OFFLINE:
            return True
        if device_status in cls.TOPO_DEVICE_STATUS_OFFLINE:
            return True
        if (
            communicate == cls.TOPO_COMMUNICATE_STATUS_ONLINE
            and device_status == 1
        ):
            return False
        if device_status == 1 and communicate is None:
            return False
        if device_status is None and communicate is None:
            return None
        # Unknown non-online combination: treat as not definitively online.
        return True if device_status not in (None, 1) else False

    def topology_find_node(
        self,
        topology: dict[str, Any] | None,
        *,
        device_type: int | None = None,
        sn_code: str | None = None,
    ) -> dict[str, Any] | None:
        """Find first topology node matching device type and/or serial."""
        sn_norm = str(sn_code).strip() if sn_code else None
        for node in self.iter_topology_nodes(topology):
            if device_type is not None and node.get("deviceType") != device_type:
                continue
            if sn_norm is not None:
                node_sn = str(node.get("snCode") or node.get("showSnCode") or "").strip()
                if node_sn != sn_norm:
                    continue
            return node
        return None

    async def topology_evdc_status(
        self, *, dc_sn: str | None = None
    ) -> dict[str, Any]:
        """Return EVDC (DC Charger) topology status for offline detection."""
        topology = await self.device_topology()
        sn = self._dc_sn(dc_sn)
        node = self.topology_find_node(
            topology,
            device_type=self.TOPO_DEVICE_TYPE_DC_CHARGER,
            sn_code=sn,
        )
        if node is None:
            node = self.topology_find_node(
                topology, device_type=self.TOPO_DEVICE_TYPE_DC_CHARGER
            )
        offline = self.topology_node_is_offline(node)
        return {
            "station_status": topology.get("stationStatus"),
            "sn_code": (node or {}).get("snCode") or (node or {}).get("showSnCode"),
            "device_type": (node or {}).get("deviceType"),
            "device_type_desc": (node or {}).get("deviceTypeDesc"),
            "device_status": (node or {}).get("deviceStatus"),
            "communicate_status": (node or {}).get("communicateStatus"),
            "offline": offline,
            "node_found": node is not None,
        }

    async def get_aio_power_on(self, *, sn_code: str | None = None) -> bool:
        """Return True when the AIO reports powered-on via mySigen power status."""
        sn = sn_code or (await self._default_aio_sn())
        data = await self._data(
            "GET",
            "device/sigenMate/getPowerOnOffStatus",
            params={"stationId": self._station_id(), "deviceSnCode": sn},
        )
        if isinstance(data, bool):
            return data
        if isinstance(data, (int, float)):
            return data != 0
        if isinstance(data, dict):
            for key in ("powerOn", "isPowerOn", "status"):
                if key in data and data[key] is not None:
                    value = data[key]
                    if isinstance(value, bool):
                        return value
                    if isinstance(value, (int, float)):
                        return value != 0
                    return bool(value)
        return bool(data)

    async def get_aio_on_off_state(self, *, sn_code: str | None = None) -> bool:
        """Return raw /device/aio/on-off-state boolean for an AIO serial."""
        sn = sn_code or (await self._default_aio_sn())
        data = await self._data(
            "GET",
            "device/aio/on-off-state",
            params={"stationId": self._station_id(), "snCode": sn},
        )
        return bool(data)

    async def toggle_aio_power(self, *, sn_code: str | None = None) -> Any:
        """Toggle AIO power via POST /device/aio/on-off (mySigen device power control)."""
        sn = sn_code or (await self._default_aio_sn())
        return await self._envelope(
            "POST",
            "device/aio/on-off",
            json={"snCode": sn, "stationId": self._station_id_int()},
        )

    async def get_station_home_status(self) -> dict[str, Any]:
        """Return station home payload fields used as power-cycle truth."""
        home = await self.refresh_station()
        return {
            "status": home.get("status"),
            "statusDesc": home.get("statusDesc"),
            "onGrid": home.get("onGrid"),
            "shutdownReason": home.get("shutdownReason"),
        }

    async def set_aio_power(
        self,
        power_on: bool,
        *,
        sn_code: str | None = None,
        timeout_s: float = 120.0,
        poll_s: float = 2.0,
    ) -> bool:
        """Drive AIO power using home status as truth (0/1/2 running, 3/5/6 power-off)."""
        import asyncio

        sn = sn_code or (await self._default_aio_sn())
        home = await self.get_station_home_status()
        status = home.get("status")
        running = status in {0, 1, 2}
        powered_off = status in {3, 5, 6}
        if power_on and running:
            return True
        if (not power_on) and powered_off:
            return True

        if power_on:
            await self._envelope(
                "POST",
                "device/aio/on-off",
                json={
                    "snCode": sn,
                    "stationId": self._station_id_int(),
                    "powerOn": True,
                },
            )
        else:
            # Plain body acts as a toggle from running -> off in app traces.
            await self.toggle_aio_power(sn_code=sn)

        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(poll_s)
            status = (await self.get_station_home_status()).get("status")
            if power_on and status in {0, 1, 2}:
                return True
            if (not power_on) and status in {3, 5, 6}:
                return True
        status = (await self.get_station_home_status()).get("status")
        return (power_on and status in {0, 1, 2}) or (
            (not power_on) and status in {3, 5, 6}
        )

    async def _force_aio_power_on(
        self,
        *,
        sn_code: str,
        timeout_s: float = 180.0,
        poll_s: float = 2.0,
    ) -> bool:
        """Best-effort power-on. Used after AIO off so the plant is not left off."""
        await self._envelope(
            "POST",
            "device/aio/on-off",
            json={
                "snCode": sn_code,
                "stationId": self._station_id_int(),
                "powerOn": True,
            },
        )
        return await self.set_aio_power(
            True, sn_code=sn_code, timeout_s=timeout_s, poll_s=poll_s
        )

    async def restart_aio(
        self,
        *,
        sn_code: str | None = None,
        off_dwell_s: float = 0.0,
        timeout_s: float = 180.0,
        poll_s: float = 2.0,
        wait_evdc_offline: bool = True,
        evdc_offline_wait_s: float = 120.0,
        # Back-compat aliases from 0.1.5 experiments.
        wait_evdc_status_change: bool | None = None,
        evdc_status_wait_s: float | None = None,
    ) -> dict[str, Any]:
        """Minimal AIO power-cycle for SECC/estcom recovery.

        Off → poll until plant Power-off → wait until topology reports EVDC
        offline (``device/devicetreepanel/topology`` DC Charger node
        ``deviceStatus`` / ``communicateStatus``) → powerOn=true → poll until
        plant running. EVDC offline wait is best-effort with timeout so the
        plant is never left powered off.

        After off is attempted, a shielded ``finally`` recovery always tries to
        restore power-on (including on cancel and status-read failures). Topology
        rate limits abort the EVDC wait early and still power on.
        """
        import asyncio
        import time

        if wait_evdc_status_change is not None:
            wait_evdc_offline = wait_evdc_status_change
        if evdc_status_wait_s is not None:
            evdc_offline_wait_s = evdc_status_wait_s

        sn = sn_code or (await self._default_aio_sn())
        t0 = time.monotonic()
        before_home = await self.get_station_home_status()
        before_power = await self.get_aio_power_on(sn_code=sn)
        try:
            evdc_before = await self.topology_evdc_status()
        except Exception:  # noqa: BLE001
            evdc_before = {"offline": None, "error": "topology_read_failed"}

        powered_off = False
        powered_on = False
        t_offline = None
        t_evdc_offline = None
        t_on_cmd: float | None = None
        t_online = None
        t_off_cmd = time.monotonic()
        evdc_after_off: dict[str, Any] | None = None
        evdc_offline_seen = False
        evdc_wait_aborted: str | None = None
        power_on_forced = False
        after_home: dict[str, Any] | None = None
        after_power: bool | None = None
        evdc_final: dict[str, Any] | None = None
        # Set before toggle await so cancel mid-off still runs recovery.
        off_attempted = False

        async def _ensure_powered_on(*, reason: str) -> None:
            """Best-effort restore. Read failures still force power-on."""
            nonlocal powered_on, power_on_forced, t_on_cmd, t_online
            nonlocal after_home, after_power
            clearly_on = False
            try:
                status_home = await self.get_station_home_status()
                power_flag = await self.get_aio_power_on(sn_code=sn)
                clearly_on = (
                    status_home.get("status") in {0, 1, 2} and power_flag is True
                )
            except Exception:  # noqa: BLE001
                clearly_on = False
            if clearly_on:
                powered_on = True
                try:
                    after_home = await self.get_station_home_status()
                    after_power = await self.get_aio_power_on(sn_code=sn)
                except Exception:  # noqa: BLE001
                    after_power = True
                return
            power_on_forced = True
            if t_on_cmd is None:
                t_on_cmd = time.monotonic()
            try:
                powered_on = await self._force_aio_power_on(
                    sn_code=sn, timeout_s=timeout_s, poll_s=poll_s
                )
            except Exception:  # noqa: BLE001
                powered_on = False
            if powered_on and t_online is None:
                t_online = time.monotonic()
            try:
                after_home = await self.get_station_home_status()
                after_power = await self.get_aio_power_on(sn_code=sn)
                if after_home.get("status") in {0, 1, 2} and after_power is True:
                    powered_on = True
            except Exception as exc:  # noqa: BLE001
                if after_home is None:
                    after_home = {
                        "status": None,
                        "error": f"ensure_on_status ({reason}): {exc}",
                    }

        try:
            off_attempted = True
            t_off_cmd = time.monotonic()
            await self.toggle_aio_power(sn_code=sn)

            deadline = asyncio.get_running_loop().time() + timeout_s
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(poll_s)
                home = await self.get_station_home_status()
                if home.get("status") in {3, 5, 6}:
                    powered_off = True
                    t_offline = time.monotonic()
                    break

            if powered_off and wait_evdc_offline:
                evdc_deadline = asyncio.get_running_loop().time() + max(
                    float(evdc_offline_wait_s), 0.0
                )
                while asyncio.get_running_loop().time() < evdc_deadline:
                    await asyncio.sleep(poll_s)
                    try:
                        evdc_after_off = await self.topology_evdc_status()
                    except SigenergyCloudRateLimitError as exc:
                        # Best-effort wait: do not hammer topology while plant is off.
                        evdc_after_off = {
                            "offline": None,
                            "error": "rate_limited",
                            "detail": str(exc),
                        }
                        evdc_wait_aborted = "topology_rate_limited"
                        break
                    except Exception as exc:  # noqa: BLE001
                        evdc_after_off = {"offline": None, "error": str(exc)}
                    if evdc_after_off.get("offline") is True:
                        evdc_offline_seen = True
                        t_evdc_offline = time.monotonic()
                        break

            if off_dwell_s > 0 and powered_off:
                await asyncio.sleep(off_dwell_s)

            t_on_cmd = time.monotonic()
            await self._envelope(
                "POST",
                "device/aio/on-off",
                json={
                    "snCode": sn,
                    "stationId": self._station_id_int(),
                    "powerOn": True,
                },
            )

            deadline = asyncio.get_running_loop().time() + timeout_s
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(poll_s)
                home = await self.get_station_home_status()
                power_on = await self.get_aio_power_on(sn_code=sn)
                if home.get("status") in {0, 1, 2} and power_on is True:
                    powered_on = True
                    t_online = time.monotonic()
                    break

            # Safety: if still power-off, try once more with powerOn true.
            after_home = await self.get_station_home_status()
            if after_home.get("status") in {3, 5, 6}:
                await _ensure_powered_on(reason="post_cycle_still_off")
        finally:
            # Invariant: once off was attempted, never leave the plant powered off.
            # Shield recovery so outer cancel/timeout cannot abort the force-on.
            if off_attempted:
                recovery = asyncio.create_task(
                    _ensure_powered_on(reason="finally"),
                    name="sigenergy-restart-aio-ensure-on",
                )
                try:
                    await asyncio.shield(recovery)
                except asyncio.CancelledError:
                    # Finish force-on even if this coroutine is cancelled.
                    await recovery
                    raise
                try:
                    evdc_final = await self.topology_evdc_status()
                except Exception:  # noqa: BLE001
                    evdc_final = {"offline": None, "error": "topology_read_failed"}

        if after_home is None:
            after_home = {}
        if after_power is None:
            try:
                after_power = await self.get_aio_power_on(sn_code=sn)
            except Exception:  # noqa: BLE001
                after_power = None
        if evdc_final is None:
            try:
                evdc_final = await self.topology_evdc_status()
            except Exception:  # noqa: BLE001
                evdc_final = {"offline": None, "error": "topology_read_failed"}

        return {
            "sn_code": sn,
            "power_on_before": before_power,
            "home_status_before": before_home.get("status"),
            "home_status_desc_before": before_home.get("statusDesc"),
            "evdc_topology_before": evdc_before,
            "evdc_topology_after_off": evdc_after_off,
            "evdc_topology_after": evdc_final,
            "evdc_offline_seen": evdc_offline_seen,
            "evdc_wait_aborted": evdc_wait_aborted,
            "power_on_forced": power_on_forced,
            "powered_off": powered_off,
            "powered_on": powered_on,
            "power_on_after": after_power,
            "home_status_after": after_home.get("status"),
            "home_status_desc_after": after_home.get("statusDesc"),
            "off_dwell_s": off_dwell_s,
            "poll_s": poll_s,
            "wait_evdc_offline": wait_evdc_offline,
            "evdc_offline_wait_s": evdc_offline_wait_s,
            "t_offline_s": None if t_offline is None else round(t_offline - t_off_cmd, 2),
            "t_evdc_offline_after_plant_off_s": (
                None
                if t_evdc_offline is None or t_offline is None
                else round(t_evdc_offline - t_offline, 2)
            ),
            "t_online_after_on_s": (
                None
                if t_online is None or t_on_cmd is None
                else round(t_online - t_on_cmd, 2)
            ),
            "full_cycle_s": (
                None if t_online is None else round(t_online - t_off_cmd, 2)
            ),
            "elapsed_s": round(time.monotonic() - t0, 2),
        }

    async def _default_aio_sn(self) -> str:
        serials = await self.aio_serials()
        if not serials:
            raise RuntimeError("No AIO serial numbers found for this station")
        return serials[0]

    async def _set_grid_limit(
        self, direction: str, limit_kw: float, enabled: bool
    ) -> dict[str, Any]:
        return await self._envelope(
            "PUT",
            f"device/energy-profile/grid/limitation/{direction}",
            json={
                "stationId": self._station_id_int(),
                "enable": enabled,
                "maxLimitationOwner": f"{limit_kw:.3f}",
                "maxLimitationInstaller": None,
            },
        )

    async def _dc_data(
        self,
        method: str,
        path: str,
        dc_sn: str | None,
        *,
        serial_key: str = "snCode",
        params: dict[str, Any] | None = None,
    ) -> Any:
        query = {"stationId": self._station_id(), serial_key: self._dc_sn(dc_sn)}
        if params:
            query.update(params)
        return await self._data(method, path, params=query)

    async def _station_data(
        self,
        method: str,
        path: str,
        *,
        station_query: bool = False,
        params: dict[str, Any] | None = None,
    ) -> Any:
        path = path.format(station_id=self._station_id())
        query = dict(params or {})
        if station_query:
            query["stationId"] = self._station_id()
        return await self._data(method, path, params=query or None)

    async def _data(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self._transport.data(await self._http_session(), method, path, **kwargs)

    async def _envelope(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return await self._transport.envelope(
            await self._http_session(), method, path, **kwargs
        )

    async def _http_session(self) -> aiohttp.ClientSession:
        if self._session is not None:
            return self._session
        if self._owned_session is None or self._owned_session.closed:
            self._owned_session = aiohttp.ClientSession()
        return self._owned_session

    def _station_id(self) -> str:
        if self.station_id is None:
            raise RuntimeError("SigenergyCloudClient.connect() has not loaded a station")
        return self.station_id

    def _station_id_int(self) -> int:
        return int(self._station_id())

    def _dc_sn(self, dc_sn: str | None) -> str:
        selected = dc_sn or self.dc_sn
        if selected is None:
            raise RuntimeError("No Sigenergy DC charger serial number is available")
        return selected
