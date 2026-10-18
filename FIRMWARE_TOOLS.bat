@echo off
rem ==================================================================
rem  Camel Guard - firmware tools (menu)
rem   1. Compile only           3. Check the ESP32 over USB
rem   2. Compile + upload       4. Change ports (USB COM / dashboard / discovery)
rem                             5. Install missing Arduino tools
rem  The same functions are in the dashboard: Settings > Firmware & ports.
rem  Uses the arduino-cli inside Arduino IDE 2 - nothing extra to install.
rem  Works from any folder: all paths are relative to this .bat file.
rem ==================================================================
setlocal
chcp 65001 >nul
title Camel Guard - firmware tools
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
echo [ERROR] Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
goto end

:havepy
%PY% "tools\firmware_tool.py" %*

:end
echo.
pause
