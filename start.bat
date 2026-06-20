@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    pythonw voice_input.py
) else (
    python voice_input.py
)
timeout /t 1 /nobreak >nul
wmic process where "commandline like '%%voice_input.py%%' and (name='python.exe' or name='pythonw.exe')" get processid 2>nul | findstr /r "[0-9]" >nul
if errorlevel 1 (
    echo Voice Input failed to start. Showing the error in console mode:
    echo.
    python voice_input.py
    echo.
    pause
)
