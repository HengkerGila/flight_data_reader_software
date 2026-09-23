#include "command_receiver.h"
#include <string.h>
#include "config.h"
#include "uart.h"
#include "frame_model.h"
#include "signal_generator.h"
#include "stream_scheduler.h"
#include "diagnostics.h"

static char line[LINE_MAX + 1];
static uint16_t line_len;
static uint8_t line_overflow;

static void reply(const char *prefix, const char *text)
{
    if (g_running) return;           /* never interleave text with the word stream */
    uart_write_str(prefix);
    uart_write_str(" ");
    uart_write_str(text);
    uart_write_str("\n");
}

static void reply_ok(const char *text) { reply("+OK", text); }
static void reply_err(const char *text) { g_diag.bad_commands++; reply("+ERR", text); }

static char *skip_spaces(char *p) { while (*p == ' ' || *p == '\t') p++; return p; }

static char *next_token(char **cursor)
{
    char *p = skip_spaces(*cursor);
    if (!*p) return 0;
    char *start = p;
    while (*p && *p != ' ' && *p != '\t') p++;
    if (*p) { *p = 0; p++; }
    *cursor = p;
    return start;
}

static void upper(char *p) { for (; *p; p++) if (*p >= 'a' && *p <= 'z') *p = (char)(*p - 'a' + 'A'); }

