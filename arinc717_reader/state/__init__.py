"""Central application state (design spec §40).

Widgets never become the source of truth; the GUI observes these stores.
The decoder reads FrameStore + DataframeStore and writes EngineeringStore.
"""
