# SIM-A717 v1 stream emulator — STM32F103C8 (Blue Pill)
# Requires arm-none-eabi-gcc.  `make` builds sim_a717.elf/.bin/.hex; `make flash`
# programs over ST-Link (openocd) and `make flash-serial` via the ROM bootloader.

TARGET  = sim_a717
BUILD   = build
SRC     = $(wildcard src/*.c)
OBJ     = $(patsubst src/%.c,$(BUILD)/%.o,$(SRC))

CC      = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
SIZE    = arm-none-eabi-size

CFLAGS  = -mcpu=cortex-m3 -mthumb -Os -g -Wall -Wextra -std=c11 -ffunction-sections -fdata-sections \
          -ffreestanding -fno-builtin-printf -fno-tree-loop-distribute-patterns -Isrc
LDFLAGS = -mcpu=cortex-m3 -mthumb -nostartfiles -nostdlib -Wl,--gc-sections -Wl,-T,linker.ld -lgcc

all: $(BUILD)/$(TARGET).bin $(BUILD)/$(TARGET).hex
	$(SIZE) $(BUILD)/$(TARGET).elf

$(BUILD)/%.o: src/%.c | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/$(TARGET).elf: $(OBJ) linker.ld
	$(CC) $(OBJ) $(LDFLAGS) -o $@

$(BUILD)/%.bin: $(BUILD)/%.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD)/%.hex: $(BUILD)/%.elf
	$(OBJCOPY) -O ihex $< $@

$(BUILD):
	mkdir -p $(BUILD)

flash: $(BUILD)/$(TARGET).elf
	openocd -f interface/stlink.cfg -f target/stm32f1x.cfg -c "program $< verify reset exit"

flash-serial: $(BUILD)/$(TARGET).bin
	stm32flash -w $< -v -g 0x0 $(PORT)

clean:
	rm -rf $(BUILD)

.PHONY: all flash flash-serial clean
