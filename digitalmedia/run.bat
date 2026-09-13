@echo off
rem Double-click to play. Requires one successful build first.
rem
rem Why this file exists: the game prints Chinese to the console (level results,
rem content/ reload messages, error hints). Double-clicking the exe directly gets
rem you a console in the system codepage -- 936 on Chinese Windows -- which turns
rem that UTF-8 text into mojibake. The chcp line below switches the console to
rem UTF-8 so it matches.
rem
rem KEEP THIS FILE ASCII-ONLY. cmd.exe reads .bat files in the OEM codepage, so
rem non-ASCII bytes (even inside rem comments) get mis-decoded and split lines
rem into garbage commands. build.bat is ASCII for exactly the same reason.
chcp 65001 >nul
cd /d "%~dp0"

if not exist build\dreamlab.exe (
    echo Not built yet -- run build.bat first. It needs VS 2022 or MinGW.
    echo Once it succeeds, build\dreamlab.exe will exist.
    pause
    exit /b 1
)

echo Controls: WASD move, mouse look, E interact, R reload content/, F2 shot, Esc menu.
echo No console hotkey -- walk to the terminal and press E to open it.
echo You start in a pitch-black room -- that is level 0 itself, not a crash.
echo.
build\dreamlab.exe %*

if errorlevel 1 (
    echo.
    echo Exit code was non-zero -- see the message above.
    pause
)
