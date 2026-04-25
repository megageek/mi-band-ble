from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import callback

from .const import (
    CONF_BATTERY_FAILURE_BACKOFF_SECONDS,
    CONF_ENABLE_BATTERY_POLLING,
    DEFAULT_BATTERY_FAILURE_BACKOFF_SECONDS,
    DEFAULT_ENABLE_BATTERY_POLLING,
    DOMAIN,
    MI_MANUFACTURER_ID,
    MI_SERVICE_UUID_FULL,
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ENABLE_BATTERY_POLLING,
                default=options.get(
                    CONF_ENABLE_BATTERY_POLLING, DEFAULT_ENABLE_BATTERY_POLLING
                ),
            ): bool,
            vol.Required(
                CONF_BATTERY_FAILURE_BACKOFF_SECONDS,
                default=options.get(
                    CONF_BATTERY_FAILURE_BACKOFF_SECONDS,
                    DEFAULT_BATTERY_FAILURE_BACKOFF_SECONDS,
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=24 * 60 * 60)),
        }
    )


def _looks_like_miband(service_info: BluetoothServiceInfoBleak) -> bool:
    sd = service_info.service_data or {}
    fee0 = sd.get(MI_SERVICE_UUID_FULL)
    if fee0 is not None and len(fee0) == 4:
        return True

    md = service_info.manufacturer_data or {}
    m = md.get(MI_MANUFACTURER_ID)
    if m is not None and len(m) >= 4:
        return True

    return False


class MiBandAdvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MiBandOptionsFlow:
        return MiBandOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        if not _looks_like_miband(discovery_info):
            return self.async_abort(reason="not_supported")

        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {
            "name": discovery_info.name or "Mi Band",
            "address": address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            address = self.unique_id
            name = self.context.get("title_placeholders", {}).get("name", "Mi Band")
            return self.async_create_entry(
                title = name or f"Mi Band ({address})",
                data={},
            )
            
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self.context.get("title_placeholders"),
        )


class MiBandOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
