#include "stream_scheduler.h"
#include "stm32f103.h"
#include "config.h"
#include "frame_model.h"
#include "signal_generator.h"
#include "diagnostics.h"
#include "system.h"
#include "uart.h"

volatile uint8_t g_running;
volatile protocol_mode_t g_protocol = PROTO_STREAM;
volatile uint16_t g_faults[FAULT_COUNT];

static volatile uint16_t word_index;       /* 0-based within the subframe */
static volatile uint8_t subframe_pos;      /* 0..3 position within the frame */
static volatile uint32_t frame_id;
static volatile uint16_t sequence;
static uint8_t order[SUBFRAME_COUNT] = {0, 1, 2, 3};
static volatile uint8_t in_subframe;
static volatile uint8_t drop_subframe, bad_crc, wrong_wps;
static volatile uint32_t resume_at_ms;     /* 0 = not paused */
static uint16_t packet_words[MAX_WPS];
static uint8_t packet_buffer[SIM_PACKET_HEADER + 2 * MAX_WPS + SIM_PACKET_CRC];

/* Bresenham period generator: 1 MHz timer tick, period = 1e6 / wps us. */
static uint32_t period_base, period_rem, period_acc;

static uint16_t effective_wps(void)
{
    if (wrong_wps) return g_wps != 128 ? 128 : 64;
    return g_wps;
}

static void program_period(void)
{
    uint16_t wps = effective_wps();
    period_base = 1000000UL / wps;
    period_rem = 1000000UL % wps;
    period_acc = 0;
}

static void load_next_period(void)
{
    uint32_t ticks = period_base;
    period_acc += period_rem;
    if (period_acc >= effective_wps()) { period_acc -= effective_wps(); ticks++; }
    TIM2->ARR = ticks - 1;
}

static int take_fault(fault_t fault)
{
    uint16_t count = g_faults[fault];
    if (count == 0) return 0;
    if (count != FAULT_CONTINUOUS) g_faults[fault] = count - 1;
    return 1;
}

void scheduler_init(void)
{
    TIM2->PSC = (SYSCLK_HZ / 1000000UL) - 1;   /* 1 MHz tick */
    program_period();
    TIM2->ARR = period_base - 1;
    TIM2->EGR = TIM_EGR_UG;
    TIM2->SR = 0;
    TIM2->DIER = TIM_DIER_UIE;
    nvic_enable(IRQ_TIM2);
    scheduler_reset_position();
}

void scheduler_reset_position(void)
{
    word_index = 0; subframe_pos = 0; frame_id = 0; sequence = 0; in_subframe = 0;
    drop_subframe = bad_crc = wrong_wps = 0;
    resume_at_ms = 0;
    for (int i = 0; i < SUBFRAME_COUNT; i++) order[i] = (uint8_t)i;
    program_period();
}

void scheduler_start(void)
{
    program_period();
    TIM2->CNT = 0;
    TIM2->ARR = period_base - 1;
    g_running = 1;
    TIM2->CR1 |= TIM_CR1_CEN;
    led_set(1);
}

void scheduler_stop(void)
{
    g_running = 0;
    TIM2->CR1 &= ~TIM_CR1_CEN;
    resume_at_ms = 0;
    led_set(0);
}

uint32_t scheduler_frame_id(void) { return frame_id; }
uint8_t scheduler_subframe(void) { return (uint8_t)(order[subframe_pos] + 1); }
uint16_t scheduler_word_index(void) { return word_index; }

static void pause_for(uint32_t ms)
{
    resume_at_ms = millis() + ms;
    if (resume_at_ms == 0) resume_at_ms = 1;
    TIM2->CR1 &= ~TIM_CR1_CEN;
}

void scheduler_poll(void)
{
    if (resume_at_ms && g_running && (int32_t)(millis() - resume_at_ms) >= 0) {
        resume_at_ms = 0;
        TIM2->CNT = 0;
        TIM2->CR1 |= TIM_CR1_CEN;
    }
}

static void begin_subframe(void)
{
    in_subframe = 1;
    drop_subframe = (uint8_t)take_fault(FAULT_DROP_SUBFRAME);
    bad_crc = (uint8_t)take_fault(FAULT_BAD_CRC);
    wrong_wps = (uint8_t)take_fault(FAULT_WRONG_WPS);
    program_period();
    if (subframe_pos == 0 && take_fault(FAULT_OUT_OF_ORDER_SUBFRAME)) {
        order[0] = 0; order[1] = 2; order[2] = 1; order[3] = 3;
    }
    if (take_fault(FAULT_DELAY_SUBFRAME)) pause_for(DELAY_SUBFRAME_MS);
}

static void end_subframe(void)
{
    uint8_t slot = order[subframe_pos];
    if (g_protocol == PROTO_FRAMED && !drop_subframe) {
        size_t len = sim_encode_packet(packet_buffer, sizeof packet_buffer, sequence, frame_id,
                                       (uint8_t)(slot + 1), effective_wps(), packet_words,
                                       effective_wps(), bad_crc);
        if (uart_write(packet_buffer, len) != (int)len) g_diag.tx_dropped_bytes++;
    }
    sequence++;
    g_diag.subframes_sent++;
    word_index = 0;
    in_subframe = 0;
    subframe_pos++;
    if (subframe_pos >= SUBFRAME_COUNT) {
        subframe_pos = 0;
        frame_id++;
        g_diag.frames_sent++;
        for (int i = 0; i < SUBFRAME_COUNT; i++) order[i] = (uint8_t)i;
        if (g_commit_pending) { frame_swap(); g_commit_pending = 0; }
    }
}

void TIM2_IRQHandler(void)
{
    TIM2->SR = 0;
    load_next_period();
    if (!g_running) return;
    if (!in_subframe) {
        begin_subframe();
        if (resume_at_ms) return;
    }
    uint8_t slot = order[subframe_pos];
    uint16_t wps = effective_wps();
    uint16_t value = signal_word(slot, word_index);
    int drop = 0;
    if (take_fault(FAULT_CORRUPT_WORD)) value ^= (uint16_t)((prng_next() % WORD_MAX) + 1);
    if (take_fault(FAULT_DROP_WORD)) drop = 1;
    if (take_fault(FAULT_PAUSE_STREAM)) pause_for(PAUSE_STREAM_MS);
    if (!drop && !drop_subframe) {
        if (g_protocol == PROTO_STREAM) {
            if (uart_tx_free() < 2) g_diag.tx_dropped_bytes += 2;
            else sim_send_word((uint16_t)(value & WORD_MAX));
        } else if (word_index < MAX_WPS) {
            packet_words[word_index] = (uint16_t)(value & WORD_MAX);
        }
    }
    g_diag.words_sent++;
    word_index++;
    if (word_index >= wps) end_subframe();
}

static const char *const fault_names[FAULT_COUNT] = {
    "DROP_WORD", "CORRUPT_WORD", "DROP_SUBFRAME", "BAD_CRC",
    "DELAY_SUBFRAME", "OUT_OF_ORDER_SUBFRAME", "WRONG_WPS", "PAUSE_STREAM",
};

const char *fault_name(fault_t fault) { return fault_names[fault]; }

int fault_parse(const char *text, fault_t *out)
{
    for (int i = 0; i < FAULT_COUNT; i++) {
        const char *a = text, *b = fault_names[i];
        while (*a && *b && *a == *b) { a++; b++; }
        if (!*a && !*b) { *out = (fault_t)i; return 1; }
    }
    return 0;
}
