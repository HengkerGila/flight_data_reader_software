# SIM-A717 v1 stream emulator for STM32F103

Firmware for an STM32F103C8 ("Blue Pill") that turns the board into the
hardware-in-the-loop stream simulator of spec v2 §26A–§26J: it emits a
continuous ARINC 717 word stream at `WPS` words per second over USART1 and
takes ASCII commands on the same UART. The wire protocol is documented in
[`docs/12-sim-a717-protocol.md`](../../docs/12-sim-a717-protocol.md); the PC
side is the Hardware page of the ARINC 717 Reader.

> The board emulates the *data stream* of a recorder bus over a UART. It is
> not an ARINC 717 electrical interface and must not be connected to one
> (spec v2 §3A, §54).

## Hardware and wiring

| Item | Value |
| --- | --- |
| MCU | STM32F103C8, 64 KB flash, 20 KB SRAM, 8 MHz crystal (HSE) |
| Clock | 72 MHz from HSE × 9 through the PLL; APB1 at 36 MHz (TIM2 therefore clocks at 72 MHz) |
| UART | USART1: PA9 TX, PA10 RX, 115200 baud, 8N1, no flow control (`UART_BAUD` in `src/config.h`) |
| Logic level | 3.3 V TTL. Set the FTDI module to 3.3 V I/O. Never connect RS-232 levels. |
| LED | PC13 (active low) is on while the stream is running |

```
STM32F103              FTDI USB-UART
--------------------------------------
PA9  USART1_TX   --->  RXD
PA10 USART1_RX   <---  TXD
GND              <-->  GND
```

Power the board from its own USB connector or from the FTDI module's 3.3 V /
5 V pin (the Blue Pill regulates 5 V down); do not feed 3.3 V from the FTDI
into the board's 5 V pin.

## Toolchain, build, flash

Requirements: `arm-none-eabi-gcc` (with binutils), `make`; for flashing,
`openocd` with an ST-Link, or `stm32flash` for the ROM bootloader over the
same UART.

```bash
cd firmware/stm32f103_sim_a717
make                                   # build/sim_a717.elf, .bin, .hex, then prints the size
make flash                             # ST-Link via openocd: program, verify, reset
make flash-serial PORT=/dev/ttyUSB0    # ROM bootloader via stm32flash (BOOT0 = 1, reset, then run)
make clean
```

The build is freestanding (`-ffreestanding -nostdlib`): `src/startup.c`
provides the vector table and C runtime initialisation, `src/libc_min.c`
provides the handful of routines GCC or the code needs (`memset`, `memcpy`,
`memmove`, `memcmp`, `strcmp`, `strlen`). `CFLAGS` includes
`-fno-tree-loop-distribute-patterns` so the compiler cannot turn those
routines' loops back into calls to themselves.

Typical size (GCC 13, `-Os`):

```
   text    data     bss
   6044      20   16060   bytes
```

Flash use is about 6 KB of 64 KB; static RAM is about 16 KB of 20 KB. The
linker script (`linker.ld`) reserves 1 KB after `.bss` and fails the link if
less than that is left for the stack.

After flashing, the ROM bootloader or ST-Link releases the UART; on the PC
choose the FTDI port on the Hardware page (baud 115200) and press
**Connect**. The handshake's `INFO` reply reads
`SIM-A717 v1 wps=256 mode=… proto=STREAM running=0 fw=1.0`.

## Module map

The layout follows spec v2 §26C.

| Spec module | Source | Role |
| --- | --- | --- |
| frame_model | `frame_model.c/.h` | Two frame buffers (`4 × MAX_WPS` words each): the **active** one streamed by the scheduler and the **staged** one filled by `LOAD_SF`; `frame_swap()` at a frame boundary after `COMMIT`; WPS and sync words. |
| scenario_state / parameter_encoder | *on the PC* | Engineering signals are generated and encoded by the application and uploaded (spec v2 §26I); the board only stores words. |
| signal_generator | `signal_generator.c/.h` | Device word sources: `FIXED`, `RANDOM` (xorshift32), `WALK` (±8 per word), `UPLOAD`. |
| stream_scheduler | `stream_scheduler.c/.h` | TIM2 interrupt: one word per period, subframe and frame bookkeeping, buffer swap, fault injection. |
| sim_a717_protocol | `sim_a717_protocol.c/.h` | 16-bit little-endian word container, framed packet encoder, CRC-32. |
| uart_driver | `uart.c/.h` | USART1 with interrupt-driven TX and RX ring buffers. |
| command_receiver | `command_receiver.c/.h` | Line assembly and the command interpreter; replies only while stopped. |
| diagnostics | `diagnostics.c/.h` | Counters: words, subframes and frames sent, TX bytes dropped, commands, bad commands. |
| — | `system.c/.h`, `startup.c`, `stm32f103.h`, `config.h`, `libc_min.c` | Clocks, SysTick millisecond counter, LED, vector table, register map, build-time limits, freestanding libc. |
| — | `main.c` | Initialises everything, then loops over `command_receiver_poll()` and `scheduler_poll()`. |

