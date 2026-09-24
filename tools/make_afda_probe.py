"""Write the AFDA probe database: examples/afda_probe/afda_probe_64wps.adb.

The probe is a small dataframe in the verified AFDA layout whose parameter
names say what each one tests.  Opening it in AFDA (and, if AFDA can save a
database, saving it back) settles the layout questions two real files could
not: selector semantics, the order of concatenated parts, decimals and
multi-state tables.  The checklist is examples/afda_probe/README.md.

Usage:  .venv/bin/python tools/make_afda_probe.py
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arinc717_reader.dataframe.adb_codec import dataframe_to_adb_text, parse_adb_text
from arinc717_reader.dataframe.adb_codec.mappings import (
    ADB_ENCODING,
    PARAM_RECORD_LENGTH,
)
from arinc717_reader.domain.dataframe import DataframeDefinition, DataframeMetadata
from arinc717_reader.domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    ConversionRule,
    DiscreteState,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)

TARGET = Path(__file__).resolve().parents[1] / "examples" / "afda_probe" / "afda_probe_64wps.adb"
WPS = 64
SYNC_WORDS = [583, 1464, 2631, 3512]


def segment(subframes, word, lsb, msb, sequence=1, weight=None):
    return ParameterSegment(
        sequence=sequence, subframes=tuple(subframes), word=word, lsb=lsb, msb=msb,
        bcd_weight=weight,
    )


def single(subframes, word, lsb, msb):
    return [ParameterOccurrence(index=1, segments=[segment(subframes, word, lsb, msb)])]


def analog(pid, name, description, occurrences, signed=False, unit="", minimum=0.0,
           maximum=4095.0, resolution=1.0, offset=0.0, decimals=0):
    return ParameterDefinition(
        id=pid, mnemonic=name, description=description,
        source_parameter_type="Signed Analog" if signed else "Unsigned Analog",
        parameter_type=TYPE_ANALOG_SIGNED if signed else TYPE_ANALOG_UNSIGNED,
        unit=unit, minimum=minimum, maximum=maximum,
        conversion=ConversionRule(resolution=resolution, offset=offset),
        decimals=decimals, occurrences=occurrences,
    )


def build_probe() -> DataframeDefinition:
    parameters = [
        analog("probe-a1", "PROBE A1 ABS W70", "Expect SF2 word 6, 12 bits, 1 sample per frame",
               single((2,), 6, 1, 12)),
        analog("probe-a2", "PROBE A2 RATE4 W5", "Expect word 5 in every subframe (4 samples per frame)",
               single((1, 2, 3, 4), 5, 1, 12)),
        analog("probe-a3", "PROBE A3 SF24 W10", "Expect word 10 in SF2 and SF4 only (2 samples per frame)",
               single((2, 4), 10, 1, 12)),
        analog("probe-b1", "PROBE B1 CONCAT MS W20 LS W21",
               "Two parts: word 20 bits 1-12 is the MOST significant, word 21 bits 1-4 the LEAST",
               [ParameterOccurrence(index=1, segments=[
                   segment((1,), 20, 1, 12, sequence=1),
                   segment((1,), 21, 1, 4, sequence=2),
               ])], maximum=65535.0),
        ParameterDefinition(
            id="probe-b2", mnemonic="PROBE B2 BCD SF4 W19",
            description="Two BCD digits in SF4 word 19: tens in bits 9-12 (weight 10), ones in bits 5-8 (weight 1)",
            source_parameter_type="BCD", parameter_type=TYPE_BCD, unit="",
            minimum=0.0, maximum=99.0, conversion=ConversionRule(resolution=1.0, offset=0.0),
            decimals=0,
            occurrences=[ParameterOccurrence(index=1, segments=[
                segment((4,), 19, 9, 12, sequence=1, weight=10.0),
                segment((4,), 19, 5, 8, sequence=2, weight=1.0),
            ])],
        ),
        ParameterDefinition(
            id="probe-c1", mnemonic="PROBE C1 DISCRETE SF4 W56 B3",
            description="Expect SF4 word 56 bit 3; 0 = ON AIR, 1 = ON GROUND",
            source_parameter_type="Discrete", parameter_type=TYPE_DISCRETE, unit="Status",
            minimum=0.0, maximum=1.0, decimals=0,
            true_state="ON GROUND", false_state="ON AIR",
            states=[DiscreteState(0, "ON AIR"), DiscreteState(1, "ON GROUND")],
            occurrences=single((4,), 56, 3, 3),
        ),
        ParameterDefinition(
            id="probe-c2", mnemonic="PROBE C2 FOUR STATES W12",
            description="SF1 word 12 bits 1-2; 0=OFF 1=LOW 2=MID 3=HIGH",
            source_parameter_type="Discrete", parameter_type=TYPE_DISCRETE, unit="",
            minimum=0.0, maximum=3.0, decimals=0,
            true_state="LOW", false_state="OFF",
            states=[DiscreteState(0, "OFF"), DiscreteState(1, "LOW"),
                    DiscreteState(2, "MID"), DiscreteState(3, "HIGH")],
            occurrences=single((1,), 12, 1, 2),
        ),
        analog("probe-d1", "PROBE D1 DECIMALS 3", "Signed, resolution 0.001, expect 3 decimals shown",
               single((1,), 14, 1, 12), signed=True, unit="V", minimum=-2.048, maximum=2.047,
               resolution=0.001, offset=0.0, decimals=3),
    ]
    metadata = DataframeMetadata(dataframe_name="afda_probe_64wps", wps=WPS, sync_words=SYNC_WORDS)
    return DataframeDefinition(metadata=metadata, parameters=parameters)


def raw_selector_rows() -> list[list[str]]:
    """Rows our writer never produces: hand-written selectors to see what AFDA does with them."""
    rows = []
    for name, description, selector, word in (
        ("PROBE S1 SEL2 W6", "Selector 2, word 6: SF2 word 6 if relative, SF1 word 6 if the selector is ignored", "2", 6),
        ("PROBE S2 SEL3 W70", "Selector 3, word 70: SF2 word 6 if absolute, SF3 word 6 if 70 is taken modulo 64, SF4 word 6 if (3-1)*64+70", "3", 70),
        ("PROBE S3 SEL13 W6", "Selector 13, word 6: SF1 and SF3 word 6 (2 samples) if the selector is a mask", "13", 6),
    ):
        record = [""] * PARAM_RECORD_LENGTH
        record[0:10] = [name, description, "Unsigned Analog", "", "0", "4095", "1", "0", "0", "-"]
        record[74:76] = ["1", "1"]
        record[76:80] = [selector, str(word), "1", "12"]
        rows.append(record)
    return rows


def main() -> None:
    text = dataframe_to_adb_text(build_probe())
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in raw_selector_rows():
        writer.writerow(row)
    text += buffer.getvalue()
    TARGET.write_bytes(text.encode(ADB_ENCODING))
    # The probe must read back through our own parser.
    parsed = parse_adb_text(text, source_filename=TARGET.name)
    print(f"wrote {TARGET} ({len(parsed.parameters)} parameters, {WPS} wps)")


if __name__ == "__main__":
    main()
