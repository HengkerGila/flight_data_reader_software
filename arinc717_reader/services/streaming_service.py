"""Live subframe decoding → ParameterSample bus (spec v2 §26G, §26K–§26M).

Every received subframe is decoded on arrival; the resulting values are
published as timestamped samples and merged into the engineering store as
the latest value per (parameter, occurrence, subframe).  Parameter
frequency is respected by construction: a parameter mapped to one
subframe yields one sample per frame, one mapped to all four yields four,
and nothing is interpolated.
"""

from __future__ import annotations

import logging

from ..decoder.parameter_decoder import ParameterDecoder
from ..domain.engineering import EngineeringValue
from ..sources.serial.subframe_assembler import SF_LATE, SF_RECEIVED, SubframeArrival
from ..state.dataframe_store import DataframeStore
from ..state.engineering_store import EngineeringStore
from ..streaming.parameter_sample import ParameterSample, sample_from_value, sample_time
from ..streaming.sample_bus import ParameterSampleBus
from ..streaming.timeseries_store import TimeSeriesStore

logger = logging.getLogger(__name__)

DECODABLE_STATES = (SF_RECEIVED, SF_LATE)


class StreamingService:
    def __init__(
        self,
        dataframe_store: DataframeStore,
        engineering_store: EngineeringStore,
        sample_bus: ParameterSampleBus,
        timeseries_store: TimeSeriesStore,
        decoder: ParameterDecoder | None = None,
    ):
        self._dataframe_store = dataframe_store
        self._engineering_store = engineering_store
        self._bus = sample_bus
        self._timeseries = timeseries_store
        self._decoder = decoder or ParameterDecoder()
        self._latest: dict[tuple[str, int, int | None], EngineeringValue] = {}
        self.samples_published = 0
        sample_bus.subscribe(timeseries_store.append)
        dataframe_store.subscribe(self._on_dataframe_event)

    def _on_dataframe_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self._latest.clear()

    def reset(self) -> None:
        self._latest.clear()

    # -- arrival handling ------------------------------------------------

    def on_arrival(self, arrival: SubframeArrival) -> list[ParameterSample]:
        """Decode one arrived subframe; returns the samples published."""
        if arrival.subframe == 0 or arrival.state not in DECODABLE_STATES:
            return []
        dataframe = self._dataframe_store.dataframe
        if dataframe is None or dataframe.metadata.wps != arrival.frame.wps:
            return []
        values = self._decoder.decode_subframe(arrival.frame, dataframe, arrival.subframe)
        samples: list[ParameterSample] = []
        wps = arrival.frame.wps
        for value in values:
            first_word = min((s.word for s in value.trace.segments), default=1)
            timestamp = sample_time(arrival.timestamp, first_word, wps)
            samples.append(sample_from_value(value, timestamp, arrival.frame_index))
            self._latest[(value.parameter_id, value.occurrence_index, value.subframe)] = value
        self._publish_engineering(dataframe)
        if samples:
            self._bus.publish(samples)
            self.samples_published += len(samples)
        return samples

    def _publish_engineering(self, dataframe) -> None:
        order = {p.id: i for i, p in enumerate(dataframe.parameters)}
        values = sorted(
            self._latest.values(),
            key=lambda v: (order.get(v.parameter_id, len(order)), v.occurrence_index, v.subframe or 0),
        )
        self._engineering_store.set_values(values)
