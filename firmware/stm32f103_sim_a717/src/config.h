/* Build-time limits and defaults for the SIM-A717 v1 stream emulator. */
#ifndef CONFIG_H
#define CONFIG_H

#define FW_VERSION          "1.0"
#define SYSCLK_HZ           72000000UL
#define UART_BAUD           115200UL
#define DEFAULT_WPS         256
#define MAX_WPS             512          /* two frame buffers of 4 x MAX_WPS words fit in 20 KB RAM */
#define SUBFRAME_COUNT      4
#define WORD_MAX            0x0FFF

#define DEFAULT_SYNC_1      583
#define DEFAULT_SYNC_2      1464
#define DEFAULT_SYNC_3      2631
#define DEFAULT_SYNC_4      3512

#define TX_RING_SIZE        2048         /* must hold one framed packet (15 + 2*WPS + 4 bytes) */
#define RX_RING_SIZE        1024
#define LINE_MAX            1600         /* "LOAD_SF n " + 3 hex chars per word */

#define PAUSE_STREAM_MS     2000
#define DELAY_SUBFRAME_MS   500

#endif
