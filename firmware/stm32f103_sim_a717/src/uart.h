#ifndef UART_H
#define UART_H
#include <stdint.h>
#include <stddef.h>
void uart_init(uint32_t baud);
int  uart_write(const uint8_t *data, size_t len);   /* queues into the TX ring; returns bytes accepted */
void uart_write_str(const char *text);
int  uart_read_byte(void);                           /* -1 when the RX ring is empty */
size_t uart_tx_free(void);
uint32_t uart_rx_overruns(void);
#endif
