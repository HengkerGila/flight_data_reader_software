/* SIM-A717 v1 stream emulator for STM32F103 (Blue Pill) + FTDI USB-UART.
 *
 * Emits a continuous ARINC 717 word stream (12-bit words in 16-bit LE
 * containers) at WPS words per second from TIM2, driven by ASCII commands
 * on the same UART.  See firmware/stm32f103_sim_a717/README.md.
 */
#include "stm32f103.h"
#include "system.h"
#include "uart.h"
#include "config.h"
#include "frame_model.h"
#include "stream_scheduler.h"
#include "command_receiver.h"

int main(void)
{
    system_init();
    uart_init(UART_BAUD);
    frame_model_init(DEFAULT_WPS);
    scheduler_init();
    command_receiver_init();
    irq_enable();
    for (;;) {
        command_receiver_poll();
        scheduler_poll();
    }
}
