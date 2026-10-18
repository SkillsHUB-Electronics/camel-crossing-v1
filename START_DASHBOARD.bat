@echo off
rem ==================================================================
rem  Camel Guard - one-click start
rem   1. Detects the laptop's WiFi (SSID, password, IP) and updates
rem      firmware\camel_guard_esp32\config.h automatically
rem   2. Starts the dashboard server and opens it in the browser
rem   3. Restarts the server when the ports are changed on the
rem      dashboard (Settings > Firmware & ports)
rem  Works from any folder: all paths are relative to this .bat file.
rem ==================================================================
setlocal
chcp 65001 >nul
title Camel Guard Dashboard
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "CG_SUPERVISED=1"

rem ---- find Python 3 ------------------------------------------------
set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
goto nopython

:havepy
rem ---- 1. configure config.h ---------------------------------------
%PY% "tools\setup_config.py"
if errorlevel 1 goto failed

rem ---- 2. start dashboard ------------------------------------------
set "OPENARG=--open"
:runserver
echo.
echo Starting dashboard server...  (close this window or press Ctrl+C to stop it)
echo.
%PY% "dashboard\server.py" %OPENARG%
rem exit code 3 = "restart me with the new ports" (from the dashboard)
if errorlevel 4 goto end
if errorlevel 3 goto restart
goto end

:restart
echo.
echo Restarting the dashboard with the new port settings...
set "OPENARG="
goto runserver

:nopython
echo [ERROR] Python 3 was not found.
echo Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during installation, then run this file again.
goto end

:failed
echo.
echo [ERROR] Configuration failed - see the messages above.

:end
echo.
pause
