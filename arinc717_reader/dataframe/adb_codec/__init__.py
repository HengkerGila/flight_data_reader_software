"""`.adb` import/export codec (design spec §33–§37)."""

from .mappings import decode_subframe_selector, encode_subframe_selector
from .parser import AdbParseError, parse_adb_file, parse_adb_text
from .writer import dataframe_to_adb_text, write_adb_file

__all__ = [
    "AdbParseError",
    "decode_subframe_selector",
    "encode_subframe_selector",
    "parse_adb_file",
    "parse_adb_text",
    "dataframe_to_adb_text",
    "write_adb_file",
]
