"""Generate examples/demo_256wps.pdf — a dataframe document for the demo
dataframe, used to try the PDF importer without a vendor document.

Usage:  .venv/bin/python examples/make_demo_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arinc717_reader.dataframe.pdf_importer.synth import write_dataframe_pdf
from arinc717_reader.demo import build_demo_dataframe


def main() -> None:
    target = Path(__file__).parent / "demo_256wps.pdf"
    write_dataframe_pdf(build_demo_dataframe(), target, rows_per_page=8)
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
