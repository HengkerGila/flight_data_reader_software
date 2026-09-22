"""PyInstaller entry point.

The package's own ``__main__`` uses a relative import, which only works when
Python runs it as part of the package (``python -m arinc717_reader``).
PyInstaller executes the entry script as a top-level module, so it needs an
absolute import.
"""

from arinc717_reader.app import main

raise SystemExit(main())
