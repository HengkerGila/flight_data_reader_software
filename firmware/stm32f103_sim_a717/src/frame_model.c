#include "frame_model.h"

static frame_buffer_t buffers[2];
static volatile uint8_t active_index;
uint16_t g_wps = DEFAULT_WPS;
uint16_t g_sync_words[SUBFRAME_COUNT] = {DEFAULT_SYNC_1, DEFAULT_SYNC_2, DEFAULT_SYNC_3, DEFAULT_SYNC_4};
volatile uint8_t g_commit_pending;

void frame_blank(frame_buffer_t *frame)
{
    for (int sf = 0; sf < SUBFRAME_COUNT; sf++) {
        for (int w = 0; w < MAX_WPS; w++) frame->words[sf][w] = 0;
        frame->words[sf][0] = g_sync_words[sf];
    }
}

void frame_model_init(uint16_t wps)
{
    g_wps = wps;
    active_index = 0;
    g_commit_pending = 0;
    frame_blank(&buffers[0]);
    frame_blank(&buffers[1]);
}

void frame_model_set_wps(uint16_t wps) { frame_model_init(wps); }

void frame_model_set_sync(const uint16_t sync[SUBFRAME_COUNT])
{
    for (int sf = 0; sf < SUBFRAME_COUNT; sf++) {
        g_sync_words[sf] = sync[sf];
        buffers[0].words[sf][0] = sync[sf];
        buffers[1].words[sf][0] = sync[sf];
    }
}

frame_buffer_t *frame_active(void) { return &buffers[active_index]; }
frame_buffer_t *frame_staged(void) { return &buffers[active_index ^ 1]; }

void frame_swap(void)
{
    active_index ^= 1;
    /* Keep the new staged buffer equal to the new active one so partial
     * LOAD_SF uploads (e.g. only SF2) still stream a coherent frame. */
    frame_buffer_t *a = &buffers[active_index], *s = &buffers[active_index ^ 1];
    for (int sf = 0; sf < SUBFRAME_COUNT; sf++)
        for (int w = 0; w < g_wps; w++) s->words[sf][w] = a->words[sf][w];
}
