/* USART1 (PA9 TX / PA10 RX), interrupt-driven ring buffers.
 * 3.3 V TTL levels: connect to the FTDI module's RXD/TXD/GND, never to RS-232. */
#include "uart.h"
#include "stm32f103.h"
#include "config.h"

static volatile uint8_t tx_ring[TX_RING_SIZE];
static volatile uint16_t tx_head, tx_tail;
static volatile uint8_t rx_ring[RX_RING_SIZE];
static volatile uint16_t rx_head, rx_tail;
static volatile uint32_t rx_overruns;

void uart_init(uint32_t baud)
{
    /* PA9: alternate-function push-pull 50 MHz; PA10: floating input. */
    GPIOA->CRH = (GPIOA->CRH & ~(0xFFUL << 4)) | (0xBUL << 4) | (0x4UL << 8);
    /* USARTDIV = 72e6 / (16 * baud); BRR holds mantissa<<4 | fraction. */
    uint32_t div16 = (SYSCLK_HZ + baud / 2) / baud;   /* = 16 * USARTDIV */
    USART1->BRR = div16;
    USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;
    nvic_enable(IRQ_USART1);
}

size_t uart_tx_free(void)
{
    uint16_t head = tx_head, tail = tx_tail;
    return (size_t)((tail - head - 1 + TX_RING_SIZE) % TX_RING_SIZE);
}

int uart_write(const uint8_t *data, size_t len)
{
    size_t n = 0;
    while (n < len) {
        uint16_t next = (uint16_t)((tx_head + 1) % TX_RING_SIZE);
        if (next == tx_tail) break;              /* ring full: drop the rest (diagnostics count it) */
        tx_ring[tx_head] = data[n++];
        tx_head = next;
    }
    USART1->CR1 |= USART_CR1_TXEIE;
    return (int)n;
}

void uart_write_str(const char *text)
{
    size_t len = 0;
    while (text[len]) len++;
    uart_write((const uint8_t *)text, len);
}

int uart_read_byte(void)
{
    if (rx_tail == rx_head) return -1;
    uint8_t byte = rx_ring[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1) % RX_RING_SIZE);
    return byte;
}

uint32_t uart_rx_overruns(void) { return rx_overruns; }

void USART1_IRQHandler(void)
{
    uint32_t sr = USART1->SR;
    if (sr & USART_SR_RXNE) {
        uint8_t byte = (uint8_t)USART1->DR;
        uint16_t next = (uint16_t)((rx_head + 1) % RX_RING_SIZE);
        if (next != rx_tail) { rx_ring[rx_head] = byte; rx_head = next; }
        else rx_overruns++;
    }
    if ((sr & USART_SR_TXE) && (USART1->CR1 & USART_CR1_TXEIE)) {
        if (tx_tail != tx_head) {
            USART1->DR = tx_ring[tx_tail];
            tx_tail = (uint16_t)((tx_tail + 1) % TX_RING_SIZE);
        } else {
            USART1->CR1 &= ~USART_CR1_TXEIE;
        }
    }
}
