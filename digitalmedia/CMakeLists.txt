# dreamlab-rt 的 CMake 构建路径 —— 这是**备路径**，主路径是 build.bat / build.sh。
#
# 为什么要有它：主路径要求本机装了 VS 或 MinGW，而 build.bat 还依赖 vswhere 能
# 探测到工具链。万一探测不到，人就卡死了 —— CMake 是那条退路，也是 macOS /
# Linux 上唯一能走的路。
#
# 用法（在仓库根目录）：
#     cmake -B build-cmake && cmake --build build-cmake
# 产物：build-cmake/dreamlab.exe
#
# ⚠ 构建目录**必须在仓库内**（就用 build-cmake/，.gitignore 已经留好了）。
#    程序启动时会从 exe 所在目录往上找仓库根（见 game/main.cpp 的 lockToRepoRoot），
#    只往上找 3 级；构建目录摆到仓库外面就找不着 assets/ 和 content/ 了。

cmake_minimum_required(VERSION 3.16)

# 只留 Release 一种配置 —— 这一条是为了让上面那两条命令**直接**产出优化过的二进制。
#
# VS 生成器默认是多配置的，`cmake --build build-cmake` 不带 --config 会去构建
# Debug：没有优化，一个软光栅化实时渲染器跑起来会很难看（帧率差好几倍），
# 而"照着 README 敲两条命令"的人不会知道要加 --config Release。
#
# 必须写在 project() **之前** —— 生成器在 project() 那一步就把配置列表定下来了。
# （另一条路是 CMAKE_DEFAULT_BUILD_TYPE，但 VS 生成器明确拒绝这个变量，只认 Ninja Multi-Config。）
set(CMAKE_CONFIGURATION_TYPES Release CACHE STRING "" FORCE)

project(dreamlab-rt CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 单配置生成器（Ninja / Makefiles）看的是 CMAKE_BUILD_TYPE
if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# 产物一律落在构建目录根上，不看生成器脸色（多配置生成器默认会塞进 Release/ 子目录）
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_BINARY_DIR})

# 源文件：递归扫这三个目录。
# 跟的是 build.sh 的 `find`（递归），而不是 build.bat 的 `for`（不递归）—— 将来
# 加个 game/ui/ 之类的子目录，这里不会漏文件。
# CONFIGURE_DEPENDS 让新增 .cpp 在下次构建时自动被扫到（学生加一关不用手动重跑 cmake）。
file(GLOB_RECURSE DREAMLAB_SOURCES CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/core/*.cpp"
     "${CMAKE_CURRENT_SOURCE_DIR}/engine/*.cpp"
     "${CMAKE_CURRENT_SOURCE_DIR}/game/*.cpp")

if(NOT DREAMLAB_SOURCES)
    message(FATAL_ERROR "一个源文件都没找到 —— 请在仓库根目录运行 cmake")
endif()

add_executable(dreamlab ${DREAMLAB_SOURCES})

# 三个平台各一套，照抄各自构建脚本里那条**实测过**的命令行
if(MSVC)
    # /utf-8 是必须的：源码里全是中文，程序也往控制台打中文。
    # user32 / gdi32 / winmm 不用在这里显式链接 —— core/platform_win32.cpp 里有
    # #pragma comment(lib, ...)，而且用 #if defined(_MSC_VER) 包着。
    target_compile_options(dreamlab PRIVATE /utf-8 /W3 /EHsc /D_CRT_SECURE_NO_WARNINGS)
elseif(MINGW)
    # user32/gdi32：开窗、画位图；winmm：调系统定时器粒度（锁帧用）
    target_link_libraries(dreamlab PRIVATE user32 gdi32 winmm)
else()
    # core/raster.cpp 与 core/framebuffer.h 用了 std::thread
    find_package(Threads REQUIRED)
    target_link_libraries(dreamlab PRIVATE Threads::Threads)
endif()

# 注：这里不覆盖优化选项，Release 由 CMake 自己管（MSVC 是 /O2，正好和 build.bat 一致）。
# 全仓库没有用 std::filesystem，所以任何工具链都不需要 -lstdc++fs。
