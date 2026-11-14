@echo off
rem TG Study launcher for Windows: double-click. (Re)installs when requirements.txt changes.
cd /d "%~dp0"
set PYV=backend\.venv\Scripts\python.exe
set STAMP=backend\.venv\.requirements-installed
if not exist %PYV% goto setup
fc /b backend\requirements.txt %STAMP% >NUL 2>&1 || goto setup
goto run
:setup
echo Setting up dependencies (a minute or two on first run)...
if exist %PYV% goto install
py -3 -m venv backend\.venv 2>NUL || python -m venv backend\.venv || goto fail
:install
%PYV% -m pip install -q -r backend\requirements.txt || goto fail
copy /y backend\requirements.txt %STAMP% >nul
:run
%PYV% desktop.py
exit /b
:fail
echo Setup failed. See the error above. You need Python 3.11+ from python.org.
pause
exit /b 1
