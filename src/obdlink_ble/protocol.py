"""Short, read-only OBDLink UART exchanges; independent of Home Assistant."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re

UART_SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_CHAR = "0000fff2-0000-1000-8000-00805f9b34fb"


class ProbeError(RuntimeError):
    """A protocol failure that must not be mistaken for success."""


class VehicleUnavailable(ProbeError):
    """The adapter replied, but the vehicle did not provide diagnostic data."""


class CommandRejected(ProbeError):
    """The adapter rejected a command without desynchronizing the channel."""


@dataclass(frozen=True)
class Metric:
    name: str
    size: int
    unit: str | None
    decode: Callable[[bytes], float | int | dict]


def _word(data: bytes) -> int:
    return int.from_bytes(data, "big")


METRICS = {
    0x01: Metric("diagnostic_status", 4, None, lambda d: {
        "check_engine_light": bool(d[0] & 0x80), "stored_fault_count": d[0] & 0x7F,
    }),
    0x04: Metric("engine_load", 1, "%", lambda d: d[0] * 100 / 255),
    0x05: Metric("coolant_temperature", 1, "C", lambda d: d[0] - 40),
    0x0C: Metric("engine_speed", 2, "rpm", lambda d: _word(d) / 4),
    0x0D: Metric("vehicle_speed", 1, "km/h", lambda d: d[0]),
    0x0F: Metric("intake_air_temperature", 1, "C", lambda d: d[0] - 40),
    0x10: Metric("mass_air_flow", 2, "g/s", lambda d: _word(d) / 100),
    0x11: Metric("throttle_position", 1, "%", lambda d: d[0] * 100 / 255),
    0x1F: Metric("engine_run_time", 2, "s", _word),
    0x21: Metric("distance_with_check_engine_light", 2, "km", _word),
    0x2F: Metric("fuel_level", 1, "%", lambda d: d[0] * 100 / 255),
    0x31: Metric("distance_since_faults_cleared", 2, "km", _word),
    0x42: Metric("ecu_supply_voltage", 2, "V", lambda d: _word(d) / 1000),
    0x46: Metric("ambient_temperature", 1, "C", lambda d: d[0] - 40),
    0x4D: Metric("time_with_check_engine_light", 2, "min", _word),
    0x4E: Metric("time_since_faults_cleared", 2, "min", _word),
    0x5B: Metric("hybrid_battery_remaining_life", 1, "%", lambda d: d[0] * 100 / 255),
    0x5C: Metric("oil_temperature", 1, "C", lambda d: d[0] - 40),
    0x5E: Metric("engine_fuel_rate", 2, "L/h", lambda d: _word(d) / 20),
    0xA6: Metric("odometer", 4, "km", lambda d: _word(d) / 10),
}
SUPPORT_PAGES = tuple(range(0, 0xC0, 0x20))
ALLOWED_COMMANDS = frozenset(
    ("STI", "STVR", "ATDP", "ATDPN", "ATH1", "ATH0", "ATCAF1",
     "ATTP0", "ATTP6", "ATTP7", "ATTP8", "ATTP9", "0900", "0902")
) | {f"01{pid:02X}" for pid in (*SUPPORT_PAGES, *METRICS)}


class CommandChannel:
    """Reassemble fragmented notifications through the adapter's '>' prompt."""

    def __init__(
        self, write: Callable[[bytes], Awaitable[None]], timeout: float = 15,
    ) -> None:
        self.write = write
        self.timeout = timeout
        self.buffer = bytearray()
        self.pending: asyncio.Future[str] | None = None
        self.lock = asyncio.Lock()
        self.failure: str | None = None

    def disconnected(self) -> None:
        self._fail("BLE connection closed.")

    def _fail(self, message: str) -> None:
        self.failure = message
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(ProbeError(message))

    def notified(self, payload: bytearray) -> None:
        self.buffer.extend(payload)
        if len(self.buffer) > 65536:
            self._fail("Response exceeded 64 KiB without a prompt.")
            self.buffer.clear()
            return
        while b">" in self.buffer:
            message, _, rest = self.buffer.partition(b">")
            self.buffer[:] = rest
            if self.pending is not None and not self.pending.done():
                try:
                    self.pending.set_result(message.decode("ascii").strip())
                except UnicodeDecodeError:
                    self._fail("Adapter returned non-ASCII data.")

    async def command(self, command: str) -> str:
        if command not in ALLOWED_COMMANDS:
            raise ProbeError("Command is outside the fixed read-only diagnostic allowlist.")
        async with self.lock:
            if self.failure:
                raise ProbeError(self.failure)
            self.buffer.clear()
            pending = self.pending = asyncio.get_running_loop().create_future()
            try:
                async with asyncio.timeout(self.timeout):
                    await self.write((command + "\r").encode("ascii"))
                    reply = await pending
                lines = [line.strip() for line in reply.replace("\r", "\n").splitlines()]
                reply = "\n".join(line for line in lines if line and line != command)
                if any(message in reply for message in (
                    "NO DATA", "UNABLE TO CONNECT", "BUS INIT: ERROR",
                )):
                    raise VehicleUnavailable(reply)
                if reply == "?":
                    raise CommandRejected(f"{command}: adapter rejected the command.")
                if not reply or reply in ("ERROR", "NO DATA"):
                    raise ProbeError(f"{command}: invalid response {reply!r}.")
                return reply
            except TimeoutError:
                self.failure = f"Timed out waiting for {command}; channel is desynchronized."
                raise ProbeError(self.failure) from None
            except asyncio.CancelledError:
                self.failure = f"Interrupted {command}; channel is desynchronized."
                raise
            finally:
                if not pending.done():
                    pending.cancel()
                elif not pending.cancelled():
                    pending.exception()
                self.pending = None


