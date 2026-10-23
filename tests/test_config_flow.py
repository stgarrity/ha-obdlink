"""Verify manual enrollment can claim an already-discovered adapter."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.hass = object()
        self.async_set_unique_id = AsyncMock()

    def _abort_if_unique_id_configured(self):
        pass

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_setup_can_complete_while_bluetooth_discovery_is_pending(self):
        import re

        path = Path(__file__).parents[1] / "custom_components/obdlink_ble/config_flow.py"
        nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, ast.ClassDef)]
        namespace = {
            "ConfigFlow": FlowBase, "ConfigFlowResult": dict, "DOMAIN": "obdlink_ble",
            "re": re, "bluetooth": SimpleNamespace(async_current_scanners=lambda hass: [
                SimpleNamespace(source="proxy", name="Proxy", connectable=True, connector=object()),
            ]),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        flow = namespace["OBDLinkConfigFlow"]()
        result = await flow.async_step_user({
            "name": "Vehicle", "address": "00:11:22:33:44:55", "proxy_source": "proxy",
        })
        self.assertEqual(result["type"], "create_entry")
        flow.async_set_unique_id.assert_awaited_once_with(
            "00:11:22:33:44:55", raise_on_progress=False,
        )
