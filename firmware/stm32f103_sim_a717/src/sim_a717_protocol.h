/* SIM-A717 v1 wire encoding (see docs/12-sim-a717-protocol.md). */
#ifndef SIM_A717_PROTOCOL_H
#define SIM_A717_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>

#define SIM_PROTOCOL_VERSION 1
#define SIM_MSG_SUBFRAME     0x01
#define SIM_PACKET_HEADER    15
#define SIM_PACKET_CRC       4

typedef enum { PROTO_STREAM = 0, PROTO_FRAMED } protocol_mode_t;

void sim_send_word(uint16_t word);                 /* 16-bit little-endian container */
size_t sim_encode_packet(uint8_t *out, size_t cap, uint16_t sequence, uint32_t frame_id,
                         uint8_t subframe, uint16_t wps, const uint16_t *words, uint16_t count,
                         int corrupt_crc);
uint32_t sim_crc32(const uint8_t *data, size_t len);
const char *protocol_name(protocol_mode_t mode);
int protocol_parse(const char *text, protocol_mode_t *out);
#endif
