from __future__ import annotations

import pytest

from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.encoder.frame_builder import build_blank_frame


@pytest.fixture
def demo_dataframe():
    return build_demo_dataframe()


@pytest.fixture
def demo_frame(demo_dataframe):
    """Blank 256 WPS frame with the demo sync words inserted."""
    return build_blank_frame(
        demo_dataframe.metadata.wps,
        frame_index=0,
        sync_words=demo_dataframe.metadata.sync_words,
    )
