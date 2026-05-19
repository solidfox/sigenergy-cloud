"""High-level Sigenergy Cloud client used by Home Assistant integrations."""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp

from .auth import OAuthSession, encrypt_password
from .errors import SigenergyCloudAPIError, SigenergyCloudRateLimitError
from .models import BatteryLevelSettings, PeakShavingSchedule, PeakShavingSlot
from .regions import base_url_for_region
from .transport import CloudTransport


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