def validate_reply(identity: str, voltage: str) -> float:
    if not re.fullmatch(r"STN\S+\s+v[\w.]+", identity):
        raise ProbeError(f"Unexpected firmware identity: {identity!r}")
    if not re.fullmatch(r"\d{1,2}(?:\.\d{1,3})?V?", voltage):
        raise ProbeError(f"Unexpected supply voltage: {voltage!r}")
    return float(voltage.removesuffix("V"))


def supported_pids(base: int, data: bytes) -> set[int]:
    if len(data) != 4:
        raise ProbeError("A supported-PID bitmap must contain exactly four bytes.")
    mask = int.from_bytes(data, "big")
    return {base + offset for offset in range(1, 33) if mask & (1 << (32 - offset))}


def parse_can_reply(reply: str) -> dict[str, list[bytes]]:
    """Preserve ECU identity and strictly reassemble CAN ISO-TP responses."""
    messages: dict[str, list[bytes]] = {}
    pending: dict[str, tuple[int, bytearray, int]] = {}
    for line in reply.upper().splitlines():
        line = line.strip()
        if line == "SEARCHING...":
            continue
        match = re.fullmatch(r"([0-9A-F]{3}|[0-9A-F]{8})\s+((?:[0-9A-F]{2}\s*)+)", line)
        if not match:
            raise ProbeError(f"Unrecognized CAN response line: {line!r}")
        ecu, body = match.groups()
        data = bytes.fromhex(body)
        if not data:
            raise ProbeError(f"{ecu}: empty CAN frame.")
        frame_type = data[0] >> 4
        if frame_type == 0:
            length = data[0] & 0x0F
            if not 0 < length <= len(data) - 1 or ecu in pending:
                raise ProbeError(f"{ecu}: invalid single-frame length or interrupted message.")
            messages.setdefault(ecu, []).append(data[1:1 + length])
        elif frame_type == 1:
            if len(data) < 3 or ecu in pending:
                raise ProbeError(f"{ecu}: invalid or duplicate ISO-TP first frame.")
            length = ((data[0] & 0x0F) << 8) | data[1]
            if length <= len(data) - 2 or length > 4095:
                raise ProbeError(f"{ecu}: invalid ISO-TP message length.")
            pending[ecu] = (length, bytearray(data[2:]), 1)
        elif frame_type == 2:
            if ecu not in pending:
                raise ProbeError(f"{ecu}: orphan ISO-TP continuation.")
            length, payload, sequence = pending[ecu]
            if data[0] & 0x0F != sequence:
                raise ProbeError(f"{ecu}: out-of-order ISO-TP continuation.")
            payload.extend(data[1:])
            if len(payload) >= length:
                messages.setdefault(ecu, []).append(bytes(payload[:length]))
                del pending[ecu]
            else:
                pending[ecu] = (length, payload, (sequence + 1) & 0x0F)
        else:
            raise ProbeError(f"{ecu}: unexpected ISO-TP frame type {frame_type}.")
    if pending:
        raise ProbeError("Incomplete ISO-TP response; refusing truncated diagnostic data.")
    if not messages:
        raise ProbeError("No CAN diagnostic responses were decoded.")
    return messages


