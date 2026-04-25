from __future__ import annotations

import dataclasses
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)

from .const import (
    DOMAIN,
    PLATFORMS,
    MI_SERVICE_UUID_FULL,
    MI_MANUFACTURER_ID,
)

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class MiBandParsed:
    steps: int | None
    heart_rate: int | None
    rssi: int | None


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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "address": address,
    }

    def _update_method(service_info: BluetoothServiceInfoBleak) -> MiBandParsed:
        steps = _parse_steps_from_fee0(service_info)
        heart_rate = _parse_heart_rate(service_info)
        rssi = service_info.rssi

        # Optional debug crumbs
        if steps is not None:
            _LOGGER.debug("Steps=%s from %s", steps, address)
        if heart_rate is not None:
            _LOGGER.debug("HR=%s from %s", heart_rate, address)

        return MiBandParsed(
            steps=steps,
            heart_rate=heart_rate,
            rssi=rssi,
        )

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"] = PassiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.PASSIVE,
        update_method=_update_method,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
