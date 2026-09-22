"""ARINC 717 dataframe-driven decoding, simulation and inspection platform.

This package intentionally keeps three domains separate (design spec §4):

- canonical dataframe — how bits are interpreted (`domain.dataframe`, `domain.parameter`)
- canonical frame — raw 12-bit words only (`domain.frame`)
- engineering data — decoded output (`domain.engineering`)

The top-level package import must stay GUI-free; PySide6 is only imported
inside :mod:`arinc717_reader.ui` and :mod:`arinc717_reader.app`.
"""

__version__ = "0.1.0"