def pid_responses(reply: str, mode: int, pid: int) -> dict[str, bytes]:
    responses = {}
    for ecu, messages in parse_can_reply(reply).items():
        matching = [message[2:] for message in messages if message[:2] == bytes((mode + 0x40, pid))]
        if len(matching) != 1:
            raise ProbeError(f"{ecu}: expected one response for {mode:02X}{pid:02X}.")
        responses[ecu] = matching[0]
    return responses


async def survey_vehicle(channel: CommandChannel) -> dict:
    """One bounded survey; no retries or periodic polling of sleeping ECUs."""
    raw: dict[str, str] = {}
    errors: list[dict] = []

    async def request(command: str) -> str:
        await asyncio.sleep(0.35)
        try:
            reply = await channel.command(command)
        except VehicleUnavailable as error:
            raw[command] = str(error)
            raise
        raw[command] = reply
        return reply

    result: dict = {"status": "surveying", "raw": raw, "errors": errors, "readings": {}}
    headers_enabled = False
    try:
        for command in ("ATTP0", "ATCAF1", "ATH1"):
            if await request(command) != "OK":
                raise ProbeError(f"{command}: adapter did not acknowledge survey setup.")
            if command == "ATH1":
                headers_enabled = True
        try:
            first = await request("0100")
        except VehicleUnavailable as error:
            result.update(status="ecu_unavailable", reason=str(error))
            return result
        result["protocol"] = await request("ATDP")
        number = (await request("ATDPN")).removeprefix("A")
        if number not in ("6", "7", "8", "9"):
            result.update(
                status="unsupported_protocol",
                reason="This prototype decodes standard 11-bit/29-bit CAN only.",
            )
            return result
        supported: dict[str, set[int]] = {}
        for base in SUPPORT_PAGES:
            if base == 0:
                reply = first
            elif not any(base in pids for pids in supported.values()):
                break
            else:
                try:
                    reply = await request(f"01{base:02X}")
                except VehicleUnavailable as error:
                    errors.append({"command": f"01{base:02X}", "error": str(error)})
                    break
            for ecu, bitmap in pid_responses(reply, 1, base).items():
                supported.setdefault(ecu, set()).update(supported_pids(base, bitmap))
        result["supported_pids"] = {
            ecu: [f"{pid:02X}" for pid in sorted(pids)]
            for ecu, pids in supported.items()
        }
        result["interesting_supported"] = {
            ecu: [metric.name for pid, metric in METRICS.items() if pid in pids]
            for ecu, pids in supported.items()
        }
        for pid, metric in METRICS.items():
            if not any(pid in pids for pids in supported.values()):
                continue
            command = f"01{pid:02X}"
            try:
                reply = await request(command)
            except VehicleUnavailable as error:
                errors.append({"command": command, "error": str(error)})
                continue
            for ecu, data in pid_responses(reply, 1, pid).items():
                if len(data) != metric.size:
                    raise ProbeError(f"{ecu}/{command}: incorrect response length.")
                value = metric.decode(data)
                if isinstance(value, float):
                    value = round(value, 3)
                result["readings"].setdefault(ecu, {})[metric.name] = {
                    "value": value, "unit": metric.unit,
                }
        try:
            supported09 = pid_responses(await request("0900"), 9, 0)
            if any(2 in supported_pids(0, data) for data in supported09.values()):
                vins = pid_responses(await request("0902"), 9, 2)
                result["vin"] = {}
                for ecu, data in vins.items():
                    if len(data) != 18 or data[0] != 1:
                        raise ProbeError(f"{ecu}: unexpected VIN response layout.")
                    if not re.fullmatch(rb"[A-HJ-NPR-Z0-9]{17}", data[1:]):
                        raise ProbeError(f"{ecu}: invalid VIN characters.")
                    vin = data[1:].decode("ascii")
                    result["vin"][ecu] = vin
        except VehicleUnavailable as error:
            errors.append({"command": "vehicle_information", "error": str(error)})
        result["status"] = "partial" if errors else "complete"
        return result
    finally:
        if headers_enabled and not channel.failure:
            if await channel.command("ATH0") != "OK":
                raise ProbeError("Could not restore header-off adapter output.")


