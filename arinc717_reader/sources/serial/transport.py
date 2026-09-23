"""Byte transports for the serial source.

``PySerialTransport`` talks to a real COM port (FTDI); ``PipeTransport``
is an in-memory full-duplex pair used by the virtual device and by tests.
Both expose the same minimal interface, so the acquisition code never
knows which one it is reading.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Protocol, runtime_checkable

VIRTUAL_PORT = "VIRTUAL"


class TransportError(OSError):
    """Raised when the underlying port cannot be opened, read or written."""


@runtime_checkable
class Transport(Protocol):
    name: str

    def read(self, max_bytes: int = 4096, timeout: float = 0.05) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...

    @property
    def is_open(self) -> bool: ...


# ---------------------------------------------------------------------------
# In-memory pipe
# ---------------------------------------------------------------------------


class _Channel:
    def __init__(self) -> None:
        self._chunks: deque[bytes] = deque()
        self._cond = threading.Condition()
        self.closed = False

    def put(self, data: bytes) -> None:
        with self._cond:
            if self.closed:
                raise TransportError("pipe closed")
            if data:
                self._chunks.append(bytes(data))
                self._cond.notify_all()

    def get(self, max_bytes: int, timeout: float) -> bytes:
        with self._cond:
            if not self._chunks and not self.closed:
                self._cond.wait(timeout)
            if not self._chunks:
                if self.closed:
                    raise TransportError("pipe closed")
                return b""
            out = bytearray()
            while self._chunks and len(out) < max_bytes:
                chunk = self._chunks[0]
                take = max_bytes - len(out)
                if len(chunk) <= take:
                    out.extend(chunk)
                    self._chunks.popleft()
                else:
                    out.extend(chunk[:take])
                    self._chunks[0] = chunk[take:]
            return bytes(out)

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def reopen(self) -> None:
        with self._cond:
            self.closed = False
            self._chunks.clear()


class PipeTransport:
    """One end of an in-memory full-duplex pipe."""

    def __init__(self, inbound: _Channel, outbound: _Channel, name: str):
        self._in = inbound
        self._out = outbound
        self.name = name
        self._open = True

    def read(self, max_bytes: int = 4096, timeout: float = 0.05) -> bytes:
        if not self._open:
            raise TransportError("transport closed")
        return self._in.get(max_bytes, timeout)

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError("transport closed")
        self._out.put(data)

    def close(self) -> None:
        self._open = False
        self._in.close()
        self._out.close()

    @property
    def is_open(self) -> bool:
        return self._open and not self._in.closed


def pipe_pair(name: str = "pipe") -> tuple[PipeTransport, PipeTransport]:
    """Return (host_end, device_end) of a connected pipe."""
    a = _Channel()
    b = _Channel()
    return PipeTransport(a, b, f"{name}:host"), PipeTransport(b, a, f"{name}:device")


# ---------------------------------------------------------------------------
# pyserial
# ---------------------------------------------------------------------------


def pyserial_available() -> bool:
    try:
        import serial  # noqa: F401
    except ImportError:
        return False
    return True


def list_serial_ports() -> list[tuple[str, str]]:
    """(device, description) for every serial port pyserial can see."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for info in list_ports.comports():
        ports.append((info.device, info.description or ""))
    return sorted(ports)


class PySerialTransport:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05):
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise TransportError("pyserial is not installed") from exc
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
                write_timeout=1.0,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportError(f"cannot open {port}: {exc}") from exc
        self.name = port
        self._exc = serial.SerialException

    def read(self, max_bytes: int = 4096, timeout: float = 0.05) -> bytes:
        try:
            if self._serial.timeout != timeout:
                self._serial.timeout = timeout
            waiting = self._serial.in_waiting
            size = max(1, min(max_bytes, waiting)) if waiting else 1
            data = self._serial.read(size)
            if data and self._serial.in_waiting:
                data += self._serial.read(min(max_bytes - len(data), self._serial.in_waiting))
            return data
        except (self._exc, OSError) as exc:
            raise TransportError(f"read failed on {self.name}: {exc}") from exc

    def write(self, data: bytes) -> None:
        try:
            self._serial.write(data)
        except (self._exc, OSError) as exc:
            raise TransportError(f"write failed on {self.name}: {exc}") from exc

    def close(self) -> None:
        try:
            self._serial.close()
        except (self._exc, OSError):
            pass

    @property
    def is_open(self) -> bool:
        return bool(self._serial.is_open)
