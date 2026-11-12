@echo off
rem Steve: reviews the drawings in input\Main and saves the results in reports\Main, then shows a box with
rem how it went and opens the results folder. Run Set Up once first on a new computer.
setlocal
cd /d "%~dp0"
title Drawing QA - Steve

if not exist "%~dp0.venv\Scripts\python.exe" (
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('The tool is not set up on this computer yet. Double-click Set Up first.', 'Steve: error', 'OK', 'Error')"
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0steve.py" review
exit /b %ERRORLEVEL%
