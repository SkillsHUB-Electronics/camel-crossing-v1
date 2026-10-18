@echo off
rem ==================================================================
rem  Camel Guard - ESP32 simulator (test the dashboard without hardware)
rem   1. Starts the dashboard in a second window if it is not running
rem   2. Runs dashboard\simulator.py with the scenario you choose
rem  Optional argument: auto, clear, camel, danger or multi
rem                     e.g.  START_SIMULATOR.bat danger
rem  Does NOT change config.h. Works from any folder.
rem  The dashboard port comes from camel_guard.ini (Settings > Firmware & ports).
rem ==================================================================
setlocal
chcp 65001 >nul
title Camel Guard Simulator
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

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
set "CGPORT="
for /f "usebackq delims=" %%p in (`%PY% "tools\project_settings.py" get dashboard`) do set "CGPORT=%%p"
if not defined CGPORT set "CGPORT=5100"
set "URL=http://localhost:%CGPORT%"

rem ---- choose scenario ---------------------------------------------
set "SCENARIO=%~1"
if defined SCENARIO goto checkserver

echo ================================================================
echo  Camel Guard - ESP32 simulator (no hardware needed)
echo ================================================================
echo   1. auto       - tour of every situation            (recommended)
echo   2. clear      - empty road, a car now and then
echo   3. camel      - a camel crosses from side A, later one from side B
echo   4. danger     - a camel crosses while cars come from both sides
echo   5. multi      - three camel crossings at the same time
echo.
choice /c 12345 /n /m "Choose a scenario [1-5]: "
set "N=%errorlevel%"
if "%N%"=="0" set "N=1"
for /f "tokens=%N%" %%s in ("auto clear camel danger multi") do set "SCENARIO=%%s"
if not defined SCENARIO set "SCENARIO=auto"

rem ---- make sure the dashboard is running ---------------------------
:checkserver
call :serverup
if not errorlevel 1 goto serverok

echo.
echo Dashboard is not running - starting it in a new window...
start "Camel Guard Dashboard" cmd /k %PY% dashboard\server.py --open
set /a TRIES=0

:waitserver
ping -n 2 127.0.0.1 >nul
call :serverup
if not errorlevel 1 goto runsim
set /a TRIES+=1
if %TRIES% lss 20 goto waitserver
echo [ERROR] The dashboard server did not start. Check the "Camel Guard Dashboard" window.
goto end

:serverok
echo.
echo Dashboard is already running: %URL%

rem ---- run the simulator -------------------------------------------
:runsim
echo.
echo ================================================================
echo  Scenario : %SCENARIO%
echo  Dashboard: %URL%
echo  - Watch the Live Highway View on the Dashboard page
echo  - Press Ctrl+C here to stop the simulator
echo  - Every simulated crossing shows up as its own card on Overview,
echo    next to any real ESP32 that is online
echo ================================================================
echo.
%PY% -u dashboard\simulator.py --scenario %SCENARIO% --port %CGPORT%
goto end

:nopython
echo [ERROR] Python 3 was not found.
echo Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during installation, then run this file again.
goto end

rem ---- helper: exit code 0 when the dashboard answers ---------------
:serverup
%PY% -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:' + os.environ['CGPORT'] + '/api/state', timeout=1)" >nul 2>&1
exit /b %errorlevel%

:end
echo.
pause
