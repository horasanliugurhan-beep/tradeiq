@echo off
set "TRADEIQ_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%TRADEIQ_PY%" goto bundled
python "%~dp0dashboard.py"
goto finished
:bundled
"%TRADEIQ_PY%" "%~dp0dashboard.py"
:finished
pause
