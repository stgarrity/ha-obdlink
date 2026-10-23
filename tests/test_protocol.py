import asyncio
import unittest
from unittest.mock import AsyncMock

from obdlink_ble import protocol
CommandChannel, ProbeError = protocol.CommandChannel, protocol.ProbeError


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_echoed_replies(self):
        channel = CommandChannel(AsyncMock(), timeout=1)

        async def write(data):
            chunks = (
                (b"STI\r\rSTN", b"2232 v5.", b"10.3\r", b">")
                if data == b"STI\r" else (b"STVR\r12.34\r>",)
            )
            for chunk in chunks:
                channel.notified(bytearray(chunk))

        channel.write = write
        identity = await channel.command("STI")
        voltage = await channel.command("STVR")
        self.assertEqual(identity, "STN2232 v5.10.3")
        self.assertEqual(protocol.validate_reply(identity, voltage), 12.34)

    async def test_timeout_poisons_channel(self):
        write = AsyncMock()
        channel = CommandChannel(write, timeout=0.01)
        with self.assertRaisesRegex(ProbeError, "Timed out"):
            await channel.command("STI")
        channel.notified(bytearray(b"late response>"))
        with self.assertRaisesRegex(ProbeError, "desynchronized"):
            await channel.command("STVR")
        self.assertEqual(write.await_count, 1)

    async def test_disconnect_fails_pending_request(self):
        channel = CommandChannel(AsyncMock(), timeout=30)
        task = asyncio.create_task(channel.command("STI"))
        await asyncio.sleep(0)
        channel.disconnected()
        with self.assertRaisesRegex(ProbeError, "closed"):
            await asyncio.wait_for(task, 0.2)

    async def test_invalid_protocol_data_fails(self):
        for response in (b"?>", b"\xff>", b"x" * 65537):
            channel = CommandChannel(AsyncMock(), timeout=1)

            async def write(data):
                channel.notified(bytearray(response))

            channel.write = write
            with self.assertRaises(ProbeError):
                await channel.command("STI")
        with self.assertRaises(ProbeError):
            protocol.validate_reply("not STN", "12.3")
        with self.assertRaises(ProbeError):
            protocol.validate_reply("STN2232 v5.10.3", "NO DATA")

    async def test_vehicle_commands_are_never_written(self):
        write = AsyncMock()
        channel = CommandChannel(write)
        with self.assertRaises(ProbeError):
            await channel.command("04")
        write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
