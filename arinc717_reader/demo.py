"""Bundled demo dataframe (256 WPS) for simulation-first development.

This is example data, not aircraft truth.  Resolutions/offsets follow the
validated examples in the design spec (§16): pitch 0.176, flap 0.0062/-1.6,
AOA 0.0187/-10.8, aileron -0.0153/+31.2.  Word 1 of each subframe is reserved
for the sync words 583/1464/2631/3512 (§6.2).
"""

from __future__ import annotations

from .domain.dataframe import DataframeDefinition, DataframeMetadata
from .domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)

DEMO_SYNC_WORDS = [583, 1464, 2631, 3512]
DEMO_WPS = 256


def _segment(subframes, word, lsb, msb, sequence=1):
    return ParameterSegment(
        sequence=sequence, subframes=tuple(subframes), word=word, lsb=lsb, msb=msb
    )


def _single(subframes, word, lsb, msb):
    return [ParameterOccurrence(index=1, segments=[_segment(subframes, word, lsb, msb)])]


def build_demo_dataframe() -> DataframeDefinition:
    metadata = DataframeMetadata(
        dataframe_name="DEMO_256WPS",
        wps=DEMO_WPS,
        aircraft_type="DEMO",
        revision="A",
        issue_date="2026-09-11",
        superframe_present=False,
        sync_words=list(DEMO_SYNC_WORDS),
        source_type="demo",
    )

    parameters = [
        ParameterDefinition(
            id="demo-pitch",
            mnemonic="PITCH ATT #1",
            description="Pitch attitude, captain side",
            source_parameter_type="Signed Analog",
            parameter_type=TYPE_ANALOG_SIGNED,
            unit="deg",
            minimum=-90.0,
            maximum=90.0,
            conversion=ConversionRule(resolution=0.176, offset=0.0),
            occurrences=[
                ParameterOccurrence(
                    index=1, segments=[_segment((1, 2, 3, 4), 4, 3, 12)]
                ),
                ParameterOccurrence(
                    index=2, segments=[_segment((1, 2, 3, 4), 132, 3, 12)]
                ),
            ],
        ),
        ParameterDefinition(
            id="demo-roll",
            mnemonic="ROLL ATT",
            description="Roll attitude",
            source_parameter_type="Signed Analog",
            parameter_type=TYPE_ANALOG_SIGNED,
            unit="deg",
            minimum=-180.0,
            maximum=180.0,
            conversion=ConversionRule(resolution=0.176, offset=0.0),
            occurrences=_single((1, 2, 3, 4), 5, 3, 12),
        ),
        ParameterDefinition(
            id="demo-heading",
            mnemonic="HDG MAG",
            description="Magnetic heading",
            source_parameter_type="Unsigned Analog",
            parameter_type=TYPE_ANALOG_UNSIGNED,
            unit="deg",
            minimum=0.0,
            maximum=360.0,
            conversion=ConversionRule(resolution=0.0879, offset=0.0),
            occurrences=_single((1, 3), 6, 1, 12),
        ),
        ParameterDefinition(
            id="demo-ias",
            mnemonic="IAS",
            description="Indicated airspeed",
            source_parameter_type="Unsigned Analog",
            parameter_type=TYPE_ANALOG_UNSIGNED,
            unit="kt",
            minimum=0.0,
            maximum=450.0,
            conversion=ConversionRule(resolution=0.25, offset=0.0),
            occurrences=_single((1, 2, 3, 4), 7, 1, 12),
        ),
        ParameterDefinition(
            id="demo-altitude",
            mnemonic="PRESS ALT",
            description="Pressure altitude, coarse+fine multi-segment",
            source_parameter_type="Signed Analog",
            parameter_type=TYPE_ANALOG_SIGNED,
            unit="ft",
            minimum=-2000.0,
            maximum=60000.0,
            conversion=ConversionRule(resolution=0.25, offset=0.0),
            occurrences=[
                ParameterOccurrence(
                    index=1,
                    segments=[
                        _segment((1, 2, 3, 4), 154, 1, 9, sequence=1),
                        _segment((1, 2, 3, 4), 153, 1, 12, sequence=2),
                    ],
                )
            ],
        ),
        ParameterDefinition(
            id="demo-flap",
            mnemonic="FLAP POS",
            description="Flap surface position",
            source_parameter_type="Unsigned Analog",
            parameter_type=TYPE_ANALOG_UNSIGNED,
            unit="deg",
            minimum=-2.0,
            maximum=45.0,
            conversion=ConversionRule(resolution=0.0062, offset=-1.6),
            occurrences=_single((2, 4), 10, 1, 12),
        ),
        ParameterDefinition(
            id="demo-aoa",
            mnemonic="AOA",
            description="Angle of attack",
            source_parameter_type="Unsigned Analog",
            parameter_type=TYPE_ANALOG_UNSIGNED,
            unit="deg",
            minimum=-11.0,
            maximum=41.0,
            conversion=ConversionRule(resolution=0.0187, offset=-10.8),
            occurrences=_single((1, 3), 11, 1, 12),
        ),
        ParameterDefinition(
            id="demo-aileron",
            mnemonic="AILERON POS",
            description="Aileron position (negative resolution)",
            source_parameter_type="Unsigned Analog",
            parameter_type=TYPE_ANALOG_UNSIGNED,
            unit="deg",
            minimum=-31.5,
            maximum=31.5,
            conversion=ConversionRule(resolution=-0.0153, offset=31.2),
            occurrences=_single((1, 2, 3, 4), 12, 1, 12),
        ),
        ParameterDefinition(
            id="demo-gear",
            mnemonic="LDG GEAR DOWN",
            description="Landing gear lever position",
            source_parameter_type="Discrete",
            parameter_type=TYPE_DISCRETE,
            true_state="DOWN",
            false_state="UP",
            occurrences=_single((1, 2, 3, 4), 13, 1, 1),
        ),
        ParameterDefinition(
            id="demo-gpws",
            mnemonic="GPWS WARN",
            description="Ground proximity warning (active low)",
            source_parameter_type="Discrete",
            parameter_type=TYPE_DISCRETE,
            true_state="NORMAL",
            false_state="WARNING",
            notes="Active low: raw 0 means warning active",
            occurrences=_single((1, 2, 3, 4), 13, 2, 2),
        ),
        ParameterDefinition(
            id="demo-course",
            mnemonic="SEL CRS",
            description="Selected course (BCD, 3 digits)",
            source_parameter_type="BCD",
            parameter_type=TYPE_BCD,
            unit="deg",
            minimum=0.0,
            maximum=359.0,
            conversion=ConversionRule(resolution=1.0, offset=0.0),
            occurrences=_single((2,), 14, 1, 12),
        ),
        ParameterDefinition(
            id="demo-spare",
            mnemonic="SPARE 15",
            description="Unassigned source type (decodes as UNSUPPORTED TYPE)",
            source_parameter_type="Special",
            parameter_type="unknown",
            occurrences=_single((1,), 15, 1, 12),
        ),
    ]
    return DataframeDefinition(metadata=metadata, parameters=parameters)
