#include "signal_generator.h"
#include "frame_model.h"

volatile device_mode_t g_mode = MODE_FIXED;
static uint32_t prng_state = 0x2545F491UL;

uint32_t prng_next(void)
{
    uint32_t x = prng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    prng_state = x;
    return x;
}

uint16_t signal_word(uint8_t subframe, uint16_t index)
{
    frame_buffer_t *frame = frame_active();
    if (index == 0) return g_sync_words[subframe];
    if (index >= MAX_WPS) return (uint16_t)(prng_next() & WORD_MAX);
    switch (g_mode) {
    case MODE_RANDOM:
        return (uint16_t)(prng_next() & WORD_MAX);
    case MODE_WALK: {
        int32_t value = frame->words[subframe][index] + (int32_t)(prng_next() % 17) - 8;
        if (value < 0) value = 0;
        if (value > WORD_MAX) value = WORD_MAX;
        frame->words[subframe][index] = (uint16_t)value;
        return (uint16_t)value;
    }
    case MODE_FIXED:
    case MODE_UPLOAD:
    default:
        return frame->words[subframe][index];
    }
}

const char *mode_name(device_mode_t mode)
{
    switch (mode) {
    case MODE_RANDOM: return "RANDOM";
    case MODE_WALK:   return "WALK";
    case MODE_UPLOAD: return "UPLOAD";
    default:          return "FIXED";
    }
}

static int str_eq(const char *a, const char *b)
{
    while (*a && *b) { if (*a != *b) return 0; a++; b++; }
    return *a == *b;
}

int mode_parse(const char *text, device_mode_t *out)
{
    if (str_eq(text, "FIXED"))  { *out = MODE_FIXED;  return 1; }
    if (str_eq(text, "RANDOM")) { *out = MODE_RANDOM; return 1; }
    if (str_eq(text, "WALK"))   { *out = MODE_WALK;   return 1; }
    if (str_eq(text, "UPLOAD")) { *out = MODE_UPLOAD; return 1; }
    return 0;
}
