@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0LaunchSite.ps1" %*
exit /b %ERRORLEVEL%
