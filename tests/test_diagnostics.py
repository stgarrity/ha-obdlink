"""Distribution metadata reads must not block Home Assistant's event loop."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_package_metadata_is_read_in_executor_without_exposing_private_state(self):
        path = Path(__file__).parents[1] / "custom_components/obdlink_ble/diagnostics.py"
        nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, ast.AsyncFunctionDef)]
        read_version = Mock()
        namespace = {
            "version": read_version,
            "async_get_integration": AsyncMock(return_value=SimpleNamespace(version="test")),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value="library-test"))
        coordinator = SimpleNamespace(data={"readings": {"fuel_level": {"value": 10}}}, lock=asyncio.Lock())
        entry = SimpleNamespace(domain="obdlink_ble", runtime_data=coordinator)
        result = await namespace["async_get_config_entry_diagnostics"](hass, entry)
        hass.async_add_executor_job.assert_awaited_once_with(read_version, "obdlink-ble")
        read_version.assert_not_called()
        self.assertNotIn("readings", result)
        self.assertTrue(result["has_fuel_snapshot"])
