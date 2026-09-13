// 窗口后端的 Windows 实现（见 platform.h 里那道边界的说明）。
//
// 为什么是 Win32 + StretchDIBits，而不是 SDL/GLFW/OpenGL：
//   ① 招新的第一要求是"几条命令就能跑起来"。Win32 零依赖：不用装包、不用配 CMake、
//      不用管网盘 —— 学生 clone 下来敲 build.bat 就有窗口。这是设计文档里的硬约束。
//   ② 画面本来就是 CPU 光栅化算出来的，最后只剩"把一块 RGB 内存显示出来"这一步。
//      StretchDIBits 正好干这件事，顺手还把缩放进 GDI 做了（--scale 低配加速就是白拿的）。
//   ③ 自己写窗口这件事本身有教学价值：消息循环、双缓冲、鼠标锁定，是"图形程序到底
//      怎么活起来"的原始材料 —— 这些在 SDL 里是一个函数，在这里是几十行能读完的代码。
#ifdef _WIN32

#include "platform.h"

#include <windows.h>
#include <timeapi.h>  // timeBeginPeriod：把系统定时器粒度从 15.6ms 调到 1ms

#include <cstring>

#if defined(_MSC_VER)
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "winmm.lib")
#endif

namespace dlab {

namespace {

const wchar_t* kClassName = L"DreamLabRTWindow";

// UTF-8 → UTF-16：窗口标题里有中文，SetWindowTextA 会按 ANSI 码页解释，出来是乱码。
std::wstring widen(const std::string& utf8) {
    if (utf8.empty()) return std::wstring();
    const int need = MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), int(utf8.size()), nullptr, 0);
    if (need <= 0) return std::wstring();
    std::wstring w(size_t(need), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), int(utf8.size()), &w[0], need);
    return w;
}

// UTF-16 → UTF-8：反过来那一趟，exe 路径里可能有中文（学生的下载目录很常见）
std::string narrow(const std::wstring& w) {
    if (w.empty()) return std::string();
    const int need = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), int(w.size()), nullptr, 0, nullptr, nullptr);
    if (need <= 0) return std::string();
    std::string s(size_t(need), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), int(w.size()), &s[0], need, nullptr, nullptr);
    return s;
}

Key vkToKey(WPARAM vk) {
    switch (vk) {
        case 'W': return Key::W;
        case 'A': return Key::A;
        case 'S': return Key::S;
        case 'D': return Key::D;
        case 'E': return Key::E;
        case 'R': return Key::R;
        case 'Q': return Key::Q;
        case VK_UP: return Key::Up;
        case VK_DOWN: return Key::Down;
        case VK_LEFT: return Key::Left;
        case VK_RIGHT: return Key::Right;
        case VK_PRIOR: return Key::PageUp;    // PgUp
        case VK_NEXT: return Key::PageDown;   // PgDn
        case VK_F2: return Key::F2;
        case VK_ESCAPE: return Key::Esc;
        case VK_RETURN: return Key::Enter;
        case VK_BACK: return Key::Backspace;
        case VK_OEM_3: return Key::Tilde;  // ` / ~（中文键盘上在 Esc 下面那颗）
        default: return Key::Count;
    }
}

Window* g_window = nullptr;  // 窗口过程是 C 函数指针，只能这样找回当前窗口（一个进程一个窗口）

