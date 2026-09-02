@echo off
setlocal
REM Resolve the repo root from this script's own location, never the caller's
REM working directory. A shortcut may start anywhere; the app resolves .env,
REM data/app.db and data/knowledge relative to the current directory.
set "REPO_ROOT=%~dp0.."
pushd "%REPO_ROOT%"
py src\ui\main.py
popd
endlocal
