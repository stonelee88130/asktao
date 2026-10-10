@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem ============================================================
rem  Asktao automation launcher -- LDPlayer instance #2
rem
rem  Same as start_asktao.bat but bound to a different emulator,
rem  so several instances can run at the same time.
rem
rem  LDPlayer adb port per instance:
rem      instance 1 (first)   -> emulator-5554
rem      instance 2          -> emulator-5556   <- this file
rem      instance 3          -> emulator-5558
rem      instance 4          -> emulator-5560
rem  (i.e. emulator-(5554 + 2*(n-1)))
rem
rem  If you need a 3rd/4th instance, copy this file and change
rem  the SERIAL line below.
rem
rem  Usage: double-click, or
rem      start_asktao_2.bat 1     shimen
rem      start_asktao_2.bat 5     chubao
rem      start_asktao_2.bat 2     arena
rem      start_asktao_2.bat 6     zhuren  (help-people)
rem      start_asktao_2.bat 7     xiulian (auto-teams)
rem      start_asktao_2.bat 4     shuadao
rem
rem  Keep this file ASCII-only (cmd.exe cannot parse UTF-8 Chinese).
rem ============================================================

set "SERIAL=emulator-5556"

set "PYEXE=%~dp0..\App\Python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

set "ARG=%~1"
if /i "%ARG%"=="shimen"  set "ARG=1"
if /i "%ARG%"=="chubao"  set "ARG=5"
if /i "%ARG%"=="arena"   set "ARG=2"
if /i "%ARG%"=="help"    set "ARG=6"
if /i "%ARG%"=="shuadao" set "ARG=4"
if /i "%ARG%"=="zhuren"  set "ARG=6"
if /i "%ARG%"=="xiulian" set "ARG=7"

if "%ARG%"=="" goto menu
"%PYEXE%" "%~dp0start_asktao.py" --serial %SERIAL% %ARG%
goto done

:menu
"%PYEXE%" "%~dp0start_asktao.py" --serial %SERIAL%

:done
echo.
pause
