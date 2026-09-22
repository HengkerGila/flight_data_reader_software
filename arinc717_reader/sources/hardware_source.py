"""Future ABUS 717 / FTDI hardware source (design spec §56).

Deliberately unimplemented: hardware acquisition is deferred until the
transport protocol / SDK is available.  When implemented it must plug into
the existing ``FrameSource`` interface and only produce ``Arinc717Frame``.
"""

from __future__ import annotations


class Abus717HardwareSource:
    name = "ABUS717"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "ABUS 717 / FTDI hardware acquisition is deferred (design spec §2, §56). "
            "Planned chain: FTDI transport → transport decoder → 12-bit word stream "
            "→ sync detector → subframe builder → frame builder → Arinc717Frame."
        )
