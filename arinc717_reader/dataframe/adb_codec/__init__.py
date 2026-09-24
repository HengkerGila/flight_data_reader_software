"""`.adb` import/export codec (design spec §33–§37, AFDA layout)."""

from .mappings import decode_subframe_selector, encode_subframe_selector
from .parser import AdbParseError, decode_adb_bytes, parse_adb_file, parse_adb_text
from .writer import (
    AdbWriteError,
    dataframe_to_adb_text,
    format_adb_number,
    write_adb_file,
)

__all__ = [
    "AdbParseError",
    "AdbWriteError",
    "decode_adb_bytes",
    "decode_subframe_selector",
    "encode_subframe_selector",
    "format_adb_number",
    "parse_adb_file",
    "parse_adb_text",
    "dataframe_to_adb_text",
    "write_adb_file",
]
