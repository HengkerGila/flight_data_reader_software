"""Save/load simulated frames as JSON (design spec §23, MVP item 12)."""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.frame import Arinc717Frame, FrameError

FORMAT_NAME = "arinc717-frame"
FORMAT_VERSION = 1


class FrameIoError(ValueError):
    """Raised when a frame file cannot be read or is malformed."""


def save_frame(path: str | Path, frame: Arinc717Frame) -> None:
    payload = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "wps": frame.wps,
        "frame_index": frame.frame_index,
        "subframes": frame.subframes,
    }
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_frame(path: str | Path) -> Arinc717Frame:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrameIoError(f"cannot read frame file {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT_NAME:
        raise FrameIoError(f"{path} is not a {FORMAT_NAME} file")
    try:
        return Arinc717Frame(
            wps=int(payload["wps"]),
            frame_index=int(payload.get("frame_index", 0)),
            subframes=[[int(v) for v in sf] for sf in payload["subframes"]],
        )
    except (KeyError, TypeError, ValueError, FrameError) as exc:
        raise FrameIoError(f"malformed frame file {path}: {exc}") from exc
