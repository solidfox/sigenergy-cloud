"""High-level client endpoint tests."""

import aiohttp
import pytest
from aioresponses import aioresponses

from sigenergy_cloud import SigenergyCloudClient


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
