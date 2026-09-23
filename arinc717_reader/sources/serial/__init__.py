"""Serial hardware-in-the-loop acquisition (design spec v2 §26A–§26F).

Chain: transport bytes → SIM-A717 stream parser → word stream →
synchronizer → subframe assembler → canonical subframes / frames.  After
the assembler, nothing downstream knows whether the words came from an
STM32 over FTDI, the in-process virtual device, or a recorded session.
"""
