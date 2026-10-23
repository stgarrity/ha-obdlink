import unittest
from unittest.mock import AsyncMock

from obdlink_ble import ArrivalGate, CommandChannel, CommandRejected, ProbeError, VehicleUnavailable
from obdlink_ble.protocol import capture_snapshot


class ArrivalTests(unittest.TestCase):
    def test_first_sighting_and_return_after_absence(self):
        gate = ArrivalGate()
        self.assertTrue(gate.observe(1000))
        self.assertFalse(gate.observe(1001))
        self.assertFalse(gate.observe(1001))
        self.assertFalse(gate.observe(900))
        self.assertFalse(gate.observe(1120))
        self.assertTrue(gate.observe(1240))

    def test_continuous_advertisements_do_not_poll_parked_vehicle(self):
        gate = ArrivalGate()
        self.assertTrue(gate.observe(1000))
        for now in range(1001, 4600):
            self.assertFalse(gate.observe(now))

    def test_recent_seen_state_suppresses_restart_duplicate(self):
        gate = ArrivalGate(last_seen=1000)
        self.assertFalse(gate.observe(1050))
        self.assertTrue(gate.observe(1170))


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    def channel(self, overrides=None):
        replies = {
            "STI": "STN2232 v1.0.0", "ATDPN": "A6",
            "012F": "7E8 03 41 2F 80",
            "0101": "7E8 06 41 01 80 00 00 00",
        }
        replies.update(overrides or {})

        async def command(value):
            response = replies.get(value, "OK")
            if isinstance(response, Exception):
                raise response
            return response

        return type("Channel", (), {"command": AsyncMock(side_effect=command), "failure": None})()

    async def test_reads_and_publishes_priority_values_without_full_survey(self):
        channel = self.channel()
        published = []

        async def publish(key, value):
            published.append((key, value))
            if key == "fuel_level":
                self.assertNotIn("0101", [c.args[0] for c in channel.command.await_args_list])

        result = await capture_snapshot(channel, publish)
        self.assertAlmostEqual(published[0][1], 50.196, places=3)
        self.assertEqual(published[1], ("check_engine", True))
        self.assertEqual(result["protocol"], "6")
        queried = [c.args[0] for c in channel.command.await_args_list]
        self.assertNotIn("0100", queried)
        self.assertNotIn("0902", queried)
        self.assertEqual(queried[-1], "ATH0")

    async def test_initial_rejected_wakeup_command_is_retried_once(self):
        calls = []
        channel = self.channel()
        original = channel.command.side_effect

        async def command(value):
            calls.append(value)
            if calls == ["STI"]:
                raise CommandRejected("rejected")
            return await original(value)

        channel.command.side_effect = command
        result = await capture_snapshot(channel, AsyncMock())
        self.assertEqual(calls[:2], ["STI", "STI"])
        self.assertTrue(result["values"]["check_engine"])

    async def test_missing_fuel_does_not_become_zero_or_erase_mil(self):
        channel = self.channel({"012F": VehicleUnavailable("NO DATA")})
        publish = AsyncMock()
        result = await capture_snapshot(channel, publish)
        publish.assert_awaited_once_with("check_engine", True)
        self.assertEqual(result["errors"][0]["field"], "fuel_level")
        self.assertNotIn("fuel_level", result["values"])

    async def test_no_vehicle_data_is_an_explicit_failure(self):
        channel = self.channel({
            "012F": VehicleUnavailable("NO DATA"),
            "0101": VehicleUnavailable("NO DATA"),
        })
        publish = AsyncMock()
        with self.assertRaises(VehicleUnavailable):
            await capture_snapshot(channel, publish)
        publish.assert_not_awaited()

    async def test_mil_is_on_if_any_responding_ecu_reports_it(self):
        channel = self.channel({
            "0101": "7E8 06 41 01 00 00 00 00\n7E9 06 41 01 81 00 00 00",
        })
        result = await capture_snapshot(channel, AsyncMock())
        self.assertTrue(result["values"]["check_engine"])

    async def test_arbitrary_ecu_configuration_commands_remain_blocked(self):
        channel = CommandChannel(AsyncMock())
        for command in ("04", "22FFFF", "ATSH123", "ATSP6", "012F\r04"):
            with self.assertRaises(ProbeError):
                await channel.command(command)
        channel.write.assert_not_awaited()
