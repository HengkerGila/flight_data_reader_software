"""Semantic dataframe comparison for round-trip acceptance (design spec §37).

``decode(existing.adb) == decode(generated.adb)`` at the semantic level:
settings, sync words, parameter identity/type/unit/conversion/states,
occurrences, segments, subframes, words, bits, and preserved unknown fields.
Byte-for-byte equality is not required.
"""

from __future__ import annotations

from ..domain.dataframe import DataframeDefinition
from .adb_codec.legacy import segment_legacy_flag


def dataframe_differences(
    a: DataframeDefinition, b: DataframeDefinition
) -> list[str]:
    """Return semantic differences; an empty list means equivalent."""
    diffs: list[str] = []

    if a.metadata.wps != b.metadata.wps:
        diffs.append(f"wps: {a.metadata.wps} != {b.metadata.wps}")
    if a.metadata.sync_words != b.metadata.sync_words:
        diffs.append(
            f"sync_words: {a.metadata.sync_words} != {b.metadata.sync_words}"
        )
    if a.metadata.adb_settings_raw and b.metadata.adb_settings_raw:
        if tuple(a.metadata.adb_settings_raw) != tuple(b.metadata.adb_settings_raw):
            diffs.append(
                f"settings record: {a.metadata.adb_settings_raw} != "
                f"{b.metadata.adb_settings_raw}"
            )

    if len(a.parameters) != len(b.parameters):
        diffs.append(
            f"parameter count: {len(a.parameters)} != {len(b.parameters)}"
        )
        return diffs

    for pa, pb in zip(a.parameters, b.parameters):
        label = pa.mnemonic
        for attr in (
            "mnemonic",
            "description",
            "source_parameter_type",
            "parameter_type",
            "unit",
            "minimum",
            "maximum",
            "true_state",
            "false_state",
            "notes",
        ):
            va, vb = getattr(pa, attr), getattr(pb, attr)
            if va != vb:
                diffs.append(f"{label}: {attr}: {va!r} != {vb!r}")
        if pa.conversion.resolution != pb.conversion.resolution:
            diffs.append(
                f"{label}: resolution: {pa.conversion.resolution} != "
                f"{pb.conversion.resolution}"
            )
        if pa.conversion.offset != pb.conversion.offset:
            diffs.append(
                f"{label}: offset: {pa.conversion.offset} != {pb.conversion.offset}"
            )

        ta = pa.provenance.extra.get("trailing_fields", []) if pa.provenance else []
        tb = pb.provenance.extra.get("trailing_fields", []) if pb.provenance else []
        if list(ta) != list(tb):
            diffs.append(f"{label}: trailing legacy fields: {ta} != {tb}")

        if len(pa.occurrences) != len(pb.occurrences):
            diffs.append(
                f"{label}: occurrence count: {len(pa.occurrences)} != "
                f"{len(pb.occurrences)}"
            )
            continue
        for oa, ob in zip(pa.occurrences, pb.occurrences):
            if oa.index != ob.index:
                diffs.append(f"{label}: occurrence index {oa.index} != {ob.index}")
            if len(oa.segments) != len(ob.segments):
                diffs.append(
                    f"{label} occ {oa.index}: segment count "
                    f"{len(oa.segments)} != {len(ob.segments)}"
                )
                continue
            for sa, sb in zip(oa.segments, ob.segments):
                seg_label = f"{label} occ {oa.index} seg {sa.sequence}"
                if sa.sequence != sb.sequence:
                    diffs.append(f"{seg_label}: sequence {sa.sequence} != {sb.sequence}")
                if tuple(sa.subframes) != tuple(sb.subframes):
                    diffs.append(
                        f"{seg_label}: subframes {sa.subframes} != {sb.subframes}"
                    )
                if (sa.word, sa.lsb, sa.msb) != (sb.word, sb.lsb, sb.msb):
                    diffs.append(
                        f"{seg_label}: word/lsb/msb "
                        f"({sa.word},{sa.lsb},{sa.msb}) != "
                        f"({sb.word},{sb.lsb},{sb.msb})"
                    )
                fa = segment_legacy_flag(sa.source_raw)
                fb = segment_legacy_flag(sb.source_raw)
                if fa != fb:
                    diffs.append(f"{seg_label}: legacy flag {fa!r} != {fb!r}")
    return diffs
