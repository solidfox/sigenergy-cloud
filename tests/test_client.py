"""High-level client endpoint tests."""

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from sigenergy_cloud import InstantManualMode, SigenergyCloudClient


@pytest.mark.asyncio
async def test_prediction_data_uses_station_path() -> None:
    session = aiohttp.ClientSession()
    client = SigenergyCloudClient("user", "password", session=session)
    try:
        with aioresponses() as mocked:
            mocked.post(
                "https://api-eu.sigencloud.com/auth/oauth/token",
                payload={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/device/owner/station/home",
                payload={
                    "code": 0,
                    "data": {
                        "stationId": "12025061000219",
                        "acSnList": [],
                        "dcSnList": [],
                    },
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/prediction/predictData/get/predictData/12025061000219",
                payload={
                    "code": 0,
                    "data": {
                        "nowTime": "2026-05-19 21:46",
                        "pvList": [
                            {
                                "time": "2026-05-20 12:00",
                                "timestamp": 1779192000,
                                "value": 10.5,
                            }
                        ],
                    },
                },
            )

            await client.connect()
            data = await client.prediction_data()

        assert data["nowTime"] == "2026-05-19 21:46"
        assert data["pvList"][0]["value"] == 10.5
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_instant_manual_control_endpoints() -> None:
    session = aiohttp.ClientSession()
    client = SigenergyCloudClient("user", "password", session=session)
    try:
        with aioresponses() as mocked:
            mocked.post(
                "https://api-eu.sigencloud.com/auth/oauth/token",
                payload={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/device/owner/station/home",
                payload={
                    "code": 0,
                    "data": {
                        "stationId": "12025061000219",
                        "acSnList": [],
                        "dcSnList": [],
                    },
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/device/energy-profile/instant/manunal/12025061000219",
                payload={
                    "code": 0,
                    "data": {
                        "enable": True,
                        "mode": "0",
                        "endTime": "1776471497",
                    },
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/device/energy-profile/instant/manunal/display/12025061000219",
                payload={
                    "code": 0,
                    "data": {
                        "batteryPower": 6.7,
                        "batterySoc": 11.1,
                    },
                },
            )
            mocked.put(
                "https://api-eu.sigencloud.com/device/energy-profile/instant/manunal",
                payload={"code": 0, "data": True},
            )

            await client.connect()
            control = await client.instant_manual_control()
            display = await client.instant_manual_display()
            result = await client.set_instant_manual_control(
                InstantManualMode.CHARGING,
                duration_minutes=120,
            )

        assert control.enabled is True
        assert control.mode is InstantManualMode.CHARGING
        assert control.end_time == 1776471497
        assert display["batteryPower"] == 6.7
        assert result["data"] is True

        put_request = mocked.requests[
            (
                "PUT",
                URL("https://api-eu.sigencloud.com/device/energy-profile/instant/manunal"),
            )
        ][0]
        assert put_request.kwargs["json"] == {
            "enable": True,
            "stationId": 12025061000219,
            "mode": "0",
            "duration": "120",
            "powerLimitation": "4294967.295",
        }
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_disable_instant_manual_control() -> None:
    session = aiohttp.ClientSession()
    client = SigenergyCloudClient("user", "password", session=session)
    try:
        with aioresponses() as mocked:
            mocked.post(
                "https://api-eu.sigencloud.com/auth/oauth/token",
                payload={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
            mocked.get(
                "https://api-eu.sigencloud.com/device/owner/station/home",
                payload={
                    "code": 0,
                    "data": {
                        "stationId": "12025061000219",
                        "acSnList": [],
                        "dcSnList": [],
                    },
                },
            )
            mocked.put(
                "https://api-eu.sigencloud.com/device/energy-profile/instant/manunal",
                payload={"code": 0, "data": True},
            )

            await client.connect()
            result = await client.disable_instant_manual_control()

        assert result["data"] is True
        put_request = mocked.requests[
            (
                "PUT",
                URL("https://api-eu.sigencloud.com/device/energy-profile/instant/manunal"),
            )
        ][0]
        assert put_request.kwargs["json"] == {
            "enable": False,
            "stationId": 12025061000219,
            "mode": "",
            "duration": "",
            "powerLimitation": "",
        }
    finally:
        await session.close()
