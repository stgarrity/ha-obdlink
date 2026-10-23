"""Entities display persisted snapshots, not current vehicle presence."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VehicleCoordinator


class VehicleEntity(CoordinatorEntity[VehicleCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: VehicleCoordinator, key: str, suffix: str = "") -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.address}_{key}{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.entry.data["name"],
            manufacturer="OBDLink",
            model="OBDLink CX",
        )

    @property
    def reading(self) -> dict | None:
        return self.coordinator.data["readings"].get(self.key)

    @property
    def available(self) -> bool:
        return self.reading is not None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        reading = self.reading
        return {
            "last_updated": self.reading["captured_at"] if self.reading else None,
            "last_attempt": data["last_attempt"],
            "last_error": data["last_error"],
            "snapshot": True,
            "capture_source": (
                "imported_snapshot" if reading.get("imported") else "vehicle"
            ) if reading else None,
        }
