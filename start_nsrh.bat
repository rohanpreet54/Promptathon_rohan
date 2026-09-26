@echo off
setlocal

rem ============================================================
rem NSRH HUB - Distributed Object Storage
rem Windows launcher for the local backend (server.py)
rem ============================================================

title NSRH HUB - Local Backend

rem Always run from the folder this .bat file lives in, so it
rem works no matter where the project folder is copied to.
cd /d "%~dp0"

echo ============================================================
echo  NSRH HUB - Distributed Object Storage
echo  Starting local backend...
echo ============================================================
echo.

rem Look for a usable Python launcher. Try "py" first (the
rem standard Windows launcher), then fall back to "python".
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set PYCMD=py -3
    goto :found_python
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    set PYCMD=python
    goto :found_python
)

echo.
echo [ERROR] Python was not found on this computer.
echo.
echo NSRH HUB needs Python 3.13 (or a compatible Python 3 version)
echo installed and available on your PATH.
echo.
echo Download Python from:
echo     https://www.python.org/downloads/
echo.
echo During installation, make sure to check the box that says
echo "Add python.exe to PATH", then re-run this file.
echo.
pause
exit /b 1

:found_python
if not exist "%~dp0server.py" (
    echo.
    echo [ERROR] server.py was not found in this folder:
    echo     %~dp0
    echo.
    echo Make sure index.html, server.py, start_nsrh.bat and
    echo README.txt are all in the SAME folder before running this.
    echo.
    pause
    exit /b 1
)

echo Using: %PYCMD%
echo Folder: %~dp0
echo.
echo The server will open your browser automatically.
echo Keep this window open while using NSRH HUB.
echo Press Ctrl+C in this window to stop the server.
echo.

%PYCMD% "%~dp0server.py"

echo.
echo NSRH HUB backend has stopped.
pause
