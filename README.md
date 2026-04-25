# Mi Band BLE

Home Assistant custom component for Xiaomi Mi Band fitness trackers using passive BLE scanning.

## Overview

This integration connects to Xiaomi Mi Band devices via Bluetooth Low Energy without requiring an active connection. It uses Home Assistant's passive BLE scanning to read:

- **Step count** - Daily step total
- **Heart rate** - Current heart rate
- **Signal strength** - RSSI

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

## Sensors

Once configured, the following sensors are available:

| Sensor | Description | Unit |
|--------|-------------|------|
| `sensor.mi_band_steps` | Step count | steps |
| `sensor.mi_band_heart_rate` | Current heart rate | BPM |
| `sensor.mi_band_rssi` | Signal strength | dBm |

## Technical Notes

- **Passive scanning only**: This integration does not maintain an active Bluetooth connection. It reads advertisement data broadcasted by the Mi Band.
- **Non-connectable**: The Mi Band does not need to be in "pairing" mode or connectable.
- **Battery efficient**: Since no active connection is required, the Mi Band battery drain is minimal.
- **Update frequency**: Sensor updates depend on the Mi Band's advertisement interval (typically every 1-5 seconds).

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

## Development

This is a custom component under development. To contribute or report issues, visit the repository.

## License

MIT License