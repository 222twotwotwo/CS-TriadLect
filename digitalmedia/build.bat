@echo off
rem dreamlab-rt build script (Windows)
rem Compiler auto-detect: existing dev shell > vswhere (Visual Studio) > MinGW g++
setlocal enabledelayedexpansion
cd /d "%~dp0"
if not exist build mkdir build

set "SRCS="
for %%f in (core\*.cpp) do set "SRCS=!SRCS! %%f"
for %%f in (engine\*.cpp) do set "SRCS=!SRCS! %%f"
for %%f in (game\*.cpp) do set "SRCS=!SRCS! %%f"
for %%f in (game\shaders\*.cpp) do set "SRCS=!SRCS! %%f"
for %%f in (game\levels\*.cpp) do set "SRCS=!SRCS! %%f"

if "!SRCS!"=="" (
    echo [build] no source files found
    exit /b 1
)

where cl >nul 2>nul && goto cl_ready

set "VSW=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "!VSW!" (
    for /f "usebackq delims=" %%i in (`"!VSW!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
    if defined VSDIR (
        call "!VSDIR!\VC\Auxiliary\Build\vcvars64.bat" >nul
        where cl >nul 2>nul && goto cl_ready
    )
)

where g++ >nul 2>nul && goto gpp_ready

echo [build] no C++ compiler found. Options:
echo [build]   1) winget install BrechtSanders.WinLibs.POSIX.UCRT
echo [build]   2) Install Visual Studio Build Tools with "Desktop development with C++"
exit /b 1

:cl_ready
echo [build] MSVC
cl /nologo /std:c++17 /O2 /EHsc /W3 /utf-8 /D_CRT_SECURE_NO_WARNINGS /Fo:build/ /Fe:build/dreamlab.exe !SRCS!
if errorlevel 1 (
    echo [build] compile failed
    exit /b 1
)
echo [build] ok: build\dreamlab.exe
exit /b 0

:gpp_ready
echo [build] MinGW g++
rem user32/gdi32: core\platform_win32.cpp 要开窗、画位图；winmm: 调系统定时器粒度（锁帧用）
g++ -std=c++17 -O2 -static -o build/dreamlab.exe !SRCS! -luser32 -lgdi32 -lwinmm
if errorlevel 1 (
    echo [build] compile failed
    exit /b 1
)
echo [build] ok: build\dreamlab.exe
exit /b 0
