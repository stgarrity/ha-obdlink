"""Automatic arrival snapshots through an existing ESPHome Bluetooth proxy."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service
import probatio

from .const import DOMAIN, PLATFORMS
from .coordinator import VehicleCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    def coordinator_for(call: ServiceCall) -> VehicleCoordinator:
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = next((item for item in entries if item.entry_id == call.data["entry_id"]), None)
        if entry is None or not hasattr(entry, "runtime_data"):
            raise ServiceValidationError("The selected OBDLink integration is not loaded.")
        return entry.runtime_data

    async def capture(call: ServiceCall) -> dict:
        return await coordinator_for(call).async_capture()

    async def import_snapshot(call: ServiceCall) -> dict:
        try:
            return await coordinator_for(call).async_import_snapshot(call.data["readings"])
        except ValueError as error:
            raise ServiceValidationError(str(error)) from error

    async_register_admin_service(
        hass, DOMAIN, "capture", capture,
        schema=probatio.Schema({probatio.Required("entry_id"): str}),
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass, DOMAIN, "import_snapshot", import_snapshot,
        schema=probatio.Schema({
            probatio.Required("entry_id"): str,
            probatio.Required("readings"): dict,
        }),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = entry.runtime_data = VehicleCoordinator(hass, entry)
    await coordinator.async_load()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.runtime_data.lock.locked():
        return False
    await entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
