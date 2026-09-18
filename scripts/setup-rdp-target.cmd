@echo off
setlocal

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting Administrator access...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Configuring this PC as a Beenco Remote Desktop target...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable-rdp-private.ps1"
if not "%errorlevel%"=="0" (
  echo.
  echo Setup failed. Review the error above.
  pause
  exit /b 1
)

echo.
echo Setup completed successfully.
echo Run the displayed Test-NetConnection command from a DIFFERENT controlling PC.
pause
