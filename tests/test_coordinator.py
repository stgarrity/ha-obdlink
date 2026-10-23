"""Execute the integration coordinator against small HA lifecycle/transport doubles."""

from __future__ import annotations

import ast
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock

from obdlink_ble import (
    ArrivalGate, CommandChannel, NOTIFY_CHAR, ProbeError, UART_SERVICE,
    WRITE_CHAR, capture_snapshot,
)


class CoordinatorBase:
    @classmethod
    def __class_getitem__(cls, key):
        return cls

    def __init__(self, hass, logger, **kwargs):
        self.hass = hass

    def async_set_updated_data(self, data):
        self.data = data

    async def async_shutdown(self):
        pass


class MemoryStore:
    @classmethod
    def __class_getitem__(cls, key):
        return cls

    def __init__(self, *args):
        self.saved = None

    async def async_load(self):
        return self.saved

    async def async_save(self, data):
        self.saved = dict(data)


class Backend:
    def __init__(self, device, **kwargs):
        self.device = device
        self.closed_callback = kwargs["disconnected_callback"]
        self.is_connected = False
        self.connect = AsyncMock(side_effect=self._connect)
        self.disconnect = AsyncMock(side_effect=self._disconnect)
        self.services = SimpleNamespace(
            get_service=lambda uuid: object(),
            get_characteristic=lambda uuid: SimpleNamespace(
                service_uuid=UART_SERVICE, properties=["notify", "write"],
            ),
        )

    async def _connect(self, **kwargs):
        self.is_connected = True

    async def _disconnect(self):
        self.is_connected = False
        self.closed_callback()

    async def start_notify(self, char, callback):
        self.notified = callback

    async def write_gatt_char(self, char, data, *, response):
        command = data.decode().strip()
        reply = {
            "STI": "STN2232 v1.0.0", "ATDPN": "A6",
            "012F": "7E8 03 41 2F 80",
            "0101": "7E8 06 41 01 00 00 00 00",
        }.get(command, "OK")
        self.notified(bytearray((reply + "\r>").encode()))


def coordinator_fixture():
    path = Path(__file__).parents[1] / "custom_components/obdlink_ble/coordinator.py"
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    clock = SimpleNamespace(wall=1000.0, mono=100.0)
    bluetooth = Mock()
    namespace = {
        "DataUpdateCoordinator": CoordinatorBase, "Store": MemoryStore,
        "ArrivalGate": ArrivalGate, "AWAY_SECONDS": 120, "CAPTURE_SECONDS": 15,
        "DOMAIN": "obdlink_ble", "LOGGER": Mock(), "asyncio": asyncio,
        "datetime": datetime, "timezone": timezone, "suppress": suppress,
        "time": SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.mono),
        "callback": lambda function: function, "bluetooth": bluetooth,
        "ESPHomeClient": Backend, "BleakError": ConnectionError,
        "CommandChannel": CommandChannel, "ProbeError": ProbeError,
        "NOTIFY_CHAR": NOTIFY_CHAR, "WRITE_CHAR": WRITE_CHAR,
        "UART_SERVICE": UART_SERVICE, "capture_snapshot": capture_snapshot,
        "Any": Any,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    hass = SimpleNamespace(
        async_create_background_task=lambda coro, name: asyncio.create_task(coro),
    )
    entry = SimpleNamespace(
        entry_id="entry", data={"address": "adapter", "proxy_source": "proxy"},
    )
    return namespace["VehicleCoordinator"](hass, entry), bluetooth, clock


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_arrivals_capture_once_and_ignore_other_proxy_and_stale_cache(self):
        coordinator, bluetooth, clock = coordinator_fixture()
        coordinator.async_capture = AsyncMock()
        coordinator._advertisement(SimpleNamespace(source="other", time=100))
        coordinator._advertisement(SimpleNamespace(source="proxy", time=90))
        self.assertIsNone(coordinator.capture_task)
        for _ in range(30):
            coordinator._advertisement(SimpleNamespace(source="proxy", time=100))
        await asyncio.sleep(0)
        coordinator.async_capture.assert_awaited_once()
        clock.wall += 121
        coordinator._advertisement(SimpleNamespace(source="proxy", time=100))
        await asyncio.sleep(0)
        self.assertEqual(coordinator.async_capture.await_count, 2)

    async def test_partial_capture_preserves_previous_value_and_original_timestamp(self):
        coordinator, _, _ = coordinator_fixture()
        old_timestamp = "2020-01-01T00:00:00+00:00"
        coordinator.data["readings"]["check_engine"] = {
            "value": True, "captured_at": old_timestamp,
        }

        async def partial():
            await coordinator._publish("fuel_level", 37.5)
            raise ProbeError("connection lost before check-engine reply")

        coordinator._capture = partial
        result = await coordinator.async_capture()
        self.assertFalse(result["success"])
        self.assertEqual(result["readings"]["fuel_level"]["value"], 37.5)
        self.assertEqual(result["readings"]["check_engine"], {
            "value": True, "captured_at": old_timestamp,
        })
        self.assertEqual(coordinator.store.saved["readings"], result["readings"])

    async def test_restores_values_and_timestamps_without_inventing_new_measurements(self):
        coordinator, _, _ = coordinator_fixture()
        saved = {
            "readings": {"fuel_level": {"value": 0, "captured_at": "2020-01-01T00:00:00+00:00"}},
            "last_seen": 900,
        }
        coordinator.store.saved = saved
        await coordinator.async_load()
        self.assertEqual(coordinator.data["readings"], saved["readings"])
        self.assertEqual(coordinator.gate.last_seen, 900)
        coordinator.store.saved = {"readings": {"check_engine": {"value": 0}}}
        with self.assertRaises(ValueError):
            await coordinator.async_load()

    async def test_every_connection_secures_link_and_releases_slot(self):
        coordinator, bluetooth, _ = coordinator_fixture()
        clients = []

        def client_factory(*args, **kwargs):
            client = Backend(*args, **kwargs)
            clients.append(client)
            return client

        connector = SimpleNamespace(can_connect=lambda: True, client=client_factory)
        bluetooth.async_scanner_devices_by_address.return_value = [
            SimpleNamespace(
                scanner=SimpleNamespace(source="proxy", connector=connector),
                ble_device="configured-device",
            ),
        ]
        for _ in range(2):
            result = await coordinator.async_capture()
            self.assertTrue(result["success"])
        for client in clients:
            client.connect.assert_awaited_once_with(pair=True)
            client.disconnect.assert_awaited_once()
            self.assertFalse(client.is_connected)

    async def test_no_fallback_to_another_proxy(self):
        coordinator, bluetooth, _ = coordinator_fixture()
        other = Mock()
        bluetooth.async_scanner_devices_by_address.return_value = [
            SimpleNamespace(scanner=SimpleNamespace(source="other", connector=other)),
        ]
        result = await coordinator.async_capture()
        self.assertFalse(result["success"])
        other.client.assert_not_called()

    async def test_manual_capture_and_arrival_do_not_race(self):
        coordinator, _, _ = coordinator_fixture()
        coordinator.async_capture = AsyncMock()
        async with coordinator.lock:
            coordinator._advertisement(SimpleNamespace(source="proxy", time=100))
            self.assertIsNone(coordinator.capture_task)
