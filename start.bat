@echo off
setlocal
rem Start the MTSA Cybersecurity Plan GUI and open it in the default browser.
rem The server runs in its own minimized window titled "MTSA CSP server".
rem Close that window to stop it. Binds 127.0.0.1 only.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on PATH.
    pause
    exit /b 1
)

netstat -ano | findstr /r /c:":8765 .*LISTENING" >nul
if not errorlevel 1 (
    echo Server already running on http://127.0.0.1:8765/
    start http://127.0.0.1:8765/
    exit /b 0
)

start /min "MTSA CSP server" cmd /k python serve.py

set tries=0
:wait
netstat -ano | findstr /r /c:":8765 .*LISTENING" >nul
if not errorlevel 1 goto open
set /a tries+=1
if %tries% geq 20 (
    echo Server did not come up. See the "MTSA CSP server" window for the error.
    pause
    exit /b 1
)
rem One-second sleep. timeout.exe is shadowed by coreutils in a bash terminal
rem and refuses to run with stdin redirected; ping has neither problem.
ping -n 2 127.0.0.1 >nul
goto wait

:open
start http://127.0.0.1:8765/
