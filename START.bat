@echo off
title Springboard Auto - Course Automator
color 0A
echo.
echo  ================================================
echo     SPRINGBOARD AUTO - Course Automator
echo  ================================================
echo.
echo  Starting server...
echo  Your browser will open automatically.
echo  DO NOT close this window while automation runs.
echo.

:: Open browser after a short delay
start "" "http://localhost:5000"

:: Start the Flask server (blocks here until you close the window)
set PYTHONIOENCODING=utf-8
python "%~dp0app.py"

pause
