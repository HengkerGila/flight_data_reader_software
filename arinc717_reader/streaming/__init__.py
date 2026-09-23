"""Engineering sample domain (spec v2 §26K–§26N, level 3 of §3A).

``ParameterSample`` is a timestamped decoded value; the ``ParameterSampleBus``
fans samples out to the Engineering View, the ``TimeSeriesStore`` and the
recorder; ``signal_generator`` produces engineering-coherent test signals
for the HIL simulator.
"""
