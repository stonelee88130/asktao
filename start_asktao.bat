@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem ============================================================
rem  Asktao automation launcher (LDPlayer instance #1)
rem
rem  Double-click -> show the task menu. All Chinese text is printed
rem  by start_asktao.py; this .bat must stay ASCII-only because
rem  cmd.exe garbles UTF-8 Chinese inside a .bat file.
rem
rem  Tasks (2026-09-21). All are OCR-driven, no hardcoded taps:
rem    1  shimen   (shi men task)         needs lvl 15
rem    2  arena    (jing ji chang)        needs lvl 30
rem    5  chubao   (chu bao task)         needs lvl 30
rem    6  zhuren   (zhu ren wei le)       daily
rem    7  xiulian  (xiu lian / xiu xing)  auto-teams itself
rem    4  shuadao  (shua dao / fu mo)     NOT DONE YET
rem    8/9/10  old 2018 scripts           broken, reference only
rem
rem  Aliases: shimen chubao arena help zhuren xiulian shuadao
rem
rem  Second emulator instance: use start_asktao_2.bat (emulator-5556).
rem  Press ESC at any time to stop a running task.
rem
rem  --- changes 2026-09-21 ---
rem  * NPC dialog: a dialog covering the screen used to have its own
rem    lines read as the quest tracker, so the bot clicked the wrong
rem    entry (the "cha xun ri chang tiao zhan ci shu" option) and got
rem    stuck on the random-shimen task. Dialogs are now detected
rem    FIRST and the bracketed option is clicked before anything else.
rem  * level-up popup: the game auto-opens the attribute-points panel
rem    when the character levels up. Its tabs sit inside the tracker
rem    region and made the bot think the quest had disappeared. The
rem    bot now clicks the confirm button to dismiss it -- it never
rem    clicks auto-allocate, that is the player's call.
rem ============================================================

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
"%PYEXE%" "%~dp0start_asktao.py" %ARG%
goto done

:menu
"%PYEXE%" "%~dp0start_asktao.py"

:done
echo.
pause
