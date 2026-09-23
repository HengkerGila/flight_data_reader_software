/* Cortex-M3 startup: vector table, data/bss initialisation, default handlers. */
#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;
extern int main(void);

void Reset_Handler(void);
void Default_Handler(void);
void SysTick_Handler(void) __attribute__((weak, alias("Default_Handler")));
void TIM2_IRQHandler(void) __attribute__((weak, alias("Default_Handler")));
void USART1_IRQHandler(void) __attribute__((weak, alias("Default_Handler")));

typedef void (*vector_t)(void);

__attribute__((section(".isr_vector"), used))
const vector_t vector_table[16 + 64] = {
    (vector_t)&_estack,
    Reset_Handler,
    Default_Handler,   /* NMI */
    Default_Handler,   /* HardFault */
    Default_Handler,   /* MemManage */
    Default_Handler,   /* BusFault */
    Default_Handler,   /* UsageFault */
    0, 0, 0, 0,
    Default_Handler,   /* SVCall */
    Default_Handler,   /* DebugMonitor */
    0,
    Default_Handler,   /* PendSV */
    SysTick_Handler,   /* SysTick */
    /* External interrupts (positions 16 + IRQn) */
    [16 + 28] = TIM2_IRQHandler,
    [16 + 37] = USART1_IRQHandler,
};

void Reset_Handler(void)
{
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    for (dst = &_sbss; dst < &_ebss; ) *dst++ = 0;
    main();
    for (;;) {}
}

void Default_Handler(void)
{
    for (;;) {}
}
