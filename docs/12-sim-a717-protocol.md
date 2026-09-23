# 12 — The SIM-A717 v1 protocol

`SIM-A717 v1` is the project-owned link between the PC application and the
hardware-in-the-loop stream emulator (an STM32F103 behind an FTDI USB-UART,
or the in-process virtual device). It carries a continuous ARINC 717 word
stream in one direction and ASCII commands in the other.

> It is a **simulation protocol** (spec v2 §26E). It does not imitate, and
> must not be presented as, the ABUS 717 transport or any other vendor
> protocol, and a UART carrying it is not an ARINC 717 electrical interface
> (spec v2 §3A, §54). Real acquisition is Phase 14 and will plug in behind
> the same subframe boundary.

The PC side lives in `arinc717_reader/sources/serial/sim_a717_protocol.py`
(encoding, decoding, constants), the device side in
`firmware/stm32f103_sim_a717/` ([README](../firmware/stm32f103_sim_a717/README.md))
and in the virtual device `sources/serial/virtual_device.py`.

## Link and timing

| Item | Value |
| --- | --- |
| Physical link | STM32 USART1 (PA9 TX, PA10 RX) at 3.3 V TTL to an FTDI USB-UART; GND shared (spec v2 §26A) |
| UART | 115200 baud, 8 data bits, no parity, 1 stop bit, no flow control (spec v2 §26D). The PC lets you pick the baud rate; the firmware's rate is a build constant (`UART_BAUD`). |
| Word period | `1 / WPS` seconds: 3.90625 ms at 256 WPS (spec v2 §26B) |
| Subframe | `WPS` words, always one second |
| Frame | Four subframes, four seconds; subframe 1 restarts at every 4 s boundary |

The device emits words continuously from a hardware timer; it never bursts
a subframe (spec v2 §26B).

## Two directions, one UART

| Direction | Content |
| --- | --- |
| Device → PC | While **stopped**: reply lines `+OK …` / `+ERR …`. While **running**: the word stream (continuous mode) or subframe packets (framed mode), and nothing else. |
| PC → device | ASCII command lines, at any time. |

Because the device only answers while it is stopped, replies can never
interleave with binary data. The PC therefore sends `STOP` first whenever it
needs an answer, and sends commands during streaming as fire-and-forget.

## Continuous word stream mode (`STREAM`)

The primary mode. Every canonical 12-bit word is sent in a 16-bit
little-endian container:

```
word 0x0A5C  →  bytes 5C 0A
word 0x0000  →  bytes 00 00
word 0x0FFF  →  bytes FF 0F
```

The upper nibble of the container is reserved and always zero. The PC parser
relies on that: a mis-aligned pair (connecting mid-stream, a dropped byte)
usually reads as a container with a non-zero upper nibble, so the parser
discards one byte and retries until pairs are valid again. Each discarded
byte counts as an *invalid word* and a *discarded byte* in the diagnostics.

There is no framing at this level: the sync words at word 1 of every
subframe (583, 1464, 2631, 3512 by default, taken from the dataframe) are the
only structure, exactly as on a recorder bus.

## Framed packet mode (`FRAMED`)

A diagnostics mode (spec v2 §26E) in which the device sends one packet per
subframe. It exists to test CRC handling, sequence gaps, dropped subframes
and deliberate corruption.

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 2 | Magic | `"SA"` |
| 2 | 1 | Protocol version | 1 |
| 3 | 1 | Message type | `0x01` subframe, `0x02` text |
| 4 | 2 | Sequence number | u16 little-endian, wraps |
| 6 | 4 | Frame id | u32 little-endian, the device's frame counter |
| 10 | 1 | Subframe | 1..4 |
| 11 | 2 | WPS | u16 |
| 13 | 2 | Payload length | bytes, at most 4096 |
| 15 | n | Payload | `WPS` words as 16-bit little-endian containers |
| 15 + n | 4 | CRC-32 | IEEE 802.3 (same as Python `zlib.crc32`) over header + payload, little-endian |

The header is 15 bytes. The PC parser hunts for the magic, validates the
header (magic, version, length), waits for the whole packet, checks the CRC,
and on any failure counts a *bad packet*, drops one byte and searches again.
A packet whose WPS, length or sync word is wrong is delivered as an
**INVALID** subframe; one whose subframe number is outside 1..4 is counted
as an invalid subframe and dropped; a gap in the frame id or subframe
sequence shows up as **MISSING** slots in the assembler.

## Command channel

A command is one line: the command word, space-separated arguments, `\n`
(`\r` is accepted too). Command words are case-insensitive. A reply is
`+OK text` or `+ERR text` on one line, sent **only while the stream is
stopped**; `START` never gets a reply because the stream begins at once.

