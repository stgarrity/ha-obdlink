"""Fuel level and explicit per-reading timestamps."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE

from .entity import VehicleEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities([
        FuelSensor(coordinator, "fuel_level"),
        UpdatedSensor(coordinator, "fuel_level"),
        UpdatedSensor(coordinator, "check_engine"),
    ])


class FuelSensor(VehicleEntity, SensorEntity):
    _attr_name = "Fuel level"
    _attr_icon = "mdi:gas-station"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        return self.reading["value"] if self.reading else None


class UpdatedSensor(VehicleEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, key) -> None:
        super().__init__(coordinator, key, "_updated")
        self._attr_name = "Fuel last updated" if key == "fuel_level" else "Check engine last updated"

    @property
    def native_value(self) -> datetime | None:
        return datetime.fromisoformat(self.reading["captured_at"]) if self.reading else None
