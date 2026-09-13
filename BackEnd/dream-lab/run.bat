@echo off
REM ============================================================
REM  一键启动脚本（Windows）
REM
REM  用法：双击本文件，或在命令行里运行 run.bat
REM  第一次运行会自动下载依赖（需要联网，约 1-2 分钟）。
REM ============================================================
cd /d "%~dp0"

java -version 2>&1 | findstr /C:"version \"21" >nul
if errorlevel 1 (
    echo.
    echo   安装方法（Windows）：
    echo     到 https://www.oracle.com/java/technologies/downloads/#jdk21-windows 下载 JDK 21
    echo     或到 https://adoptium.net 下载 Temurin 21，安装时勾选 "Set JAVA_HOME variable"
    echo
    echo     装完后重新打开命令行，运行 java -version 确认版本是 21，
    echo     然后重新运行本脚本（run.bat）即可
    echo.
    pause
    exit /b 1
)

if not exist data mkdir data

echo.
echo   正在点亮实验室……（第一次会下载依赖，请稍等）
echo.

call mvnw.cmd -q spring-boot:run
pause
