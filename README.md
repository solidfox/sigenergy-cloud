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

# Temporarily override the active strategy without leaving Sigen AI mode.
await client.set_instant_manual_control("0", duration_minutes=120)  # Charging
await client.disable_instant_manual_control()

await client.close()
```

## Shape

- Use `SigenergyCloudClient` for cloud calls.
- Call `connect()` once before reading data or changing settings.
- The client keeps the station ID and charger serial numbers after connecting.
- Simple settings use small typed value objects.
- Vendor response payloads are returned as dictionaries where the API shape is
  still being mapped.

## Instant Manual Control

The Sigenergy app exposes Instant Manual Control through the misspelled private
path `device/energy-profile/instant/manunal`. The app labels the modes as:

| Mode | Label |
| --- | --- |
| `0` | Charging |
| `1` | Discharging |
| `2` | Hold Battery |
| `3` | Self-Consumption |

Use `set_instant_manual_control()` for a 30-120 minute temporary override, and
`disable_instant_manual_control()` to hand control back to the active strategy.

## Regions

| Region | Base URL |
| --- | --- |
| `eu` | `https://api-eu.sigencloud.com/` |
| `cn` | `https://api-cn.sigencloud.com/` |
| `apac` | `https://api-apac.sigencloud.com/` |
| `us` | `https://api-us.sigencloud.com/` |
