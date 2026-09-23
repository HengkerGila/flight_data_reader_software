/* Minimal STM32F103 register map for the SIM-A717 stream emulator.
 * Only what this firmware touches; bit names follow the reference manual (RM0008). */
#ifndef STM32F103_H
#define STM32F103_H

#include <stdint.h>

#define __IO volatile

typedef struct {
    __IO uint32_t CR, CFGR, CIR, APB2RSTR, APB1RSTR, AHBENR, APB2ENR, APB1ENR, BDCR, CSR;
} RCC_TypeDef;

typedef struct {
    __IO uint32_t ACR, KEYR, OPTKEYR, SR, CR, AR, RESERVED, OBR, WRPR;
} FLASH_TypeDef;

typedef struct {
    __IO uint32_t CRL, CRH, IDR, ODR, BSRR, BRR, LCKR;
} GPIO_TypeDef;

typedef struct {
    __IO uint32_t SR, DR, BRR, CR1, CR2, CR3, GTPR;
} USART_TypeDef;

typedef struct {
    __IO uint32_t CR1, CR2, SMCR, DIER, SR, EGR, CCMR1, CCMR2, CCER, CNT, PSC, ARR;
} TIM_TypeDef;

typedef struct {
    __IO uint32_t CTRL, LOAD, VAL, CALIB;
} SysTick_TypeDef;

typedef struct {
    __IO uint32_t ISER[8];
} NVIC_TypeDef;

#define RCC     ((RCC_TypeDef *)0x40021000UL)
#define FLASH   ((FLASH_TypeDef *)0x40022000UL)
#define GPIOA   ((GPIO_TypeDef *)0x40010800UL)
#define GPIOC   ((GPIO_TypeDef *)0x40011000UL)
#define USART1  ((USART_TypeDef *)0x40013800UL)
#define TIM2    ((TIM_TypeDef *)0x40000000UL)
#define SysTick ((SysTick_TypeDef *)0xE000E010UL)
#define NVIC    ((NVIC_TypeDef *)0xE000E100UL)

/* RCC */
#define RCC_CR_HSEON        (1UL << 16)
#define RCC_CR_HSERDY       (1UL << 17)
#define RCC_CR_PLLON        (1UL << 24)
#define RCC_CR_PLLRDY       (1UL << 25)
#define RCC_CFGR_SW_PLL     (2UL << 0)
#define RCC_CFGR_SWS_MASK   (3UL << 2)
#define RCC_CFGR_SWS_PLL    (2UL << 2)
#define RCC_CFGR_PPRE1_DIV2 (4UL << 8)
#define RCC_CFGR_PLLSRC_HSE (1UL << 16)
#define RCC_CFGR_PLLMUL9    (7UL << 18)
#define RCC_APB2ENR_AFIOEN  (1UL << 0)
#define RCC_APB2ENR_IOPAEN  (1UL << 2)
#define RCC_APB2ENR_IOPCEN  (1UL << 4)
#define RCC_APB2ENR_USART1EN (1UL << 14)
#define RCC_APB1ENR_TIM2EN  (1UL << 0)

/* FLASH */
#define FLASH_ACR_LATENCY_2 (2UL << 0)
#define FLASH_ACR_PRFTBE    (1UL << 4)

/* USART */
#define USART_SR_RXNE   (1UL << 5)
#define USART_SR_TC     (1UL << 6)
#define USART_SR_TXE    (1UL << 7)
#define USART_CR1_RE    (1UL << 2)
#define USART_CR1_TE    (1UL << 3)
#define USART_CR1_RXNEIE (1UL << 5)
#define USART_CR1_TXEIE (1UL << 7)
#define USART_CR1_UE    (1UL << 13)

/* TIM */
#define TIM_CR1_CEN     (1UL << 0)
#define TIM_DIER_UIE    (1UL << 0)
#define TIM_SR_UIF      (1UL << 0)
#define TIM_EGR_UG      (1UL << 0)

/* SysTick */
#define SYSTICK_CTRL_ENABLE    (1UL << 0)
#define SYSTICK_CTRL_TICKINT   (1UL << 1)
#define SYSTICK_CTRL_CLKSOURCE (1UL << 2)

/* NVIC interrupt numbers */
#define IRQ_TIM2    28
#define IRQ_USART1  37

static inline void nvic_enable(uint32_t irq) { NVIC->ISER[irq >> 5] = 1UL << (irq & 31); }
static inline void irq_disable(void) { __asm volatile("cpsid i" ::: "memory"); }
static inline void irq_enable(void) { __asm volatile("cpsie i" ::: "memory"); }

#endif
