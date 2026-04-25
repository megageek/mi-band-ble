from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import (
    DOMAIN,
    MI_SERVICE_UUID_FULL,
    MI_MANUFACTURER_ID,
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
