"""Read-only OBDLink BLE UART protocol and arrival snapshot helpers."""

from .arrival import ArrivalGate
from .protocol import (
    CommandChannel,
    CommandRejected,
    NOTIFY_CHAR,
    ProbeError,
    UART_SERVICE,
    VehicleUnavailable,
    WRITE_CHAR,
    capture_snapshot,
)

__all__ = [
    "ArrivalGate", "CommandChannel", "CommandRejected", "NOTIFY_CHAR",
    "ProbeError", "UART_SERVICE", "VehicleUnavailable", "WRITE_CHAR",
    "capture_snapshot",
]