## How the timing is generated

TIM2 is prescaled to a 1 MHz tick. The nominal word period is
`1 000 000 / WPS` microseconds, which is not an integer for every WPS
(3906.25 µs at 256 WPS). The scheduler keeps the fraction with a Bresenham
accumulator: every period is `period_base` ticks, and the remainder
`1 000 000 mod WPS` is accumulated; whenever it reaches `WPS` the next
period is one tick longer. Over a subframe the periods therefore sum to
exactly one second, and the auto-reload register is rewritten at every
update interrupt with the next period.

Each interrupt emits one word: it takes the value from the signal generator
(word 1 is always the sync word), applies any armed word-level fault, queues
the two container bytes in the TX ring (continuous mode) or appends the word
to the packet under construction (framed mode, sent as one packet at the end
of the subframe), and advances the word / subframe / frame counters. A
`COMMIT` received while running swaps the buffers at the next frame
boundary, never mid-frame.

Subframe-level faults (`DROP_SUBFRAME`, `BAD_CRC`, `WRONG_WPS`,
`DELAY_SUBFRAME`, `OUT_OF_ORDER_SUBFRAME`) are decided when a subframe
begins. Pauses (`PAUSE_STREAM`, `DELAY_SUBFRAME`) stop the timer and let the
main loop restart it from the SysTick millisecond counter.
`DISCONNECT_RECONNECT` is rejected: only the PC's virtual device can unplug
its own cable.

## UART rings and limits

| Constant (`config.h`) | Value | Why |
| --- | --- | --- |
| `MAX_WPS` | 512 | Two frame buffers of `4 × MAX_WPS` 16-bit words (8 KB) must fit in 20 KB with everything else. `SET_WPS` above this is refused. |
| `TX_RING_SIZE` | 2048 bytes | Must hold one complete framed packet: 15 + 2 × WPS + 4 bytes (1043 at 512 WPS). When the ring is full, bytes are dropped and counted in `tx_dropped_bytes` rather than blocking the interrupt. |
| `RX_RING_SIZE` | 1024 bytes | Command bytes are taken out by the main loop, which drains far faster than 115200 baud fills. |
| `LINE_MAX` | 1600 characters | A `LOAD_SF` line is `"LOAD_SF n "` plus 3 hex characters per word (1546 at 512 WPS). Longer lines are answered with `+ERR line too long`. |
| `PAUSE_STREAM_MS`, `DELAY_SUBFRAME_MS` | 2000, 500 | Fault durations. |

Bandwidth check at 115200 baud (11.5 KB/s): the continuous stream needs
`2 × WPS` bytes per second (512 B/s at 256 WPS, 1 KB/s at 512 WPS), a framed
packet a little more; the PC's engineering-signal upload sends about 3.1 KB
per 4 s frame at 256 WPS in the other direction. Both directions have ample
margin, which is why the word timer never has to wait for the UART.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| **Connect fails: "no SIM-A717 device answered"** | The PC sent `STOP` and `PING` and got nothing back within 1.5 s. Check TX/RX are crossed (PA9 → RXD, PA10 ← TXD), GND is shared, the FTDI module is at 3.3 V, and the PC baud matches `UART_BAUD` (115200). A board still in the bootloader (BOOT0 high) does not run the firmware. |
| **Connect fails with `INVALID_SIM_PROTOCOL`** | Something answered, but not with `+OK PONG` or it rejected `SET_WPS` / `SET_SYNC` / `SET_PROTOCOL`. Another device is on the port, or the firmware is older than the PC application. |
| **Stream runs (LED on, bytes received) but sync stays `SEARCHING`** | The dataframe's sync words or WPS differ from what the board streams. Press **Reset device**, which re-sends both. If the Hardware page says "sync spacing looks like 128 WPS", the board is streaming at another WPS than the dataframe declares. |
| **Frequent `STREAM_SYNC_LOST` without faults armed** | Check `Discarded bytes` on the Hardware page: dropped or corrupted bytes on the cable (bad ground, long jumper wires, a 5 V FTDI driving a 3.3 V input). |
| **`+ERR line too long`** | A `LOAD_SF` line exceeded `LINE_MAX`; the WPS is above what the firmware supports. |
| **`Bad packets` climbing in framed mode** | Expected only with `BAD_CRC` armed; otherwise the cable is dropping bytes. |
| **Nothing after `make flash`** | openocd needs the ST-Link connected to SWDIO/SWCLK/GND (and 3.3 V if the board is unpowered); `make flash-serial` needs BOOT0 = 1 and a reset before, BOOT0 = 0 and a reset after. |

The PC-side symptoms and states are listed in
[`docs/10-troubleshooting.md`](../../docs/10-troubleshooting.md).
