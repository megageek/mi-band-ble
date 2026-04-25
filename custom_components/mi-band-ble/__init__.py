from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from datetime import datetime
from typing import Final

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    AUTH_CHARACTERISTIC_UUID,
    BATTERY_CHARACTERISTIC_UUID,
    BATTERY_POLL_INTERVAL_SECONDS,
    CONF_AUTH_KEY,
    CONF_BATTERY_FAILURE_BACKOFF_SECONDS,
    CONF_ENABLE_BATTERY_POLLING,
    DEFAULT_AUTH_KEY,
    DEFAULT_BATTERY_FAILURE_BACKOFF_SECONDS,
    DEFAULT_ENABLE_BATTERY_POLLING,
    DOMAIN,
    MI_MANUFACTURER_ID,
    MI_SERVICE_UUID_FULL,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)
AUTH_COMMAND_SEND_KEY: Final = 0x01
AUTH_COMMAND_REQUEST_RANDOM: Final = 0x02
AUTH_COMMAND_SEND_ENCRYPTED: Final = 0x03
AUTH_RESPONSE: Final = 0x10
AUTH_SUCCESS: Final = 0x01
AUTH_KEY_LENGTH: Final = 16
AUTH_CHALLENGE_LENGTH: Final = 16
BATTERY_CONNECT_TIMEOUT: Final = 15.0
BATTERY_AUTH_TIMEOUT: Final = 10.0
BATTERY_READ_TIMEOUT: Final = 10.0
BATTERY_DISCONNECT_TIMEOUT: Final = 5.0
BATTERY_POST_DISCONNECT_QUIET_SECONDS: Final = 60.0
BATTERY_ENTITY_KEYS: Final = frozenset(
    {
        "battery",
        "charging",
        "full_charging_timestamp",
        "last_charging_timestamp",
        "battery_last_charging",
    }
)
BATTERY_ENTITY_NAMES: Final = frozenset(
    {
        "Battery",
        "Battery Charging",
        "Full Charging Timestamp",
        "Last Charging Timestamp",
        "Battery Last Charging",
    }
)


class MiBandConnectTimeoutError(Exception):
    """Raised when an active BLE connection cannot be established in time."""


class MiBandAuthError(Exception):
    """Raised when Mi Band auth-key authentication fails."""


class MiBandBatteryReadError(Exception):
    """Raised when an active battery GATT read fails."""


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


def _clear_battery_data(parsed: MiBandParsed) -> MiBandParsed:
    return dataclasses.replace(
        parsed,
        battery=None,
        charging=None,
        full_charging_timestamp=None,
        last_charging_timestamp=None,
        battery_last_charging=None,
    )


def _registry_entry_config_entry_ids(registry_entry) -> set[str]:
    config_entry_ids = getattr(registry_entry, "config_entry_ids", None)
    if config_entry_ids is not None:
        return set(config_entry_ids)

    config_entry_id = getattr(registry_entry, "config_entry_id", None)
    return {config_entry_id} if config_entry_id else set()


def _registry_entry_name(registry_entry) -> str | None:
    return (
        getattr(registry_entry, "original_name", None)
        or getattr(registry_entry, "name", None)
    )


def _is_battery_entity_registry_entry(
    registry_entry,
    entry: ConfigEntry,
) -> bool:
    if entry.entry_id not in _registry_entry_config_entry_ids(registry_entry):
        return False

    if registry_entry.domain not in {"sensor", "binary_sensor"}:
        return False

    unique_id = (registry_entry.unique_id or "").lower()
    for key in BATTERY_ENTITY_KEYS:
        if (
            unique_id == key
            or unique_id.endswith(f"-{key}")
            or unique_id.endswith(f"_{key}")
            or unique_id.endswith(f":{key}")
        ):
            return True

    name = _registry_entry_name(registry_entry)
    return name in BATTERY_ENTITY_NAMES