async def capture_snapshot(
    channel: CommandChannel,
    on_value: Callable[[str, float | bool], Awaitable[None]],
    *,
    protocol_hint: str | None = None,
) -> dict:
    """Capture fuel and MIL first, publishing each value before the next request."""
    try:
        identity = await channel.command("STI")
    except CommandRejected:
        await asyncio.sleep(0.15)
        identity = await channel.command("STI")
    if not re.fullmatch(r"STN\S+\s+v[\w.]+", identity):
        raise ProbeError("The connected adapter did not identify as an STN device.")
    if protocol_hint not in ("6", "7", "8", "9"):
        protocol_hint = (await channel.command("ATDPN")).removeprefix("A")
    protocol = protocol_hint if protocol_hint in ("6", "7", "8", "9") else "0"
    result: dict = {"identity": identity, "values": {}, "errors": [], "protocol": None}
    headers_enabled = False
    try:
        for command in (f"ATTP{protocol}", "ATCAF1", "ATH1"):
            if await channel.command(command) != "OK":
                raise ProbeError(f"{command}: adapter did not acknowledge setup.")
            headers_enabled |= command == "ATH1"
        for pid, key in ((0x2F, "fuel_level"), (0x01, "check_engine")):
            try:
                reply = await channel.command(f"01{pid:02X}")
            except VehicleUnavailable as error:
                result["errors"].append({"field": key, "error": str(error)})
                continue
            values = pid_responses(reply, 1, pid)
            decoded = []
            for data in values.values():
                if len(data) != METRICS[pid].size:
                    raise ProbeError(f"{key}: incorrect response length.")
                value = METRICS[pid].decode(data)
                decoded.append(value["check_engine_light"] if isinstance(value, dict) else value)
            if key == "check_engine":
                value = any(decoded)
            else:
                if max(decoded) - min(decoded) > 1:
                    raise ProbeError("Multiple ECUs reported conflicting fuel levels.")
                value = round(decoded[0], 3)
            result["values"][key] = value
            await on_value(key, value)
        if result["values"]:
            number = (await channel.command("ATDPN")).removeprefix("A")
            if number in ("6", "7", "8", "9"):
                result["protocol"] = number
        else:
            raise VehicleUnavailable("The vehicle returned neither fuel level nor check-engine status.")
        return result
    finally:
        if headers_enabled and not channel.failure:
            if await channel.command("ATH0") != "OK":
                raise ProbeError("Could not restore header-off adapter output.")
