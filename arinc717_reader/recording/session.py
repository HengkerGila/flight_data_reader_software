"""Session file format: JSON Lines (spec v2 §26R).

Line 1 is the header; every following line is a record with a ``kind``:

* ``subframe`` — ``t`` (wall clock), ``frame``, ``sf``, ``state``,
  ``words`` (hex string, 3 chars per word) — the raw stream data;
* ``sample`` — one ``ParameterSample``;
* ``event`` — a stream event (sync loss, disconnect, …);
* ``end`` — closing record with totals.

JSON Lines lets the recorder append without holding the session in memory
and lets a truncated file (application killed mid-recording) still replay
up to the last complete line.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from ..domain.frame import WORD_MAX
from ..streaming.parameter_sample import ParameterSample

FORMAT_NAME = "arinc717-session"
FORMAT_VERSION = 1
SESSION_SUFFIX = ".a717session"


class SessionError(ValueError):
    """Raised when a session file cannot be read."""


@dataclass
class SessionHeader:
    format: str = FORMAT_NAME
    version: int = FORMAT_VERSION
    created: float = 0.0
    dataframe_name: str = ""
    dataframe_source: str | None = None
    dataframe_hash: str | None = None
    wps: int = 256
    sync_words: list[int] = field(default_factory=list)
    source_type: str = ""
    port: str = ""
    baudrate: int = 0
    protocol_mode: str = ""
    simulator: dict = field(default_factory=dict)
    notes: str = ""


@dataclass
class SubframeRecord:
    t: float
    frame: int
    sf: int
    state: str
    words: list[int]


@dataclass
class EventRecord:
    t: float
    kind: str
    message: str


def encode_words(words) -> str:
    return "".join(f"{w & WORD_MAX:03X}" for w in words)


def decode_words(text: str) -> list[int]:
    if len(text) % 3:
        raise SessionError("word string length is not a multiple of 3")
    return [int(text[i : i + 3], 16) for i in range(0, len(text), 3)]


class SessionWriter:
    def __init__(self, path: str | Path, header: SessionHeader):
        self.path = Path(path)
        self._file = self.path.open("w", encoding="utf-8")
        self._file.write(json.dumps(asdict(header)) + "\n")
        self.subframes = 0
        self.samples = 0
        self.events = 0
        self.first_t: float | None = None
        self.last_t: float | None = None

    def _touch(self, t: float) -> None:
        if self.first_t is None:
            self.first_t = t
        self.last_t = t

    def write_subframe(self, record: SubframeRecord) -> None:
        self._touch(record.t)
        self.subframes += 1
        self._file.write(
            json.dumps(
                {
                    "kind": "subframe",
                    "t": record.t,
                    "frame": record.frame,
                    "sf": record.sf,
                    "state": record.state,
                    "words": encode_words(record.words),
                }
            )
            + "\n"
        )

    def write_samples(self, samples: list[ParameterSample]) -> None:
        for sample in samples:
            self._touch(sample.timestamp)
            self.samples += 1
            self._file.write(
                json.dumps(
                    {
                        "kind": "sample",
                        "t": sample.timestamp,
                        "id": sample.parameter_id,
                        "name": sample.parameter_name,
                        "frame": sample.frame_index,
                        "sf": sample.subframe,
                        "occ": sample.occurrence_index,
                        "raw": sample.raw_value,
                        "dec": sample.decoded_decimal,
                        "eng": sample.engineering_value,
                        "unit": sample.unit,
                        "status": sample.status,
                    }
                )
                + "\n"
            )

    def write_event(self, record: EventRecord) -> None:
        self._touch(record.t)
        self.events += 1
        self._file.write(
            json.dumps({"kind": "event", "t": record.t, "event": record.kind, "message": record.message})
            + "\n"
        )

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        if self._file.closed:
            return
        self._file.write(
            json.dumps(
                {
                    "kind": "end",
                    "subframes": self.subframes,
                    "samples": self.samples,
                    "events": self.events,
                    "first_t": self.first_t,
                    "last_t": self.last_t,
                }
            )
            + "\n"
        )
        self._file.close()

    @property
    def duration(self) -> float:
        if self.first_t is None or self.last_t is None:
            return 0.0
        return self.last_t - self.first_t


class SessionReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                first = handle.readline()
        except OSError as exc:
            raise SessionError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(first)
        except json.JSONDecodeError as exc:
            raise SessionError(f"{self.path} is not a session file: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
            raise SessionError(f"{self.path} is not a {FORMAT_NAME} file")
        if data.get("version") != FORMAT_VERSION:
            raise SessionError(f"unsupported session version {data.get('version')!r}")
        known = {f for f in SessionHeader.__dataclass_fields__}
        self.header = SessionHeader(**{k: v for k, v in data.items() if k in known})

    def records(self) -> Iterator[dict]:
        with self.path.open("r", encoding="utf-8") as handle:
            handle.readline()  # header
            for line_no, line in enumerate(handle, start=2):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A truncated last line (killed mid-write) ends the session.
                    return

    def subframes(self) -> Iterator[SubframeRecord]:
        for record in self.records():
            if record.get("kind") == "subframe":
                yield SubframeRecord(
                    t=float(record["t"]),
                    frame=int(record["frame"]),
                    sf=int(record["sf"]),
                    state=str(record.get("state", "RECEIVED")),
                    words=decode_words(record["words"]),
                )

    def samples(self) -> Iterator[ParameterSample]:
        for record in self.records():
            if record.get("kind") == "sample":
                yield ParameterSample(
                    parameter_id=record["id"],
                    parameter_name=record.get("name", record["id"]),
                    timestamp=float(record["t"]),
                    frame_index=int(record.get("frame", 0)),
                    subframe=int(record.get("sf", 0)),
                    occurrence_index=int(record.get("occ", 1)),
                    raw_value=record.get("raw"),
                    decoded_decimal=record.get("dec"),
                    engineering_value=record.get("eng"),
                    unit=record.get("unit"),
                    status=record.get("status", "VALID"),
                )

    def summary(self) -> dict:
        """Totals from the ``end`` record, or counted when the file is truncated."""
        counts = {"subframes": 0, "samples": 0, "events": 0, "first_t": None, "last_t": None}
        for record in self.records():
            kind = record.get("kind")
            if kind == "end":
                return {**counts, **{k: record.get(k) for k in counts}}
            if kind in ("subframes", "samples", "events"):
                pass
            if kind == "subframe":
                counts["subframes"] += 1
            elif kind == "sample":
                counts["samples"] += 1
            elif kind == "event":
                counts["events"] += 1
            t = record.get("t")
            if t is not None:
                counts["first_t"] = t if counts["first_t"] is None else counts["first_t"]
                counts["last_t"] = t
        return counts
