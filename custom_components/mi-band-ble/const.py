DOMAIN = "mi-band-ble"

PLATFORMS = ["sensor", "binary_sensor"]

BATTERY_POLL_INTERVAL_SECONDS = 6 * 60 * 60

# HA advert monitor shows service_data keyed by the full 128-bit UUID:
MI_SERVICE_UUID_FULL = "0000fee0-0000-1000-8000-00805f9b34fb"
BATTERY_CHARACTERISTIC_UUID = "00000006-0000-3512-2118-0009af100700"

# Huami/Xiaomi-ish manufacturer id
MI_MANUFACTURER_ID = 0x0157
