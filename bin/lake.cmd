@echo off
rem Windows version of bin/lake. Runs scripts\lc.py lake with the Python launcher (py) if present, otherwise python.
setlocal
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% "%~dp0..\scripts\lc.py" lake %*
exit /b %errorlevel%
