"""ARINC data sources (design spec §21–§24, §56).

Every source implements the ``FrameSource`` protocol and only produces
``Arinc717Frame``.  No GUI code depends on a specific implementation.
"""
