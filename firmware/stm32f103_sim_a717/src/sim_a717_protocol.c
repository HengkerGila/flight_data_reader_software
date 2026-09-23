#include "sim_a717_protocol.h"
#include "uart.h"

void sim_send_word(uint16_t word)
{
    uint8_t bytes[2] = { (uint8_t)(word & 0xFF), (uint8_t)((word >> 8) & 0x0F) };
    uart_write(bytes, 2);
}

/* CRC-32 (IEEE 802.3, reflected, same as zlib.crc32). */
uint32_t sim_crc32(const uint8_t *data, size_t len)
{
    uint32_t crc = 0xFFFFFFFFUL;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xEDB88320UL & (0UL - (crc & 1UL)));
    }
    return ~crc;
}

static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16)); }

size_t sim_encode_packet(uint8_t *out, size_t cap, uint16_t sequence, uint32_t frame_id,
                         uint8_t subframe, uint16_t wps, const uint16_t *words, uint16_t count,
                         int corrupt_crc)
{
    size_t payload = (size_t)count * 2;
    size_t total = SIM_PACKET_HEADER + payload + SIM_PACKET_CRC;
    if (total > cap) return 0;
    out[0] = 'S'; out[1] = 'A';
    out[2] = SIM_PROTOCOL_VERSION;
    out[3] = SIM_MSG_SUBFRAME;
    put16(out + 4, sequence);
    put32(out + 6, frame_id);
    out[10] = subframe;
    put16(out + 11, wps);
    put16(out + 13, (uint16_t)payload);
    for (uint16_t i = 0; i < count; i++) put16(out + SIM_PACKET_HEADER + 2 * i, (uint16_t)(words[i] & 0x0FFF));
    uint32_t crc = sim_crc32(out, SIM_PACKET_HEADER + payload);
    if (corrupt_crc) crc ^= 0xA5A5A5A5UL;
    put32(out + SIM_PACKET_HEADER + payload, crc);
    return total;
}

const char *protocol_name(protocol_mode_t mode) { return mode == PROTO_FRAMED ? "FRAMED" : "STREAM"; }

int protocol_parse(const char *text, protocol_mode_t *out)
{
    const char *s = "STREAM", *f = "FRAMED";
    int is_s = 1, is_f = 1;
    for (int i = 0; i < 7; i++) {
        if (text[i] != s[i]) is_s = 0;
        if (text[i] != f[i]) is_f = 0;
        if (!text[i]) break;
    }
    if (is_s) { *out = PROTO_STREAM; return 1; }
    if (is_f) { *out = PROTO_FRAMED; return 1; }
    return 0;
}