static int parse_uint(const char *text, uint32_t *out)
{
    if (!text || !*text) return 0;
    uint32_t value = 0;
    for (; *text; text++) {
        if (*text < '0' || *text > '9') return 0;
        value = value * 10 + (uint32_t)(*text - '0');
    }
    *out = value;
    return 1;
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static void utoa10(uint32_t value, char *out)
{
    char tmp[12]; int n = 0;
    do { tmp[n++] = (char)('0' + value % 10); value /= 10; } while (value);
    while (n) *out++ = tmp[--n];
    *out = 0;
}

static void append(char *dst, const char *src) { while (*dst) dst++; while (*src) *dst++ = *src++; *dst = 0; }

static void info_reply(void)
{
    char text[96] = "SIM-A717 v1 wps=";
    char num[12];
    utoa10(g_wps, num); append(text, num);
    append(text, " mode="); append(text, mode_name(g_mode));
    append(text, " proto="); append(text, protocol_name(g_protocol));
    append(text, " running="); append(text, g_running ? "1" : "0");
    append(text, " fw=" FW_VERSION);
    reply_ok(text);
}

static void execute(char *text)
{
    char *cursor = text;
    char *cmd = next_token(&cursor);
    if (!cmd) return;
    upper(cmd);
    g_diag.commands++;

    if (!strcmp(cmd, "PING")) { reply_ok("PONG"); return; }
    if (!strcmp(cmd, "INFO")) { info_reply(); return; }
    if (!strcmp(cmd, "START")) { scheduler_start(); return; }   /* no reply: stream begins */
    if (!strcmp(cmd, "STOP")) { scheduler_stop(); reply_ok("STOPPED"); return; }
    if (!strcmp(cmd, "RESET")) {
        scheduler_stop();
        scheduler_reset_position();
        frame_model_init(g_wps);
        g_mode = MODE_FIXED;
        g_protocol = PROTO_STREAM;
        for (int i = 0; i < FAULT_COUNT; i++) g_faults[i] = 0;
        reply_ok("RESET");
        return;
    }
    if (!strcmp(cmd, "SET_WPS")) {
        uint32_t wps;
        if (!parse_uint(next_token(&cursor), &wps) || wps == 0 || wps > MAX_WPS) { reply_err("WPS must be 1..512"); return; }
        if (g_running) { reply_err("stop the stream before changing WPS"); return; }
        frame_model_set_wps((uint16_t)wps);
        scheduler_reset_position();
        char t[20] = "WPS "; char n[12]; utoa10(wps, n); append(t, n);
        reply_ok(t);
        return;
    }
    if (!strcmp(cmd, "SET_MODE")) {
        char *arg = next_token(&cursor);
        device_mode_t mode;
        if (!arg) { reply_err("SET_MODE needs a mode"); return; }
        upper(arg);
        if (!mode_parse(arg, &mode)) { reply_err("unknown mode"); return; }
        g_mode = mode;
        char t[20] = "MODE "; append(t, mode_name(mode));
        reply_ok(t);
        return;
    }
    if (!strcmp(cmd, "SET_PROTOCOL")) {
        char *arg = next_token(&cursor);
        protocol_mode_t proto;
        if (!arg) { reply_err("SET_PROTOCOL needs STREAM or FRAMED"); return; }
        upper(arg);
        if (!protocol_parse(arg, &proto)) { reply_err("unknown protocol mode"); return; }
        if (g_running) { reply_err("stop the stream before changing protocol"); return; }
        g_protocol = proto;
        char t[24] = "PROTOCOL "; append(t, protocol_name(proto));
        reply_ok(t);
        return;
    }
    if (!strcmp(cmd, "SET_SYNC")) {
        uint16_t sync[SUBFRAME_COUNT];
        for (int i = 0; i < SUBFRAME_COUNT; i++) {
            uint32_t v;
            if (!parse_uint(next_token(&cursor), &v) || v > WORD_MAX) { reply_err("SET_SYNC needs four 12-bit words"); return; }
            sync[i] = (uint16_t)v;
        }
        frame_model_set_sync(sync);
        char t[40] = "SYNC";
        for (int i = 0; i < SUBFRAME_COUNT; i++) { char n[12]; utoa10(sync[i], n); append(t, " "); append(t, n); }
        reply_ok(t);
        return;
    }
    if (!strcmp(cmd, "SET_WORD")) {
        uint32_t sf, w, v;
        if (!parse_uint(next_token(&cursor), &sf) || !parse_uint(next_token(&cursor), &w) ||
            !parse_uint(next_token(&cursor), &v) || sf < 1 || sf > SUBFRAME_COUNT || w < 1 || w > g_wps || v > WORD_MAX) {
            reply_err("SET_WORD <sf 1..4> <word 1..WPS> <value 0..4095>");
            return;
        }
        frame_active()->words[sf - 1][w - 1] = (uint16_t)v;
        frame_staged()->words[sf - 1][w - 1] = (uint16_t)v;
        reply_ok("WORD");
        return;
    }
    if (!strcmp(cmd, "LOAD_SF")) {
        uint32_t sf;
        if (!parse_uint(next_token(&cursor), &sf) || sf < 1 || sf > SUBFRAME_COUNT) { reply_err("subframe outside 1..4"); return; }
        char *hex = next_token(&cursor);
        if (!hex) { reply_err("LOAD_SF needs hex words"); return; }
        uint16_t count = 0;
        frame_buffer_t *staged = frame_staged();
        for (const char *p = hex; p[0] && p[1] && p[2]; p += 3) {
            int a = hex_nibble(p[0]), b = hex_nibble(p[1]), c = hex_nibble(p[2]);
            if (a < 0 || b < 0 || c < 0) { reply_err("bad hex word"); return; }
            if (count < g_wps) staged->words[sf - 1][count] = (uint16_t)((a << 8) | (b << 4) | c);
            count++;
        }
        if (count != g_wps) { reply_err("word count does not match WPS"); return; }
        char t[20] = "LOADED "; char n[12]; utoa10(sf, n); append(t, n);
        reply_ok(t);
        return;
    }
    if (!strcmp(cmd, "COMMIT")) {
        if (g_running) g_commit_pending = 1; else frame_swap();
        reply_ok("COMMIT");
        return;
    }
    if (!strcmp(cmd, "FAULT")) {
        char *name = next_token(&cursor);
        fault_t fault;
        uint32_t count = 1;
        if (!name) { reply_err("FAULT <name> [count]"); return; }
        upper(name);
        if (!strcmp(name, "DISCONNECT_RECONNECT")) { reply_err("DISCONNECT_RECONNECT is only available on the virtual device"); return; }
        if (!fault_parse(name, &fault)) { reply_err("unknown fault"); return; }
        char *arg = next_token(&cursor);
        if (arg && (!parse_uint(arg, &count) || count > FAULT_CONTINUOUS)) { reply_err("count outside 0..65535"); return; }
        g_faults[fault] = (uint16_t)count;
        char t[40] = "FAULT "; append(t, fault_name(fault)); char n[12]; utoa10(count, n); append(t, " "); append(t, n);
        reply_ok(t);
        return;
    }
    reply_err("unknown command");
}

void command_receiver_init(void) { line_len = 0; line_overflow = 0; }

void command_receiver_poll(void)
{
    int byte;
    while ((byte = uart_read_byte()) >= 0) {
        if (byte == '\n' || byte == '\r') {
            if (line_overflow) { line_overflow = 0; line_len = 0; reply_err("line too long"); continue; }
            if (line_len) { line[line_len] = 0; execute(line); }
            line_len = 0;
        } else if (line_len < LINE_MAX) {
            line[line_len++] = (char)byte;
        } else {
            line_overflow = 1;
        }
    }
}
