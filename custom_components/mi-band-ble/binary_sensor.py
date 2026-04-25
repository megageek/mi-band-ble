from __future__ import annotations

from datetime import timedelta

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.components import bluetooth
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

UPDATE_INTERVAL = timedelta(seconds=10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities,
) -> None:
    async_add_entities([MiBandPresenceEntity(hass, entry)])


class MiBandPresenceEntity(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, hass: HomeAssistant, entry: config_entries.ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        store = hass.data[DOMAIN][entry.entry_id]
        address = store["address"].upper()

        # Look up the actual device name from the device registry
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
        )

        device_name = device.name if device and device.name else f"Mi Band ({address})"

        self._attr_name = f"{device_name} Presence"
        self._attr_unique_id = f"{address}-presence"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
        )

        self._unsub = None

    @property
    def is_on(self) -> bool:
        store = self.hass.data[DOMAIN][self.entry.entry_id]
        address = store["address"].upper()

        # Use connectable=False so presence works via non-connectable proxies too.
        # (Your band is connectable, but this makes the check more inclusive.)
        return bluetooth.async_address_present(self.hass, address, connectable=False)

    async def async_added_to_hass(self) -> None:
        @callback
        def _tick(_now):
            self.async_write_ha_state()

        self._unsub = async_track_time_interval(self.hass, _tick, UPDATE_INTERVAL)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
