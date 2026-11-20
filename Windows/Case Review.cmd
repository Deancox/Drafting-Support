@echo off
rem Case Review: opens the window to go through a run's review cases and record decisions in the QA register.
rem Run Set Up once first on a new computer.
setlocal
cd /d "%~dp0.."
if not exist "%CD%\.venv\Scripts\pythonw.exe" (
    powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('The tool is not set up on this computer yet. Double-click Set Up first.', 'Case Review: error', 'OK', 'Error')"
    exit /b 1
)
start "" "%CD%\.venv\Scripts\pythonw.exe" -m drawing_qa.viewer
