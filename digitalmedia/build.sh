#!/usr/bin/env bash
# dreamlab-rt 构建脚本（bash：Linux / macOS / Git Bash / WSL）
# Windows 上优先复用 build.bat（自动探测 VS 与 MinGW），避免两套逻辑分叉。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build

if [ -n "${OS:-}" ] && [[ "${OS}" == Windows_NT* ]]; then
    if command -v cmd.exe >/dev/null 2>&1; then
        exec cmd.exe //c build.bat
    fi
fi

SRCS=$(find core engine game -name '*.cpp' 2>/dev/null | sort | tr '\n' ' ')
if [ -z "${SRCS}" ]; then
    echo "[build] 找不到任何源文件"
    exit 1
fi

for cxx in g++ clang++; do
    if command -v "$cxx" >/dev/null 2>&1; then
        echo "[build] $cxx 编译中..."
        # shellcheck disable=SC2086
        "$cxx" -std=c++17 -O2 -pthread -o build/dreamlab ${SRCS}
        echo "[build] 完成 -> build/dreamlab"
        exit 0
    fi
done

echo "[build] 没找到 C++ 编译器。"
echo "        Linux:  sudo apt install g++"
echo "        macOS:  xcode-select --install"
echo "        Windows: 直接运行 build.bat"
exit 1
