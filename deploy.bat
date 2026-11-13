@echo off
rem Daily News Brief - one-click deploy.
rem ASCII-only on purpose: cmd reads .bat with the active OEM code page.
rem UTF-8 Chinese here becomes mojibake, escapes stop working, and the
rem code-page switch can make cmd re-decode its buffered bytes mid-file.
rem Only ONE prompt: the GitHub token. Owner and repo are taken from
rem deploy_target.conf when present, otherwise the built-in default below.
chcp 65001 >nul 2>nul
set "PYTHONIOENCODING=utf-8"
setlocal
cd /d "%~dp0"
echo.
echo  ============================================================
echo   Daily News Brief - Deploy to GitHub - run once only
echo  ============================================================
echo.
echo   Token needs BOTH repo and workflow permissions.
echo   Token is visible on screen. Revoke it in GitHub after use.
echo   This window stays open. Keep it open until SUCCESS appears.
echo.
if not exist "index.html" goto NO_INDEX
if not exist "deploy.py" goto NO_SCRIPT
set "GITHUB_OWNER=lucky-wren"
set "GITHUB_REPO=daily-news-studio"
if exist "deploy_target.conf" (
  for /f "usebackq tokens=1,2 delims==" %%a in ("deploy_target.conf") do (
    if "%%a"=="OWNER" set "GITHUB_OWNER=%%b"
    if "%%a"=="REPO" set "GITHUB_REPO=%%b"
  )
)
echo   Target: %GITHUB_OWNER%/%GITHUB_REPO%
echo   No owner/repo prompt. Change the target in deploy_target.conf only.
echo.
set "GITHUB_TOKEN="
set /p "GITHUB_TOKEN=GitHub Token - repo plus workflow: "
if not defined GITHUB_TOKEN goto CANCELLED
python --version >nul 2>nul
if not errorlevel 1 goto HAVE_PYTHON
py --version >nul 2>nul
if errorlevel 1 goto NO_PYTHON
set "PY=py"
goto RUN
:HAVE_PYTHON
set "PY=python"
goto RUN
:RUN
echo.
echo   Step 1 of 2 - local checklist, no network.
"%PY%" "deploy.py" --dry-run
if errorlevel 1 goto DEPLOY_FAILED
echo.
echo   Step 2 of 2 - uploading now, first run takes 1-3 minutes.
"%PY%" "deploy.py"
if errorlevel 1 goto DEPLOY_FAILED
echo.
echo  ============================================================
echo   SUCCESS - deploy.py finished. Now do these on github.com:
echo   Settings - Pages - Source - GitHub Actions
echo   Actions - Run workflow once - then Ctrl+F5 in the browser.
echo  ============================================================
goto DONE
:DEPLOY_FAILED
echo.
echo   FAILED - deploy.py exited with an error.
echo   Read the ERROR or FAILED lines above. Fix the cause, then rerun.
echo   Do NOT trigger the workflow until all files are uploaded.
goto DONE
:NO_INDEX
echo   ERROR - index.html not found in this folder.
echo   Unzip into a NEW EMPTY folder. index.html must be at top level.
goto DONE
:NO_SCRIPT
echo   ERROR - deploy.py not found in this folder.
echo   Keep deploy.bat and deploy.py together, then run again.
goto DONE
:CANCELLED
echo   Nothing entered. Cancelled.
goto DONE
:NO_PYTHON
echo   ERROR - Python 3 not found.
echo   Install from python.org and tick Add Python to PATH, then rerun.
goto DONE
:DONE
echo.
echo   ---- finished ----
echo   You can close this window now.
pause