async def _async_remove_battery_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    address: str,
) -> None:
    entity_registry = er.async_get(hass)
    entity_ids = [
        registry_entry.entity_id
        for registry_entry in list(entity_registry.entities.values())
        if _is_battery_entity_registry_entry(registry_entry, entry)
    ]

    for entity_id in entity_ids:
        entity_registry.async_remove(entity_id)
        if hass.states.get(entity_id) is not None:
            hass.states.async_remove(entity_id)

    if entity_ids:
        _LOGGER.debug(
            "Removed battery entities for %s because battery polling is disabled: %s",
            address,
            entity_ids,
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


def _encrypt_auth_challenge(auth_key: bytes, challenge: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(auth_key), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


async def _async_wait_for_auth_response(
    responses: asyncio.Queue[bytes],
    command: int,
) -> bytes:
    try:
        async with asyncio.timeout(BATTERY_AUTH_TIMEOUT):
            while True:
                response = await responses.get()
                if len(response) >= 3 and response[0] == AUTH_RESPONSE and response[1] == command:
                    return response
    except TimeoutError as err:
        raise MiBandAuthError(f"timed out waiting for auth command 0x{command:02x}") from err


def _validate_auth_success(response: bytes, command: int) -> None:
    if len(response) < 3:
        raise MiBandAuthError(f"malformed auth response for command 0x{command:02x}")

    if response[2] != AUTH_SUCCESS:
        raise MiBandAuthError(
            f"auth command 0x{command:02x} returned status 0x{response[2]:02x}"
        )


async def _async_authenticate_miband(client, address: str, auth_key_hex: str) -> None:
    auth_key = bytes.fromhex(auth_key_hex)
    if len(auth_key) != AUTH_KEY_LENGTH:
        raise MiBandAuthError("auth key must be 16 bytes")

    responses: asyncio.Queue[bytes] = asyncio.Queue()

    def _auth_notification(_sender, data: bytearray) -> None:
        responses.put_nowait(bytes(data))

    _LOGGER.debug("Starting Mi Band auth-key authentication for %s", address)

    try:
        await client.start_notify(AUTH_CHARACTERISTIC_UUID, _auth_notification)
    except Exception as err:
        raise MiBandAuthError("auth characteristic is not available") from err

    try:
        await client.write_gatt_char(
            AUTH_CHARACTERISTIC_UUID,
            bytes([AUTH_COMMAND_SEND_KEY, 0x00]) + auth_key,
            response=False,
        )
        send_key_response = await _async_wait_for_auth_response(
            responses, AUTH_COMMAND_SEND_KEY
        )
        _validate_auth_success(send_key_response, AUTH_COMMAND_SEND_KEY)

        await client.write_gatt_char(
            AUTH_CHARACTERISTIC_UUID,
            bytes([AUTH_COMMAND_REQUEST_RANDOM, 0x00]),
            response=False,
        )
        random_response = await _async_wait_for_auth_response(
            responses, AUTH_COMMAND_REQUEST_RANDOM
        )
        _validate_auth_success(random_response, AUTH_COMMAND_REQUEST_RANDOM)

        challenge = random_response[3:]
        if len(challenge) != AUTH_CHALLENGE_LENGTH:
            raise MiBandAuthError(
                f"auth challenge has unexpected length {len(challenge)}"
            )

        await client.write_gatt_char(
            AUTH_CHARACTERISTIC_UUID,
            bytes([AUTH_COMMAND_SEND_ENCRYPTED, 0x00])
            + _encrypt_auth_challenge(auth_key, challenge),
            response=False,
        )
        encrypted_response = await _async_wait_for_auth_response(
            responses, AUTH_COMMAND_SEND_ENCRYPTED
        )
        _validate_auth_success(encrypted_response, AUTH_COMMAND_SEND_ENCRYPTED)
    finally:
        with contextlib.suppress(Exception):
            await client.stop_notify(AUTH_CHARACTERISTIC_UUID)

    _LOGGER.debug("Mi Band auth-key authentication succeeded for %s", address)


async def _async_read_battery(
    connectable_device,
    address: str,
    auth_key_hex: str,
) -> MiBandBatteryData | None:
    client = None
    try:
        async with asyncio.timeout(BATTERY_CONNECT_TIMEOUT):
            client = await establish_connection(
                BleakClientWithServiceCache,
                connectable_device,
                connectable_device.address,
                timeout=BATTERY_CONNECT_TIMEOUT,
            )
    except TimeoutError as err:
        _LOGGER.debug("Battery connection timed out for %s", address)
        raise MiBandConnectTimeoutError from err

    try:
        try:
            if auth_key_hex:
                await _async_authenticate_miband(client, address, auth_key_hex)

            try:
                _LOGGER.debug(
                    "Reading battery characteristic %s from %s",
                    BATTERY_CHARACTERISTIC_UUID,
                    address,
                )
                async with asyncio.timeout(BATTERY_READ_TIMEOUT):
                    raw = bytes(
                        await client.read_gatt_char(BATTERY_CHARACTERISTIC_UUID)
                    )
            except Exception as err:
                reason = (
                    "battery_gatt_failed"
                    if auth_key_hex
                    else "auth_not_configured_maybe_required"
                )
                raise MiBandBatteryReadError(reason) from err
        finally:
            try:
                async with asyncio.timeout(BATTERY_DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except TimeoutError:
                _LOGGER.warning(
                    "Battery poll disconnect timed out for %s; Notify may need longer to reconnect",
                    address,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Battery poll disconnect failed for %s: %s: %s",
                    address,
                    type(err).__name__,
                    err,
                    exc_info=True,
                )
            else:
                _LOGGER.debug("Battery poll disconnected cleanly from %s", address)
    except MiBandAuthError:
        raise

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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "address": address,
        "battery_poll_in_progress": False,
        "last_battery_poll_finished_monotonic": None,
        "last_parsed": MiBandParsed(),
        "last_battery_failure_monotonic": None,
    }

    store = hass.data[DOMAIN][entry.entry_id]

    async def _async_options_updated(
        hass: HomeAssistant, updated_entry: ConfigEntry
    ) -> None:
        store["last_battery_failure_monotonic"] = None
        if not updated_entry.options.get(
            CONF_ENABLE_BATTERY_POLLING, DEFAULT_ENABLE_BATTERY_POLLING
        ):
            store["last_parsed"] = _clear_battery_data(store["last_parsed"])
            store["last_battery_poll_finished_monotonic"] = None
            await _async_remove_battery_entities(hass, updated_entry, address)
        _LOGGER.debug(
            "Mi Band BLE options updated for %s; battery failure backoff cleared",
            updated_entry.unique_id,
        )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    def _battery_polling_enabled() -> bool:
        return entry.options.get(
            CONF_ENABLE_BATTERY_POLLING, DEFAULT_ENABLE_BATTERY_POLLING
        )

    def _battery_failure_backoff_seconds() -> int:
        return int(
            entry.options.get(
                CONF_BATTERY_FAILURE_BACKOFF_SECONDS,
                DEFAULT_BATTERY_FAILURE_BACKOFF_SECONDS,
            )
        )

    def _auth_key() -> str:
        return entry.options.get(CONF_AUTH_KEY, DEFAULT_AUTH_KEY)

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

        battery_polling_enabled = _battery_polling_enabled()
        parsed = MiBandParsed(
            steps=steps,
            heart_rate=heart_rate,
            rssi=rssi,
            battery=previous.battery if battery_polling_enabled else None,
            charging=previous.charging if battery_polling_enabled else None,
            full_charging_timestamp=(
                previous.full_charging_timestamp if battery_polling_enabled else None
            ),
            last_charging_timestamp=(
                previous.last_charging_timestamp if battery_polling_enabled else None
            ),
            battery_last_charging=(
                previous.battery_last_charging if battery_polling_enabled else None
            ),
        )
        store["last_parsed"] = parsed
        return parsed

    def _needs_poll(
        service_info: BluetoothServiceInfoBleak, last_poll: float | None
    ) -> bool:
        if not _battery_polling_enabled():
            _LOGGER.debug(
                "Battery poll check for %s: should_poll=False, battery polling disabled",
                service_info.device.address,
            )
            return False

        connectable_device = _connectable_device(service_info)
        has_connectable_device = connectable_device is not None
        battery_poll_in_progress = store["battery_poll_in_progress"]
        last_finished = store["last_battery_poll_finished_monotonic"]
        post_poll_quiet_remaining = 0.0
        if last_finished is not None:
            post_poll_quiet_remaining = max(
                0.0,
                BATTERY_POST_DISCONNECT_QUIET_SECONDS
                - (time.monotonic() - last_finished),
            )
        last_failure = store["last_battery_failure_monotonic"]
        failure_backoff_remaining = 0.0
        if last_failure is not None:
            failure_backoff_remaining = max(
                0.0,
                _battery_failure_backoff_seconds() - (time.monotonic() - last_failure),
            )

        should_poll = (
            hass.state == CoreState.running
            and (last_poll is None or last_poll >= BATTERY_POLL_INTERVAL_SECONDS)
            and has_connectable_device
            and not battery_poll_in_progress
            and post_poll_quiet_remaining == 0.0
            and failure_backoff_remaining == 0.0
        )
        _LOGGER.debug(
            (
                "Battery poll check for %s: should_poll=%s, hass_running=%s, "
                "last_poll=%s, has_connectable_device=%s, service_info_connectable=%s, "
                "battery_poll_in_progress=%s, post_poll_quiet_remaining=%s, "
                "failure_backoff_remaining=%s"
            ),
            service_info.device.address,
            should_poll,
            hass.state == CoreState.running,
            last_poll,
            has_connectable_device,
            service_info.connectable,
            battery_poll_in_progress,
            round(post_poll_quiet_remaining, 1),
            round(failure_backoff_remaining, 1),
        )
        if post_poll_quiet_remaining > 0.0:
            _LOGGER.debug(
                "Battery poll skipped for %s because post-poll quiet window has %.1fs remaining",
                service_info.device.address,
                post_poll_quiet_remaining,
            )
        if battery_poll_in_progress:
            _LOGGER.debug(
                "Battery poll skipped for %s because another battery poll is already running",
                service_info.device.address,
            )
        if not has_connectable_device:
            _LOGGER.debug(
                (
                    "Battery poll unavailable for %s: no_connectable_device, "
                    "service_info_connectable=%s"
                ),
                service_info.device.address,
                service_info.connectable,
            )
        return should_poll

    def _connectable_device(service_info: BluetoothServiceInfoBleak):
        if service_info.connectable:
            return service_info.device

        return async_ble_device_from_address(
            hass, service_info.device.address, connectable=True
        )

    async def _async_poll(service_info: BluetoothServiceInfoBleak) -> MiBandParsed:
        address = service_info.device.address
        active_poll_started = False
        if store["battery_poll_in_progress"]:
            _LOGGER.debug(
                "Battery poll skipped for %s because another battery poll is already running",
                address,
            )
            return store["last_parsed"]

        store["battery_poll_in_progress"] = True
        connectable_device = _connectable_device(service_info)

        try:
            if connectable_device is None:
                _LOGGER.debug(
                    (
                        "Battery poll skipped for %s: no_connectable_device, "
                        "service_info_connectable=%s"
                    ),
                    address,
                    service_info.connectable,
                )
                return store["last_parsed"]

            active_poll_started = True
            _LOGGER.debug(
                "Starting battery poll for %s: service_info_connectable=%s, device_address=%s, details=%s",
                address,
                service_info.connectable,
                connectable_device.address,
                getattr(connectable_device, "details", None),
            )

            try:
                battery = await _async_read_battery(connectable_device, address, _auth_key())
            except MiBandConnectTimeoutError:
                store["last_battery_failure_monotonic"] = time.monotonic()
                _LOGGER.warning(
                    (
                        "Battery poll failed for %s: connect_timeout; possible causes "
                        "include range, adapter/proxy issues, or another app such as "
                        "Notify currently connected"
                    ),
                    address,
                )
                return store["last_parsed"]
            except MiBandAuthError as err:
                store["last_battery_failure_monotonic"] = time.monotonic()
                _LOGGER.warning(
                    "Battery poll failed for %s: auth_failed, %s",
                    address,
                    err,
                    exc_info=True,
                )
                return store["last_parsed"]
            except MiBandBatteryReadError as err:
                store["last_battery_failure_monotonic"] = time.monotonic()
                _LOGGER.warning(
                    "Battery poll failed for %s: %s",
                    address,
                    err,
                    exc_info=True,
                )
                return store["last_parsed"]
            except Exception as err:
                store["last_battery_failure_monotonic"] = time.monotonic()
                _LOGGER.warning(
                    "Battery poll failed for %s: connect_or_gatt_failed, %s: %s",
                    address,
                    type(err).__name__,
                    err,
                    exc_info=True,
                )
                return store["last_parsed"]

            if battery is None:
                _LOGGER.debug("Battery poll did not produce parsed data for %s", address)
                return store["last_parsed"]

            if not _battery_polling_enabled():
                parsed = _clear_battery_data(store["last_parsed"])
                store["last_parsed"] = parsed
                _LOGGER.debug(
                    "Discarded battery poll result for %s because battery polling is disabled",
                    address,
                )
                return parsed

            store["last_battery_failure_monotonic"] = None
            parsed = _combine(store["last_parsed"], battery)
            store["last_parsed"] = parsed
            _LOGGER.debug(
                "Battery poll succeeded for %s: battery=%s charging=%s",
                address,
                battery.battery,
                battery.charging,
            )
            return parsed
        finally:
            store["battery_poll_in_progress"] = False
            if active_poll_started:
                store["last_battery_poll_finished_monotonic"] = time.monotonic()

    coordinator = store["coordinator"] = ActiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.PASSIVE,
        update_method=_update_method,
        needs_poll_method=_needs_poll,
        poll_method=_async_poll,
        connectable=True,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
