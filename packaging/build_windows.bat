@echo off
REM Build ARINC717Reader.exe (one-folder) on Windows.
REM Run from a "Developer Command Prompt" or plain cmd in the project root:
REM     packaging\build_windows.bat
REM Requires Python 3.12 (64-bit) from python.org on PATH.

setlocal
cd /d "%~dp0\.."

if not exist .venv-win (
    echo Creating virtual environment .venv-win ...
    py -3.12 -m venv .venv-win || python -m venv .venv-win || goto :fail
)
call .venv-win\Scripts\activate.bat || goto :fail

echo Installing the application and build tools ...
python -m pip install --upgrade pip || goto :fail
python -m pip install -e .[ocr,build] || goto :fail

echo Building with PyInstaller ...
pyinstaller --noconfirm --clean packaging\arinc717_reader.spec || goto :fail

echo.
echo Build finished: dist\ARINC717Reader\ARINC717Reader.exe
echo Checking the build ...
dist\ARINC717Reader\ARINC717Reader.exe --selftest
echo Copy the whole dist\ARINC717Reader folder to another PC; the .exe needs the files beside it.

where wix >nul 2>nul
if not errorlevel 1 (
    echo.
    echo WiX found - building the MSI installer ...
    call packaging\build_msi.bat
) else (
    echo.
    echo To produce a one-file MSI installer, install WiX ^(see packaging\README.md^) and run packaging\build_msi.bat
)
exit /b 0

:fail
echo.
echo BUILD FAILED (see messages above)
exit /b 1
