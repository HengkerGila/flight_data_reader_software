@echo off
REM Build the Windows installer  dist\ARINC717Reader-<version>-x64.msi  from the
REM PyInstaller folder produced by packaging\build_windows.bat (WiX Toolset v5+).
REM
REM     packaging\build_windows.bat   builds dist\ARINC717Reader and, when WiX is
REM                                   installed, calls this script at the end
REM     packaging\build_msi.bat       installer only (needs an existing dist folder)
REM
REM One-time setup:  install the .NET SDK (8 or newer), then in a new prompt
REM     dotnet tool install --global wix

setlocal
cd /d "%~dp0\.."

if not exist dist\ARINC717Reader\ARINC717Reader.exe (
    echo dist\ARINC717Reader\ARINC717Reader.exe not found - run packaging\build_windows.bat first.
    exit /b 1
)
where wix >nul 2>nul
if errorlevel 1 (
    echo WiX not found on PATH.
    echo Install the .NET SDK, then run:  dotnet tool install --global wix
    echo and open a new command prompt before retrying.
    exit /b 1
)

set PY=.venv-win\Scripts\python.exe
if not exist %PY% set PY=python
for /f "usebackq delims=" %%v in (`%PY% -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"`) do set VERSION=%%v
if "%VERSION%"=="" (
    echo Could not read the version from pyproject.toml
    exit /b 1
)

echo Making sure the WiX UI extension is available ...
wix extension add -g WixToolset.UI.wixext >nul 2>nul

set OUT=dist\ARINC717Reader-%VERSION%-x64.msi
echo Building %OUT% ...
wix build -arch x64 ^
    -d Version=%VERSION% ^
    -d SourceDir="%CD%\dist\ARINC717Reader" ^
    -d IconFile="%CD%\packaging\icon.ico" ^
    -ext WixToolset.UI.wixext ^
    -o "%OUT%" ^
    packaging\windows\ARINC717Reader.wxs || goto :fail

echo.
echo Installer ready: %OUT%
echo   install    double-click it, or:  msiexec /i "%OUT%" /l*v install.log
echo   silent     msiexec /i "%OUT%" /qn
echo   uninstall  Settings ^> Apps, or:  msiexec /x "%OUT%" /qn
exit /b 0

:fail
echo.
echo MSI BUILD FAILED (see messages above)
echo If WiX reports an unknown element "Files", the installed WiX is older than v5: run  dotnet tool update --global wix
echo If it reports an extension version mismatch, run  wix --version  and add the matching extension, e.g.
echo     wix extension add -g WixToolset.UI.wixext/5.0.2
exit /b 1
