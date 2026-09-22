"""Regenerate examples/demo_256wps.adb from the bundled demo dataframe.

Usage:  .venv/bin/python examples/make_demo_adb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arinc717_reader.dataframe.adb_codec import write_adb_file
from arinc717_reader.demo import build_demo_dataframe


def main() -> None:
    target = Path(__file__).parent / "demo_256wps.adb"
    write_adb_file(target, build_demo_dataframe())
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