| Command | Arguments | Reply | Effect |
| --- | --- | --- | --- |
| `PING` | — | `+OK PONG` | Liveness check. |
| `INFO` | — | `+OK SIM-A717 v1 wps=256 mode=UPLOAD proto=STREAM running=0 fw=1.0` (firmware) / `… virtual=1` (virtual device) | Current configuration. |
| `START` | — | *none* | Starts the timer-driven stream at the current word position. |
| `STOP` | — | `+OK STOPPED` | Stops the stream. |
| `RESET` | — | `+OK RESET` | Stops, rewinds to frame 0 / subframe 1 / word 1, blanks both frame buffers (sync words kept), mode `FIXED`, protocol `STREAM`, all faults cleared. WPS and sync words are kept. |
| `SET_WPS` | `n` | `+OK WPS n` | Words per second; refused while running. Firmware: 1..512, virtual device: 1..4096. Blanks the frame buffers. |
| `SET_MODE` | `FIXED` \| `RANDOM` \| `WALK` \| `UPLOAD` | `+OK MODE X` | Device word source, see below. Allowed while running. |
| `SET_PROTOCOL` | `STREAM` \| `FRAMED` | `+OK PROTOCOL X` | Stream framing; refused while running. |
| `SET_SYNC` | `s1 s2 s3 s4` | `+OK SYNC s1 s2 s3 s4` | Sync words (0..4095) written into word 1 of both frame buffers. |
| `SET_WORD` | `sf word value` | `+OK WORD` (firmware) / `+OK WORD sf word value` (virtual) | Writes one word of the active frame (subframe 1..4, word 1..WPS, value 0..4095). |
| `LOAD_SF` | `sf hexwords` | `+OK LOADED sf` | Uploads one subframe into the **staged** frame: exactly `WPS` words as 3 hex characters each (`247000D54…`). |
| `COMMIT` | — | `+OK COMMIT` | Makes the staged frame active at the next frame boundary; immediately when stopped. |
| `FAULT` | `NAME [count]` | `+OK FAULT NAME count` | Arms a fault `count` times (default 1, 0 disarms, 65535 = continuous). |

Errors are `+ERR reason`, for example `+ERR unknown command`,
`+ERR stop the stream before changing WPS`, `+ERR word count does not match
WPS`.

### Device word sources (`SET_MODE`)

Word 1 of every subframe is always the sync word, whatever the mode.

| Mode | Words 2..WPS | Use |
| --- | --- | --- |
| `FIXED` | The active frame buffer, unchanged. | Deterministic frames set with `SET_WORD` / `LOAD_SF`. |
| `RANDOM` | Uniform random 12-bit words. | Parser, Frame View and stress testing (spec v2 §26H "Raw Random"); engineering values are meaningless. |
| `WALK` | Each word of the active buffer takes a random step of −8..+8 per emission, clamped to 0..4095. | A slowly changing raw stream without PC involvement. |
| `UPLOAD` | The active frame buffer, which the PC keeps replacing through `LOAD_SF` + `COMMIT`. | Engineering-coherent signals (spec v2 §26H "Engineering Random"); the application's default. |

### Frame buffers

The device holds an **active** frame (what the scheduler streams) and a
**staged** frame (what `LOAD_SF` fills). `COMMIT` swaps them at the next
frame boundary, so a frame is never replaced halfway through, and the new
staged buffer is initialised as a copy of the new active one so a partial
upload (only some subframes) still streams a coherent frame.

### Faults (`FAULT`)

All faults are disarmed by default (spec v2 §26J). A fault with a count fires
that many times at the point where it applies.

| Fault | Applies at | Effect on the wire | What the PC reports |
| --- | --- | --- | --- |
| `DROP_WORD` | one word | The word is not transmitted. | Sync loss one subframe later (the stream is now one word short), then re-lock. |
| `CORRUPT_WORD` | one word | The value is XOR-ed with a random non-zero pattern. | A wrong word; a sync loss if it hits word 1. |
| `DROP_SUBFRAME` | start of a subframe | The whole subframe is not transmitted (in framed mode no packet is sent). | An INVALID subframe and `STREAM_SYNC_LOST`, then re-lock (continuous mode); a MISSING slot (framed mode). |
| `BAD_CRC` | start of a subframe | The packet's CRC is corrupted (framed mode only). | A bad packet, then a MISSING slot when the next packet arrives. |
| `DELAY_SUBFRAME` | start of a subframe | Emission pauses 500 ms before the subframe. | The word rate dips; nothing is lost. |
| `OUT_OF_ORDER_SUBFRAME` | start of a frame | The frame is sent as SF1, SF3, SF2, SF4. | In continuous mode a sync loss and re-lock; in framed mode SF2 is MISSING and then LATE. |
| `WRONG_WPS` | start of a subframe | That subframe is emitted with 128 words (64 when WPS is 128) at the matching faster rate. | Sync loss; while searching, `STREAM_RATE_MISMATCH` names the spacing the sync words actually have. |
| `PAUSE_STREAM` | one word | Emission stops for 2 s, then continues. | The rate falls to 0 WPS and recovers; no sync loss. |
| `DISCONNECT_RECONNECT` | one word | **Virtual device only**: the virtual cable is closed for 1.5 s. The firmware answers `+ERR DISCONNECT_RECONNECT is only available on the virtual device`. | `SERIAL_DISCONNECTED`, `RECONNECTING`, `RECONNECTED`, and the stream restarts. |

