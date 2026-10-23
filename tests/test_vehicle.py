import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_protocol import protocol


def vin_frames(vin):
    data = b"\x49\x02\x01" + vin.encode()
    frames = [f"7E8 10 {len(data):02X} " + data[:6].hex(" ")]
    for index, start in enumerate(range(6, len(data), 7), 1):
        frames.append(f"7E8 {0x20 + index:02X} " + data[start:start + 7].hex(" "))
    return "\n".join(frames)


class VehicleDecodeTests(unittest.TestCase):
    def test_ecu_identity_and_multi_frame_vin_are_preserved(self):
        vin = "A" * 17
        reply = vin_frames(vin)
        decoded = protocol.pid_responses(reply, 9, 2)
        self.assertEqual(decoded, {"7E8": b"\x01" + vin.encode()})
        decoded = protocol.pid_responses(
            "7E8 04 41 0C 1F 40\n7E9 04 41 0C 00 00", 1, 0x0C,
        )
        self.assertEqual(
            {ecu: protocol.METRICS[0x0C].decode(data) for ecu, data in decoded.items()},
            {"7E8": 2000, "7E9": 0},
        )

    def test_incomplete_or_out_of_order_frames_are_rejected(self):
        frames = vin_frames("A" * 17).splitlines()
        for reply in (
            frames[0], "\n".join([frames[0], frames[2], frames[1]]),
            "7E8 07 41 00 00", "7E8 21 01 02", "garbled data",
        ):
            with self.assertRaises(protocol.ProbeError):
                protocol.parse_can_reply(reply)

    def test_support_bitmap_boundaries(self):
        self.assertEqual(
            protocol.supported_pids(0x20, bytes.fromhex("80000001")), {0x21, 0x40},
        )
        self.assertEqual(protocol.supported_pids(0, b"\0" * 4), set())
        with self.assertRaises(protocol.ProbeError):
            protocol.supported_pids(0, b"\x80")


class VehicleSurveyTests(unittest.IsolatedAsyncioTestCase):
    async def test_sleeping_ecu_stops_after_first_request(self):
        async def command(value):
            if value == "0100":
                raise protocol.VehicleUnavailable("UNABLE TO CONNECT")
            return "OK"

        channel = Mock(command=AsyncMock(side_effect=command), failure=None)
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await protocol.survey_vehicle(channel)
        self.assertEqual(result["status"], "ecu_unavailable")
        self.assertEqual(
            [call.args[0] for call in channel.command.await_args_list],
            ["ATTP0", "ATCAF1", "ATH1", "0100", "ATH0"],
        )
        self.assertEqual(result["readings"], {})

    async def test_survey_queries_only_supported_metrics_and_preserves_ecus(self):
        responses = {
            "0100": "7E8 06 41 00 08 18 00 00\n7E9 06 41 00 00 10 00 00",
            "ATDP": "ISO 15765-4 (CAN 11/500)", "ATDPN": "A6",
            "0105": "7E8 03 41 05 82",
            "010C": "7E8 04 41 0C 1F 40\n7E9 04 41 0C 00 00",
            "010D": "7E8 03 41 0D 00",
            "0900": "7E8 06 49 00 40 00 00 00",
            "0902": vin_frames("A" * 17),
        }

        async def command(value):
            return responses[value] if value in responses else "OK"

        channel = Mock(command=AsyncMock(side_effect=command), failure=None)
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await protocol.survey_vehicle(channel)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["readings"]["7E8"]["coolant_temperature"]["value"], 90)
        self.assertEqual(result["readings"]["7E9"]["engine_speed"]["value"], 0)
        self.assertEqual(result["vin"]["7E8"], "A" * 17)
        queried = [call.args[0] for call in channel.command.await_args_list]
        self.assertNotIn("012F", queried)
        self.assertNotIn("01A6", queried)
        self.assertEqual(queried[-1], "ATH0")

    async def test_fault_clear_or_arbitrary_requests_cannot_be_sent(self):
        channel = protocol.CommandChannel(AsyncMock())
        for command in ("04", "0801", "22F190", "ATSH7E0", "ATSP6", "010C\r04"):
            with self.assertRaises(protocol.ProbeError):
                await channel.command(command)
        channel.write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
