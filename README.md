# sigenergy-cloud

Async Python client for the Sigenergy Cloud app API.

This package is a minimal async wrapper around the private Sigenergy Cloud app
API. It is built for Home Assistant style integrations that need to read and
control Sigenergy stations, batteries, and DC chargers.

## Install

```bash
python -m pip install sigenergy-cloud
```

## Example

```python
from sigenergy_cloud import SigenergyCloudClient

client = SigenergyCloudClient("user@example.com", "password", region="eu")
await client.connect()

flow = await client.energy_flow()
mode = await client.current_operational_mode()
chargers = client.dc_sns

await client.close()
```

## Shape

- Use `SigenergyCloudClient` for cloud calls.
- Call `connect()` once before reading data or changing settings.
- The client keeps the station ID and charger serial numbers after connecting.
- Simple settings use small typed value objects.
- Vendor response payloads are returned as dictionaries where the API shape is
  still being mapped.

## Regions

| Region | Base URL |
| --- | --- |
| `eu` | `https://api-eu.sigencloud.com/` |
| `cn` | `https://api-cn.sigencloud.com/` |
| `apac` | `https://api-apac.sigencloud.com/` |
| `us` | `https://api-us.sigencloud.com/` |
