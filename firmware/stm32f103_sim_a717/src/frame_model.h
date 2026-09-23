/* Frame buffers: an active frame streamed by the scheduler and a staged
 * frame filled by LOAD_SF, swapped at the next frame boundary on COMMIT. */
#ifndef FRAME_MODEL_H
#define FRAME_MODEL_H
#include <stdint.h>
#include "config.h"

typedef struct {
    uint16_t words[SUBFRAME_COUNT][MAX_WPS];
} frame_buffer_t;

extern uint16_t g_wps;
extern uint16_t g_sync_words[SUBFRAME_COUNT];
extern volatile uint8_t g_commit_pending;

void frame_model_init(uint16_t wps);
void frame_model_set_wps(uint16_t wps);
void frame_model_set_sync(const uint16_t sync[SUBFRAME_COUNT]);
frame_buffer_t *frame_active(void);
frame_buffer_t *frame_staged(void);
void frame_swap(void);                       /* called by the scheduler at a frame boundary */
void frame_blank(frame_buffer_t *frame);     /* zeros + sync words */
#endif
