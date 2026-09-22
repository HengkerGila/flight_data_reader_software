#!/usr/bin/env bash
# Build the one-folder Linux binary with the same spec as the Windows build.
# Used to validate the spec; a Linux build does not run on Windows.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
"$PY" -m PyInstaller --noconfirm --clean packaging/arinc717_reader.spec
echo "Build finished: dist/ARINC717Reader/ARINC717Reader"
