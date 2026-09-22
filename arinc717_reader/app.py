"""Application assembly and entry point.

Wires stores, services and the decoder together (design spec §40) and starts
the Qt event loop.  Everything below the UI layer is importable and testable
without a QApplication.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .services.dataframe_service import DataframeService
from .services.decoding_service import DecodingService
from .services.frame_service import FrameService
from .services.simulation_service import SimulationService
from .state.dataframe_store import DataframeStore
from .state.engineering_store import EngineeringStore
from .state.frame_store import FrameStore


@dataclass
class AppContext:
    dataframe_store: DataframeStore
    frame_store: FrameStore
    engineering_store: EngineeringStore
    dataframe_service: DataframeService
    frame_service: FrameService
    decoding_service: DecodingService
    simulation_service: SimulationService


def build_context() -> AppContext:
    dataframe_store = DataframeStore()
    frame_store = FrameStore()
    engineering_store = EngineeringStore()
    return AppContext(
        dataframe_store=dataframe_store,
        frame_store=frame_store,
        engineering_store=engineering_store,
        dataframe_service=DataframeService(dataframe_store),
        frame_service=FrameService(frame_store, dataframe_store),
        decoding_service=DecodingService(
            dataframe_store, frame_store, engineering_store
        ),
        simulation_service=SimulationService(
            dataframe_store, frame_store, engineering_store
        ),
    )


LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def log_file_path() -> Path:
    """Where a windowed build (no console) writes its log."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / "arinc717_reader" / "arinc717-reader.log"


def configure_logging(level: int = logging.INFO) -> None:
    """Log to stderr, or to a file when there is no console.

    A PyInstaller build made with ``console=False`` (the Windows .exe) runs
    with ``sys.stderr`` set to None; logging to it would be silently lost.
    """
    if sys.stderr is not None:
        logging.basicConfig(level=level, format=LOG_FORMAT)
        return
    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=level, format=LOG_FORMAT, filename=str(path))
    except OSError:
        logging.basicConfig(level=level, format=LOG_FORMAT, handlers=[logging.NullHandler()])


def bundled_examples_dir() -> Path:
    """The examples folder: next to the source tree, or inside a PyInstaller bundle."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "examples"
    return Path(__file__).resolve().parent.parent / "examples"


def selftest(argv: list[str]) -> int:
    """Check a packaged build without a GUI: decoder, ADB codec, PDF import, OCR.

    ``ARINC717Reader --selftest`` prints one line per check and exits 0 when
    every required check passes (OCR is reported but optional).  Runs headless
    so it also works over a remote shell.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report: list[tuple[str, bool, str]] = []

    def check(name: str, required: bool = True):
        def run(fn):
            try:
                detail = fn() or "ok"
                report.append((name, True, str(detail)))
            except Exception as exc:  # report every failure, keep going
                report.append((name, not required, f"FAILED: {exc!r}"))
            return fn
        return run

    from .demo import build_demo_dataframe

    @check("decoder + encoder closed loop")
    def _decoder():
        from .decoder.parameter_decoder import ParameterDecoder
        from .encoder.frame_builder import build_scenario_frame

        dataframe = build_demo_dataframe()
        frame, _ = build_scenario_frame(dataframe, {"demo-pitch": -20.0})
        values = ParameterDecoder().decode_frame(frame, dataframe)
        pitch = next(v for v in values if v.parameter_id == "demo-pitch" and v.subframe == 1)
        assert abs(pitch.engineering_value - (-20.064)) < 1e-6, pitch.engineering_value
        return f"pitch -20 deg -> {pitch.engineering_value:.3f} deg"

    @check("ADB codec round trip")
    def _adb():
        from .dataframe.adb_codec import dataframe_to_adb_text, parse_adb_text
        from .dataframe.compare import dataframe_differences

        original = build_demo_dataframe()
        parsed = parse_adb_text(dataframe_to_adb_text(original), source_filename="demo.adb")
        diffs = dataframe_differences(original, parsed)
        assert not diffs, diffs
        return f"{len(parsed.parameters)} parameters"

    @check("Qt GUI construction (offscreen)")
    def _gui():
        from PySide6.QtWidgets import QApplication

        from .ui.main_window import MainWindow

        app = QApplication.instance() or QApplication(argv)
        ctx = build_context()
        window = MainWindow(ctx)
        ctx.dataframe_service.set_dataframe(build_demo_dataframe())
        ctx.frame_service.new_random(seed=1)
        app.processEvents()
        assert window.frame_view_page._model.rowCount() == 256
        window.close()
        return "main window built, 256 rows in Frame View"

    @check("PDF import (PyMuPDF)")
    def _pdf():
        from .dataframe.pdf_importer import import_pdf, pdf_support_available

        assert pdf_support_available(), "pymupdf not bundled"
        path = bundled_examples_dir() / "demo_256wps.pdf"
        assert path.exists(), f"missing {path}"
        session = import_pdf(path)
        assert len(session.items) >= 10, len(session.items)
        return f"{path.name}: {len(session.items)} rows, {session.tables_found} tables"

    @check("OCR engine (RapidOCR, optional)", required=False)
    def _ocr():
        import numpy as np

        from .dataframe.pdf_importer.scan import RapidOcrEngine

        assert RapidOcrEngine.available(), "rapidocr-onnxruntime not bundled"
        engine = RapidOcrEngine()
        blank = np.full((40, 120, 3), 255, dtype=np.uint8)
        engine.recognize(blank)  # exercises model loading; a blank image yields no text
        return "models loaded"

    width = max(len(name) for name, _, _ in report)
    for name, passed, detail in report:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<{width}}  {detail}")
    failures = [name for name, passed, _ in report if not passed]
    print(f"log file: {log_file_path()}")
    print("SELFTEST OK" if not failures else f"SELFTEST FAILED: {', '.join(failures)}")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv[1:]:
        configure_logging()
        return selftest([a for a in argv if a != "--selftest"])

    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    configure_logging()
    app = QApplication(argv)
    ctx = build_context()
    window = MainWindow(ctx)

    if len(argv) > 1:
        try:
            ctx.dataframe_service.load_adb(argv[1])
        except Exception as exc:  # surface, don't crash on a bad CLI path
            logging.getLogger(__name__).error(
                "event=startup_adb_load_failed file=%s error=%s", argv[1], exc
            )

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