LRESULT CALLBACK windowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    Window* self = g_window;
    if (self == nullptr) return DefWindowProcW(hwnd, msg, wp, lp);

    switch (msg) {
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        case WM_SIZE:
            if (wp != SIZE_MINIMIZED) self->onResize(int(LOWORD(lp)), int(HIWORD(lp)));
            return 0;
        case WM_KEYDOWN:
        case WM_SYSKEYDOWN: {
            const Key k = vkToKey(wp);
            if (k != Key::Count) {
                FrameInput& in = self->frameInput();
                const int i = int(k);
                if (!in.down[i]) in.pressed[i] = true;  // 按住不放只有第一帧算"按下"
                in.down[i] = true;
                return 0;  // F10/Alt 这类系统键也吃掉，免得弹出边框菜单
            }
            break;
        }
        case WM_KEYUP:
        case WM_SYSKEYUP: {
            const Key k = vkToKey(wp);
            if (k != Key::Count) {
                self->frameInput().down[int(k)] = false;
                return 0;
            }
            break;
        }
        case WM_CHAR:
            // 只放可打印 ASCII 进来：中文输入法的字符在这里就被挡掉，
            // 控制台永远不会出现"半个汉字"或候选框抢焦点的问题（见 console.h 文件头）
            if (wp >= 0x20 && wp < 0x7F) self->frameInput().typed += char(wp & 0xFF);
            return 0;
        case WM_MOUSEMOVE: {
            const float cx = float(int16_t(LOWORD(lp)));
            const float cy = float(int16_t(HIWORD(lp)));
            // 绝对位置单独记一份：菜单要靠它做命中判定（相对位移点不了东西）
            self->frameInput().mouseX = cx;
            self->frameInput().mouseY = cy;
            self->onMouseTo(cx, cy);
            return 0;
        }
        case WM_LBUTTONDOWN:
            // 这一下永远算"点击"（菜单要用），但"顺手抓鼠标"是可关的 ——
            // 菜单/控制台开着时游戏会把它关掉，否则点一下菜单鼠标就被锁走了。
            self->frameInput().mousePressed = true;
            if (self->clickToCapture()) self->setMouseCaptured(true);
            return 0;
        case WM_KILLFOCUS:
            self->onLoseFocus();
            return 0;
        default: break;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

}  // namespace

Window::Window() {
    LARGE_INTEGER f;
    QueryPerformanceFrequency(&f);
    clockFreq_ = f.QuadPart > 0 ? f.QuadPart : 1;
    LARGE_INTEGER n;
    QueryPerformanceCounter(&n);
    clockStart_ = n.QuadPart;
}

Window::~Window() { close(); }

bool Window::open(int w, int h, const std::string& title) {
    if (hwnd_ != nullptr) return true;
    width_ = w > 0 ? w : 1;
    height_ = h > 0 ? h : 1;

    HINSTANCE inst = GetModuleHandleW(nullptr);
    instance_ = inst;
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.style = CS_OWNDC | CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = windowProc;
    wc.hInstance = inst;
    // IDC_ARROW 是 MAKEINTRESOURCE 造的"假指针"，类型上是 LPSTR；
    // 头文件在 UNICODE 下把它期望成 LPCWSTR，所以这里必须显示转一下才对得上。
    wc.hCursor = LoadCursorW(nullptr, reinterpret_cast<LPCWSTR>(IDC_ARROW));
    wc.lpszClassName = kClassName;
    RegisterClassExW(&wc);  // 重复注册会失败，无害

    RECT r = {0, 0, width_, height_};
    AdjustWindowRect(&r, WS_OVERLAPPEDWINDOW, FALSE);  // 让"客户区"正好等于我们要的分辨率
    const std::wstring wideTitle = widen(title);
    HWND hwnd = CreateWindowExW(0, kClassName, wideTitle.c_str(), WS_OVERLAPPEDWINDOW, CW_USEDEFAULT,
                               CW_USEDEFAULT, r.right - r.left, r.bottom - r.top, nullptr, nullptr, inst,
                               nullptr);
    if (hwnd == nullptr) {
        std::fprintf(stderr, "[窗口] CreateWindow 失败（错误码 %lu）\n", GetLastError());
        return false;
    }
    hwnd_ = hwnd;
    g_window = this;
    hdc_ = GetDC(hwnd);
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);
    setMouseCaptured(true);  // 开局就锁鼠标：学生不用先学会"点一下画面"这个规矩

    // 系统定时器粒度默认是 15.6ms 一档：sleep_for(5ms) 会睡到 15.6ms，
    // 想锁 60 帧的主循环就会掉到三十来帧。调到 1ms 才锁得住。
    // （Win10 2004 起这个设置只影响本进程，不再拖累整机；Win7 时代是全局的。）
    timeBeginPeriod(1);
    return true;
}

