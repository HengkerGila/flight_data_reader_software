/* Device-side word sources (spec v2 §26H "Raw Random" / standalone mode).
 * Engineering-coherent signals are produced on the PC and uploaded. */
#ifndef SIGNAL_GENERATOR_H
#define SIGNAL_GENERATOR_H
#include <stdint.h>

typedef enum { MODE_FIXED = 0, MODE_RANDOM, MODE_WALK, MODE_UPLOAD } device_mode_t;

extern volatile device_mode_t g_mode;

uint32_t prng_next(void);                              /* xorshift32 */
uint16_t signal_word(uint8_t subframe, uint16_t index); /* value for (subframe, word index) */
const char *mode_name(device_mode_t mode);
int mode_parse(const char *text, device_mode_t *out);
#endif
