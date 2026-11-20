@echo off
setlocal
title HoSub Studio 2.0 (Offline Multilingual Subtitle ^& Flashcard Studio)
cd /d "%~dp0"

REM Try Python launcher (py) first, then python in PATH — zero pip installs or internet needed!
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 hosub.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python hosub.py
    goto :end
)

echo.
echo  [HoSub Studio] Python 3.9+ was not found on PATH.
echo  Please install Python 3 from python.org (check "Add python.exe to PATH")
echo  and run Run_HoSub.bat again. No extra packages or internet connection required!
echo.
pause

:end
if %errorlevel% neq 0 pause
