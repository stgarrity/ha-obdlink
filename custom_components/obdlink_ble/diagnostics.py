"""Diagnostics omit vehicle identifiers, values, and infrastructure addresses."""

from importlib.metadata import version

from homeassistant.loader import async_get_integration


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    integration = await async_get_integration(hass, entry.domain)
    return {
        "integration_version": integration.version,
        "protocol_library_version": version("obdlink-ble"),
        "has_fuel_snapshot": "fuel_level" in coordinator.data["readings"],
        "has_check_engine_snapshot": "check_engine" in coordinator.data["readings"],
        "capture_in_progress": coordinator.lock.locked(),
    }
