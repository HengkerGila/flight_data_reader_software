# Building a standalone executable

PyInstaller packages the interpreter, the application and every library it
loads into one folder with an executable at the top. It only builds for the
operating system it runs on, so the Windows `.exe` must be built on Windows.

## Windows

1. Install **Python 3.12, 64-bit** from python.org (tick "Add python.exe to
   PATH" in the installer).
2. Copy the project folder to the Windows PC (or clone it).
3. Open a command prompt in the project folder and run:

   ```bat
   packaging\build_windows.bat
   ```

   The script creates `.venv-win`, installs the application with the OCR
   extra plus PyInstaller, and runs the spec. The first run downloads
   PySide6, PyMuPDF, onnxruntime and RapidOCR (~500 MB) and takes several
   minutes.

4. The result is the folder `dist\ARINC717Reader\`. Start
   `ARINC717Reader.exe` from there, or copy the **whole folder** to another
   PC. The executable does not work on its own: it needs the DLLs, Qt
   plugins and OCR models beside it.

   `ARINC717Reader.exe path\to\dataframe.adb` opens that dataframe at start.
   A demo dataframe and document are in `dist\ARINC717Reader\examples\`.

5. The script ends by running the build's self-test. You can repeat it at
   any time from a command prompt:

   ```bat
   dist\ARINC717Reader\ARINC717Reader.exe --selftest
   ```

   It prints one PASS/FAIL line per check (decoder closed loop, ADB codec,
   GUI construction, PDF import through PyMuPDF, OCR model loading) and ends
   with `SELFTEST OK`. OCR is the only optional check.

Because the build has no console window, log output goes to
`%LOCALAPPDATA%\arinc717_reader\arinc717-reader.log`. Look there first if
the window does not appear.

## Size

The folder is around 500 MB, most of it OpenCV (pulled in by RapidOCR, and
on Linux it brings ffmpeg and GTK along), onnxruntime and Qt. Swapping
`opencv-python` for `opencv-python-headless` in the build venv before
running PyInstaller removes the GUI and video libraries and is safe: the
importer only uses OpenCV through RapidOCR's image preprocessing.

## Linux (validation build)

```bash
packaging/build.sh
dist/ARINC717Reader/ARINC717Reader examples/demo_256wps.adb
```

## What the spec does

`arinc717_reader.spec` collects the RapidOCR model files (`*.onnx`,
`config.yaml`) and the PyMuPDF binaries explicitly, because they are loaded
at run time and static analysis does not see them; names the lazily imported
OCR modules as hidden imports; bundles the two example files; and excludes
the Qt modules the application never uses (WebEngine, Qml, Multimedia, …)
to keep the folder around 350 MB instead of 700 MB.

## Common problems

| Symptom | Cause / fix |
| --- | --- |
| Windows SmartScreen warns "unrecognised app" | The exe is unsigned. Click "More info" → "Run anyway", or sign it with `signtool` and a code-signing certificate. |
| The window never appears | Read the log file above. A missing DLL typically means the `dist\ARINC717Reader` folder was copied incompletely. |
| "PDF import needs the 'pymupdf' package" inside the exe | The build was made from a venv without PyMuPDF; rerun the script (it installs `.[ocr]`, which includes it). |
| OCR unavailable inside the exe | Same for `rapidocr-onnxruntime`; check `dist\ARINC717Reader\_internal\rapidocr_onnxruntime\models\` contains three `.onnx` files. |
| Antivirus quarantines the exe | A known false positive with PyInstaller builds; add an exclusion or sign the build. |
| Build fails with a `py -3.12` error | Python 3.12 is not installed or not on PATH; install it from python.org. |
| `ImportError: attempted relative import` at start | The spec must point at `packaging/launcher.py`, not at the package's `__main__.py`. |
