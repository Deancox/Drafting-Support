@echo off
rem Set Up: installs everything the Drawing QA tool needs on this computer, then shows a box saying it worked
rem (or what went wrong). Double-click it once on a new computer, and again whenever the tool is updated.
rem Installs Python 3.12 if no Python 3.11+ is found, then the requirements into .venv in the project folder.
setlocal
cd /d "%~dp0.."
title Drawing QA - Set Up

set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python 3.11 or newer is not installed. Installing Python 3.12 with winget ...
    winget --version >nul 2>&1 || goto :nopython
    winget install --exact --id Python.Python.3.12 --scope user
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
        "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set PY="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3
    )
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    if not defined PY goto :reopen
)

%PY% "%CD%\Windows\launcher.py" setup %*
exit /b %ERRORLEVEL%

:nopython
call :box "Python is not installed and could not be installed automatically (winget is not available). Install Python 3.12 from python.org (tick 'Add python.exe to PATH'), then run Set Up again."
exit /b 1

:reopen
call :box "Python has been installed. Run Set Up once more to finish."
exit /b 1

:box
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('%~1', 'Set Up: error', 'OK', 'Error')"
exit /b