bool Window::pump() {
    MSG msg;
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
        if (msg.message == WM_QUIT) return false;
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return hwnd_ != nullptr;
}

void Window::present(const std::vector<uint8_t>& rgb, int w, int h) {
    if (hwnd_ == nullptr || w <= 0 || h <= 0 || rgb.size() < size_t(w) * size_t(h) * 3) return;

    if (dibPixels_ == nullptr || dibW_ != w || dibH_ != h) {
        if (dib_ != nullptr) {
            DeleteObject(HBITMAP(dib_));
            dib_ = nullptr;
            dibPixels_ = nullptr;
        }
        BITMAPINFO bmi = {};
        bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bmi.bmiHeader.biWidth = w;
        bmi.bmiHeader.biHeight = -h;  // 负 = 自上而下，和 framebuffer 行序一致，不用翻转
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;  // 32 位对齐：stride 永远是 4 的倍数，不用算行填充
        bmi.bmiHeader.biCompression = BI_RGB;
        void* bits = nullptr;
        HBITMAP bmp = CreateDIBSection(HDC(hdc_), &bmi, DIB_RGB_COLORS, &bits, nullptr, 0);
        if (bmp == nullptr || bits == nullptr) return;
        dib_ = bmp;
        dibPixels_ = static_cast<uint8_t*>(bits);
        dibW_ = w;
        dibH_ = h;
    }

    // RGB → BGRA。这是整条软件渲染路线上唯一的像素搬运，memcpy 级开销：
    // 720p 一帧 3.7 MB，60 FPS 也就 220 MB/s，现代内存随手扛。
    uint8_t* dst = dibPixels_;
    for (size_t i = 0, n = size_t(w) * size_t(h); i < n; ++i) {
        dst[i * 4 + 0] = rgb[i * 3 + 2];
        dst[i * 4 + 1] = rgb[i * 3 + 1];
        dst[i * 4 + 2] = rgb[i * 3 + 0];
        dst[i * 4 + 3] = 255;  // 32 位 DIB 的第 4 字节没人看，但留着免得被当成脏内存
    }

    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = w;
    bmi.bmiHeader.biHeight = -h;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;
    // 放大用最近邻：像素字会变成整齐的方块，而不是糊成一片。
    // GDI 的默认值本来就是 COLORONCOLOR，显式写出来是为了不依赖默认值
    // （全屏是"小分辨率拉伸铺满"，这条直接决定了画面看起来是块状还是糊状）。
    SetStretchBltMode(HDC(hdc_), COLORONCOLOR);
    // 客户区尺寸可能和 framebuffer 不同（--scale、全屏拉伸、或刚切过分辨率）：让 GDI 帮我们缩放
    StretchDIBits(HDC(hdc_), 0, 0, width_, height_, 0, 0, w, h, dibPixels_, &bmi, DIB_RGB_COLORS, SRCCOPY);
}

void Window::close() {
    if (hwnd_ != nullptr) {
        setMouseCaptured(false);
        timeEndPeriod(1);  // 和 open() 里的 timeBeginPeriod(1) 配对，改了要还
        if (dib_ != nullptr) {
            DeleteObject(HBITMAP(dib_));
            dib_ = nullptr;
            dibPixels_ = nullptr;
        }
        if (hdc_ != nullptr) {
            ReleaseDC(HWND(hwnd_), HDC(hdc_));
            hdc_ = nullptr;
        }
        DestroyWindow(HWND(hwnd_));
        hwnd_ = nullptr;
    }
    if (g_window == this) g_window = nullptr;
}

