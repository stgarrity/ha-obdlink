"""One explicit approval per adapter; subsequent arrivals are automatic."""

import re

import probatio

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class OBDLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_bluetooth(self, discovery_info) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": "OBDLink CX"}
        return await self.async_step_user()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        scanners = {
            scanner.source: scanner.name
            for scanner in bluetooth.async_current_scanners(self.hass)
            if scanner.connectable and scanner.connector is not None
        }
        if not scanners:
            return self.async_abort(reason="no_proxy")
        errors = {}
        if user_input is not None:
            address = user_input["address"].upper()
            name = user_input["name"].strip()
            if (
                re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address) is None
                or not name or len(name) > 100
                or user_input["proxy_source"] not in scanners
            ):
                errors["base"] = "invalid_configuration"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={**user_input, "address": address, "name": name},
                )
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=probatio.Schema({
                probatio.Required("name", default="Vehicle"): str,
                probatio.Required("address", default=self.unique_id or ""): str,
                probatio.Required("proxy_source"): probatio.In(scanners),
            }),
        )