## What the PC sends and when

![Handshake replies in the Stream events log](../img/mockups_with_data/hardware_data.png)

*The Stream events log after Connect and Start: the `+OK` replies to SET_PROTOCOL, SET_MODE, LOAD_SF 1–4, COMMIT and INFO, then STREAM_STARTED and STREAM_SYNC_LOCKED.*


The serial service (`services/serial_service.py`) runs this dialogue.

**Connect** (spec v2 §26I: the PC is the configuration authority):

```
STOP                      → +OK STOPPED         (also silences a device left running)
PING                      → +OK PONG            (anything else: INVALID_SIM_PROTOCOL)
SET_WPS 256               → +OK WPS 256         (from the loaded dataframe)
SET_SYNC 583 1464 2631 3512 → +OK SYNC …        (from the loaded dataframe)
SET_PROTOCOL STREAM       → +OK PROTOCOL STREAM (or FRAMED)
SET_MODE UPLOAD           → +OK MODE UPLOAD     (engineering signals; a device mode otherwise)
LOAD_SF 1 …  LOAD_SF 2 …  LOAD_SF 3 …  LOAD_SF 4 …  COMMIT   (engineering signals only)
INFO                      → +OK SIM-A717 v1 …   (shown on the Hardware page)
```

Every step waits up to 1.5 s for its reply; a missing reply means no
SIM-A717 device is on that port (`SERIAL_PORT_ERROR`), a rejected step
means an incompatible device (`INVALID_SIM_PROTOCOL`).

**Start stream:** with engineering signals, frame 0 is uploaded and committed
first (with replies, since the device is stopped); then `START` is sent, the
PC's parser switches from text to word mode and the synchronizer starts
searching.

**During the stream** (engineering signals): each time subframe 1 of frame
*n* arrives, the PC generates frame *n + 1* and sends `LOAD_SF` × 4 +
`COMMIT` without waiting for replies; the device swaps buffers at the end of
frame *n*. Fault injection, `SET_MODE` and `SET_WORD` are likewise sent
without replies while running.

**Stop stream:** `STOP` is sent, the parser returns to text mode (any word
bytes still in flight are ignored because they are not reply lines), and the
last received frame becomes a static, editable frame in the Frame View.

**Reset device:** stops the stream if needed, then `RESET`, `SET_WPS`,
`SET_SYNC`, `SET_PROTOCOL`, `SET_MODE` (and the upload) as at connect.

**Disconnect and reconnect:** when the transport fails (`SERIAL_DISCONNECTED`)
while the user still wants to be connected, the service retries the whole
connect dialogue every 0.5 s and, if the stream was running, sends `START`
again once it succeeds.

## The virtual device

Selecting the port `VIRTUAL` on the Hardware page connects to
`VirtualSimDevice`, an in-process emulation of the firmware over an
in-memory pipe. It implements the same commands, framing, buffers and faults,
so the whole acquisition, decoding, graphing and recording path runs and is
tested without hardware. It is a stepping engine: a background thread calls
`step(now)` and emits every word that is due, which is what lets the tests
drive it with a manual clock and lets the Hardware page run it faster than
real time (the *Virtual speed* factor).

| Aspect | Firmware | Virtual device |
| --- | --- | --- |
| `INFO` | `… fw=1.0` | `… virtual=1` |
| `SET_WPS` range | 1..512 (`MAX_WPS`, RAM bound) | 1..4096 |
| `SET_WORD` reply | `+OK WORD` | `+OK WORD sf word value` |
| `DISCONNECT_RECONNECT` | rejected | supported |
| Time base | TIM2 at 1 MHz | `time.monotonic()` × speed factor |
| Pause / delay / disconnect durations | 2 s / 0.5 s / — | 2 s / 0.5 s / 1.5 s, divided by the speed factor |

Everything downstream of the subframe assembler is identical for the
firmware, the virtual device and a replayed session: see
[13 — Live streaming, graphs and recording](13-live-streaming-graphs-recording.md).
