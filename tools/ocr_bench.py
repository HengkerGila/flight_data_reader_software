#!/usr/bin/env python
"""OCR benchmark for the scanned-page importer.

Measures the scanned-page pipeline (render → deskew → grid → OCR → cells)
against known ground truth, so recognizer models and settings are compared
by numbers instead of by eye.

* **synthetic** (default): a dataframe with many numeric cells is rendered
  as image-only scans (``synth.write_dataframe_pdf``) in the generic and
  the FDS layouts.  Every cell's true text is known, so the exact-match
  rate and the character error rate (CER) are reported per column, plus
  the OCR time per page.
* ``--real PDF --pages A-B``: runs the full import on a real document and
  reports the review outcome (rows, auto-approved, review required, OCR
  digit repairs, low-confidence flags) and the time per page.  Without
  ground truth this is an indirect measure: fewer repairs and flags with
  the same number of rows means cleaner recognition.

Recognizers are given with ``--rec`` (repeatable): ``ch`` (RapidOCR's own
Chinese-plus-Latin model), ``en`` (the bundled English model) or the path
of a recognizer ONNX file; ``--keys`` names the character list for a path
whose model does not embed one.

    .venv/bin/python tools/ocr_bench.py
    .venv/bin/python tools/ocr_bench.py --rec ch --rec en --dpi 150 --dpi 200
    .venv/bin/python tools/ocr_bench.py --rec /path/en_PP-OCRv4_rec_mobile.onnx --keys /path/en_dict.txt
    .venv/bin/python tools/ocr_bench.py --real examples/FDS81.pdf --pages 14-21 --rec ch --rec en
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arinc717_reader.dataframe.pdf_importer.extract import extract_tables, rows_from_tables  # noqa: E402
from arinc717_reader.dataframe.pdf_importer.ingest import ingest_pdf  # noqa: E402
from arinc717_reader.dataframe.pdf_importer.normalize import ImportProfile, cell_value  # noqa: E402
from arinc717_reader.dataframe.pdf_importer.pipeline import import_pdf  # noqa: E402
from arinc717_reader.dataframe.pdf_importer.synth import (  # noqa: E402
    STYLE_FDS,
    STYLE_GENERIC,
    dataframe_to_document_rows,
    write_dataframe_pdf,
)
from arinc717_reader.domain.dataframe import DataframeDefinition, DataframeMetadata  # noqa: E402
from arinc717_reader.domain.parameter import (  # noqa: E402
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)

WPS = 256

# Realistic vocabulary so the synthetic document looks like a vendor's.
_ANALOG = [
    ("PITCH ATT", "Pitch attitude", "deg", -90, 90),
    ("ROLL ATT", "Roll attitude", "deg", -180, 180),
    ("HDG MAG", "Magnetic heading", "deg", 0, 360),
    ("IAS", "Indicated airspeed", "kt", 0, 450),
    ("PRESS ALT", "Pressure altitude", "ft", -2000, 60000),
    ("RAD ALT", "Radio altitude", "ft", -20, 2500),
    ("N1 ENG 1", "Engine 1 fan speed", "%", 0, 120),
    ("N2 ENG 2", "Engine 2 core speed", "%", 0, 120),
    ("EGT ENG 1", "Engine 1 exhaust gas temperature", "degC", -50, 1200),
    ("FUEL FLOW 2", "Engine 2 fuel flow", "lb/h", 0, 9000),
    ("FLAP POS", "Flap surface position", "deg", -2, 45),
    ("AOA", "Angle of attack", "deg", -11, 41),
    ("AILERON POS", "Aileron position", "deg", -31.5, 31.5),
    ("ELEV POS", "Elevator position", "deg", -30, 30),
    ("RUDDER POS", "Rudder position", "deg", -35, 35),
    ("VERT ACCEL", "Vertical acceleration", "g", -3, 6),
    ("LONG ACCEL", "Longitudinal acceleration", "g", -1, 1),
    ("LAT ACCEL", "Lateral acceleration", "g", -1, 1),
    ("GS DEV", "Glideslope deviation", "dot", -2, 2),
    ("LOC DEV", "Localizer deviation", "dot", -2, 2),
    ("TAT", "Total air temperature", "degC", -60, 90),
    ("SAT", "Static air temperature", "degC", -80, 60),
    ("MACH", "Mach number", "mach", 0, 1),
    ("DME DIST 1", "DME 1 distance", "nm", 0, 399.9),
    ("HYD PRESS A", "Hydraulic pressure system A", "psi", 0, 4000),
    ("OIL PRESS 1", "Engine 1 oil pressure", "psi", 0, 150),
    ("TRIM PITCH", "Pitch trim position", "deg", -15, 15),
    ("SPOILER L", "Left spoiler position", "deg", 0, 60),
    ("CABIN ALT", "Cabin altitude", "ft", -1000, 15000),
    ("GROUND SPEED", "Ground speed", "kt", 0, 700),
    ("WIND SPEED", "Wind speed", "kt", 0, 250),
    ("DRIFT ANGLE", "Drift angle", "deg", -45, 45),
]
_DISCRETE = [
    ("AP ENGAGED", "Autopilot engaged", "ENGAGED", "OFF"),
    ("LDG GEAR DOWN", "Landing gear lever", "DOWN", "UP"),
    ("GPWS WARN", "Ground proximity warning", "NORMAL", "WARNING"),
    ("TCAS RA", "TCAS resolution advisory", "RA", "NONE"),
    ("MASTER WARN", "Master warning", "ON", "OFF"),
    ("MASTER CAUT", "Master caution", "ON", "OFF"),
    ("WOW", "Weight on wheels", "GROUND", "AIR"),
    ("VHF KEY 1", "VHF 1 push to talk", "KEYED", "IDLE"),
    ("STALL WARN", "Stick shaker", "ACTIVE", "OFF"),
    ("FIRE ENG 1", "Engine 1 fire", "FIRE", "NORMAL"),
]
_BCD = [
    ("SEL CRS", "Selected course", "deg", 0, 359),
    ("SEL HDG", "Selected heading", "deg", 0, 359),
    ("UTC HOURS", "UTC hours", "h", 0, 23),
    ("UTC MIN", "UTC minutes", "min", 0, 59),
    ("DATE DAY", "Date, day", "day", 1, 31),
    ("SEL ALT", "Selected altitude", "ft", 0, 50000),
]
_RESOLUTIONS = (0.176, 0.0879, 0.25, 0.0062, 0.0187, 0.03125, 0.5, 1, 0.0195, 0.125, 2, 0.001, 0.01, 0.35, 0.0625)
_OFFSETS = (0, 0, 0, -1.6, -10.8, 31.2, -40, 100, -273.15, 12.5, -3)


def build_bench_dataframe(seed: int = 7) -> DataframeDefinition:
    """A dataframe with many numeric cells (single words, pairs, several occurrences)."""
    rng = random.Random(seed)
    words = iter(rng.sample(range(2, WPS - 3), 120))
    subframe_choices = [(1, 2, 3, 4), (1, 2, 3, 4), (1, 3), (2, 4), (1,), (2,), (3,), (4,)]
    parameters: list[ParameterDefinition] = []

    def single(word, subframes, lsb, msb, occurrences=1):
        occs = []
        for index in range(1, occurrences + 1):
            occs.append(
                ParameterOccurrence(
                    index=index,
                    segments=[ParameterSegment(1, subframes, word if index == 1 else next(words), lsb, msb)],
                )
            )
        return occs

    for n, (mnemonic, description, unit, lo, hi) in enumerate(_ANALOG):
        signed = lo < 0
        subframes = rng.choice(subframe_choices)
        width = rng.choice((8, 9, 10, 11, 12))
        lsb = rng.choice((1, 1, 1, 2, 3))
        msb = min(12, lsb + width - 1)
        if n % 6 == 5:
            # Coarse + fine: one occurrence, two words (a word pair in FDS style).
            first = next(words)
            occurrences = [
                ParameterOccurrence(
                    index=1,
                    segments=[
                        ParameterSegment(1, subframes, first, 1, 9),
                        ParameterSegment(2, subframes, first + 1, 1, 12),
                    ],
                )
            ]
        else:
            occurrences = single(next(words), subframes, lsb, msb, occurrences=rng.choice((1, 1, 1, 2, 4)))
        parameters.append(
            ParameterDefinition(
                id=f"bench-{len(parameters) + 1:03d}",
                mnemonic=mnemonic,
                description=description,
                source_parameter_type="Signed Analog" if signed else "Unsigned Analog",
                parameter_type=TYPE_ANALOG_SIGNED if signed else TYPE_ANALOG_UNSIGNED,
                unit=unit,
                minimum=float(lo),
                maximum=float(hi),
                conversion=ConversionRule(resolution=rng.choice(_RESOLUTIONS), offset=rng.choice(_OFFSETS)),
                occurrences=occurrences,
            )
        )
    for mnemonic, description, true_state, false_state in _DISCRETE:
        bit = rng.randint(1, 12)
        parameters.append(
            ParameterDefinition(
                id=f"bench-{len(parameters) + 1:03d}",
                mnemonic=mnemonic,
                description=description,
                source_parameter_type="Discrete",
                parameter_type=TYPE_DISCRETE,
                true_state=true_state,
                false_state=false_state,
                occurrences=single(next(words), rng.choice(subframe_choices), bit, bit),
            )
        )
    for mnemonic, description, unit, lo, hi in _BCD:
        parameters.append(
            ParameterDefinition(
                id=f"bench-{len(parameters) + 1:03d}",
                mnemonic=mnemonic,
                description=description,
                source_parameter_type="BCD",
                parameter_type=TYPE_BCD,
                unit=unit,
                minimum=float(lo),
                maximum=float(hi),
                conversion=ConversionRule(resolution=1.0, offset=0.0),
                occurrences=single(next(words), rng.choice(subframe_choices), 1, 12),
            )
        )
    metadata = DataframeMetadata(
        dataframe_name="OCR_BENCH", wps=WPS, aircraft_type="BENCH", revision="B",
        issue_date="2026-09-23", sync_words=[583, 1464, 2631, 3512], source_type="bench",
    )
    return DataframeDefinition(metadata=metadata, parameters=parameters)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Whitespace-insensitive, placeholders ("-") treated as empty like the normalizer does."""
    return cell_value(text.replace("\n", " "))


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def cer(truth: str, read: str) -> float:
    truth, read = normalize_text(truth), normalize_text(read)
    if not truth and not read:
        return 0.0
    return levenshtein(truth, read) / max(1, len(truth))


