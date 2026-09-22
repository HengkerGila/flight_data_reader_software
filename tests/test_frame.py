import pytest

from arinc717_reader.domain.frame import Arinc717Frame, FrameError
from arinc717_reader.sources.frame_io import FrameIoError, load_frame, save_frame
from arinc717_reader.sources.random_source import RandomFrameSource


def test_blank_frame_shape():
    frame = Arinc717Frame.blank(256)
    assert len(frame.subframes) == 4
    assert all(len(sf) == 256 for sf in frame.subframes)
    assert all(v == 0 for sf in frame.subframes for v in sf)


def test_set_and_get_word():
    frame = Arinc717Frame.blank(256)
    frame.set_word(1, 4, 3412)
    assert frame.word(1, 4) == 3412
    assert frame.word(2, 4) == 0  # only that canonical cell changed


def test_word_value_range_enforced():
    frame = Arinc717Frame.blank(256)
    with pytest.raises(FrameError):
        frame.set_word(1, 1, 4096)
    with pytest.raises(FrameError):
        frame.set_word(1, 1, -1)


def test_address_range_enforced():
    frame = Arinc717Frame.blank(256)
    with pytest.raises(FrameError):
        frame.word(5, 1)
    with pytest.raises(FrameError):
        frame.word(0, 1)
    with pytest.raises(FrameError):
        frame.word(1, 257)
    with pytest.raises(FrameError):
        frame.word(1, 0)


def test_malformed_construction_rejected():
    with pytest.raises(FrameError):
        Arinc717Frame(wps=4, subframes=[[0, 0, 0, 0]] * 3)
    with pytest.raises(FrameError):
        Arinc717Frame(wps=4, subframes=[[0, 0, 0]] * 4)
    with pytest.raises(FrameError):
        Arinc717Frame(wps=4, subframes=[[0, 0, 0, 5000]] * 4)


def test_copy_is_independent():
    frame = Arinc717Frame.blank(8)
    clone = frame.copy()
    clone.set_word(1, 1, 99)
    assert frame.word(1, 1) == 0


def test_random_source_produces_valid_frames():
    source = RandomFrameSource(64, seed=42)
    first = source.next_frame()
    second = source.next_frame()
    assert first.wps == 64
    assert first.frame_index == 0
    assert second.frame_index == 1
    assert all(0 <= v <= 4095 for sf in first.subframes for v in sf)


def test_frame_json_roundtrip(tmp_path):
    frame = Arinc717Frame.blank(16, frame_index=7)
    frame.set_word(3, 9, 1234)
    path = tmp_path / "frame.json"
    save_frame(path, frame)
    loaded = load_frame(path)
    assert loaded.wps == 16
    assert loaded.frame_index == 7
    assert loaded.word(3, 9) == 1234
    assert loaded.subframes == frame.subframes


def test_frame_load_rejects_garbage(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(FrameIoError):
        load_frame(path)
    path.write_text('{"format": "other"}')
    with pytest.raises(FrameIoError):
        load_frame(path)
