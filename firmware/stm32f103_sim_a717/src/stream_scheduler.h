/* TIM2-driven word emitter: one word every 1/WPS s (spec v2 §26B, §26C). */
#ifndef STREAM_SCHEDULER_H
#define STREAM_SCHEDULER_H
#include <stdint.h>
#include "sim_a717_protocol.h"

typedef enum {
    FAULT_DROP_WORD = 0, FAULT_CORRUPT_WORD, FAULT_DROP_SUBFRAME, FAULT_BAD_CRC,
    FAULT_DELAY_SUBFRAME, FAULT_OUT_OF_ORDER_SUBFRAME, FAULT_WRONG_WPS, FAULT_PAUSE_STREAM,
    FAULT_COUNT
} fault_t;

#define FAULT_CONTINUOUS 65535

extern volatile uint8_t g_running;
extern volatile protocol_mode_t g_protocol;
extern volatile uint16_t g_faults[FAULT_COUNT];

void scheduler_init(void);
void scheduler_start(void);
void scheduler_stop(void);
void scheduler_reset_position(void);
void scheduler_poll(void);              /* main-loop part: pause / delay timing */
const char *fault_name(fault_t fault);
int fault_parse(const char *text, fault_t *out);
uint32_t scheduler_frame_id(void);
uint8_t scheduler_subframe(void);
uint16_t scheduler_word_index(void);
#endif
