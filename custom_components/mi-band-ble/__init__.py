from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from typing import Final

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_processor import (
    ActiveBluetoothProcessorCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_CHARACTERISTIC_UUID,
    BATTERY_POLL_INTERVAL_SECONDS,
    DOMAIN,
    MI_MANUFACTURER_ID,
    MI_SERVICE_UUID_FULL,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)
BATTERY_READ_TIMEOUT: Final = 10.0


@dataclasses.dataclass(frozen=True)
class MiBandBatteryData:
    battery: int | None = None
    charging: bool | None = None
    full_charging_timestamp: datetime | None = None
    last_charging_timestamp: datetime | None = None
    battery_last_charging: int | None = None


@dataclasses.dataclass(frozen=True)
class MiBandParsed:
    steps: int | None = None
    heart_rate: int | None = None
    rssi: int | None = None
    battery: int | None = None
    charging: bool | None = None
    full_charging_timestamp: datetime | None = None
    last_charging_timestamp: datetime | None = None
    battery_last_charging: int | None = None


def _combine(parsed: MiBandParsed, battery: MiBandBatteryData | None = None) -> MiBandParsed:
    if battery is None:
        return parsed

    return dataclasses.replace(
        parsed,
        battery=battery.battery,
        charging=battery.charging,
        full_charging_timestamp=battery.full_charging_timestamp,
        last_charging_timestamp=battery.last_charging_timestamp,
        battery_last_charging=battery.battery_last_charging,
    )


def _build_datetime(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime | None:
    if year < 2000 or month < 1 or month > 12 or day < 1 or day > 31:
        return None

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=dt_util.UTC)
    except ValueError:
        return None


def _parse_miband_battery(raw: bytes) -> MiBandBatteryData | None:
    if len(raw) == 20:
        if raw[0] != 0x0F:
            _LOGGER.debug("Ignoring battery payload with unexpected signature: 0x%02X", raw[0])
            return None

        return MiBandBatteryData(
            battery=int(raw[1]),
            charging=raw[2] == 1,
            full_charging_timestamp=_build_datetime(
                (raw[4] << 8) | raw[3], raw[5], raw[6], raw[7], raw[8], raw[9]
            ),
            last_charging_timestamp=_build_datetime(
                (raw[12] << 8) | raw[11], raw[13], raw[14], raw[15], raw[16], raw[17]
            ),
            battery_last_charging=int(raw[19]),
        )

    if len(raw) == 10:
        return MiBandBatteryData(
            battery=int(raw[0]),
            charging=raw[9] == 2,
            last_charging_timestamp=_build_datetime(
                2000 + raw[1], raw[2], raw[3], raw[4], raw[5], raw[6]
            ),
        )

    _LOGGER.debug("Ignoring battery payload with unexpected length: %s", len(raw))
    return None


async def _async_read_battery(
    hass: HomeAssistant,
    address: str,
) -> MiBandBatteryData | None:
    connectable_device = async_ble_device_from_address(hass, address, connectable=True)
    if connectable_device is None:
        _LOGGER.debug("No connectable BLE device available for battery read: %s", address)
        return None

    _LOGGER.debug(
        "Resolved connectable BLE device for %s via %s",
        address,
        getattr(connectable_device, "details", None),
    )

    client = await establish_connection(
        BleakClientWithServiceCache,
        connectable_device,
        connectable_device.address,
        timeout=BATTERY_READ_TIMEOUT,
    )
    try:
        _LOGGER.debug(
            "Reading battery characteristic %s from %s",
            BATTERY_CHARACTERISTIC_UUID,
            address,
        )
        raw = bytes(await client.read_gatt_char(BATTERY_CHARACTERISTIC_UUID))
    finally:
        await client.disconnect()

    _LOGGER.debug(
        "Battery read returned %s bytes from %s: %s",
        len(raw),
        address,
        raw.hex(),
    )
    return _parse_miband_battery(raw)


def _parse_steps_from_fee0(service_info: BluetoothServiceInfoBleak) -> int | None:
    """Parse steps from 4 bytes little-endian in FEE0 service data."""
    sd = service_info.service_data or {}

    # Keys are typically full UUID strings in HA (as your monitor shows)
    raw = sd.get(MI_SERVICE_UUID_FULL)
    if not raw or len(raw) != 4:
        return None

    return int.from_bytes(raw, byteorder="little", signed=False)


