"""Check-engine snapshot; failed reads never become a false 'off'."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import VehicleEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([CheckEngineSensor(entry.runtime_data, "check_engine")])


class CheckEngineSensor(VehicleEntity, BinarySensorEntity):
    _attr_name = "Check engine"
    _attr_icon = "mdi:engine"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        return self.reading["value"] if self.reading else None
