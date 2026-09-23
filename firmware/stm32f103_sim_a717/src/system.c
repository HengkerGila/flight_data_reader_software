#include "system.h"
#include "stm32f103.h"
#include "config.h"

static volatile uint32_t tick_ms;

void SysTick_Handler(void) { tick_ms++; }

uint32_t millis(void) { return tick_ms; }

void led_set(int on)
{
    /* Blue Pill LED on PC13 is active low. */
    if (on) GPIOC->BRR = (1UL << 13); else GPIOC->BSRR = (1UL << 13);
}

void system_init(void)
{
    /* Flash: 2 wait states + prefetch for 72 MHz. */
    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_2;
    /* HSE 8 MHz -> PLL x9 = 72 MHz; APB1 = 36 MHz (TIM2 clock stays 72 MHz). */
    RCC->CR |= RCC_CR_HSEON;
    while (!(RCC->CR & RCC_CR_HSERDY)) {}
    RCC->CFGR = RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_PLLSRC_HSE | RCC_CFGR_PLLMUL9;
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY)) {}
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS_MASK) != RCC_CFGR_SWS_PLL) {}

    RCC->APB2ENR |= RCC_APB2ENR_AFIOEN | RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPCEN | RCC_APB2ENR_USART1EN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM2EN;

    /* PC13: output push-pull 2 MHz (LED). */
    GPIOC->CRH = (GPIOC->CRH & ~(0xFUL << 20)) | (0x2UL << 20);
    led_set(0);

    /* SysTick at 1 kHz. */
    SysTick->LOAD = SYSCLK_HZ / 1000UL - 1UL;
    SysTick->VAL = 0;
    SysTick->CTRL = SYSTICK_CTRL_CLKSOURCE | SYSTICK_CTRL_TICKINT | SYSTICK_CTRL_ENABLE;
}
