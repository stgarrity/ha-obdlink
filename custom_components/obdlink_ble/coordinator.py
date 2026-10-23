"""Bounded, advertisement-triggered captures using HA's existing proxy connection."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import time
from typing import Any

from bleak import BleakError
from bleak_esphome.backend.client import ESPHomeClient
from obdlink_ble import (
    ArrivalGate, CommandChannel, NOTIFY_CHAR, ProbeError, UART_SERVICE,
    WRITE_CHAR, capture_snapshot,
)

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import AWAY_SECONDS, CAPTURE_SECONDS, DOMAIN

LOGGER = logging.getLogger(__name__)


class VehicleCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self.address = entry.data["address"]
        self.source = entry.data["proxy_source"]
        self.store = Store[dict[str, Any]](hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.gate = ArrivalGate(AWAY_SECONDS)
        self.lock = asyncio.Lock()
        self.capture_task: asyncio.Task | None = None
        self.unsubscribe = None
        self.data = {
            "readings": {}, "last_attempt": None, "last_error": None,
            "protocol": None, "last_seen": None,
        }

    async def async_load(self) -> None:
        if (stored := await self.store.async_load()) is not None:
            if not isinstance(stored, dict) or not isinstance(stored.get("readings"), dict):
                raise ValueError("Invalid saved OBDLink state; refusing to fabricate readings.")
            self._validate_readings(stored["readings"])
            self.data.update(stored)
            self.gate.last_seen = stored.get("last_seen")

    @staticmethod
    def _validate_readings(readings: dict) -> None:
        for key, reading in readings.items():
            if key not in ("fuel_level", "check_engine") or not isinstance(reading, dict):
                raise ValueError("Invalid saved OBDLink reading.")
            value, captured = reading.get("value"), reading.get("captured_at")
            valid_value = (
                isinstance(value, bool) if key == "check_engine"
                else type(value) in (int, float) and 0 <= value <= 100
            )
            if not valid_value or not isinstance(captured, str):
                raise ValueError("Invalid saved OBDLink value or timestamp.")
            if datetime.fromisoformat(captured).tzinfo is None:
                raise ValueError("Saved OBDLink timestamps must have a timezone.")

    async def async_import_snapshot(self, readings: dict) -> dict:
        """Restore verified historical data without refreshing its timestamp."""
        if self.lock.locked():
            raise ProbeError("Cannot import a snapshot while a capture is running.")
        self._validate_readings(readings)
        now = datetime.now(timezone.utc)
        if any(datetime.fromisoformat(item["captured_at"]) > now for item in readings.values()):
            raise ValueError("Imported snapshots cannot have future timestamps.")
        imported, retained = [], []
        async with self.lock:
            updated = dict(self.data["readings"])
            for key, reading in readings.items():
                existing = updated.get(key)
                if existing and datetime.fromisoformat(existing["captured_at"]) >= datetime.fromisoformat(reading["captured_at"]):
                    retained.append(key)
                    continue
                updated[key] = {
                    "value": reading["value"], "captured_at": reading["captured_at"],
                    "imported": True,
                }
                imported.append(key)
            self.async_set_updated_data({**self.data, "readings": updated})
            await self._save()
        return {"success": True, "imported": imported, "retained_newer": retained}

    def async_start(self) -> None:
        self.unsubscribe = bluetooth.async_register_advertisement_callback(
            self.hass, self._advertisement, self.address,
        )

    @callback
    def _advertisement(self, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if info.source != self.source or time.monotonic() - info.time > 5:
            return
        if self.gate.observe(time.time()) and (
            self.capture_task is None or self.capture_task.done()
        ) and not self.lock.locked():
            self.capture_task = self.hass.async_create_background_task(
                self.async_capture(), f"{DOMAIN} arrival capture",
            )

    async def _save(self) -> None:
        self.data["last_seen"] = self.gate.last_seen
        await self.store.async_save(self.data)

    async def _publish(self, key: str, value: float | bool) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self.async_set_updated_data({
            **self.data,
            "readings": {
                **self.data["readings"], key: {"value": value, "captured_at": timestamp},
            },
        })
        await self._save()

    async def _capture(self) -> dict:
        paths = [
            path for path in bluetooth.async_scanner_devices_by_address(
                self.hass, self.address, connectable=True,
            )
            if path.scanner.source == self.source
        ]
        if not paths:
            raise ProbeError("The configured proxy cannot currently see this adapter.")
        path = paths[0]
        connector = path.scanner.connector
        if connector is None or not connector.can_connect():
            raise ProbeError("The configured proxy has no free connection slot.")
        channel: CommandChannel | None = None

        def disconnected() -> None:
            if channel is not None:
                channel.disconnected()

        client = connector.client(
            path.ble_device, timeout=5, disconnected_callback=disconnected,
        )
        if not isinstance(client, ESPHomeClient):
            raise ProbeError("Only HA-managed ESPHome Bluetooth proxies are supported.")
        try:
            # Every new link needs encryption, even when the peer is already bonded.
            await client.connect(pair=True)
            service = client.services.get_service(UART_SERVICE)
            notify = client.services.get_characteristic(NOTIFY_CHAR)
            write = client.services.get_characteristic(WRITE_CHAR)
            if (
                service is None or notify is None or write is None
                or notify.service_uuid != UART_SERVICE or write.service_uuid != UART_SERVICE
                or "notify" not in notify.properties or "write" not in write.properties
            ):
                raise ProbeError("The expected OBDLink UART service is unavailable.")

            async def send(data: bytes) -> None:
                await client.write_gatt_char(write, data, response=True)

            channel = CommandChannel(send, timeout=4)
            await client.start_notify(notify, channel.notified)
            return await capture_snapshot(
                channel, self._publish, protocol_hint=self.data.get("protocol"),
            )
        finally:
            if client.is_connected:
                async with asyncio.timeout(5):
                    await client.disconnect()

    async def async_capture(self) -> dict:
        if self.lock.locked():
            raise ProbeError("A capture is already running for this adapter.")
        async with self.lock:
            self.async_set_updated_data({
                **self.data, "last_attempt": datetime.now(timezone.utc).isoformat(),
                "last_error": None,
            })
            try:
                async with asyncio.timeout(CAPTURE_SECONDS):
                    result = await self._capture()
                errors = result["errors"]
                self.async_set_updated_data({
                    **self.data, "protocol": result["protocol"],
                    "last_error": "; ".join(item["error"] for item in errors) or None,
                })
                await self._save()
                return {"success": not errors, "partial": bool(errors), **self.data}
            except (ProbeError, BleakError, TimeoutError, NotImplementedError) as error:
                message = str(error) or "Capture exceeded its time budget."
                LOGGER.warning("Vehicle snapshot failed: %s", message)
                self.async_set_updated_data({**self.data, "last_error": message})
                await self._save()
                return {"success": False, **self.data}

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None
        if self.capture_task is not None and not self.capture_task.done():
            self.capture_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.capture_task
        await self._save()
