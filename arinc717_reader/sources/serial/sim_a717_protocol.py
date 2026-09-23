"""SIM-A717 v1 — the project-owned HIL transport protocol (spec v2 §26E).

This is a simulation protocol between the PC and the STM32 stream
emulator.  It does not imitate, and must not be presented as, any ABUS
or other vendor transport.

Two stream modes share one UART:

* **Continuous word stream** (primary).  Every canonical 12-bit ARINC word is
  sent in a 16-bit little-endian container.  The upper nibble of the
  container is reserved and MUST be zero; the parser uses that invariant
  to recover byte alignment after connecting mid-stream.
* **Framed packet** (diagnostics).  One packet per subframe with sequence
  number, frame id, subframe id, WPS, payload and CRC-32.

Commands travel PC → device as ASCII lines terminated by ``\\n``.  The
device only answers (``+OK …`` / ``+ERR …`` lines) while its stream is
stopped, so replies never interleave with word data; the PC therefore
sends ``STOP`` before anything it expects an answer to.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from ...domain.frame import WORD_MAX

PROTOCOL_NAME = "SIM-A717"
PROTOCOL_VERSION = 1

MODE_STREAM = "STREAM"
MODE_FRAMED = "FRAMED"
STREAM_MODES = (MODE_STREAM, MODE_FRAMED)

# ---------------------------------------------------------------------------
# Continuous word stream
# ---------------------------------------------------------------------------

WORD_CONTAINER_BYTES = 2
CONTAINER_VALID_MASK = 0x0FFF


def encode_word(word: int) -> bytes:
    """Serialize one 12-bit word into its 16-bit little-endian container."""
    if not 0 <= word <= WORD_MAX:
        raise ValueError(f"word {word} outside 0..{WORD_MAX}")
    return struct.pack("<H", word)


def encode_words(words) -> bytes:
    return b"".join(encode_word(w) for w in words)


def container_is_valid(container: int) -> bool:
    """A container whose reserved upper nibble is non-zero is invalid."""
    return (container & ~CONTAINER_VALID_MASK) == 0


# ---------------------------------------------------------------------------
# Framed packet mode
# ---------------------------------------------------------------------------

PACKET_MAGIC = b"SA"
MSG_SUBFRAME = 0x01
MSG_TEXT = 0x02

# magic(2) version(1) type(1) seq(2) frame(4) subframe(1) wps(2) length(2)
_HEADER = struct.Struct("<2sBBHIBHH")
PACKET_HEADER_BYTES = _HEADER.size  # 15
PACKET_CRC_BYTES = 4
PACKET_MAX_PAYLOAD = 4096


@dataclass
class SubframePacket:
    sequence: int
    frame_id: int
    subframe: int
    wps: int
    words: list[int] = field(default_factory=list)
    message_type: int = MSG_SUBFRAME
    version: int = PROTOCOL_VERSION


class PacketError(ValueError):
    """Raised when bytes do not form a valid SIM-A717 packet."""


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode_packet(packet: SubframePacket, corrupt_crc: bool = False) -> bytes:
    payload = encode_words(packet.words)
    header = _HEADER.pack(
        PACKET_MAGIC,
        packet.version,
        packet.message_type,
        packet.sequence & 0xFFFF,
        packet.frame_id & 0xFFFFFFFF,
        packet.subframe & 0xFF,
        packet.wps & 0xFFFF,
        len(payload),
    )
    body = header + payload
    crc = crc32(body)
    if corrupt_crc:
        crc ^= 0xA5A5A5A5
    return body + struct.pack("<I", crc)


def packet_length(header_bytes: bytes) -> int:
    """Total packet length implied by a header (raises on a bad header)."""
    if len(header_bytes) < PACKET_HEADER_BYTES:
        raise PacketError("header too short")
    magic, version, _mtype, _seq, _frame, _sf, _wps, length = _HEADER.unpack_from(
        header_bytes
    )
    if magic != PACKET_MAGIC:
        raise PacketError("bad magic")
    if version != PROTOCOL_VERSION:
        raise PacketError(f"unsupported protocol version {version}")
    if length > PACKET_MAX_PAYLOAD:
        raise PacketError(f"payload length {length} too large")
    return PACKET_HEADER_BYTES + length + PACKET_CRC_BYTES


def decode_packet(data: bytes) -> SubframePacket:
    """Decode one complete packet; raises PacketError on CRC or format errors."""
    total = packet_length(data)
    if len(data) < total:
        raise PacketError("packet truncated")
    body, crc_bytes = data[: total - PACKET_CRC_BYTES], data[total - PACKET_CRC_BYTES : total]
    (crc,) = struct.unpack("<I", crc_bytes)
    if crc != crc32(body):
        raise PacketError("CRC mismatch")
    _magic, version, mtype, seq, frame_id, subframe, wps, length = _HEADER.unpack_from(
        body
    )
    payload = body[PACKET_HEADER_BYTES:]
    if mtype == MSG_SUBFRAME:
        if length % WORD_CONTAINER_BYTES:
            raise PacketError("odd payload length")
        containers = struct.unpack(f"<{length // 2}H", payload)
        words = [c & CONTAINER_VALID_MASK for c in containers]
    else:
        words = list(payload)
    return SubframePacket(
        sequence=seq,
        frame_id=frame_id,
        subframe=subframe,
        wps=wps,
        words=words,
        message_type=mtype,
        version=version,
    )


# ---------------------------------------------------------------------------
# Command channel (PC → device)
# ---------------------------------------------------------------------------

CMD_START = "START"
CMD_STOP = "STOP"
CMD_RESET = "RESET"
CMD_PING = "PING"
CMD_INFO = "INFO"
CMD_SET_WPS = "SET_WPS"
CMD_SET_MODE = "SET_MODE"
CMD_SET_PROTOCOL = "SET_PROTOCOL"
CMD_SET_SYNC = "SET_SYNC"
CMD_SET_WORD = "SET_WORD"
CMD_LOAD_SF = "LOAD_SF"
CMD_COMMIT = "COMMIT"
CMD_FAULT = "FAULT"

# Device-side signal modes (spec v2 §26H / §26I).  Engineering-coherent
# signals are generated on the PC and uploaded (``UPLOAD``); the device's own
# modes work on raw words and exist for parser / stress testing.
DEVICE_MODE_FIXED = "FIXED"
DEVICE_MODE_RANDOM = "RANDOM"
DEVICE_MODE_WALK = "WALK"
DEVICE_MODE_UPLOAD = "UPLOAD"
DEVICE_MODES = (DEVICE_MODE_FIXED, DEVICE_MODE_RANDOM, DEVICE_MODE_WALK, DEVICE_MODE_UPLOAD)

FAULT_DROP_WORD = "DROP_WORD"
FAULT_CORRUPT_WORD = "CORRUPT_WORD"
FAULT_DROP_SUBFRAME = "DROP_SUBFRAME"
FAULT_BAD_CRC = "BAD_CRC"
FAULT_DELAY_SUBFRAME = "DELAY_SUBFRAME"
FAULT_OUT_OF_ORDER_SUBFRAME = "OUT_OF_ORDER_SUBFRAME"
FAULT_WRONG_WPS = "WRONG_WPS"
FAULT_PAUSE_STREAM = "PAUSE_STREAM"
FAULT_DISCONNECT_RECONNECT = "DISCONNECT_RECONNECT"
FAULTS = (
    FAULT_DROP_WORD,
    FAULT_CORRUPT_WORD,
    FAULT_DROP_SUBFRAME,
    FAULT_BAD_CRC,
    FAULT_DELAY_SUBFRAME,
    FAULT_OUT_OF_ORDER_SUBFRAME,
    FAULT_WRONG_WPS,
    FAULT_PAUSE_STREAM,
    FAULT_DISCONNECT_RECONNECT,
)
FAULT_CONTINUOUS = 65535

REPLY_OK = "+OK"
REPLY_ERR = "+ERR"
LINE_END = "\n"


def command_line(command: str, *args) -> bytes:
    """Format a command line: ``CMD arg1 arg2\\n``."""
    parts = [command, *(str(a) for a in args)]
    return (" ".join(parts) + LINE_END).encode("ascii")


def load_subframe_line(subframe: int, words) -> bytes:
    """``LOAD_SF n <hex3 hex3 …>``: upload one subframe of the staged frame."""
    return command_line(CMD_LOAD_SF, subframe, "".join(f"{w & WORD_MAX:03X}" for w in words))


def parse_load_subframe(args: list[str]) -> tuple[int, list[int]]:
    if len(args) != 2:
        raise ValueError("LOAD_SF needs <subframe> <hexwords>")
    subframe = int(args[0])
    hexwords = args[1]
    if len(hexwords) % 3:
        raise ValueError("hex word string must be a multiple of 3 characters")
    words = [int(hexwords[i : i + 3], 16) for i in range(0, len(hexwords), 3)]
    return subframe, words


def parse_command_line(line: str) -> tuple[str, list[str]]:
    parts = line.strip().split()
    if not parts:
        raise ValueError("empty command")
    return parts[0].upper(), parts[1:]


def parse_reply(line: str) -> tuple[bool, str]:
    """Return (ok, message) from a ``+OK …`` / ``+ERR …`` reply line."""
    text = line.strip()
    if text.startswith(REPLY_OK):
        return True, text[len(REPLY_OK) :].strip()
    if text.startswith(REPLY_ERR):
        return False, text[len(REPLY_ERR) :].strip()
    raise ValueError(f"not a reply line: {text!r}")