def _parse_heart_rate(service_info: BluetoothServiceInfoBleak) -> int | None:
    """Parse HR from manufacturer data 0x0157, using byte [3] when not 0xFF.

    Payload length varies by model/firmware; we only require >= 4 bytes.
    """
    md = service_info.manufacturer_data or {}
    raw = md.get(MI_MANUFACTURER_ID)
    if not raw or len(raw) < 4:
        return None

    hr = raw[3]
    if hr == 0xFF:
        return None
    return int(hr)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address = entry.unique_id
    assert address is not None

    _LOGGER.info("Setting up Mi Band BLE for %s", address)
    _LOGGER.debug(
        "Connectable scanner count at setup for %s: %s",
        address,
        bluetooth.async_scanner_count(hass, connectable=True),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "address": address,
        "last_parsed": MiBandParsed(),
    }

    store = hass.data[DOMAIN][entry.entry_id]

    def _update_method(service_info: BluetoothServiceInfoBleak) -> MiBandParsed:
        steps = _parse_steps_from_fee0(service_info)
        heart_rate = _parse_heart_rate(service_info)
        rssi = service_info.rssi
        previous = store["last_parsed"]

        # Optional debug crumbs
        if steps is not None:
            _LOGGER.debug("Steps=%s from %s", steps, address)
        if heart_rate is not None:
            _LOGGER.debug("HR=%s from %s", heart_rate, address)

        parsed = MiBandParsed(
            steps=steps,
            heart_rate=heart_rate,
            rssi=rssi,
            battery=previous.battery,
            charging=previous.charging,
            full_charging_timestamp=previous.full_charging_timestamp,
            last_charging_timestamp=previous.last_charging_timestamp,
            battery_last_charging=previous.battery_last_charging,
        )
        store["last_parsed"] = parsed
        return parsed

    def _needs_poll(
        service_info: BluetoothServiceInfoBleak, last_poll: float | None
    ) -> bool:
        has_connectable_device = bool(
            async_ble_device_from_address(
                hass, service_info.device.address, connectable=True
            )
        )
        should_poll = (
            hass.state == CoreState.running
            and (last_poll is None or last_poll >= BATTERY_POLL_INTERVAL_SECONDS)
            and has_connectable_device
        )
        _LOGGER.debug(
            (
                "Battery poll check for %s: should_poll=%s, hass_running=%s, "
                "last_poll=%s, has_connectable_device=%s, service_info_connectable=%s"
            ),
            service_info.device.address,
            should_poll,
            hass.state == CoreState.running,
            last_poll,
            has_connectable_device,
            service_info.connectable,
        )
        return should_poll

    async def _async_poll(service_info: BluetoothServiceInfoBleak) -> MiBandParsed:
        _LOGGER.debug("Starting battery poll for %s", service_info.device.address)
        battery = await _async_read_battery(hass, service_info.device.address)
        if battery is None:
            _LOGGER.debug("Battery poll did not produce parsed data for %s", service_info.device.address)
            return store["last_parsed"]

        parsed = _combine(store["last_parsed"], battery)
        store["last_parsed"] = parsed
        _LOGGER.debug(
            "Battery poll succeeded for %s: battery=%s charging=%s",
            service_info.device.address,
            battery.battery,
            battery.charging,
        )
        return parsed

    coordinator = store["coordinator"] = ActiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.PASSIVE,
        update_method=_update_method,
        needs_poll_method=_needs_poll,
        poll_method=_async_poll,
        connectable=False,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    try:
        _LOGGER.debug("Attempting immediate battery refresh for %s during setup", address)
        battery = await _async_read_battery(hass, address)
        if battery is None:
            _LOGGER.debug("Immediate battery refresh returned no parsed data for %s", address)
        else:
            store["last_parsed"] = _combine(store["last_parsed"], battery)
            _LOGGER.debug(
                "Immediate battery refresh succeeded for %s: battery=%s charging=%s",
                address,
                battery.battery,
                battery.charging,
            )
    except Exception:
        _LOGGER.exception("Immediate battery refresh failed for %s", address)

    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