def row_cost(truth_row: list[str], read_row: list[str]) -> float:
    n = max(len(truth_row), 1)
    return sum(cer(t, r) for t, r in zip(truth_row, read_row)) / n + abs(len(truth_row) - len(read_row)) / n


def align_rows(truth: list[list[str]], read: list[list[str]], gap: float = 1.0) -> list[tuple[int, int | None]]:
    """Order-preserving alignment (edit distance over rows); a missed row costs ``gap``."""
    n, m = len(truth), len(read)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i * gap
    for j in range(1, m + 1):
        cost[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + row_cost(truth[i - 1], read[j - 1]),
                cost[i - 1][j] + gap,
                cost[i][j - 1] + gap,
            )
    pairs: list[tuple[int, int | None]] = []
    i, j = n, m
    while i > 0:
        if j > 0 and cost[i][j] == cost[i - 1][j - 1] + row_cost(truth[i - 1], read[j - 1]):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + gap:
            pairs.append((i - 1, None))
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def squeeze(text: str) -> str:
    return "".join(normalize_text(text).split())


class ColumnScore:
    """exact = identical after whitespace normalization; loose = identical
    ignoring spaces altogether (the normalizer tolerates "40,103" for
    "40, 103"); CER = character error rate against the truth."""

    def __init__(self) -> None:
        self.cells = 0
        self.exact = 0
        self.loose = 0
        self.cer_sum = 0.0
        self.errors: list[tuple[str, str]] = []

    def add(self, truth: str, read: str) -> None:
        self.cells += 1
        error = cer(truth, read)
        self.cer_sum += error
        if error == 0:
            self.exact += 1
        if squeeze(truth) == squeeze(read):
            self.loose += 1
        elif len(self.errors) < 6:
            self.errors.append((normalize_text(truth), normalize_text(read)))

    @property
    def exact_rate(self) -> float:
        return self.exact / self.cells if self.cells else 0.0

    @property
    def loose_rate(self) -> float:
        return self.loose / self.cells if self.cells else 0.0

    @property
    def mean_cer(self) -> float:
        return self.cer_sum / self.cells if self.cells else 0.0