void Window::setMouseCaptured(bool on) {
    if (hwnd_ == nullptr || captured_ == on) return;
    captured_ = on;
    if (on) {
        SetCapture(HWND(hwnd_));
        ShowCursor(FALSE);
        recenterMouse();
    } else {
        ReleaseCapture();
        ShowCursor(TRUE);
    }
}

void Window::onResize(int w, int h) {
    if (w <= 0 || h <= 0 || (w == width_ && h == height_)) return;
    width_ = w;
    height_ = h;
    input_.resized = true;
}

void Window::onMouseTo(float clientX, float clientY) {
    // 没锁鼠标时只记录位置（鼠标是自由的，移动不算视角输入）
    if (!captured_) {
        mouseX_ = clientX;
        mouseY_ = clientY;
        return;
    }
    input_.mouseDX += clientX - mouseX_;
    input_.mouseDY += clientY - mouseY_;
    mouseX_ = clientX;
    mouseY_ = clientY;
    recenterMouse();
}

void Window::onLoseFocus() {
    // 松开所有键 + 放开鼠标：否则切出去再切回来，人还在原地自己往前走
    for (int i = 0; i < kKeyCount; ++i) input_.down[i] = false;
    setMouseCaptured(false);
}

void Window::recenterMouse() {
    if (hwnd_ == nullptr || !captured_) return;
    RECT r;
    GetClientRect(HWND(hwnd_), &r);
    POINT c{(r.right - r.left) / 2, (r.bottom - r.top) / 2};  // 客户区坐标
    // mouseX_/mouseY_ 记的必须是客户区坐标 —— onMouseTo 收到的是 WM_MOUSEMOVE 的
    // 客户区坐标，这两个口径一混，每帧就凭空多出「窗口在屏幕上的位置」那么大的
    // 视角增量，鼠标一动不动画面也会自己狂转。所以先记客户区中心，再换算去挪光标。
    mouseX_ = float(c.x);
    mouseY_ = float(c.y);
    ClientToScreen(HWND(hwnd_), &c);
    SetCursorPos(c.x, c.y);
}

double Window::time() const {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    return double(now.QuadPart - clockStart_) / double(clockFreq_);
}

void Window::setTitle(const std::string& title) {
    if (hwnd_ != nullptr) SetWindowTextW(HWND(hwnd_), widen(title).c_str());
}

