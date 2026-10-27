@echo off
rem Windows version of bin/dbtw. Runs scripts\lc.py dbt with the Python launcher (py) if present, otherwise python.
setlocal
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% "%~dp0..\scripts\lc.py" dbt %*
exit /b %errorlevel%
