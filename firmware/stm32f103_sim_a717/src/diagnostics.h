#ifndef DIAGNOSTICS_H
#define DIAGNOSTICS_H
#include <stdint.h>
typedef struct {
    uint32_t words_sent, subframes_sent, frames_sent, tx_dropped_bytes, commands, bad_commands;
} diagnostics_t;
extern volatile diagnostics_t g_diag;
#endif
