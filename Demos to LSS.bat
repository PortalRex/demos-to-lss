@echo off
rem Double-click for a folder picker, or drag a demo folder onto this file.
setlocal
set "SCRIPT=%~dp0demos_to_lss.py"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%SCRIPT%" %*
) else (
    python "%SCRIPT%" %*
)
if errorlevel 1 pause
