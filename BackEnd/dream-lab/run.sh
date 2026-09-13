#!/usr/bin/env bash
# ============================================================
#  一键启动脚本（macOS / Linux）
#
#  用法：./run.sh
#  第一次运行会自动下载依赖（需要联网，约 1-2 分钟），之后就是秒级启动。
# ============================================================
set -e

cd "$(dirname "$0")"

# ---- 寻找 Java 21 ----
# 本项目需要 Java 21。脚本会按顺序找：JAVA_HOME -> 系统默认 java。
if [ -n "$JAVA_HOME" ]; then
    JAVA_BIN="$JAVA_HOME/bin/java"
else
    JAVA_BIN="java"
fi

if ! "$JAVA_BIN" -version 2>&1 | grep -q 'version "21'; then
    echo ""
    echo "  ⚠️  没找到 Java 21。当前 java 版本是："
    "$JAVA_BIN" -version 2>&1 | head -1
    echo ""
    echo "  安装方法："
    echo "    macOS:   brew install --cask oracle-jdk@21"
    echo "    Windows: https://www.oracle.com/java/technologies/downloads/#jdk21-windows"
    echo ""
    echo "  装好后可以验证一下当前jdk版本：java -version"
    echo "  最后重新运行 run.sh 即可"
    exit 1
fi

mkdir -p data

echo ""
echo "    正在点亮实验室……（第一次会下载依赖，请稍等）"
echo ""

./mvnw -q spring-boot:run