def score_document(columns: list[str], truth: list[list[str]], read_rows) -> dict:
    read = [list(row.cells) for row in read_rows]
    scores = {column: ColumnScore() for column in columns}
    pairs = align_rows(truth, read)
    matched = 0
    for truth_index, read_index in pairs:
        truth_row = truth[truth_index]
        read_row = read[read_index] if read_index is not None else [""] * len(columns)
        matched += read_index is not None
        for column, t_cell, r_cell in zip(columns, truth_row, read_row):
            scores[column].add(t_cell, r_cell)
    total = ColumnScore()
    for score in scores.values():
        total.cells += score.cells
        total.exact += score.exact
        total.loose += score.loose
        total.cer_sum += score.cer_sum
    return {
        "rows_expected": len(truth),
        "rows_read": len(read),
        "rows_matched": matched,
        "columns": scores,
        "total": total,
    }


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def parse_pages(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    first, _, last = text.partition("-")
    return int(first), int(last or first)


def synthetic_cases(out_dir: Path, font_size: float, skew: float, scan_dpi: int):
    dataframe = build_bench_dataframe()
    cases = []
    for style in (STYLE_GENERIC, STYLE_FDS):
        path = write_dataframe_pdf(
            dataframe,
            out_dir / f"ocr_bench_{style}.pdf",
            style=style,
            rows_per_page=14,
            scanned="image",
            skew_degrees=skew,
            scan_dpi=scan_dpi,
            font_size=font_size,
        )
        cases.append((style, path, dataframe_to_document_rows(dataframe, style)))
    return cases


def run_synthetic(cases, recognizer: str, keys: str | None, dpi: int, cls: bool, cells: bool) -> dict:
    results = {}
    for style, path, truth in cases:
        document = ingest_pdf(path)
        started = time.perf_counter()
        tables, issues = extract_tables(
            document,
            ocr="auto",
            dpi=dpi,
            ocr_recognizer=recognizer,
            ocr_keys_path=keys,
            ocr_angle_classifier=cls,
            ocr_cells=cells,
        )
        elapsed = time.perf_counter() - started
        rows = rows_from_tables(tables)
        columns = list(tables[0].header) if tables else []
        if not tables:
            results[style] = {"error": "no table found", "issues": [i.message for i in issues]}
            continue
        result = score_document(columns, truth, rows)
        result["seconds_per_page"] = elapsed / max(1, document.page_count)
        result["pages"] = document.page_count
        results[style] = result
    return results


def run_real(path: Path, pages, recognizer: str, keys: str | None, dpi: int, cls: bool, cells: bool) -> dict:
    profile = ImportProfile(
        page_range=pages,
        ocr_dpi=dpi,
        ocr_recognizer=recognizer,
        ocr_keys_path=keys,
        ocr_angle_classifier=cls,
        ocr_cells=cells,
    )
    started = time.perf_counter()
    session = import_pdf(path, profile)
    elapsed = time.perf_counter() - started
    page_count = (pages[1] - pages[0] + 1) if pages else session.page_count
    counts = session.state_counts()
    items = session.items
    rules = [issue.rule for item in items for issue in item.normalization_issues]
    confidences = [
        p.confidence
        for item in items
        for p in item.raw.provenance.values()
        if p.confidence is not None
    ]
    return {
        "rows": len(items),
        "approved": counts["APPROVED"],
        "review_required": counts["REVIEW_REQUIRED"],
        "no_candidate": sum(1 for item in items if item.candidate is None),
        "with_errors": sum(1 for item in items if item.error_count),
        "ocr_repairs": sum(1 for rule in rules if rule.endswith("_repaired")),
        "low_confidence_flags": sum(1 for rule in rules if rule == "normalize.ocr_confidence"),
        "mean_confidence": sum(confidences) / len(confidences) if confidences else None,
        "seconds_per_page": elapsed / max(1, page_count),
        "engine": next((i.message for i in session.issues if i.code == "PDF_OCR_ENGINE"), ""),
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def print_synthetic(label: str, results: dict, verbose: bool) -> None:
    for style, result in results.items():
        if "error" in result:
            print(f"  [{style}] {result['error']}: {result['issues']}")
            continue
        total = result["total"]
        print(
            f"  [{style}] rows {result['rows_matched']}/{result['rows_expected']} matched "
            f"({result['rows_read']} read)  cells exact {100 * total.exact_rate:5.1f}%  "
            f"loose {100 * total.loose_rate:5.1f}%  CER {100 * total.mean_cer:5.2f}%  "
            f"{result['seconds_per_page']:.1f} s/page"
        )
        for column, score in result["columns"].items():
            line = (
                f"      {column[:26]:<26} exact {100 * score.exact_rate:5.1f}%  "
                f"loose {100 * score.loose_rate:5.1f}%  CER {100 * score.mean_cer:5.2f}%"
            )
            if verbose and score.errors:
                line += "   e.g. " + "; ".join(f"{t!r}→{r!r}" for t, r in score.errors[:3])
            print(line)


def print_real(result: dict) -> None:
    print(
        f"  rows {result['rows']}  approved {result['approved']}  review {result['review_required']}  "
        f"no-candidate {result['no_candidate']}  errors {result['with_errors']}  "
        f"OCR repairs {result['ocr_repairs']}  low-confidence flags {result['low_confidence_flags']}  "
        f"mean confidence {result['mean_confidence']:.3f}  {result['seconds_per_page']:.1f} s/page"
        if result["mean_confidence"] is not None
        else f"  rows {result['rows']}  approved {result['approved']}  review {result['review_required']}"
    )
    if result["engine"]:
        print(f"  {result['engine']}")


def jsonable(value):
    if isinstance(value, ColumnScore):
        return {
            "cells": value.cells,
            "exact_rate": value.exact_rate,
            "loose_rate": value.loose_rate,
            "mean_cer": value.mean_cer,
            "errors": value.errors,
        }
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rec", action="append", help="recognizer: ch, en or a model path (repeatable)")
    parser.add_argument("--keys", help="character list for a recognizer given as a path")
    parser.add_argument("--dpi", action="append", type=int, help="OCR render dpi (repeatable)")
    parser.add_argument("--cls", action="store_true", help="enable RapidOCR's 180° angle classifier (off by default)")
    parser.add_argument(
        "--page-detector", action="store_true",
        help="detect text lines on the whole page (RapidOCR detector) instead of recognizing each grid cell",
    )
    parser.add_argument("--font", type=float, default=6.5, help="synthetic document font size (points)")
    parser.add_argument("--skew", type=float, default=0.6, help="synthetic scan skew (degrees)")
    parser.add_argument("--scan-dpi", type=int, default=200, help="resolution of the synthetic scan image")
    parser.add_argument("--out", type=Path, help="folder for the generated PDFs (default: a temp folder)")
    parser.add_argument("--real", type=Path, help="run the full import on this PDF instead")
    parser.add_argument("--pages", help="page range for --real, e.g. 14-21")
    parser.add_argument("--json", type=Path, help="write the results to this JSON file")
    parser.add_argument("-v", "--verbose", action="store_true", help="show example errors per column")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up when redirected to a log

    recognizers = args.rec or ["ch"]
    dpis = args.dpi or [150]
    report: dict = {}
    cells = not args.page_detector
    suffix = (" cls=on" if args.cls else "") + (" page-detector" if args.page_detector else " cells")
    if args.real:
        pages = parse_pages(args.pages)
        for recognizer in recognizers:
            for dpi in dpis:
                label = f"rec={Path(recognizer).name} dpi={dpi}{suffix}"
                print(f"== real {args.real.name} pages {args.pages or 'all'}  {label}")
                result = run_real(args.real, pages, recognizer, args.keys, dpi, args.cls, cells)
                print_real(result)
                report[label] = result
    else:
        out_dir = args.out or Path(__file__).resolve().parent / "_ocr_bench"
        out_dir.mkdir(parents=True, exist_ok=True)
        cases = synthetic_cases(out_dir, args.font, args.skew, args.scan_dpi)
        print(f"synthetic scans: font {args.font} pt, skew {args.skew}°, scan {args.scan_dpi} dpi → {out_dir}")
        for recognizer in recognizers:
            for dpi in dpis:
                label = f"rec={Path(recognizer).name} dpi={dpi}{suffix}"
                print(f"== {label}")
                results = run_synthetic(cases, recognizer, args.keys, dpi, args.cls, cells)
                print_synthetic(label, results, args.verbose)
                report[label] = results
    if args.json:
        args.json.write_text(json.dumps(jsonable(report), indent=2), encoding="utf-8")
        print(f"results written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
