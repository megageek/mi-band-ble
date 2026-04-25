from __future__ import annotations

from dataclasses import dataclass

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
    PassiveBluetoothProcessorCoordinator,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)

from .const import DOMAIN
from . import MiBandParsed


@dataclass(frozen=True, kw_only=True)
class MiBandSensorDescription(SensorEntityDescription):
    pass


STEPS_DESC = MiBandSensorDescription(
    key="steps",
    name="Steps",
    native_unit_of_measurement="steps",
)

RSSI_DESC = MiBandSensorDescription(
    key="rssi",
    name="RSSI",
    native_unit_of_measurement="dBm",
    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
)

HEART_DESC = MiBandSensorDescription(
    key="heart_rate",
    name="Heart Rate",
    native_unit_of_measurement="bpm",
)


def _to_update(parsed: MiBandParsed) -> PassiveBluetoothDataUpdate[int]:
    """Convert parsed data to a PassiveBluetoothDataUpdate."""
    entity_data = {}
    entity_desc = {}
    entity_names = {}

    def add(key: str, desc: SensorEntityDescription, value: int | None):
        if value is None:
            return
        ek = PassiveBluetoothEntityKey(key=key, device_id=None)
        entity_data[ek] = value
        entity_desc[ek] = desc
        entity_names[ek] = None

    # Create these entities only once their payloads are observed.
    add("steps", STEPS_DESC, parsed.steps)
    add("rssi", RSSI_DESC, parsed.rssi)

    # Heart rate is optional/toggleable; ONLY create if HR adverts are received.
    add("heart_rate", HEART_DESC, parsed.heart_rate)

    return PassiveBluetoothDataUpdate(
        devices={},
        entity_descriptions=entity_desc,
        entity_data=entity_data,
        entity_names=entity_names,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PassiveBluetoothProcessorCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    processor = PassiveBluetoothDataProcessor(_to_update)

    entry.async_on_unload(
        processor.async_add_entities_listener(MiBandSensorEntity, async_add_entities)
    )
    entry.async_on_unload(coordinator.async_register_processor(processor))


class MiBandSensorEntity(PassiveBluetoothProcessorEntity, SensorEntity):
    """Sensors driven by BLE advertisements."""

    @property
    def native_value(self) -> int | None:
        return self.processor.entity_data.get(self.entity_key)
