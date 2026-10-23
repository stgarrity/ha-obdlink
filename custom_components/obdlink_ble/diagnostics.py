"""Diagnostics omit vehicle identifiers, values, and infrastructure addresses."""

async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "integration_version": "0.1.0",
        "has_fuel_snapshot": "fuel_level" in coordinator.data["readings"],
        "has_check_engine_snapshot": "check_engine" in coordinator.data["readings"],
        "capture_in_progress": coordinator.lock.locked(),
    }
