# Mi Band BLE

Home Assistant custom component for Xiaomi Mi Band fitness trackers using BLE advertisements for live data and periodic active reads for battery information.

## Overview

This integration connects to Xiaomi Mi Band devices via Bluetooth Low Energy. It uses Home Assistant's passive BLE scanning for live advertisement data and short periodic active reads for battery information:

- **Step count** - Daily step total
- **Heart rate** - Current heart rate
- **Signal strength** - RSSI
- **Battery level** - Battery percentage
- **Charging state** - Whether the band is currently charging

## Supported Devices

- Xiaomi Mi Band 3
- Xiaomi Mi Band 4
- Xiaomi Mi Band 5
- Xiaomi Mi Band 6
- Xiaomi Mi Band 7

## Requirements

- Home Assistant 2023.12 or later
- Bluetooth integrated in Home Assistant
- A Xiaomi Mi Band within Bluetooth range

## Installation

### Option 1: HACS (Recommended)

1. Open Home Assistant
2. Navigate to **HACS** → **Integrations**
3. Search for "Mi Band BLE" and install

### Option 2: Manual

1. Copy the `mi-band-ble` folder to `/config/custom_components/` in your Home Assistant configuration directory
2. Restart Home Assistant

## Configuration

### Via UI

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Mi Band BLE"
4. Select your Mi Band from the discovered devices list
5. Follow the on-screen instructions

The integration will automatically discover Mi Band devices broadcasting nearby.

### Options

After adding the integration, open **Configure** to adjust optional battery polling.

- **Enable battery polling**: Off by default. Turn this on only if your Bluetooth setup can make reliable active BLE connections to the band.
- **Battery retry backoff**: Controls how long the integration waits before retrying a failed battery read.
- **Mi Band auth key**: Optional 32-character hexadecimal key for Mi Band 5 and other models that require auth-key authentication before GATT reads.

## Sensors

Once configured, the following sensors are available:

| Sensor | Description | Unit |
|--------|-------------|------|
| `sensor.mi_band_steps` | Step count | steps |
| `sensor.mi_band_heart_rate` | Current heart rate | BPM |
| `sensor.mi_band_rssi` | Signal strength | dBm |
| `sensor.mi_band_battery` | Battery level | % |
| `binary_sensor.mi_band_battery_charging` | Charging state | on/off |

Additional battery history sensors are created disabled by default:

- `sensor.mi_band_full_charging_timestamp`
- `sensor.mi_band_last_charging_timestamp`
- `sensor.mi_band_battery_last_charging`

## Technical Notes

- **Passive plus active reads**: Steps, heart rate, RSSI, and presence come from advertisements. Battery details are fetched with a short active BLE read when battery polling is enabled.
- **Connectable required for battery**: Battery entities need the Mi Band to be connectable and in range of a connectable Home Assistant Bluetooth adapter.
- **Battery efficient**: Battery reads are infrequent and disconnect immediately after reading.
- **Update frequency**: Advertisement-backed sensors update as broadcasts arrive. Battery data is refreshed about every 6 hours when the band is reachable.
- **Auth-key support**: Some Mi Band models or firmware versions require an auth key before GATT characteristics can be read. Paste a key obtained from Notify, Zepp Life/Mi Fit, or a freemyband-style flow into the integration options.
- **Auth-key storage**: The auth key is stored in Home Assistant's config entry options. Do not share logs or config storage that include your key.
- **Battery entities**: Battery entities are deleted when battery polling is disabled, and recreated after polling is re-enabled and a battery read succeeds.
- **Notify coexistence**: Mi Bands usually allow only one active BLE connection at a time. After an active battery read, the integration waits briefly before another active poll so apps like Notify have a chance to reconnect.

## Troubleshooting

### Device not discovered

- Ensure Bluetooth is enabled in Home Assistant
- Place the Mi Band close to the Home Assistant host
- Verify the Mi Band is powered on and not in "Do Not Disturb" mode
- **Ensure "Discoverable" mode is enabled** in the Mi Band phone app (e.g., Mi Fit, Zepp)

### No sensor data

- Confirm the Mi Band is not connected to another app (e.g., Mi Fit)
- Check Home Assistant logs for errors: **Settings** → **System** → **Logs**
- Verify Bluetooth integration is working with other devices

### Battery polling does not update

- Enable debug logging for `custom_components.mi-band-ble` and check the battery poll reason.
- `no_connectable_device` means Home Assistant is only seeing passive advertisements; move the band closer to a connectable Bluetooth adapter or enable a connectable Bluetooth proxy.
- `connect_timeout` means a connectable device was found, but the connection did not complete before the timeout. This can happen if Notify, Zepp Life, or another app is currently connected to the band.
- `auth_failed` means the configured key was rejected, the auth characteristic was unavailable, or the auth notification flow timed out.
- `auth_not_configured_maybe_required` means the battery read failed without an auth key configured; Mi Band 5 usually requires a valid key.
- `battery_gatt_failed` means authentication succeeded, but the battery characteristic read failed.
- `connect_or_gatt_failed` means the connection/read failed after polling started with an unexpected error.
- If you reset or unpair the band, the auth key may be invalidated and need to be retrieved again.

## Development

This is a custom component under development. To contribute or report issues, visit the repository.

## License

MIT License
