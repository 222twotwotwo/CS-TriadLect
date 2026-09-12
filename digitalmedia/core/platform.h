// 窗口、键盘、鼠标、计时。
//
// 这个头文件是"游戏代码"和"操作系统"之间唯一的那道门：
//   ① 按键用 Key 这套自己的编号，不把 Windows 的 VK_ 码泄进游戏逻辑。
//      将来真要移植，改的是 platform_win32.cpp，游戏代码一行不动。
//   ② 输入被攒成 FrameInput 结构再交出去 —— 真人按键和 --walk 脚本喂的是
//      同一个结构、同一套 updatePlayer 代码。所以"离屏跑 300 帧模拟往前走"
//      验证过的碰撞，和玩家在窗口里撞到的是同一堵墙。
//
// 非 Windows 平台上这个类会"打开失败"，离屏渲染（--shot）照常能用 ——
// 整个项目的验证手段都是离屏的，窗口只是给学生玩的那一层皮。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace dlab {

// 游戏动作键。控制台的字符输入不走这里（它吃 WM_CHAR 的可打印 ASCII）。
enum class Key {
    W,
    A,
    S,
    D,
    E,
    R,
    Q,         // 看入群二维码（四关全通之后才解锁，见 engine/recruit.h）
    Up,
    Down,
    Left,
    Right,
    PageUp,    // 控制台翻页（面板显示不下的行可以往回看）
    PageDown,
    F2,
    Esc,
    Enter,
    Backspace,
    Tilde,
    Count,
};

constexpr int kKeyCount = int(Key::Count);

struct FrameInput {
    bool down[kKeyCount] = {};     // 按住
    bool pressed[kKeyCount] = {};  // 这一帧刚按下（边沿）
    float mouseDX = 0.0f;          // 本帧鼠标位移，像素
    float mouseDY = 0.0f;
    // 客户区坐标的绝对位置 —— 菜单要靠它做命中判定（相对位移点不了东西）。
    // 注意它是**状态**不是增量，所以 resetFrame() 不清它。
    float mouseX = 0.0f;
    float mouseY = 0.0f;
    bool mousePressed = false;     // 这一帧刚按下左键（边沿，不是"按住"）
    std::string typed;             // 本帧敲进来的可打印 ASCII（控制台输入用）
    bool resized = false;          // 本帧客户区尺寸变过

    void resetFrame() {
        for (int i = 0; i < kKeyCount; ++i) pressed[i] = false;
        mouseDX = 0.0f;
        mouseDY = 0.0f;
        mousePressed = false;
        typed.clear();
        resized = false;
    }
};

// 窗口模式。三种都**不改变渲染分辨率** —— 那是调用方的事（见 game/main.cpp 的菜单接线）。
enum class WindowMode {
    Windowed,    // 普通带边框窗口
    Borderless,  // 无边框窗口（按所选分辨率定尺寸、居中）
    Fullscreen,  // 无边框全屏（铺满显示器，含任务栏区域）
};

class Window {
public:
    Window();
    ~Window();
    Window(const Window&) = delete;
    Window& operator=(const Window&) = delete;

    // 打开一个 w×h 客户区的窗口。失败返回 false（无窗口也能跑离屏渲染）
    bool open(int w, int h, const std::string& title);

    // 换窗口模式 / 尺寸。三种模式都**只改窗口样式，不重建窗口** —— 保住 HWND、DC、
    // DIB 和输入状态，切换不会闪一下。（open() 在已经开过窗之后会直接返回，本来也重建不了。）
    // w/h 是"想要的客户区尺寸"；Fullscreen 时忽略它们，铺满显示器。
    // **不改渲染分辨率** —— 那是调用方的事（全屏是把它拉伸铺满）。
    bool setWindowMode(WindowMode mode, int w, int h);

    // 收消息。返回 false = 用户要关窗（点右上角 X）。
    // Esc 不在这里处理：控制台开着时 Esc 是"关控制台"，关了才是"退出游戏"，
    // 这个判断属于游戏逻辑，不属于窗口。
    bool pump();

    // 把 RGB8 像素推到屏幕上。dib 是内部缓冲，调用方不用管格式
    void present(const std::vector<uint8_t>& rgb, int w, int h);

    void close();

    int width() const { return width_; }
    int height() const { return height_; }
    bool isOpen() const { return hwnd_ != nullptr; }
    const FrameInput& input() const { return input_; }
    void endFrame() { input_.resetFrame(); }

    // 鼠标锁定进窗口（第一人称视角需要）；按 Esc / 失去焦点时自动放开
    void setMouseCaptured(bool on);
    bool mouseCaptured() const { return captured_; }

    // "在窗口里点一下就抓鼠标"（第一人称的老规矩）。
    // **菜单/控制台开着时必须关掉它** —— 否则"点一下菜单"会被当成"点一下画面"：
    // 鼠标当场被锁走、光标还会跳到窗口中心，菜单就点不动了。
    void setClickToCapture(bool on) { clickToCapture_ = on; }
    bool clickToCapture() const { return clickToCapture_; }

    // 秒表：构造时算 0
    double time() const;

    void setTitle(const std::string& title);

    // ---- 下面这几个是"消息 → 状态"的内部入口。它们是 public 只因为窗口回调
    //      是自由函数（窗口过程必须是 C 函数指针，拿不到类的私有权限）。
    //      游戏代码不要调这些，只读 input() 就够了。
    FrameInput& frameInput() { return input_; }
    void onResize(int w, int h);
    void onMouseTo(float clientX, float clientY);
    void onLoseFocus();
    void recenterMouse();

private:
    void* hwnd_ = nullptr;  // HWND（不 include windows.h，别把 win32 头泄出去）
    void* hdc_ = nullptr;
    void* instance_ = nullptr;
    void* dib_ = nullptr;  // DIB 段（present 直接往里拷，省一次内存→内存的转换）
    uint8_t* dibPixels_ = nullptr;
    int dibW_ = 0;
    int dibH_ = 0;
    int width_ = 0;
    int height_ = 0;
    bool captured_ = false;
    bool clickToCapture_ = true;  // 见 setClickToCapture
    float mouseX_ = 0.0f;  // 客户区坐标；锁定鼠标时每帧回中心
    float mouseY_ = 0.0f;
    long long clockStart_ = 0;
    long long clockFreq_ = 1;
    FrameInput input_;
};

// 把想要的窗口尺寸收进"这块屏幕放得下"的范围（装得下就原样返回；真缩了也不小于
// 640x360，免得标题栏被顶出桌面）。
//
// 注意开多大**不由这里决定**：开窗尺寸直接等于渲染分辨率（只有 --scale 才降采样），
// 所以它是个性能参数，由 game/main.cpp 按机器并行度挑一档（见 README §7 约定⑩）。
// 这个函数只管一件事：别开出一扇屏幕装不下的窗。离屏出图不吃它 —— 那边必须严格
// 等于 --width/--height，截图比对脚本指望着。
void fitWindowToScreen(int& w, int& h);

// 本程序可执行文件所在目录（末尾不带分隔符，UTF-8）。取不到时返回空串。
// 用途：双击 exe 时工作目录会是 build/，而程序所有资源路径都是相对仓库根写的，
// 得靠它往上找仓库根（见 game/main.cpp 的 lockToRepoRoot）。
std::string executableDir();

// 换工作目录。成功返回 true。不换目录就没法用相对路径找 assets/ 和 content/。
bool setCurrentDir(const std::string& dir);

}  // namespace dlab
