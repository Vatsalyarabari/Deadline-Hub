@echo off
REM Builds dist\DeadlineHub.exe on this computer.
cd /d %~dp0
python -m pip install -r requirements-app.txt pyinstaller || goto :fail
python -m PyInstaller --noconfirm packaging\DeadlineHub.spec || goto :fail
echo.
echo Done: %~dp0dist\DeadlineHub.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
