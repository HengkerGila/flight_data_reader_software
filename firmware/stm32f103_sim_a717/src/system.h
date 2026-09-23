#ifndef SYSTEM_H
#define SYSTEM_H
#include <stdint.h>
void system_init(void);          /* 72 MHz from 8 MHz HSE, SysTick 1 ms, LED pin */
uint32_t millis(void);
void led_set(int on);
#endif
