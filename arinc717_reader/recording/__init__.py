"""Session recording and replay (spec v2 §26R).

A session file (``.a717session``, JSON Lines) holds a header with the
dataframe identity, WPS, sync words, source and simulator configuration,
then one record per received subframe (raw words, state, timestamp) and
one per published parameter sample.  Replay feeds the recorded subframes
into the normal assembler → decoder → graph path.
"""