bool Window::setWindowMode(WindowMode mode, int w, int h) {
    if (hwnd_ == nullptr) return false;
    HWND hw = HWND(hwnd_);

    // 只改样式，不销毁重建 —— 保住 HWND / DC / DIB / 输入状态，切换不会闪。
    // 三种模式的区别全在"用哪套样式"和"摆多大"上。
    DWORD style = DWORD(GetWindowLongPtrW(hw, GWL_STYLE));
    style &= ~DWORD(WS_OVERLAPPEDWINDOW | WS_POPUP | WS_THICKFRAME | WS_CAPTION | WS_SYSMENU |
                    WS_MINIMIZEBOX | WS_MAXIMIZEBOX);

    if (mode == WindowMode::Windowed) {
        int cw = w > 0 ? w : width_;
        int ch = h > 0 ? h : height_;
        fitWindowToScreen(cw, ch);  // 复用"别开出一扇屏幕装不下的窗"那套逻辑
        RECT r = {0, 0, cw, ch};
        AdjustWindowRect(&r, WS_OVERLAPPEDWINDOW, FALSE);  // 让客户区正好是 cw x ch
        SetWindowLongPtrW(hw, GWL_STYLE, LONG_PTR(style | WS_OVERLAPPEDWINDOW));
        SetWindowPos(hw, nullptr, 0, 0, r.right - r.left, r.bottom - r.top,
                     SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED);
        width_ = cw;
        height_ = ch;
        return true;
    }

    // 无边框窗口与全屏都走 WS_POPUP（没有标题栏、没有边框）
    SetWindowLongPtrW(hw, GWL_STYLE, LONG_PTR(style | WS_POPUP));

    const int screenW = GetSystemMetrics(SM_CXSCREEN);
    const int screenH = GetSystemMetrics(SM_CYSCREEN);
    int x = 0, y = 0, outW = screenW, outH = screenH;

    if (mode == WindowMode::Borderless) {
        outW = w > 0 ? w : width_;
        outH = h > 0 ? h : height_;
        x = (screenW - outW) / 2;
        y = (screenH - outH) / 2;
    }
    // Fullscreen 就是"铺满显示器"—— 用 SM_CXSCREEN 而不是工作区，要连任务栏一起盖住

    SetWindowPos(hw, HWND_TOP, x, y, outW, outH, SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    width_ = outW;
    height_ = outH;
    return true;
}

void fitWindowToScreen(int& w, int& h) {
    RECT wa = {};
    if (!SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0)) return;  // 问不出来就原样放行

    const int availW = wa.right - wa.left;
    const int availH = wa.bottom - wa.top;
    if (availW <= 0 || availH <= 0) return;

    const int maxW = availW * 92 / 100;  // 留一圈边
    const int maxH = availH * 92 / 100;  // 再留标题栏
    if (w <= maxW && h <= maxH) return;

    // 装不下就按 16:9 等比缩，别让标题栏被顶出桌面
    const float kx = float(maxW) / float(w);
    const float ky = float(maxH) / float(h);
    const float k = kx < ky ? kx : ky;
    w = int(float(w) * k);
    h = int(float(h) * k);
    if (w < 640) w = 640;
    if (h < 360) h = 360;
}

std::string executableDir() {
    wchar_t buf[4096];
    const DWORD n = GetModuleFileNameW(nullptr, buf, DWORD(sizeof(buf) / sizeof(buf[0])));
    if (n == 0 || n >= sizeof(buf) / sizeof(buf[0])) return std::string();
    const std::wstring w(buf, n);
    const size_t cut = w.find_last_of(L"\\/");
    if (cut == std::wstring::npos) return std::string();
    return narrow(w.substr(0, cut));
}

bool setCurrentDir(const std::string& dir) {
    if (dir.empty()) return false;
    return SetCurrentDirectoryW(widen(dir).c_str()) != 0;
}

}  // namespace dlab

#else  // 非 Windows：窗口开不出来，但离屏渲染照常 —— 见 platform.h 文件头

#include "platform.h"

namespace dlab {

Window::Window() = default;
Window::~Window() = default;
bool Window::open(int w, int h, const std::string& title) {
    (void)w;
    (void)h;
    (void)title;
    return false;
}
bool Window::pump() { return false; }
void Window::present(const std::vector<uint8_t>& rgb, int w, int h) {
    (void)rgb;
    (void)w;
    (void)h;
}
void Window::close() {}
void Window::setMouseCaptured(bool on) { (void)on; }
void Window::onResize(int w, int h) {
    (void)w;
    (void)h;
}
void Window::onMouseTo(float clientX, float clientY) {
    (void)clientX;
    (void)clientY;
}
void Window::onLoseFocus() {}
void Window::recenterMouse() {}
double Window::time() const { return 0.0; }
void Window::setTitle(const std::string& title) { (void)title; }

bool Window::setWindowMode(WindowMode mode, int w, int h) {
    (void)mode;  // 这边根本开不出窗口，也就无所谓模式
    (void)w;
    (void)h;
    return false;
}

void fitWindowToScreen(int& w, int& h) {
    (void)w;  // 这边开不出窗口，没什么可收的
    (void)h;
}

std::string executableDir() { return std::string(); }

bool setCurrentDir(const std::string& dir) {
    (void)dir;  // 非 Windows 下不折腾工作目录，照旧要求从仓库根跑
    return false;
}

}  // namespace dlab

#endif
