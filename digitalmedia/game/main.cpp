// dreamlab-rt —— 逐梦创新实验室 · 数媒组
// 主程序：命令行解析 + 场景装配 + 渲染循环。
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <string>
#include <thread>
#include <vector>

#include "../core/font.h"
#include "../core/framebuffer.h"
#include "../core/material.h"
#include "../core/platform.h"
#include "../core/png_write.h"
#include "../core/raster.h"
#include "../core/texture.h"
#include "../engine/console.h"
#include "../engine/content.h"
#include "../engine/mesh.h"
#include "../engine/reload.h"
#include "../engine/menu.h"
#include "../engine/recruit.h"
#include "../engine/save.h"
#include "../engine/settings.h"
#include "../engine/world.h"
#include "level.h"
#include "player.h"
#include "workshop.h"

using namespace dlab;

namespace {

// 空场景的底色：不是纯黑，留一点冷色，让人一眼看出「这是没照到光的暗处」而不是「渲染坏了」
const Vec3 kClearColor{0.012f, 0.014f, 0.02f};

// 开窗模式的标题和常驻提示。提示直接写在画面里而不是只写在 README ——
// 第一次运行的人会先看画面，不会先看文档。
const char* kWindowTitle = "dreamlab-rt —— 数媒组工作室（WASD 走 · 鼠标看 · E 交互 · F2 拍照）";
const char* kWindowHint = "WASD 走 · 鼠标看 · E 交互 · F2 拍照 · Esc 退出";
const char* kAutopilotHint = "自动演示：--walk 正在接管输入（想自己走就别给 --walk）";

// ---------------------------------------------------------------- 命令行

struct Args {
    std::string shot = "build/out.png";  // 截图输出路径
    // 离屏出图的默认分辨率。**不低于 480p** 是硬要求：出图既是验证素材，也是
    // 文档/README 里给人看的图，480x270 那个老默认值太低（当年是为了逐像素比对
    // 跑得快，可脚本一直都显式传尺寸，这个默认值根本没人靠它提速，倒是谁不带
    // 尺寸跑一次 --shot 就拿到一张糊图）。854x480 也是 16:9。
    int width = 854;
    int height = 480;
    bool hasWidth = false;               // 显式给过 --width 就别再替它挑窗口尺寸
    bool hasHeight = false;
    int frames = 1;                      // 离屏模式下运行的帧数
    int threads = 0;                     // 0 = 自动
    float exposure = 1.0f;
    float fovDeg = 0.0f;                 // 0 = 用场景默认
    Vec3 cam{0.0f, 0.0f, 0.0f};
    Vec3 look{0.0f, 0.0f, 0.0f};
    bool hasCam = false;
    bool hasLook = false;
    bool selftest = false;
    bool preview = false;                // 把字模画成终端 ASCII 图
    std::string previewText;
    bool hud = false;                    // 叠一层文字 HUD（验证中文字模进 PNG 的整条链）
    bool qrPreview = false;              // 强行打开入群二维码覆盖层（离屏出图核对这张码用）
    int watchMs = 0;                     // >0 = 截图前先等 content 变化（离屏验证热重载）
    bool console = false;                // 显示控制台面板
    std::vector<std::string> cmds;       // --cmd：进游戏前注入的命令（可重复）
    bool hasShot = false;                // 给了 --shot 就是离屏模式；否则开窗打游戏
    std::string walk;                    // --walk "w:120,d:60,e"：脚本输入（自动演示/验收）
    bool trace = false;                  // 每帧打印玩家位置（离屏验收碰撞用）
    int scale = 1;                       // --scale N：按 1/N 分辨率渲染，窗口放大显示
    int fpsCap = 60;                     // --fpscap N：开窗锁多少帧；0 = 不锁（测性能用）
    bool noclip = false;
    bool reset = false;                  // --reset：删掉存档，从头开始
    int level = -1;                      // --level N：直接站在第 N 关（-1 = 听存档的）
    bool allLevels = false;              // --all-levels：四关依次走一遍做冒烟
    bool autoSolve = false;              // --auto：配合 --all-levels，每关先把解答喂进去
    bool help = false;
};

void printUsage() {
    std::printf(
        "dreamlab-rt —— 逐梦实验室数媒组示例项目\n"
        "\n"
        "不带参数直接运行 = 开窗打游戏：WASD 走，鼠标看，E 交互，F2 拍照，Esc 退出。\n"
        "控制台不是快捷键叫出来的 —— 走到桌子前的终端按 E 才打得开。\n"
        "给了 --shot 就是离屏模式：不开窗，渲完写一张 PNG（脚本、验收、无桌面环境都用这个）。\n"
        "\n"
        "用法: dreamlab [选项]\n"
        "  --shot <路径>      离屏截图输出路径（给了它就一定不开窗）\n"
        "  --width <像素>     渲染宽度 / 窗口客户区宽度（离屏默认 854；开窗按核数自适应）\n"
        "  --height <像素>    渲染高度 / 窗口客户区高度（离屏默认 480；开窗按核数自适应）\n"
        "  --scale <倍数>     按 1/N 分辨率渲染再放大铺满窗口（低配机器用 2 或 3）\n"
        "  --fpscap <帧率>    开窗锁帧（默认 60）；给 0 就不锁 —— 想看看自己机器能跑多快用它\n"
        "  --frames <帧数>    离屏：跑多少帧后出图；开窗：跑够多少帧自动退出（默认 1 / 一直跑）\n"
        "  --threads <数量>   渲染线程数（默认自动）\n"
        "  --exposure <倍数>  曝光（默认 1.0）\n"
        "  --fov <角度>       竖直视场角（默认用场景的 50°）\n"
        "  --cam x,y,z        相机（眼睛）位置（逗号隔开，写成 --cam 1.5,1.6,2 这样）\n"
        "  --look x,y,z       相机看向的点（同上，逗号隔开）\n"
        "  --walk \"w:120,a:60\" 脚本输入：按 w 走 120 帧、再按 a 走 60 帧，e 是「按一下」\n"
        "                     键名 w a s d e，冒号后是帧数（60 帧 = 1 秒）。给了它就忽略键盘\n"
        "  --trace            每帧打印位置 / 朝向（开窗还带 work= 一帧干活的毫秒数、period= 帧间隔）\n"
        "  --noclip           穿墙（调试和拍图用）\n"
        "  --reset            删掉存档，从出生点从头开始（存档在 saved/save.bin）\n"
        "  --level <序号>     直接站在第 N 关（0 = 第 0 关「黑暗」），不看存档\n"
        "  --all-levels       四关依次走一遍做冒烟（每关判一次分，配 --shot 就每关出一张图）\n"
        "  --auto             配 --all-levels：每关先把解答喂进去再判分。第 0 关没有条目 ——\n"
        "                     它的解答是改 kAmbientStrength 再重编译，运行时达成不了\n"
        "  --preview [文本]   把中文字模画成终端 ASCII 图（检查字模是否完好）\n"
        "  --hud              在画面上叠一层文字（验证中文渲染进 PNG）\n"
        "  --qr-preview       强行打开入群二维码覆盖层（离屏核对这张码画得对不对）\n"
        "  --watch <毫秒>     先应用 content/，再等文件变化并重新应用，然后才截图\n"
        "                     （没有窗口也能验证「改数据 → 按 R → 世界变化」）\n"
        "  --console          显示游戏内控制台面板\n"
        "  --cmd \"命令\"       进游戏前注入一条控制台命令（可重复，隐含 --console）\n"
        "  --selftest         运行内置自检\n"
        "  -h, --help         显示本帮助\n");
}

const char* takeValue(int argc, char** argv, int& i, const char* name) {
    if (i + 1 >= argc) {
        std::fprintf(stderr, "[错误] %s 需要一个参数值\n", name);
        std::exit(2);
    }
    return argv[++i];
}

// 解析 "1.5,-2,3" 这种三元组
bool parseVec3(const char* text, Vec3& out) {
    return std::sscanf(text, "%f,%f,%f", &out.x, &out.y, &out.z) == 3;
}

// 三元组写错了就直接报错退出，不悄悄当没给过。
// 教训：以前这里是把解析失败吞进 hasCam=false 的，于是 "--cam 1 2 3"（用空格而不是逗号）
// 会安静地什么都不做 —— 拍出来的图和没给 --cam 一模一样，人只会怀疑"相机参数坏了"。
[[noreturn]] void badVec3(const char* name, const char* text) {
    std::fprintf(stderr, "[错误] %s 需要三个数，用逗号隔开（例：--cam 1.5,1.6,2）；收到的是 \"%s\"\n",
                 name, text);
    std::exit(2);
}

Args parseArgs(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        const std::string s = argv[i];
        if (s == "--shot") {
            a.shot = takeValue(argc, argv, i, "--shot");
            a.hasShot = true;
        } else if (s == "--width") {
            a.width = std::atoi(takeValue(argc, argv, i, "--width"));
            a.hasWidth = true;
        } else if (s == "--height") {
            a.height = std::atoi(takeValue(argc, argv, i, "--height"));
            a.hasHeight = true;
        } else if (s == "--frames") {
            a.frames = std::atoi(takeValue(argc, argv, i, "--frames"));
        } else if (s == "--threads") {
            a.threads = std::atoi(takeValue(argc, argv, i, "--threads"));
        } else if (s == "--exposure") {
            a.exposure = float(std::atof(takeValue(argc, argv, i, "--exposure")));
        } else if (s == "--fov") {
            a.fovDeg = float(std::atof(takeValue(argc, argv, i, "--fov")));
        } else if (s == "--cam") {
            const char* v = takeValue(argc, argv, i, "--cam");
            if (!parseVec3(v, a.cam)) badVec3("--cam", v);
            a.hasCam = true;
        } else if (s == "--look") {
            const char* v = takeValue(argc, argv, i, "--look");
            if (!parseVec3(v, a.look)) badVec3("--look", v);
            a.hasLook = true;
        } else if (s == "--preview") {
            a.preview = true;
            // 文本是可选的；后面跟着的不是选项就当作要预览的文本
            if (i + 1 < argc && argv[i + 1][0] != '-') a.previewText = argv[++i];
        } else if (s == "--hud") {
            a.hud = true;
        } else if (s == "--qr-preview") {
            a.qrPreview = true;
        } else if (s == "--watch") {
            a.watchMs = std::atoi(takeValue(argc, argv, i, "--watch"));
        } else if (s == "--console") {
            a.console = true;
        } else if (s == "--cmd") {
            a.cmds.push_back(takeValue(argc, argv, i, "--cmd"));
            a.console = true;
        } else if (s == "--walk") {
            a.walk = takeValue(argc, argv, i, "--walk");
        } else if (s == "--trace") {
            a.trace = true;
        } else if (s == "--scale") {
            a.scale = std::atoi(takeValue(argc, argv, i, "--scale"));
        } else if (s == "--fpscap") {
            a.fpsCap = std::atoi(takeValue(argc, argv, i, "--fpscap"));
        } else if (s == "--noclip") {
            a.noclip = true;
        } else if (s == "--reset") {
            a.reset = true;
        } else if (s == "--level") {
            a.level = std::atoi(takeValue(argc, argv, i, "--level"));
        } else if (s == "--all-levels") {
            a.allLevels = true;
        } else if (s == "--auto") {
            a.autoSolve = true;
        } else if (s == "--selftest") {
            a.selftest = true;
        } else if (s == "-h" || s == "--help") {
            a.help = true;
        } else {
            std::fprintf(stderr, "[错误] 未知参数: %s（用 --help 查看用法）\n", s.c_str());
            std::exit(2);
        }
    }
    // 开窗又没显式给尺寸时，按机器并行度挑一档 —— 开窗尺寸**直接等于**渲染分辨率
    // （只有 --scale 才降采样，而 --scale 会把中文糊掉），所以它是个性能参数，
    // 不能随手往大了开：给 4 核学生机开 1600x900 就只有十来帧了。
    //
    // 档位照**真实开窗**的实测帧率定的（含推屏 present，含随身补光；4/8 线程是
    // --threads 模拟低配，32 核是开发机）。注意：**离屏的 --shot 数字不能用来定这个**，
    // 它不含推屏那一步，比真实开窗好看两倍多 —— 这个坑 2026-09-12 真踩过，见 README 约定⑩。
    //   960x540  : 4 线程 36 帧 / 8 线程 56 帧 / 32 核 57 帧   ← 唯一稳过 30 帧的底档
    //   1280x720 : 8 线程 37 帧            / 32 核 46 帧
    //   1600x900 : 8 线程 24 帧           / 32 核 41 帧
    // 以前这里沿用了离屏的默认值 480x270 —— 双击 exe 开出来是个邮票大的窗口。
    // 离屏出图不动：截图比对脚本指着精确的 --width/--height。
    if (!a.hasShot && !a.hasWidth && !a.hasHeight) {
        const int hw = a.threads > 0 ? a.threads : int(std::thread::hardware_concurrency());
        const int cores = hw > 0 ? hw : 4;
        if (cores >= 16) {
            a.width = 1600;
            a.height = 900;
        } else if (cores >= 8) {
            a.width = 1280;
            a.height = 720;
        } else {
            a.width = 960;
            a.height = 540;
        }
        fitWindowToScreen(a.width, a.height);  // 屏幕装不下再等比缩
    }
    if (a.width < 1) a.width = 1;
    if (a.height < 1) a.height = 1;
    if (a.frames < 1) a.frames = 1;
    if (a.exposure <= 0.0f) a.exposure = 1.0f;
    if (a.scale < 1) a.scale = 1;
    if (a.scale > 8) a.scale = 8;
    if (a.fpsCap > 1000) a.fpsCap = 1000;
    if (a.fpsCap < 0) a.fpsCap = 0;
    return a;
}

// ---------------------------------------------------------------- 场景

struct Scene {
    World world;
    Workshop workshop;
    Camera camera;
    Player player;  // 玩家才是"人在哪"的真相，camera 每帧从它算出来
};

void buildScene(Scene& s) {
    s.workshop = buildWorkshop(s.world);
    // 出生点：站在屋子中间偏前，面朝桌子和终端（-z）。手感和"推门进来"一致。
    // 低头 0.06 弧度是 M3.1 就定下的取景：桌面、终端、地上的棋盘格都在画面里 ——
    // 视角换成第一人称之后这一点也不能漂，不然"和上一版的图逐像素对得上"就没了。
    s.player.feet = Vec3{0.0f, 0.0f, 4.5f};
    s.player.yaw = 0.0f;
    s.player.pitch = -0.06f;
}

// 眼睛在哪、往哪看 —— 渲染只认 camera，所以走路之后必须同步一次。
// 单独抽出来是为了让"忘了同步"这件事有个唯一的、容易被看见的地方。
void syncCamera(Scene& s) {
    s.camera.position = s.player.eye();
    s.camera.yaw = s.player.yaw;
    s.camera.pitch = s.player.pitch;
    // fovY 不动：它是镜头参数，不是人的姿态
}

// 每帧把整场景重新提交一次（P0 不做场景图缓存，先把管线跑通）
//
// playerGlow：玩家随身的一点微光，跟着眼睛走。只负责一件事 —— 让"全黑的屋子"
// 里看得见脚下的路（第 0 关开局四周纯黑，不然人不知道该往哪走，也不知道自己在动）。
// 它**不进评委画面**：Judge::evaluate 走 render() 时不传这一盏，所以第 0 关的出厂
// 亮度仍然是精确的 0.0000、第 1 关的"灯贡献"仍然是 0.2005 对 0.1650。
//
// 离屏 --shot 默认不给（调用方传 false）：截图是素材和逐像素比对用的，"干净"比
// "像玩家看到的"更要紧；而且评委机位那几张对比图一旦多一圈光斑，计划文档里
// 记录过的画面对不上号。
void renderFrame(Rasterizer& rz, const Scene& s, float timeSeconds, bool playerGlow = false) {
    (void)timeSeconds;
    Light glow;
    const Light* extra = nullptr;
    if (playerGlow) {
        // 强度和半径是照着实拍对比图定的：再亮一档（intensity 5.5 / radius 3.0）
        // 桌椅展台是更清楚了，但"这屋子是黑的"这个前提就淡了，第 0 关过关时的反差也跟着变小。
        glow.position = s.camera.position;
        glow.color = Vec3{1.00f, 0.95f, 0.88f};
        glow.intensity = 2.2f;
        glow.radius = 2.2f;
        extra = &glow;
    }
    s.world.render(rz, s.camera, extra);
}

// ---------------------------------------------------------------- 屏幕上的小提示
// 「按一下 E 没反应」是最容易让人以为程序坏了的事，所以任何一次按键都要有回声。
struct Toast {
    std::string text;
    double until = -1.0;  // 到点自动消失（秒，懒得引第二个时钟，用窗口的）
    void show(const std::string& s, double now, double seconds = 1.8) {
        text = s;
        until = now + seconds;
    }
    bool alive(double now) const { return !text.empty() && now < until; }
};

// ---------------------------------------------------------------- HUD
// 文字直接叠进颜色缓冲，不参与深度测试。颜色是线性 HDR 且最后统一过 ACES，
// 所以"纯白文字"要给 2.2 左右 —— 给 1.0 出来是灰的（渲染顺序决定的，不是 bug）。
// bottomInset：控制台面板占掉的高度。贴底的状态栏得往上让开，不然会被半透明
// 面板盖成一层灰影 —— 两样东西叠在一起，比哪一样单独显示都难读。

// 半透明底板：白墙前面的白字根本读不了，垫一层深色是唯一办法
void blendRect(Framebuffer& fb, int x, int y, int w, int h, Vec3 color, float alpha) {
    for (int gy = 0; gy < h; ++gy)
        for (int gx = 0; gx < w; ++gx) fb.blendPixel(x + gx, y + gy, color, alpha);
}

// 画一个实心三角形（2D，像素坐标）。箭头就是它拼的。
// 3D 的重心坐标填充都在 core/raster.cpp 里写过了，这里是它的二维特例。
void fillTri(Framebuffer& fb, float x0, float y0, float x1, float y1, float x2, float y2, Vec3 c,
             float alpha) {
    const int minX = std::max(0, int(std::min({x0, x1, x2})));
    const int maxX = std::min(fb.width - 1, int(std::max({x0, x1, x2})) + 1);
    const int minY = std::max(0, int(std::min({y0, y1, y2})));
    const int maxY = std::min(fb.height - 1, int(std::max({y0, y1, y2})) + 1);
    const float d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
    if (d > -1e-6f && d < 1e-6f) return;  // 退化成一个点或一条线
    for (int y = minY; y <= maxY; ++y) {
        for (int x = minX; x <= maxX; ++x) {
            const float px = float(x) + 0.5f;
            const float py = float(y) + 0.5f;
            const float w0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / d;
            const float w1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / d;
            const float w2 = 1.0f - w0 - w1;
            if (w0 >= 0.0f && w1 >= 0.0f && w2 >= 0.0f) fb.blendPixel(x, y, c, alpha);
        }
    }
}

// 「本关下一步要动的东西在哪儿」—— 屏幕上指个方向。
//
// 为什么是箭头、不是给那东西描边：描边只在东西**已经进了画面**时才有用，而第 1 关
// 要教的恰恰是"往哪走" —— 吊灯挂在天花板上，多数时候根本不在视野里。箭头说的是
// "往那边转"，比"这东西长这样"早一步，而且不碰任何 3D 渲染的讲究
// （背面剔除、深度偏移那套，之前用描边时踩过）。
//
// 在画面里 → 在它上方顶一个小三角；出了画面 → 夹到边缘、箭头朝外，并写上名字。
// 名字是必须的：光有个三角，零基础的人还是不知道那指的是什么。
void drawTargetArrow(Framebuffer& fb, const Font& font, const Camera& cam, const Vec3& target,
                     const std::string& label) {
    const int W = fb.width;
    const int H = fb.height;
    if (W < 200 || H < 200) return;  // 太小就画不下了，别互相压

    const Mat4 view = cam.view();
    const Mat4 proj = perspective(cam.fovY, float(W) / float(H), 0.05f, 80.0f);
    const Vec4 clip = proj * (view * Vec4(target, 1.0f));
    if (clip.w <= 1e-4f) return;  // 在相机背后：这时说不了方向，先转弯再说

    const float ndcX = clip.x / clip.w;
    const float ndcY = clip.y / clip.w;
    const Vec3 color{0.10f, 1.55f, 1.95f};  // 和终端屏幕、随身微光一个色系
    const float alpha = 0.85f;

    const int sx = int((ndcX * 0.5f + 0.5f) * float(W));
    const int sy = int((0.5f - ndcY * 0.5f) * float(H));

    if (ndcX >= -1.0f && ndcX <= 1.0f && ndcY >= -1.0f && ndcY <= 1.0f) {
        // 已经在画面里：在它上方 30 像素顶一个朝下的小三角，别挡住东西本身
        const float cx = float(sx);
        const float cy = float(sy - 30);
        fillTri(fb, cx, cy + 10.0f, cx - 9.0f, cy - 6.0f, cx + 9.0f, cy - 6.0f, color, alpha);
        return;
    }

    // 出画面了：夹到边缘。上边留得多一点 —— 左上角是关卡目标面板，别压上去。
    const int topMargin = 132;
    const int edge = 46;
    const int cx = std::min(std::max(sx, edge), W - edge);
    const int cy = std::min(std::max(sy, topMargin), H - edge);

    // 箭头朝哪边：看"超出画面"哪个方向更多（按各自半屏折算成像素再比）
    const float overX = (std::fabs(ndcX) - 1.0f) * float(W) * 0.5f;
    const float overY = (std::fabs(ndcY) - 1.0f) * float(H) * 0.5f;
    if (overX > overY) {
        if (ndcX > 0.0f) {
            fillTri(fb, float(cx + 13), float(cy), float(cx - 4), float(cy - 10),
                    float(cx - 4), float(cy + 10), color, alpha);  // 朝右
        } else {
            fillTri(fb, float(cx - 13), float(cy), float(cx + 4), float(cy - 10),
                    float(cx + 4), float(cy + 10), color, alpha);  // 朝左
        }
    } else {
        if (ndcY > 0.0f) {
            fillTri(fb, float(cx), float(cy - 13), float(cx - 10), float(cy + 4),
                    float(cx + 10), float(cy + 4), color, alpha);  // 朝上
        } else {
            fillTri(fb, float(cx), float(cy + 13), float(cx - 10), float(cy - 4),
                    float(cx + 10), float(cy - 4), color, alpha);  // 朝下
        }
    }

    if (!label.empty()) {
        const int tw = font.measureLine(label);
        const int tx = std::min(std::max(cx - tw / 2, 4), std::max(4, W - tw - 4));
        const int ty = std::min(std::max(cy + 17, 4), std::max(4, H - font.lineHeight() - 4));
        blendRect(fb, tx - 5, ty - 3, tw + 10, font.lineHeight() + 6, Vec3{0.02f, 0.03f, 0.05f},
                  0.75f);
        font.drawLine(fb, tx, ty, label, color, 1.0f);
    }
}

// 开局那张引导卡。**写给零基础的人**。
//
// 为什么光靠 HUD 上的目标面板不够：目标写的是**终态**（"让房间重新亮起来"），
// 不是"你现在该按哪个键"。一个没玩过第一人称游戏的人，进来看见一片全黑，
// 第一反应是"是不是坏了"，而不是"我该往前走"。这张卡补的就是那一步。
//
// 几秒后自己淡掉（alpha 从 1 到 0），不挡路 —— 看懂了的人不会被它烦到。
void drawGuideCard(Framebuffer& fb, const Font& font, float alpha) {
    if (alpha <= 0.01f) return;
    const Vec3 white{2.2f, 2.2f, 2.25f};
    const Vec3 dim{1.6f, 1.6f, 1.65f};
    const Vec3 key{2.10f, 1.95f, 1.25f};  // 和 HUD 那句"下一步"一个颜色：都是"该你动了"
    const Vec3 panel{0.02f, 0.025f, 0.04f};

    const char* l1 = "这是全黑的 —— 第 0 关就是让你自己把灯打开。";
    const char* l2 = "WASD 走 · 鼠标转头 · 走到东西跟前按 E";
    // 说"正前方"是错的：这条提示出现的时候玩家可能已经转过头了（看一眼就转走了）。
    // 所以只说"黑暗里唯一亮着的那块屏幕"—— 不管朝哪边都指得到。
    const char* l3 = "黑暗里只有一块发亮的屏幕，那就是终端 —— 走过去看着它按 E。";

    const int pad = 10;
    int textW = font.measureLine(l1);
    textW = std::max(textW, font.measureLine(l2));
    textW = std::max(textW, font.measureLine(l3));
    const int lineH = font.lineHeight();
    const int boxW = textW + pad * 2;
    const int boxH = lineH * 3 + pad * 2;
    const int x = (fb.width - boxW) / 2;
    // 放在偏上一点：中间是准星，底下是操作提示行，都不挤
    const int y = std::max(16, fb.height / 4);

    blendRect(fb, x, y, boxW, boxH, panel, 0.82f * alpha);
    int ty = y + pad;
    font.drawLine(fb, x + pad, ty, l1, white, alpha);
    ty += lineH;
    font.drawLine(fb, x + pad, ty, l2, key, alpha);
    ty += lineH;
    font.drawLine(fb, x + pad, ty, l3, dim, alpha);
}

// 左上角那块「你现在该干什么」。进度来自评委机位那张小图 —— 和玩家站在哪、
// 朝哪看都无关（见 game/level.h 的开头）。
// 没有关卡（title 为空）时整块不画，画面和上一版逐像素一致。
struct GoalPanel {
    std::string title;
    std::string goal;
    float progress = 0.0f;
    bool passed = false;
    std::string nextStep;  // 空 = 不显示这一行（见 nextStepText）
};

// 「下一步该干嘛」。**只看进度，不看是哪一关** —— 这样它永远是真的，
// 也不用给每关每阶段维护一份文案。
// 为什么需要它：零基础的人看着"目标：让房间重新亮起来"是不知道该动什么的 ——
// 目标说的是"终态"，不是"下一个动作"。这一行补的就是那个动作。
// 把**客户区**坐标换算到 **framebuffer** 坐标。
//
// 为什么需要它：鼠标坐标是客户区的，而菜单（和所有 UI）画在 framebuffer 上。
// 平时这两者相等（窗口尺寸 = 渲染分辨率），一但窗口被拉大 —— 最大化、拖边框、
// 或者 `--scale` 让推屏把画面放大铺满 —— 就不是一回事了。拿客户区坐标直接去和
// framebuffer 上的矩形比，只有左上角那一块能点中，看着就是"菜单大部分按不了"。
//
// 抽成纯函数是为了能自检：这条逻辑本身是纯算术，不该靠"开个窗点一下"来验。
void mouseToFramebuffer(float& mx, float& my, int clientW, int clientH, int fbW, int fbH) {
    if (clientW <= 0 || clientH <= 0 || fbW <= 0 || fbH <= 0) return;
    mx *= float(fbW) / float(clientW);
    my *= float(fbH) / float(clientH);
}

// 把"当前世界的状态"装成关卡要的那个视图 —— 关卡自己算下一步要用它。
LevelView makeView(const World& w, const Judge& judge, const LevelStatus& st) {
    return LevelView{w, judge.frame(), st.luminance, st.lightLuminance};
}

// 「下一步该干什么」—— **由关卡自己算**（见 Level::nextStep）。
//
// 以前这里是"只看进度"：0% 说去终端、有进度就说"照着终端说的改"。太粗 ——
// 进度条说"走到 40% 了"，可那 40% 是"三件事里的哪一件"？只有关卡自己答得上来，
// 它本来就拆过判定（灯罩亮没亮 / 位置对不对 / 亮度够不够）。
std::string nextStepFor(const Level& lv, const LevelView& view, bool passed) {
    if (lv.nextStep != nullptr) {
        const std::string t = lv.nextStep(view).text;
        if (!t.empty()) return t;
    }
    // 这一关没什么"下一步"可说了（做完了）—— **但别留一片空白**。
    // 会走到这儿的情形：用 level 跳回一个早过了的关卡。以前那种情况下 HUD 上
    // "下一步"那一行整个消失，人只看到一个 100% 的进度条，不知道该干嘛。
    if (passed) {
        return "这一关已经过了 —— 想继续玩：改 content/*.txt 自己配色，或者敲 level 换一关";
    }
    return std::string();
}

void drawGoalPanel(Framebuffer& fb, const Font& font, int x, int y, const GoalPanel& goal) {
    if (goal.title.empty()) return;
    const Vec3 white{2.2f, 2.2f, 2.25f};
    const Vec3 dim{1.5f, 1.5f, 1.55f};
    const Vec3 panel{0.02f, 0.025f, 0.04f};
    const Vec3 barBack{0.16f, 0.18f, 0.22f};
    const Vec3 barDone{2.60f, 2.00f, 0.75f};  // 过关 = 琥珀色，和终端说"成功"一个颜色
    const Vec3 barWork{0.55f, 1.55f, 1.30f};
    const int pad = 6;
    const int barW = 150, barH = 8;

    char pct[32];
    // passed 是从 progress 推出来的，这里只喂 progress 就够（见 level.h 的 progressPercent）
    std::snprintf(pct, sizeof(pct), "%d%%", progressPercent(LevelStatus{goal.progress, 0.0f}));
    const std::string goalLine = std::string("目标：") + goal.goal;
    // "下一步"用亮一点的颜色：它是这一屏里唯一一条"你现在该做什么"，
    // 零基础的人第一眼要抓到的是它，不是目标。
    const Vec3 stepColor{1.9f, 1.75f, 1.15f};
    const std::string stepLine =
        goal.nextStep.empty() ? std::string() : std::string("下一步：") + goal.nextStep;
    const bool hasStep = !stepLine.empty();

    int textW = std::max(font.measureLine(goalLine), font.measureLine(goal.title));
    if (hasStep) textW = std::max(textW, font.measureLine(stepLine));
    const int rowW = std::max(textW, barW + 12 + font.measureLine(pct)) + pad * 2;
    const int rowH = font.lineHeight() * (hasStep ? 4 : 3);
    blendRect(fb, x, y, rowW, rowH, panel, 0.68f);

    int ty = y + pad / 2;
    font.drawLine(fb, x + pad, ty, goal.title, white, 1.0f, 1);
    ty += font.lineHeight();
    font.drawLine(fb, x + pad, ty, goalLine, dim, 1.0f, 1);
    ty += font.lineHeight();
    if (hasStep) {
        font.drawLine(fb, x + pad, ty, stepLine, stepColor, 1.0f, 1);
        ty += font.lineHeight();
    }

    // 进度条：外面一圈描边 + 里面按比例填。用矩形而不是字符块画 ——
    // 字模里没有「█」这种方块字，硬画会变成豆腐块。
    const int barY = ty + (font.glyphH() - barH) / 2;
    blendRect(fb, x + pad - 1, barY - 1, barW + 2, barH + 2, barBack, 0.85f);
    blendRect(fb, x + pad, barY, barW, barH, Vec3{0.01f, 0.012f, 0.02f}, 1.0f);
    const int fill = int(goal.progress * float(barW) + 0.5f);
    if (fill > 0) blendRect(fb, x + pad, barY, fill, barH, goal.passed ? barDone : barWork, 1.0f);
    font.drawLine(fb, x + pad + barW + 12, ty, pct, goal.passed ? barDone : dim, 1.0f, 1);
}

// 四关全通之后才解锁的那张图：入群二维码。
//
// 画法上只有一个讲究，但它决定这张码**能不能扫出来**：按**整数倍**放大。
// 每格 module 正好是 N×N 个屏幕像素，格线落在像素边界上，边缘绝对干净。
// 按任意比例缩（或者开双线性）会让格线糊在半个像素上，手机就认不出来了 ——
// 这也是 tools/gen_recruit_qr.py 把素材存成"一格一个 bit"的全部理由。
//
// 黑和白都**覆盖写**（alpha = 1.0），不和背景混合：二维码靠对比度吃饭，
// 后面那间屋子是亮是暗都不该影响它。过完色调映射大约 0 对 248。
void drawRecruitQr(Framebuffer& fb, const Font& font, const RecruitQr& qr, bool loaded) {
    const Vec3 ink{0.0f, 0.0f, 0.0f};
    const Vec3 paper{2.6f, 2.6f, 2.6f};
    const Vec3 dim{1.15f, 1.15f, 1.20f};
    const Vec3 strong{2.6f, 2.6f, 2.7f};

    for (int y = 0; y < fb.height; ++y)
        for (int x = 0; x < fb.width; ++x) fb.blendPixel(x, y, ink, 0.88f);

    const int lh = font.lineHeight();
    const int topH = lh * 2 + 24;  // 标题那一块
    const int botH = lh * 2 + 20;  // 底部两行说明
    const int quiet = 4;           // 静默区：二维码规范要求四周留 4 格白边，缺了有些读取器不认

    int scale = 0;
    if (loaded) {
        const int availW = fb.width - 48;
        const int availH = fb.height - topH - botH;
        const int sw = availW / (qr.modulesW + quiet * 2);
        const int sh = availH / (qr.modulesH + quiet * 2);
        scale = sw < sh ? sw : sh;
        if (scale < 1) scale = 1;  // 窗口小到放不下也按"一格一个像素"画，总比不画好
    }

    auto center = [&](const std::string& s, int y, Vec3 c, int sc) {
        font.drawLine(fb, (fb.width - font.measureLine(s) * sc) / 2, y, s, c, 1.0f, sc);
    };

    if (loaded) {
        const int plate = (qr.modulesW + quiet * 2) * scale;
        const int px = (fb.width - plate) / 2;
        const int py = topH + (fb.height - topH - botH - plate) / 2;
        for (int y = 0; y < plate; ++y)
            for (int x = 0; x < plate; ++x) fb.setPixel(px + x, py + y, paper);
        const int qx = px + quiet * scale;
        const int qy = py + quiet * scale;
        for (int r = 0; r < qr.modulesH; ++r) {
            for (int c = 0; c < qr.modulesW; ++c) {
                if (!qr.dark(c, r)) continue;
                for (int dy = 0; dy < scale; ++dy)
                    for (int dx = 0; dx < scale; ++dx)
                        fb.setPixel(qx + c * scale + dx, qy + r * scale + dy, ink);
            }
        }
        center("逐梦 26 数媒组 · 入群二维码", 26, strong, 2);
        center("手机微信扫一扫，进数媒组的招新群", fb.height - botH + 4, dim, 1);
        center("按 Q 或 Esc 关掉", fb.height - lh - 14, dim, 1);
    } else {
        // 素材没读出来：把"什么坏了、怎么修"写在脸上，别让人对着一片黑猜
        center("入群二维码的素材没读出来", fb.height / 2 - lh * 2, strong, 2);
        center("跑这一条重新生成：", fb.height / 2 + lh, dim, 1);
        center("python tools/gen_recruit_qr.py --src <二维码图>", fb.height / 2 + lh * 2, dim, 1);
        center("按 Q 或 Esc 关掉", fb.height - lh - 14, dim, 1);
    }
}

void drawHud(Framebuffer& fb, const Font& font, float fps, int bottomInset = 0,
             const std::string& hint = "", const GoalPanel& goal = GoalPanel{}) {
    const Vec3 white{2.2f, 2.2f, 2.25f};
    const Vec3 dim{1.5f, 1.5f, 1.55f};
    const Vec3 panel{0.02f, 0.025f, 0.04f};
    const int pad = 6;

    // 标题：2 倍字号，验证中文放大后依然是干净的像素字
    const std::string title = "数媒组工作室";
    const int titleW = font.measureLine(title) * 2;
    blendRect(fb, 8, 8, titleW + pad * 2, font.glyphH() * 2 + pad * 2, panel, 0.68f);
    font.drawLine(fb, 8 + pad, 8 + pad, title, white, 1.0f, 2);

    // 关卡目标就贴在标题下面：进门第一眼要看见"我该干什么"，而不是自己找
    drawGoalPanel(fb, font, 8, 8 + font.glyphH() * 2 + pad * 2 + 4, goal);

    // 状态行：1 倍字号，中文 + 拉丁 + 数字混排，顺便验证比例字距
    char stats[96];
    std::snprintf(stats, sizeof(stats), "DreamLab 2026 · %.0f FPS · %dx%d", double(fps), fb.width, fb.height);
    const int statsW = font.measureLine(stats);
    const int statsY = fb.height - bottomInset - font.glyphH() - pad - 8;
    blendRect(fb, 8, statsY - pad, statsW + pad * 2, font.glyphH() + pad * 2, panel, 0.68f);
    font.drawLine(fb, 8 + pad, statsY, stats, dim, 1.0f, 1);

    // 操作提示（只有开窗模式才给）：告诉人这台机器能按哪些键。
    // 不写这一行，第一次运行的人只会盯着画面发呆 —— 按键是唯一需要"教"的东西。
    if (!hint.empty()) {
        const int hintW = font.measureLine(hint);
        const int hintY = statsY - font.glyphH() - pad * 2 - 4;
        blendRect(fb, 8, hintY - pad, hintW + pad * 2, font.glyphH() + pad * 2, panel, 0.68f);
        font.drawLine(fb, 8 + pad, hintY, hint, white, 1.0f, 1);
    }
}

// 准星 + 「你正看着什么」+ 一闪而过的提示。只在开窗模式画：
// --shot 出的图是拿去当素材/验收的，不该在上面烧一个十字。
void drawCrosshair(Framebuffer& fb, const Font& font, const std::string& prompt,
                   const std::string& toast) {
    const Vec3 ink{2.10f, 2.12f, 2.16f};
    const int cx = fb.width / 2;
    const int cy = fb.height / 2;
    const int arm = 6, gap = 3;
    for (int i = gap; i <= gap + arm; ++i) {
        fb.blendPixel(cx - i, cy, ink, 0.70f);
        fb.blendPixel(cx + i, cy, ink, 0.70f);
        fb.blendPixel(cx, cy - i, ink, 0.70f);
        fb.blendPixel(cx, cy + i, ink, 0.70f);
    }
    fb.blendPixel(cx, cy, ink, 0.90f);

    // 有可交互的东西就显示它的 prompt（文字来自实体本身，不是这里写死的）；
    // 没有就显示临时消息（「按了没反应」的那些回声）。
    const std::string text = !prompt.empty() ? prompt : toast;
    if (text.empty()) return;
    const Vec3 color = !prompt.empty() ? Vec3{2.35f, 2.32f, 2.30f} : Vec3{2.60f, 2.00f, 0.75f};
    const int w = font.measureLine(text);
    const int x = cx - w / 2;
    const int y = cy + 24;
    blendRect(fb, x - 6, y - 4, w + 12, font.glyphH() + 8, Vec3{0.014f, 0.017f, 0.026f}, 0.72f);
    font.drawLine(fb, x, y, text, color, 1.0f, 1);
}

// ---------------------------------------------------------------- 控制台命令
// 命令放这里而不是 console.h：知道"世界"的东西不该塞进"终端"里。
// 每条命令只做一件小事、回一行中文 —— 命令和结果之间没有黑箱，这是给学生看的。
// 命令本身全是 ASCII，不用切输入法。

std::vector<std::string> splitTokens(const std::string& line) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : line) {
        if (c == ' ' || c == '\t') {
            if (!cur.empty()) {
                out.push_back(cur);
                cur.clear();
            }
        } else {
            cur += c;
        }
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

std::string joinFloats(const std::vector<float>& v) {
    char buf[64];
    std::string out;
    for (size_t i = 0; i < v.size(); ++i) {
        std::snprintf(buf, sizeof(buf), "%s%.4g", i ? " " : "", double(v[i]));
        out += buf;
    }
    return out;
}

std::string joinNames(const std::vector<std::string>& v) {
    std::string out;
    for (size_t i = 0; i < v.size(); ++i) out += (i ? " " : "") + v[i];
    return out;
}

// 当前这一关的目标、提示、进度。终端（走到它面前按 E）和 level 命令
// 打印的是同一段话 —— 学生从哪条路问进来，看到的答案都一样。
// 打印顺序按"读下去"的顺序：标题 → 目标 → 提示 → 进度。
// 最后一行是进度，因为控制台只显示最近的几行，这样它一定留在眼前。
void printLevelBriefing(Console& con, const LevelRuntime& rt, const World& w, const Judge& judge) {
    const Level& lv = levelAt(rt.index);
    char buf[192];
    con.printOk(lv.title);
    std::snprintf(buf, sizeof(buf), "目标：%s", lv.goal);
    con.print(buf);
    // hint 允许写成多行（用 '\n' 分行）。控制台是"一次 print = 一行"、不会自己拆，
    // 所以这里手工拆开。为什么值得拆：零基础的人要靠这段文字动手，
    // 编号步骤的排版比一大段长句子有用得多（长句子会被按宽度硬折，折在哪全看运气）。
    {
        const std::string& h = lv.hint;
        size_t start = 0;
        for (size_t i = 0; i <= h.size(); ++i) {
            if (i == h.size() || h[i] == '\n') {
                con.print(h.substr(start, i - start));
                start = i + 1;
            }
        }
    }
    // 学生只需要看到百分比。后面那串"评委机位亮度 xxx，其中灯贡献 xxx"是**我们定达标线
    // 用的内部数字** —— 摆在这里既没用又吓人（"评委会是什么？"），拿掉。
    // 要看详细数字：用 --trace，或者看离屏那行 [关卡] 输出（那是给开发看的）。
    if (rt.status.passed()) {
        std::snprintf(buf, sizeof(buf), "已经过关了");
    } else {
        std::snprintf(buf, sizeof(buf), "现在 %d%%", progressPercent(rt.status));
    }
    con.print(buf);

    // 「下一步」—— 和 HUD 上那行是**同一个来源**（关卡自己算的），两处说法不会打架。
    // 为什么终端也要念：零基础的人看完题面还是不知道"我现在该干嘛"，而题面写的是
    // 整件事的做法，不是"你卡在哪一步"。这一行补的就是那个 —— 世界一变它当场就变。
    const std::string step = nextStepFor(lv, makeView(w, judge, rt.status), rt.status.passed());
    if (!step.empty()) {
        con.printOk("下一步：");
        con.print("  " + step);
    }
}

// 过关之后往下走：推进到下一关，并把下一关的题面念出来。到了最后一关就说"四关都过了"。
//
// **不喊"你过了"** —— 喊不喊是另一件事（只有"头一回过"才喊）。两处过关收尾共用它：
// 开场就发现已经过关、和循环里当场过关。上一轮就是没共用，两条路各写了一半 ——
// "开场那条只说一句、不推进"，人卡在已完成的房间里没有指引（实测撞到过）。
// 返回值 = **是不是四关全通了**。调用方靠它决定要不要放下面的入群二维码 ——
// 这个判断只能有一个出处，散在两处迟早会有一处忘了改。
bool advanceAfterPass(Console& con, World& world, Judge& judge, LevelRuntime& rt) {
    if (rt.index + 1 >= levelCount()) {
        con.printOk("四关都过了 —— 整条管线你已经亲手走通一遍了。");
        con.print("想继续玩：改 content/*.txt 自己配色，或者读 core/raster.cpp"
                  "（那 300 行是引擎的心脏）。");
        return true;
    }
    rt.index += 1;
    // 推进之后马上重判一次：题面、HUD 上的进度条都指着 rt.status，
    // 不重判的话会有一帧"标题是下一关、进度条还是上一关的 100%"。
    rt.status = judge.evaluate(world, levelAt(rt.index));
    con.print("下面是下一关 —— 想重看哪一关的要求，敲 level（或走到终端按 E）。");
    printLevelBriefing(con, rt, world, judge);
    return false;
}

void runCommand(const std::string& line, Console& con, World& world, ContentWatcher& watcher,
                const std::function<void()>& takeShot, const std::function<bool()>& showRecruit,
                Judge& judge, LevelRuntime& rt) {
    const std::vector<std::string> t = splitTokens(line);
    if (t.empty()) return;
    const std::string& cmd = t[0];

    if (cmd == "help") {
        con.print("命令（都是 ASCII，不用切输入法）：");
        con.print("  help                 这份帮助");
        con.print("  cls                  清屏");
        con.print("  ambient <亮度|r g b> 环境光，例如 ambient 0.1 或 ambient 0.3 0.4 0.6");
        con.print("  light                列出每盏灯的位置和强度");
        con.print("  light <强度>         主光，例如 light 60");
        con.print("  light <灯号> <强度>  指定某一盏，例如 light 0 60 / light 1 30");
        con.print("                       （灯号就是 content/lights.txt 里的编号）");
        con.print("  inspect [材质|物体]  看材质参数，例如 inspect plastic / inspect 地板");
        con.print("                       （带贴图的材质还会列出平铺次数 / 寻址 / 滤波）");
        con.print("                       （不给名字就列出全部材质）");
        con.print("  look <物体>          看物体的铭牌，例如 look lamp / look board");
        con.print("  reload               重新读 content/ 的数据文件（= 按 R）");
        con.print("  level                这一关要你干什么、现在做到哪了");
        con.print("  level <关号>         跳到某一关，例如 level 0");
        con.print("  use terminal         同上（走到桌子前的终端按 E 走的就是这条命令）");
        con.print("  shot                 现在存一张干净的 PNG（不含面板）到 shots/");
        con.print("  qr                   看入群二维码（四关全通之后才解锁）");
        con.print("小提示：改 content/*.txt 再敲 reload，比敲命令更接近「做美术」这件事。");
        return;
    }

    if (cmd == "level") {
        // level <关号>：直接跳关。宣讲现场"从哪儿开始看"、验收某一关、
        // 学生自己回头重看第 0 关，都靠它 —— 存档里的进度不会被这一步改掉。
        int want = -1;
        if (t.size() >= 2 && detail::parseInt(t[1], want)) {
            if (want < 0 || want >= levelCount()) {
                con.printError("总共只有 " + std::to_string(levelCount()) + " 关（0 ~ " +
                               std::to_string(levelCount() - 1) + "），没有第 " + std::to_string(want) + " 关");
                return;
            }
            rt.index = want;
            // 立刻重判一次：不重判的话，下面那段开场白念的是上一关的进度
            rt.status = judge.evaluate(world, levelAt(rt.index));
            con.printOk("跳到" + std::string(levelAt(rt.index).title));
        }
        printLevelBriefing(con, rt, world, judge);
        return;
    }

    if (cmd == "use") {
        // 现在能"用"的东西只有终端 —— 它是关卡系统的公告板。所以两条命令
        // 打印同一段话：E 键那条路是"跟终端说话"，level 是手动查。
        printLevelBriefing(con, rt, world, judge);
        return;
    }

    if (cmd == "cls") {
        con.clear();
        return;
    }

    if (cmd == "ambient") {
        std::vector<float> v;
        for (size_t i = 1; i < t.size(); ++i) {
            float f = 0.0f;
            if (detail::parseNumber(t[i], f)) v.push_back(f);
        }
        if (v.size() == 1) v = {v[0], v[0], v[0]};
        if (v.size() != 3) {
            con.printError("用法：ambient <亮度> 或 ambient <r> <g> <b>，例如 ambient 0.1");
            return;
        }
        world.ambient = Vec3{v[0], v[1], v[2]};
        con.printOk("环境光 = " + joinFloats(v) + "（按 R 或用文件里的值会把它盖回去）");
        return;
    }

    if (cmd == "light") {
        int idx = 0;
        float value = 0.0f;
        if (t.size() == 1) {
            // 不带参数 = 把每盏灯现在是几号、在哪儿、多亮念一遍。
            // 学生改完 content/lights.txt 按 R，想核对"游戏里到底成了什么样"，
            // 不用去猜、也不用翻文件 —— 编号就是文件里的 light <编号>。
            con.print("场景里的灯（编号 = content/lights.txt 里的 light <编号>）：");
            for (int i = 0; i < world.lightCount; ++i) {
                const Light& l = world.lights[i];
                char buf[192];
                std::snprintf(buf, sizeof(buf), "  light %d  位置 %.2f %.2f %.2f  强度 %.2f  半径 %.2f", i,
                              double(l.position.x), double(l.position.y), double(l.position.z),
                              double(l.intensity), double(l.radius));
                con.print(buf);
            }
            con.print("  光走的是一条直线到了表面才拐弯：位置改一米，地上的光斑就挪一米。");
            return;
        }
        if (t.size() == 2 && detail::parseNumber(t[1], value)) {
            // light 60 —— 只有一盏灯的场景里不该逼学生先记住灯号
        } else if (t.size() == 3 && detail::parseInt(t[1], idx) && detail::parseNumber(t[2], value)) {
            // light 1 30
        } else {
            con.printError("用法：light <强度> 或 light <灯号> <强度>，例如 light 60 / light 1 30");
            return;
        }
        if (idx >= world.lightCount) {
            con.printError("场景里只有 " + std::to_string(world.lightCount) + " 盏灯，没有第 " +
                           std::to_string(idx) + " 盏");
            return;
        }
        world.lights[idx].intensity = value;
        con.printOk("第 " + std::to_string(idx) + " 盏灯强度 = " + joinFloats({value}));
        return;
    }

    if (cmd == "inspect") {
        if (t.size() == 1) {
            con.print("场景里的材质（" + std::to_string(world.materialNames.size()) + " 个）：" +
                      joinNames(world.materialNames));
            return;
        }
        int idx = world.findMaterial(t[1]);
        std::string title = t[1];
        if (idx < 0) {
            // 也认实体名 —— 学生面前只有一个「塑料球」，没有 "plastic"。
            // 这条别名让按 E 观察材质成为一条能走通的链，而不是一句报错。
            const int ent = world.findEntity(t[1]);
            if (ent >= 0) {
                const Entity& e = world.entities[size_t(ent)];
                if (e.material >= 0 && e.material < int(world.materials.size())) {
                    idx = e.material;
                    title = t[1] + " 用的是「" + world.materialNames[size_t(idx)] + "」";
                }
            }
        }
        if (idx < 0) {
            con.printError("没有叫 \"" + t[1] + "\" 的材质或物体。材质有：" + joinNames(world.materialNames));
            return;
        }
        const Material& m = world.materials[size_t(idx)];
        const std::string matName = world.materialNames[size_t(idx)];
        char buf[256];
        std::snprintf(buf, sizeof(buf), "%s：albedo %.3f %.3f %.3f · 粗糙度 %.2f · 金属度 %.2f", title.c_str(),
                      double(m.albedo.x), double(m.albedo.y), double(m.albedo.z), double(m.roughness),
                      double(m.metallic));
        con.print(buf);
        if (m.albedoTexture != nullptr) {
            // 贴图三件套打印成"词"（repeat / bilinear）：学生要改的就是这几个词，
            // 那么"现在是什么态、要不要改"就得能在游戏里当场读出来，不该逼他翻文件。
            std::snprintf(buf, sizeof(buf),
                          "  带贴图（代码画的方格砖）：uv 平铺 %.2g x %.2g · 寻址 %s · 滤波 %s",
                          double(m.uvScale.x), double(m.uvScale.y), detail::wrapWord(m.wrapMode),
                          detail::filterWord(m.filterMode));
            con.print(buf);
        }
        if (m.emissive.x > 0.0f || m.emissive.y > 0.0f || m.emissive.z > 0.0f) {
            std::snprintf(buf, sizeof(buf), "  自发光 %.2f %.2f %.2f", double(m.emissive.x),
                          double(m.emissive.y), double(m.emissive.z));
            con.print(buf);
        }
        // 指路要指对文件：地板是唯一带贴图的材质，它的那一段住在 textures.txt。
        const char* home = m.albedoTexture != nullptr ? "content/textures.txt" : "content/materials.txt";
        con.print(std::string("  想看它变样：改 ") + home + " 里 material " + matName +
                  " 那一段，存盘后敲 reload");
        return;
    }

    if (cmd == "look") {
        // 「看东西」这个动作：走到一个物体前面按 E，它自己的 command 就是 look <名字>。
        // 铭牌上的数字全部现场从世界里取 —— 灯挪了、参数改了，铭牌跟着变，
        // 不会出现"文件上写着 A、游戏里其实是 B"这种把人带沟里的事。
        const std::string what = t.size() > 1 ? t[1] : std::string();
        char buf[256];
        if (what == "lamp") {
            const Entity* lamp = world.entity("吊灯");
            const Vec3 mount = lamp != nullptr ? lamp->position : Vec3{0.0f, 0.0f, 0.0f};
            con.printOk("吊灯 · 主光挂点");
            std::snprintf(buf, sizeof(buf), "  灯罩装在 (%.2f, %.2f, %.2f)，离天花板 %.2f 米",
                          double(mount.x), double(mount.y), double(mount.z), double(3.4f - mount.y));
            con.print(buf);
            if (world.lightCount > 0) {
                const Light& main = world.lights[0];
                std::snprintf(buf, sizeof(buf), "  content/lights.txt 的 light 0 现在挂在 (%.2f, %.2f, %.2f)，"
                                                "强度 %.1f",
                              double(main.position.x), double(main.position.y), double(main.position.z),
                              double(main.intensity));
                con.print(buf);
                std::snprintf(buf, sizeof(buf), "  → 离灯罩 %.2f 米。差得越远，展台收到的光越少（亮度按距离平方衰减）",
                              double(length(main.position - mount)));
                con.print(buf);
            }
            // 这里原来写的是"两样得一起弄：lights.txt 的 pos/intensity + materials.txt 里
            // material lamp 的 emissive" —— 实测被提出来过："两样是哪两样？改成什么？"
            // 那是行话里的"你懂的"，而看这块铭牌的人恰恰是最不懂的人。
            // 现在直接复用关卡自己算的"下一步"：该改哪个文件、哪个字段、改成什么，说全。
            const std::string step =
                nextStepFor(levelAt(rt.index), makeView(world, judge, rt.status), rt.status.passed());
            if (step.empty()) {
                con.print("  这一步已经做完了。");
            } else {
                con.printOk("下一步：");
                con.print("  " + step);
            }
            return;
        }
        if (what == "board") {
            con.printOk("展板 · 数媒组");
            con.print("实时渲染 / 三维建模 / 材质光照 / 动效 / 交互装置 —— 就是数媒组每天在做的事。");
            con.print("你手上跑的这个程序是一份活样本：它一个第三方库都没用，光栅化、着色、");
            con.print("中文字模、PNG 编码全是这个仓库里自己写的 C++，九千多行。");
            con.print("想改它：敲 level 看这一关要什么，然后改 content/*.txt，存盘按 R。");
            return;
        }
        con.printError("这里没有能看的 \"" + what + "\"（能看的有：lamp 吊灯 / board 展板）");
        return;
    }

    if (cmd == "reload") {
        std::vector<std::string> log;
        const std::vector<std::string> changed = watcher.forceReload(world, &log);
        bool hadError = false;
        for (const std::string& l : log) {
            // 解析错误长这样：「content/materials.txt:70: 未知字段 ...」，
            // 汇总行以「  →」开头 —— 这两种要红着显示，否则学生划过去就当没看见
            const bool looksLikeError = l.rfind("content/", 0) == 0 || l.rfind("  →", 0) == 0;
            hadError = hadError || looksLikeError;
            if (looksLikeError) {
                con.printError(l);
            } else {
                con.print(l);
            }
        }
        // 「读了几个文件」和「有几个文件生效」不是一回事：写坏的那个整份不生效，
        // 所以有错的时候不能说「重新应用了 N 个文件」—— 学生会以为改动进去了。
        if (changed.empty()) {
            con.print("content/ 没有变化（和上次读进来的一模一样）");
        } else if (hadError) {
            con.print("重新读了 " + std::to_string(changed.size()) +
                      " 个文件；报错的那几个整份没生效，其它照常");
        } else {
            con.printOk("重新应用了 " + std::to_string(changed.size()) + " 个文件");
        }
        return;
    }

    if (cmd == "shot") {
        takeShot();
        return;
    }

    if (cmd == "qr") {
        // 和按 Q 走**同一条路**：没通关就是没解锁，命令也不给开。
        // 这是唯一一个"故意不听话"的开发命令 —— 留个后门等于把这个功能取消了。
        if (!showRecruit()) {
            con.printError("入群二维码还没解锁 —— 四关全通之后它才会开。");
        } else {
            con.printOk("入群二维码已打开（按 Q 或 Esc 关掉）。");
        }
        return;
    }

    con.printError("没有这个命令：" + cmd + "（敲 help 看全部命令）");
}

// ---------------------------------------------------------------- 输入 → 意图
// 键盘、脚本、以后的手柄，最后都变成同一个 InputState 交给 updatePlayer。
// 走路的正确性（碰撞、滑墙）因此可以在离屏下自动化验证 —— 见 testPlayer()。

// 窗口这一帧的键鼠 → 意图
InputState fromWindowInput(const FrameInput& pi) {
    InputState in;
    if (pi.down[int(Key::W)]) in.moveForward += 1.0f;
    if (pi.down[int(Key::S)]) in.moveForward -= 1.0f;
    if (pi.down[int(Key::D)]) in.moveRight += 1.0f;
    if (pi.down[int(Key::A)]) in.moveRight -= 1.0f;
    in.lookDX = pi.mouseDX;
    in.lookDY = pi.mouseDY;
    in.interact = pi.pressed[int(Key::E)];
    return in;
}

// --walk "w:120,w+d:60,e" → 一串「按住哪些键、按多少帧」。
// 帧数是绝对的，所以同一串脚本在哪台机器上跑出来都一样 —— 验收要的就是这个。
struct WalkStep {
    std::vector<Key> keys;
    int frames = 1;
};

std::vector<WalkStep> parseWalk(const std::string& text) {
    std::vector<WalkStep> steps;
    std::string seg;
    auto flush = [&]() {
        if (seg.empty()) return;
        const size_t colon = seg.find(':');
        const std::string names = colon == std::string::npos ? seg : seg.substr(0, colon);
        int frames = colon == std::string::npos ? 1 : std::atoi(seg.c_str() + colon + 1);
        if (frames < 1) frames = 1;

        WalkStep step;
        step.frames = frames;
        std::string name;
        for (size_t i = 0; i <= names.size(); ++i) {
            if (i == names.size() || names[i] == '+') {
                if (name == "w") step.keys.push_back(Key::W);
                else if (name == "s") step.keys.push_back(Key::S);
                else if (name == "a") step.keys.push_back(Key::A);
                else if (name == "d") step.keys.push_back(Key::D);
                else if (name == "e") step.keys.push_back(Key::E);
                else if (name == "left") step.keys.push_back(Key::Left);
                else if (name == "right") step.keys.push_back(Key::Right);
                else if (name == "up") step.keys.push_back(Key::Up);
                else if (name == "down") step.keys.push_back(Key::Down);
                else {
                    std::fprintf(stderr, "[错误] --walk 不认识 \"%s\"（可用：w a s d e left right up down，"
                                         "多个键用 + 连，:后面是帧数，例如 w:120,w+d:60,e）\n",
                                 name.c_str());
                    std::exit(2);
                }
                name.clear();
            } else {
                name += names[i];
            }
        }
        steps.push_back(step);
        seg.clear();
    };
    for (char c : text) {
        if (c == ',' || c == ';' || c == ' ' || c == '\t') flush();
        else seg += c;
    }
    flush();
    return steps;
}

InputState walkInputAt(const std::vector<WalkStep>& steps, int frame) {
    InputState in;
    int t = 0;
    for (const WalkStep& s : steps) {
        if (frame < t + s.frames) {
            for (Key k : s.keys) {
                switch (k) {
                    case Key::W: in.moveForward += 1.0f; break;
                    case Key::S: in.moveForward -= 1.0f; break;
                    case Key::D: in.moveRight += 1.0f; break;
                    case Key::A: in.moveRight -= 1.0f; break;
                    case Key::Left: in.lookDX -= 8.0f; break;
                    case Key::Right: in.lookDX += 8.0f; break;
                    case Key::Up: in.lookDY -= 8.0f; break;
                    case Key::Down: in.lookDY += 8.0f; break;
                    case Key::E: in.interact = true; break;
                    default: break;
                }
            }
            return in;
        }
        t += s.frames;
    }
    return in;
}

// 看着某个东西按 E：执行它自己带的命令，并且走控制台那条路。
// 「实体 → 命令 → 控制台」这条链让交互、打字、--cmd 共用同一个解释器：
// 关卡作者只要给实体写一句 command，不用碰 C++。
// 这个物体现在能不能按 E（见 Entity::forLevel）。
bool interactableNow(const Entity& e, int levelIndex) {
    return !e.command.empty() && (e.forLevel < 0 || e.forLevel == levelIndex);
}

// 准星旁边那行"按 E …"。用不上的东西**不提示** —— 提示了却不能按，
// 比干脆不提示更让人困惑（"我按了怎么没反应"）。
std::string promptFor(const World& w, int entity, int levelIndex) {
    if (entity < 0) return std::string();
    const Entity& e = w.entities[size_t(entity)];
    return interactableNow(e, levelIndex) ? e.prompt : std::string();
}

void interact(Scene& s, Console& con, Toast& toast, double now, int levelIndex) {
    const World::RayHit hit = s.world.castRay(s.player.eye(), s.player.forward(), Player::kReach);
    if (hit.entity < 0) {
        toast.show("准星前面没有能互动的东西（走近点，或者低头看看）", now);
        return;
    }
    const Entity& e = s.world.entities[size_t(hit.entity)];
    if (e.command.empty()) {
        toast.show("「" + e.name + "」现在还没接上动作", now);
        return;
    }
    // 不是这一关的东西：**不开控制台**，但要说清楚。
    // 零基础的人进了游戏会把 E 按一圈 —— 第 0 关（一间黑屋子）按到材质球上，弹出一屏
    // "roughness / metallic"，他不知道那是什么、也不知道跟自己该干的事有什么关系，
    // 只会更懵。回一句"这是第 N 关的事"，他反而摸清了"这个世界是按关分的"。
    if (e.forLevel >= 0 && e.forLevel != levelIndex) {
        char buf[160];
        std::snprintf(buf, sizeof(buf), "「%s」是第 %d 关的事，现在还用不上", e.name.c_str(),
                      e.forLevel);
        toast.show(buf, now, 2.5);
        return;
    }
    // 把控制台翻开：命令的来龙去脉（执行了哪一句、结果是什么）就在眼前，
    // 而不是"按了 E 之后画面悄悄变了，不知道发生了什么"。
    con.setVisible(true);
    con.print("（看着「" + e.name + "」按下 E）");
    con.run(e.command);
}

// 入群二维码的状态。做成一个结构体传，是因为它要跨三处用：通关那一刻解锁、
// 主循环里按 Q 开关、以及离屏出图（--qr-preview）时核对这张码画得对不对。
struct RecruitState {
    RecruitQr qr;
    bool unlocked = false;  // 四关全通之后才为真
    bool open = false;      // 覆盖层开没开
};

// ---------------------------------------------------------------- 开窗模式
int runWindow(const Args& args, Window& win, Scene& scene, Rasterizer& rz, Console& con,
              const std::function<void()>& takeShot, const std::function<bool()>& showRecruit,
              Toast& toast, Judge& judge, LevelRuntime& rt, RecruitState& recruit,
              Settings& settings, int& resIndex, ContentWatcher& watcher) {
    Font font;
    font.loadFromFile("assets/font/pixel12.bin");  // 失败会画红块占位，绝不白屏

    Menu menu;  // Esc 叫出来的暂停菜单

    const std::vector<WalkStep> walk = parseWalk(args.walk);
    const bool autopilot = !args.walk.empty();
    if (autopilot) std::printf("[dreamlab-rt] --walk 接管输入（%s），键盘这局不生效\n", args.walk.c_str());

    double last = win.time();
    const double runStart = win.time();  // 开局引导卡按这个倒计时（见 drawGuideCard）
    float fps = 60.0f;
    int frame = 0;
    // 开场就过关的两种情形要分开（见 main 里那段注释）：早就过了的别再喊，
    // 刚改完代码回来的必须喊 —— 第 0 关的全部意义就在那一下。
    bool wasPassed = rt.status.passed();

    // 过关的这句话是整个循环里唯一的奖励，喊多了就不值钱了，所以只有一个地方喊。
    // 后面接的那句"下面是下一关"由推进关卡的那段补 —— 这里只管把"你做到了"喊出来。
    // 过关的这句话是整个循环里唯一的奖励，喊多了就不值钱了，所以只有一个地方喊。
    auto announcePass = [&](const Level& lv, double now) {
        con.setVisible(true);
        con.printOk("目标达成：" + std::string(lv.goal));
        // 每关补一句自己的话（见 Level::passNote）：过关那一下该说什么，是这一关的
        // 教学点，不是通用客套。第 0 关是"你改的是代码、重编译过，可你还站在原地"。
        if (lv.passNote != nullptr && lv.passNote[0] != '\0') con.print(lv.passNote);
        toast.show("目标达成：" + std::string(lv.goal), now, 4.0);
    };

    // 过关之后的**收尾**：喊一声 → 记下这关过了 → 推进下一关 → 把下一关的题面念出来。
    // 两处过关必须走同一套，不能各写一半：
    //   · 循环里"当场改了数据按 R"那一帧（从没过变成过了）
    //   · 开场就发现"你刚改完代码、重编译回来"（freshWin）
    //
    // 以前开场那条路只喊了一声就完了。而第 0 关**恰好最容易走这条路**（它的解法就是
    // 改 C++ 重编译），于是全项目情绪最高的那一下反馈最薄：房间亮了，然后没有下文，
    // 人不知道自己该干嘛 —— 这是实测被提出来的原话（"缺乏引导、鼓励和下一关的信息"）。
    auto finishLevel = [&](const Level& lv, double now) {
        announcePass(lv, now);
        rt.goals = markLevelDone(rt.goals, rt.index);
        // 推进 + 念下一关的题面：**和"开场就发现过关"那条路共用同一个函数**。
        // 以前两条路各写一半（一条会推进、另一条不会），于是有了那个死角。
        if (advanceAfterPass(con, scene.world, judge, rt)) {
            // 四关全通 = 解锁入群二维码。这是整个游戏的**结束动作**，所以它自己弹出来，
            // 不用再让玩家去猜"刚才那一堆字里是不是有什么东西开了"。
            recruit.unlocked = true;
            recruit.open = true;
            con.printOk("【入群二维码已解锁】—— 就是下面这张，手机微信扫一扫。");
            con.print("（以后想再看：按 Q，或者走到终端敲 qr）");
            toast.show("入群二维码已解锁", now, 6.0);
        }
    };
    if (rt.freshWin) {
        finishLevel(levelAt(rt.index), win.time());
        wasPassed = true;  // 刚补喊过，别让循环的第一帧再喊一遍
        rt.freshWin = false;
    }

    // 限帧：开窗是靠 present() 直接贴位图，没有垂直同步管着 —— 不锁的话这个循环
    // 会往上百帧跑，把每个核都吃满。宣讲一两个小时，风扇狂转、笔记本掉电都很难看，
    // 所以要锁。锁得住的前提是"睡得准"：系统定时器粒度默认 15.6ms 一档，睡 5ms
    // 会睡到 15.6ms、60 帧直接掉成 30 帧 —— platform_win32.cpp 开窗时把它调到 1ms 了。
    // 提前干完就睡到下一帧的点上；干不完就别睡，让帧率自己掉下去。
    // --fpscap 0 = 不锁（量性能用），--fpscap 30 = 低配机器省电用。
    const double kFrameBudget = args.fpsCap > 0 ? 1.0 / double(args.fpsCap) : 0.0;
    auto prevFrameStart = std::chrono::steady_clock::now();

    // 推屏用的 RGB8 缓冲，常驻复用 —— 每帧新建一个 1600x900 的 vector 就是每帧
    // 白送一次 4.3MB 的 malloc/free（见 core/framebuffer.h 的 toRGB8Into）。
    std::vector<uint8_t> rgbFrame;

    while (win.pump()) {
        const auto frameStart = std::chrono::steady_clock::now();
        // 上一帧到这一帧的间隔（帧率就是它的倒数）。和 work 一起看，
        // 就知道时间是花在渲染上还是花在等上。
        const double periodMs =
            std::chrono::duration<double, std::milli>(frameStart - prevFrameStart).count();
        prevFrameStart = frameStart;
        const double now = win.time();
        float dt = float(now - last);
        last = now;
        // 卡一下（拖动窗口、切出去回来）不要变成一大步 —— dt 大了会一步穿过墙
        dt = clampf(dt, 0.0005f, 0.05f);

        // HUD 上的 FPS 是「玩家眼里的一秒多少帧」（帧间隔的倒数，含限帧等待），
        // 不是「渲染一帧要多久」—— 限了帧以后这两个能差三倍，写哪个都得说清楚。
        // 渲染本身多快，看 --trace 里的 work=。离屏出图没有"帧率"可言，
        // 那边显示的仍是渲染耗时换算出来的吞吐，两个数各有各的用处。
        if (periodMs > 0.0) fps = fps * 0.9f + float(1000.0 / periodMs) * 0.1f;

        const FrameInput& pi = win.input();

        // 菜单/控制台开着时，不接受"点一下就抓鼠标" —— 否则点菜单的那一下会把鼠标锁走，
        // 光标跳到窗口中心，菜单就点不动了。（见 platform.h 的 setClickToCapture）
        win.setClickToCapture(!(con.visible() || menu.visible() || recruit.open));

        // ---- 全局按键
        // Esc 的三级优先级：控制台 → 菜单 → 打开菜单。
        // **不再直接退出** —— 那是以前的行为，想关控制台手滑按两下，游戏就没了。
        // 退出改到菜单里主动点（见下面的 MenuAction::Quit）。
        if (pi.pressed[int(Key::Esc)]) {
            if (recruit.open) {
                // 二维码在最上层，Esc 先关它 —— 关掉之后回到刚才那一层（多半是控制台）。
                // 鼠标要不要还回去，取决于下面那层是不是也要用鼠标。
                recruit.open = false;
                win.setMouseCaptured(!(con.visible() || menu.visible()));
            } else if (con.visible()) {
                con.setVisible(false);
                win.setMouseCaptured(true);  // 刚按 Esc 的人一定在窗口里，直接回到"鼠标看视角"
            } else if (menu.visible()) {
                menu.setVisible(false);
                win.setMouseCaptured(true);
            } else {
                menu.setVisible(true);
                win.setMouseCaptured(false);  // 菜单要用鼠标点，必须放开
            }
        }
        // ~ 不再是"叫出控制台"的快捷键：控制台是世界里的那台终端，得走过去按 E 才打得开
        // （见 interact()）。这样它就不是一个悬在画面上的调试窗口，而是房间里的一个东西 ——
        // 和"走到终端前按 E，游戏告诉你该改哪个文件"这条核心循环对得上。
        //
        // 但按了要有回声：本项目的规矩是"按一下没反应"最容易让人以为程序坏了。
        if (pi.pressed[int(Key::Tilde)]) {
            toast.show("控制台要到桌子前的终端那儿开 —— 走近了按 E", now);
        }
        // Q = 看入群二维码。没通关时不响应，但**要有回声** ——
        // 本项目的规矩是"按一下没反应"最容易让人以为程序坏了。
        // 控制台开着时这个键留给打字（命令里没有 q 开头的，但规矩要一致）。
        if (pi.pressed[int(Key::Q)] && !con.visible()) {
            if (recruit.unlocked || args.qrPreview) {
                recruit.open = !recruit.open;
                win.setMouseCaptured(!recruit.open);
            } else {
                toast.show("入群二维码还没解锁 —— 把四关都过了它就开了", now);
            }
        }
        if (pi.pressed[int(Key::F2)]) takeShot();

        // ---- 控制台开着的时候，键盘全给它。
        // 不然敲 ambient 里的 a/w/d 会顺手把人挪走 —— 那是第一次用就会骂人的 bug。
        if (con.visible()) {
            for (char c : pi.typed) {
                // '~' 不再是入口了，但 WM_CHAR 照样会送它进来（0x7E 落在可打印区间里）。
                // 命令里没有它，直接丢掉，免得输入行里凭空多出一个字符。
                if (c == '~') continue;
                con.typeChar(c);
            }
            if (pi.pressed[int(Key::Backspace)]) con.backspace();
            if (pi.pressed[int(Key::Enter)]) con.submit();
            // 翻看历史：面板显示不下的行不是丢了，是视口钉在末尾 —— 关卡的提示是
            // 编号步骤，"看不到第一步"等于没有提示。↑↓ 一行一行，PgUp/PgDn 一整屏。
            if (pi.pressed[int(Key::Up)]) con.scrollBy(1);
            if (pi.pressed[int(Key::Down)]) con.scrollBy(-1);
            if (pi.pressed[int(Key::PageUp)]) con.scrollBy(Console::pageRows());
            if (pi.pressed[int(Key::PageDown)]) con.scrollBy(-Console::pageRows());
            win.setMouseCaptured(false);  // 打字要看得见鼠标，也不能让视角跟着甩
        } else if (!autopilot) {
            if (pi.pressed[int(Key::R)]) con.run("reload");  // R = 重读 content/（和 --cmd reload 同一条路）
            // 控制台关着的时候，可打印字符一律当没看见 —— 它只能在终端那儿用 E 打开。
            //
            // 这里原先写的是"随便敲一个可打印字符就把控制台顶开"，为的是省掉
            // "新生得先知道 ~ 在哪"这一步。想法没错，但 W/A/S/D 本身就是可打印
            // 字符（WM_CHAR 收 0x20~0x7E，见 platform_win32.cpp）：于是按 W 想往前
            // 走，会当场把控制台弹出来，接着 con.visible() 为真、玩家输入被清空,
            // 一步都走不动 —— 整个 WASD 全废。
            //
            // 这个 bug 躲了很久，因为离屏那套验证全走 --walk，而 --walk 会置
            // autopilot，刚好跳过这一支（见上面的 else if (!autopilot)），
            // 键盘这条路没有任何自动化覆盖。改了这里请手动开窗试一下 WASD。
        }

        // ---- 走一步
        // 控制台或菜单开着的时候玩家输入清零（= 暂停）—— 同一套机制，
        // 不然敲命令/点菜单会顺手把人挪走。
        const InputState in =
            autopilot ? walkInputAt(walk, frame)
                      : ((con.visible() || menu.visible() || recruit.open) ? InputState{}
                                                                            : fromWindowInput(pi));
        updatePlayer(scene.player, in, scene.world, dt);
        syncCamera(scene);
        if (in.interact) interact(scene, con, toast, now, rt.index);

        // ---- 关卡判定：评委机位给世界拍一张 96x54 的小图，算进度。
        // 每帧都算：学生改了 content/ 按 R 之后，进度条当场就动 ——
        // 判定慢半拍的话，"我改了它却说我还没改"是最劝退的体验。
        const Level& lv = levelAt(rt.index);
        rt.status = judge.evaluate(scene.world, lv);
        if (rt.status.passed() && !wasPassed) {
            // 过关只在"从没过变成过了"的那一帧喊一次（比如当场改了 content/ 按 R）。
            // 这也是唯一一处"当场看见因果"的奖励：刚敲下的那行数字，让房间亮了。
            finishLevel(lv, now);
        }
        if (rt.status.passed()) rt.goals = markLevelDone(rt.goals, rt.index);
        wasPassed = rt.status.passed();

        // ---- 暂停菜单：喂进当前进度、收下点击、就地应用设置
        // 放在判定之后：菜单里显示的进度必须是这一帧的（上面那段可能刚把 rt.index
        // 推到下一关，所以这里重新取 levelAt(rt.index)，不用上面那个 lv 引用）。
        if (menu.visible()) {
            const Level& lvNow = levelAt(rt.index);
            menu.setModel(MenuModel{lvNow.title, lvNow.goal, progressPercent(rt.status),
                                    rt.status.passed()});
            menu.setDisplay(resIndex, settings.mode);

            // 鼠标坐标是**客户区**的，而菜单画在 **framebuffer** 上 —— 窗口一被拉大
            // （最大化、或者 --scale 之后推屏会把画面放大铺满），这两套坐标就不是一回事了：
            // 拿客户区坐标去和 framebuffer 上的矩形比，只有左上角那一块能点中，
            // 看着就是"菜单大部分按不了"。这里把鼠标换算到 framebuffer 坐标系再交进去。
            FrameInput menuIn = pi;
            mouseToFramebuffer(menuIn.mouseX, menuIn.mouseY, win.width(), win.height(),
                               rz.framebuffer().width, rz.framebuffer().height);
            const MenuAction act =
                menu.update(menuIn, rz.framebuffer().width, rz.framebuffer().height, font);

            if (act.kind == MenuAction::Quit) break;  // 走正常退出路径（退出时会写存档）

            if (act.kind == MenuAction::Restart) {
                // 从头开始：清进度 + 把**所有已跟踪的文件**还原成仓库里的样子。
                //
                // 为什么用 git 而不是"在仓库里再存一份出厂副本"：仓库里那份**就是**
                // 出厂状态，再存一份副本迟早会和正本对不上（学生一改，两处都得改）。
                // 顺带这也是设计文档里"覆盖 Git 入门"的一条 —— 他会亲眼看到
                // git 能一键把改动全撤掉。
                menu.setVisible(false);
                win.setMouseCaptured(true);
                clearSave();
                const int rc = std::system("git checkout -- .");

                rt.index = 0;
                rt.goals = 0;
                // 数据文件当场重读：不重启就能看见 content/ 回到了出厂的样子
                std::vector<std::string> reloadLog;
                watcher.forceReload(scene.world, &reloadLog);
                rt.status = judge.evaluate(scene.world, levelAt(0));
                // 人回出生点 —— 和 --reset 一样（存档已经清了）
                scene.player.feet = Vec3{0.0f, 0.0f, 4.5f};
                scene.player.yaw = 0.0f;
                scene.player.pitch = -0.06f;
                syncCamera(scene);

                con.setVisible(true);
                if (rc == 0) {
                    con.printOk("已经回到起点：进度清空，content/ 和代码都还原成仓库里的样子了。");
                } else {
                    con.printError("进度清了，但**文件没能还原**（git 用不了，或者这里不是 git 仓库）。");
                    con.print("手动办法：重新 clone 一份，或者把 content/ 和 game/ 换回仓库里的版本。");
                }
                // 有一件事游戏自己做不到，必须说清楚，不然他会以为"我明明重置了，怎么还是亮的"
                if (rt.status.passed()) {
                    con.print("注意：现在跑的还是**改动过的那份二进制**，所以第 0 关仍然是亮的。");
                    con.print("退出后重新运行一次 build.bat，它就是全黑的了 —— 那一步游戏做不到。");
                }
                printLevelBriefing(con, rt, scene.world, judge);
                toast.show("已回到起点", now, 3.0);
            } else if (act.kind == MenuAction::Resume) {
                menu.setVisible(false);
                win.setMouseCaptured(true);
            } else if (act.kind == MenuAction::SetResolution || act.kind == MenuAction::SetMode) {
                const int newIndex = act.kind == MenuAction::SetResolution ? act.value : resIndex;
                const WindowMode newMode =
                    act.kind == MenuAction::SetMode ? WindowMode(act.value) : settings.mode;
                const Resolution r = kResolutions[newIndex];

                win.setWindowMode(newMode, r.w, r.h);
                // 渲染分辨率永远是表里那一档，和窗口模式无关 ——
                // 全屏只是把这一档拉伸铺满屏幕。也**不乘 --scale**：
                // 菜单里选的就是渲染分辨率，再叠一层缩放会让人对不上号。
                rz.resize(r.w, r.h);

                settings.mode = newMode;
                settings.width = r.w;
                settings.height = r.h;
                resIndex = newIndex;
                // 立刻落盘：改完设置崩了不该丢（几行的文本文件，写它很便宜）
                if (!saveSettings(settings, kSettingsPath)) {
                    toast.show("设置没能存下来（saved/ 写不进去）", now, 3.0);
                }
            }
        }

        // ---- 「现在差哪一步」：关卡自己算（见 Level::nextStep）。
        // 一句话给 HUD，一个实体名给渲染描边。每帧算一次 —— 世界一变它当场就跟上。
        const Level& shown = levelAt(rt.index);
        const LevelView stepView = makeView(scene.world, judge, rt.status);
        // 走 nextStepFor 而不是直接调关卡的 —— 它会兜底"这一关已经过了"那句，
        // 不然用 level 跳回旧关卡时 HUD 上那一行会整个消失。
        const NextStep step{nextStepFor(shown, stepView, rt.status.passed()),
                            shown.nextStep != nullptr ? shown.nextStep(stepView).focusEntity
                                                      : std::string()};
        const Entity* focus =
            step.focusEntity.empty() ? nullptr : scene.world.entity(step.focusEntity);

        // ---- 画一帧
        rz.framebuffer().clear(kClearColor);
        renderFrame(rz, scene, float(now), true);  // 开窗这一路才给随身微光

        // 叠字层：HUD → 准星 → 控制台面板（后画的盖住先画的）
        const int inset = con.panelHeight(font, rz.framebuffer().width, rz.framebuffer().height);
        GoalPanel goal;
        goal.title = shown.title;
        goal.goal = shown.goal;
        goal.progress = rt.status.progress;
        goal.passed = rt.status.passed();
        goal.nextStep = step.text;
        drawHud(rz.framebuffer(), font, fps, inset, autopilot ? kAutopilotHint : kWindowHint, goal);
        // 「下一步要动的东西在哪儿」—— 屏幕上指个方向。菜单开着时不画：
        // 那时候画面被压暗了，箭头只会变成面板底下的一块噪点。
        if (focus != nullptr && !menu.visible() && !recruit.open) {
            drawTargetArrow(rz.framebuffer(), font, scene.camera, focus->position, focus->name);
        }
        // 菜单开着时不画准星：它和它的提示文字会从菜单的半透明遮罩下透出来，糊在面板中间。
        // （HUD 留着 —— 它在面板外面，被压暗之后正好当背景信息。）
        if (!menu.visible()) {
            const World::RayHit look =
                scene.world.castRay(scene.player.eye(), scene.player.forward(), Player::kReach);
            drawCrosshair(rz.framebuffer(), font, promptFor(scene.world, look.entity, rt.index),
                          toast.alive(now) ? toast.text : std::string());
        }
        if (con.visible()) con.draw(rz.framebuffer(), font);
        // 开局那张引导卡：只在第 0 关、还没开始动手的时候出现（= 真正的新生场景，
        // 也是唯一会"一进来全黑、不知道能不能动"的地方）。十秒后淡掉。
        if (rt.index == 0 && rt.status.progress <= 0.0f && !menu.visible() && !con.visible()) {
            const double since = now - runStart;
            if (since < 12.0) {
                // 前 9 秒实心，之后 3 秒淡出 —— 直接消失太突然
                const float a = since < 9.0 ? 1.0f : float((12.0 - since) / 3.0);
                drawGuideCard(rz.framebuffer(), font, a);
            }
        }
        if (menu.visible()) menu.draw(rz.framebuffer(), font);  // 最后画：盖住 HUD 和控制台
        // 入群二维码盖在所有层之上 —— 它是通关奖励，没有别的 UI 该压住它。
        // （下面那两处"菜单开着就不画"的准星和箭头同理，见上面的 if。）
        if (recruit.open) drawRecruitQr(rz.framebuffer(), font, recruit.qr, recruit.qr.ready());

        rz.framebuffer().toRGB8Into(rgbFrame, args.exposure, true, args.threads);
        win.present(rgbFrame, rz.framebuffer().width, rz.framebuffer().height);
        win.endFrame();

        // 这一帧从起床到贴完屏幕一共花了多少毫秒。放在 --trace 里是因为
        // "卡在哪一段"这种事，没有数字就只能猜；真要调性能时也不用另加计时器。
        const double spent = std::chrono::duration<double, std::milli>(
                                 std::chrono::steady_clock::now() - frameStart)
                                 .count();

        if (args.trace) {
            // prog= 是这一帧的关卡进度：验证"改了数据按 R，进度当场就动"靠它
            std::printf("[trace] f=%d pos=(%.3f,%.3f,%.3f) yaw=%.3f pitch=%.3f prog=%.3f work=%.2fms period=%.2fms\n",
                        frame, double(scene.player.feet.x), double(scene.player.feet.y),
                        double(scene.player.feet.z), double(scene.player.yaw),
                        double(scene.player.pitch), double(rt.status.progress), spent, periodMs);
        }
        ++frame;
        if (args.frames > 1 && frame >= args.frames) break;  // 冒烟测试：跑够帧数自己退

        // 提前干完就睡到下一帧的点上 —— 别把 CPU 空转掉。留 1ms 余量：
        // 睡过了头会白等一整格，宁可早醒一丝（63 帧和 60 帧肉眼没区别）
        const double rest = kFrameBudget * 1000.0 - spent - 1.0;
        if (rest > 0.0) std::this_thread::sleep_for(std::chrono::duration<double, std::milli>(rest));
    }

    win.close();
    std::printf("[dreamlab-rt] 窗口关了（跑了 %d 帧）\n", frame);
    return 0;
}

// ---------------------------------------------------------------- 自检

int g_checks = 0;
int g_failures = 0;

void check(bool ok, const char* what) {
    ++g_checks;
    if (!ok) {
        ++g_failures;
        std::printf("[selftest] 失败: %s\n", what);
    }
}

void checkClose(float got, float want, float tol, const char* what) {
    ++g_checks;
    if (!(std::fabs(got - want) <= tol)) {
        ++g_failures;
        std::printf("[selftest] 失败: %s（期望 %.5f，实际 %.5f）\n", what, want, got);
    }
}

bool sameVec3(Vec3 a, Vec3 b) { return a.x == b.x && a.y == b.y && a.z == b.z; }

// 造一个覆盖整个 NDC 的大三角形，四个角全包住
Mesh makeFullScreenTriangle(float z) {
    Mesh m;
    m.vertices.push_back(Vertex{Vec3{-1.0f, -1.0f, z}, Vec3{0.0f, 0.0f, -1.0f}, Vec2{0.0f, 0.0f}, Vec3{1, 1, 1}});
    m.vertices.push_back(Vertex{Vec3{5.0f, -1.0f, z}, Vec3{0.0f, 0.0f, -1.0f}, Vec2{1.0f, 0.0f}, Vec3{1, 1, 1}});
    m.vertices.push_back(Vertex{Vec3{-1.0f, 5.0f, z}, Vec3{0.0f, 0.0f, -1.0f}, Vec2{0.0f, 1.0f}, Vec3{1, 1, 1}});
    m.indices = {0, 1, 2};
    return m;
}

void testMath() {
    const Vec3 x{1.0f, 0.0f, 0.0f};
    const Vec3 y{0.0f, 1.0f, 0.0f};
    const Vec3 z{0.0f, 0.0f, 1.0f};

    checkClose(dot(x, y), 0.0f, 1e-6f, "dot：正交基内积为 0");
    checkClose(cross(x, y).z, 1.0f, 1e-6f, "cross：x × y = z");
    checkClose(length(normalize(Vec3{3.0f, 4.0f, 0.0f})), 1.0f, 1e-6f, "normalize：长度为 1");

    // 绕 y 轴转 90°：(1,0,0) 应该转到 (0,0,-1)
    const Vec3 rotated = transformDir(rotationY(radians(90.0f)), x);
    checkClose(rotated.x, 0.0f, 1e-5f, "rotationY(90°) 的 x 分量");
    checkClose(rotated.z, -1.0f, 1e-5f, "rotationY(90°) 的 z 分量");

    // 相机看向原点：目标点应落在 NDC 正中心
    const Mat4 view = lookAt(Vec3{0.0f, 0.0f, 5.0f}, Vec3{0.0f, 0.0f, 0.0f}, Vec3{0.0f, 1.0f, 0.0f});
    const Mat4 proj = perspective(radians(60.0f), 1.0f, 0.1f, 100.0f);
    const Vec4 clip = (proj * view) * Vec4(Vec3{0.0f, 0.0f, 0.0f}, 1.0f);
    check(clip.w > 0.0f, "投影：目标点在相机前方（w > 0）");
    checkClose(clip.x / clip.w, 0.0f, 1e-4f, "投影：目标点 NDC x = 0");
    checkClose(clip.y / clip.w, 0.0f, 1e-4f, "投影：目标点 NDC y = 0");

    // 相机在 z=5 看向 -z：前方 0.1 处正好是近裁剪面，前方 100 处是远裁剪面
    const Vec4 nearClip = (proj * view) * Vec4(Vec3{0.0f, 0.0f, 4.9f}, 1.0f);
    checkClose(nearClip.z / nearClip.w, -1.0f, 1e-3f, "投影：近裁剪面 → NDC z = -1");
    const Vec4 farClip = (proj * view) * Vec4(Vec3{0.0f, 0.0f, -95.0f}, 1.0f);
    checkClose(farClip.z / farClip.w, 1.0f, 1e-3f, "投影：远裁剪面 → NDC z = +1");

    // 矩阵求逆：M * M⁻¹ = I
    const Mat4 m = translation(Vec3{1.0f, 2.0f, 3.0f}) * rotationY(radians(37.0f)) * scaling(Vec3{2.0f, 0.5f, 1.3f});
    const Mat4 prod = m * inverse(m);
    float maxErr = 0.0f;
    for (int r = 0; r < 4; ++r)
        for (int c = 0; c < 4; ++c) maxErr = maxf(maxErr, std::fabs(prod.at(r, c) - (r == c ? 1.0f : 0.0f)));
    checkClose(maxErr, 0.0f, 1e-3f, "矩阵求逆：M × M⁻¹ = 单位阵");

    // 非等比缩放下，法线矩阵必须让法线与切向量保持垂直
    const Mat4 squash = scaling(Vec3{1.0f, 4.0f, 0.6f}) * rotationY(radians(30.0f));
    const Vec3 tWorld = transformDir(squash, x);
    const Vec3 nWorld = normalize(transformDir(normalMatrix(squash), y));
    checkClose(dot(tWorld, nWorld), 0.0f, 1e-3f, "法线矩阵：非等比缩放下仍与切向量垂直");

    checkClose(Framebuffer::tonemapACES(0.0f), 0.0f, 1e-6f, "色调映射：0 映射到 0");
    check(Framebuffer::tonemapACES(100.0f) > 0.95f, "色调映射：高光收敛到 1 附近");
    check(Framebuffer::tonemapACES(1.0f) > Framebuffer::tonemapACES(0.5f), "色调映射：单调递增");
}

void testRasterizer() {
    Rasterizer rz(2);
    rz.resize(64, 48);
    const Mat4 identity;
    ShadeEnv env;  // 无灯、无环境光：只留下自发光

    Material red;
    red.albedo = Vec3{0.0f, 0.0f, 0.0f};
    red.emissive = Vec3{1.0f, 0.0f, 0.0f};

    Material green;
    green.albedo = Vec3{0.0f, 0.0f, 0.0f};
    green.emissive = Vec3{0.0f, 1.0f, 0.0f};

    const Mesh nearTri = makeFullScreenTriangle(0.5f);   // 深度 0.75
    const Mesh farTri = makeFullScreenTriangle(0.8f);    // 深度 0.90

    const size_t centerIndex = size_t(24) * 64 + size_t(32);

    // ① 能不能画出一个像素
    rz.framebuffer().clear();
    rz.begin(identity, identity, Vec3{0.0f, 0.0f, 0.0f}, env);
    rz.draw(nearTri, identity, red);
    rz.flush();
    checkClose(rz.framebuffer().color[centerIndex].x, 1.0f, 1e-5f, "光栅化：屏幕中心被着色");
    checkClose(rz.framebuffer().color[centerIndex].y, 0.0f, 1e-5f, "光栅化：颜色来自着色器");
    checkClose(rz.framebuffer().depth[centerIndex], 0.75f, 1e-4f, "光栅化：深度正确插值");

    // ② 深度测试：远处的东西不能盖住近处的
    rz.framebuffer().clear();
    rz.begin(identity, identity, Vec3{0.0f, 0.0f, 0.0f}, env);
    rz.draw(nearTri, identity, red);
    rz.draw(farTri, identity, green);
    rz.flush();
    checkClose(rz.framebuffer().color[centerIndex].x, 1.0f, 1e-5f, "深度测试：近处的红留下");
    checkClose(rz.framebuffer().color[centerIndex].y, 0.0f, 1e-5f, "深度测试：远处的绿被挡住");

    // ③ 反过来的顺序也应该是红赢
    rz.framebuffer().clear();
    rz.begin(identity, identity, Vec3{0.0f, 0.0f, 0.0f}, env);
    rz.draw(farTri, identity, green);
    rz.draw(nearTri, identity, red);
    rz.flush();
    checkClose(rz.framebuffer().color[centerIndex].x, 1.0f, 1e-5f, "深度测试：换顺序仍是近处胜出");

    // ④ 近平面裁剪：有一个顶点跑到相机背后，三角形既不能崩，也不能整块丢掉
    const Mat4 proj = perspective(radians(60.0f), 1.0f, 0.1f, 100.0f);  // 相机在原点看向 -z
    Mesh straddling;
    straddling.vertices.push_back(Vertex{Vec3{-0.1f, -0.1f, -1.0f}, Vec3{0, 0, -1}, Vec2{0, 0}, Vec3{1, 1, 1}});
    straddling.vertices.push_back(Vertex{Vec3{0.1f, -0.1f, -1.0f}, Vec3{0, 0, -1}, Vec2{1, 0}, Vec3{1, 1, 1}});
    straddling.vertices.push_back(Vertex{Vec3{0.0f, 0.1f, 0.2f}, Vec3{0, 0, -1}, Vec2{0.5f, 1}, Vec3{1, 1, 1}});
    straddling.indices = {0, 1, 2};

    rz.framebuffer().clear();
    rz.begin(Mat4{}, proj, Vec3{0.0f, 0.0f, 0.0f}, env);
    rz.draw(straddling, Mat4{}, red);
    rz.flush();

    check(rz.drawnTriangles() > 0, "近平面裁剪：跨过相机的三角形被切成可见的部分");
    int redPixels = 0;
    for (const Vec3& c : rz.framebuffer().color)
        if (c.x > 0.5f) ++redPixels;
    const int totalPixels = rz.framebuffer().width * rz.framebuffer().height;
    check(redPixels > 0, "近平面裁剪：可见部分确实画出来了");
    check(redPixels < totalPixels / 2, "近平面裁剪：相机背后的部分被裁掉（没糊满全屏）");
}

// 把码点编成 UTF-8（自检里用来做往返测试）
std::string utf8Encode(uint32_t cp) {
    std::string s;
    if (cp < 0x80u) {
        s += char(cp);
    } else if (cp < 0x800u) {
        s += char(0xC0u | (cp >> 6));
        s += char(0x80u | (cp & 0x3Fu));
    } else if (cp < 0x10000u) {
        s += char(0xE0u | (cp >> 12));
        s += char(0x80u | ((cp >> 6) & 0x3Fu));
        s += char(0x80u | (cp & 0x3Fu));
    } else {
        s += char(0xF0u | (cp >> 18));
        s += char(0x80u | ((cp >> 12) & 0x3Fu));
        s += char(0x80u | ((cp >> 6) & 0x3Fu));
        s += char(0x80u | (cp & 0x3Fu));
    }
    return s;
}

// 字模自检。这里断言的具体数字都是照着 assets/font/pixel12.bin 实测的，
// 换字体时它们会第一时间炸，而不是等到游戏里文字变糊才发现。
void testFont() {
    Font font;
    const bool ok = font.loadFromFile("assets/font/pixel12.bin");
    check(ok, "字模：assets/font/pixel12.bin 能加载（缺了就重跑 tools/gen_font_atlas.py）");
    if (!ok) return;

    check(font.glyphW() == 12 && font.glyphH() == 12, "字模：格子 12x12");
    check(font.glyphCount() > 6000, "字模：字数覆盖 GB2312 常用字（>6000）");
    check(font.advanceOf(' ') > 0 && font.advanceOf(' ') < 12, "字模：半角空格是窄步进");

    // 二分查找的前提：码点表严格升序
    bool ascending = true;
    for (int i = 1; i < font.glyphCount(); ++i)
        if (font.codepointAt(i - 1) >= font.codepointAt(i)) ascending = false;
    check(ascending, "字模：码点表严格升序");

    // 取一个字的墨迹纵向范围
    int top = 0;
    int bottom = 0;
    auto inkRows = [&](uint32_t cp) {
        top = -1;
        bottom = -1;
        const int idx = font.indexOf(cp);
        if (idx < 0) return;
        for (int y = 0; y < font.glyphH(); ++y)
            for (int x = 0; x < font.glyphW(); ++x)
                if (font.inkAt(idx, x, y)) {
                    if (top < 0) top = y;
                    bottom = y;
                }
    };

    // 汉字要"顶天立地"：这曾是一个真实 bug —— 基线按 ascent 算，导致汉字顶部被切掉
    inkRows(0x4E2Du);  // 中
    check(top == 0, "字模：汉字顶到第 0 行（基线校准正确，没切头）");
    check(bottom == 10, "字模：汉字落在基线第 10 行");
    check(font.advanceOf(0x4E2Du) == 12, "字模：汉字占满全宽 12px");

    // ASCII 的 descender 不能被切：g 的尾巴要伸到基线以下
    inkRows('g');
    check(top >= 3 && bottom == 11, "字模：小写 g 的尾巴伸到第 11 行（descender 没被切）");
    check(font.advanceOf('A') < 12, "字模：ASCII 是比例宽度，不是全角");

    inkRows(' ');
    check(top < 0, "字模：空格不落墨");

    // 字集外的码点要明确报"没有"，而不是猜一个形状出来
    check(font.indexOf(0x2F00u) < 0, "字模：字集外的码点返回未命中");
    check(font.advanceOf(0x2F00u) == 12, "字模：未命中的字按一格宽度前进");

    // UTF-8 解码：1/2/3/4 字节都要解对，非法字节不能原地打转
    const std::string round = utf8Encode(0x4E2Du) + utf8Encode('A') + utf8Encode(0x1F600u);
    size_t i = 0;
    check(utf8Next(round, i) == 0x4E2Du, "UTF-8：解出 3 字节汉字");
    check(utf8Next(round, i) == 'A', "UTF-8：解出 1 字节 ASCII");
    check(utf8Next(round, i) == 0x1F600u, "UTF-8：解出 4 字节码点");
    check(i == round.size(), "UTF-8：刚好解完整串");
    const std::string bad = "\xFF\xE4";
    size_t j = 0;
    utf8Next(bad, j);
    check(j == 1, "UTF-8：非法首字节只前进 1 字节");

    // 排版宽度 = 逐字步进之和（比例字体，不是等宽）
    const std::string zh = utf8Encode(0x4E2Du) + utf8Encode(0x6587u);  // 中文
    check(font.measureLine(zh) == 24, "排版：两个汉字宽 24px");
    check(font.measureLine(utf8Encode('A') + utf8Encode('A')) == 2 * font.advanceOf('A'),
          "排版：ASCII 宽度按步进表累加");

    // 真的往帧缓冲画一遍：笔画要落下去
    Framebuffer fb;
    fb.resize(font.measureLine(zh) + 2, font.glyphH() + 2);
    fb.clear();
    const int drawnW = font.drawLine(fb, 0, 0, zh, Vec3{1.0f, 1.0f, 1.0f});
    check(drawnW == 24, "排版：drawLine 返回推进宽度 24");
    int lit = 0;
    for (const Vec3& c : fb.color)
        if (c.x > 0.5f) ++lit;
    check(lit > 60, "排版：汉字笔画确实画进了帧缓冲");

    // 多行：drawText 按 '\n' 换行，高度 = 两行字 + 一行行距
    const std::string twoLines = zh + "\n" + utf8Encode(0x5149u);  // 中文\n光
    check(font.measureText(twoLines) == 24, "排版：多行取最宽一行");
    Framebuffer fb2;
    fb2.resize(24 + 2, font.lineHeight() * 2 + 2);
    fb2.clear();
    check(font.drawText(fb2, 0, 0, twoLines, Vec3{1.0f, 1.0f, 1.0f}) == font.glyphH() * 2 + Font::kLineGap,
          "排版：drawText 返回两行高度（末行不留行距）");

    // 2 倍缩放：控制台面板大字号要用
    Framebuffer fb3;
    fb3.resize(48 + 4, 24 + 4);
    fb3.clear();
    check(font.drawLine(fb3, 0, 0, zh, Vec3{1.0f, 1.0f, 1.0f}, 1.0f, 2) == 48, "排版：2 倍缩放宽度翻倍");

    // 缺字：画空心框占位，既不崩也不白屏
    Framebuffer fb4;
    fb4.resize(font.glyphW() + 2, font.glyphH() + 2);
    fb4.clear();
    font.drawLine(fb4, 0, 0, utf8Encode(0x2F00u), Vec3{1.0f, 1.0f, 1.0f});
    int tofu = 0;
    for (const Vec3& c : fb4.color)
        if (c.x > 0.5f) ++tofu;
    check(tofu > 20 && tofu < 100, "字模：缺字画成空心框占位（不是实心块，也不是空白）");
    check(font.missingGlyphs() > 0, "字模：缺字会被计数（诊断用）");
}

// --preview：把字模画成终端里的 ASCII 图。
// 没有单元测试框架时，可视化就是最好的验证 —— 汉字结构、基线、字距一眼可查。
int runPreview(const std::string& textIn, int scale) {
    Font font;
    if (!font.loadFromFile("assets/font/pixel12.bin")) return 1;
    const std::string text = textIn.empty() ? std::string("黑光材质 DreamLab 2026") : textIn;

    std::printf("[font] %s：%dx%d，共 %d 字，行高 %d\n", font.path().c_str(), font.glyphW(), font.glyphH(),
                font.glyphCount(), font.lineHeight());
    std::printf("[font] 预览文本码点：");
    size_t i = 0;
    while (i < text.size()) {
        const uint32_t cp = utf8Next(text, i);
        if (cp == '\n') {
            std::printf("\\n ");
        } else {
            std::printf("U+%04X ", unsigned(cp));
        }
    }
    std::printf("\n");

    int lines = 1;
    for (char ch : text)
        if (ch == '\n') ++lines;
    const int w = font.measureText(text);
    const int h = font.glyphH() * lines + Font::kLineGap * (lines - 1);

    Framebuffer fb;
    fb.resize(w > 0 ? w : 1, h > 0 ? h : 1);
    fb.clear();
    font.drawText(fb, 0, 0, text, Vec3{1.0f, 1.0f, 1.0f}, 1.0f, scale);
    for (int y = 0; y < h; ++y) {
        std::string row = "  ";
        for (int x = 0; x < w; ++x) row += fb.color[size_t(y) * size_t(w) + size_t(x)].x > 0.5f ? '#' : '.';
        std::printf("%s\n", row.c_str());
    }
    std::printf("[font] 缺字 %d 个（空心框即缺字）\n", font.missingGlyphs());
    return 0;
}

void testWorld() {
    World w;
    const Workshop ws = buildWorkshop(w);
    (void)ws;

    // 房间尺寸：地板是 12x12 的平面，y 恰好贴地
    const Entity* floor = w.entity("地板");
    check(floor != nullptr, "世界：找得到「地板」");
    if (floor != nullptr) {
        checkClose(floor->aabbMin.x, -6.0f, 1e-3f, "世界：地板包围盒 x 下界");
        checkClose(floor->aabbMax.x, 6.0f, 1e-3f, "世界：地板包围盒 x 上界");
        checkClose(floor->aabbMax.y, 0.0f, 1e-3f, "世界：地板贴地（y 上界 = 0）");
    }

    // 墙的内表面要正好落在 ±6 上，否则玩家会卡在墙里或走进虚空
    const Entity* back = w.entity("后墙");
    const Entity* front = w.entity("前墙");
    check(back != nullptr && front != nullptr, "世界：找得到前后墙");
    if (back != nullptr) checkClose(back->aabbMax.z, -6.0f, 1e-3f, "世界：后墙内表面 z = -6");
    if (front != nullptr) checkClose(front->aabbMin.z, 6.0f, 1e-3f, "世界：前墙内表面 z = +6");

    // 斜着摆的木箱：8 角点变换后的 AABB 一定要比没转时更大（否则就是漏算了旋转）
    // 边长 0.8 的箱子绕 y 转 24°，AABB 的 x 方向尺寸应该 ≈ 0.8*(cos24+sin24) ≈ 1.06
    const Entity* crate = w.entity("木箱");
    check(crate != nullptr, "世界：找得到「木箱」");
    if (crate != nullptr) {
        const float sx = crate->aabbMax.x - crate->aabbMin.x;
        checkClose(sx, 1.056f, 0.01f, "世界：斜摆放的木箱 AABB 尺寸（旋转被算进去了）");
    }

    // 碰撞查询：出生点要空、展台里要有东西、墙里要有东西
    check(!w.overlapsSolid(Vec3{0.0f, 1.62f, 4.5f}, 0.3f), "碰撞：出生点没被堵住");
    check(w.overlapsSolid(Vec3{3.6f, 0.5f, -3.0f}, 0.3f), "碰撞：展台A 挡住人");
    check(w.overlapsSolid(Vec3{0.0f, 1.62f, 6.1f}, 0.3f), "碰撞：前墙挡住人");
    check(!w.overlapsSolid(Vec3{0.0f, 1.62f, -4.0f}, 0.3f), "碰撞：桌子前方可以站人");

    // 交互目标必须挂上命令，否则按 E 会没反应
    for (int i = 0; i < 3; ++i) {
        const Entity& orb = w.entities[size_t(ws.entOrbs[i])];
        check(!orb.prompt.empty() && !orb.command.empty(), "世界：展台上的球可以交互");
    }

    // 材质按名字能找回来 —— content/materials.txt 就靠这个对上号
    check(w.findMaterial("chrome") >= 0, "世界：材质表里有 chrome");
    check(w.findMaterial("不存在的材质") < 0, "世界：找不到的材质返回 -1");
}

// 走路和碰撞。这一节的存在理由：碰撞错了（差一厘米就穿模）靠肉眼看窗口是看不出来的，
// 只有能离屏跑、能断言"停在哪一米"才敢说它是对的。所有期望值都从世界的包围盒算出来，
// 不写死数字 —— 关卡改尺寸，测试跟着走。
void testPlayer() {
    World w;
    buildWorkshop(w);
    const float dt = 1.0f / 60.0f;

    auto run = [&](Player& p, const InputState& in, int frames) {
        bool bumped = false;
        for (int i = 0; i < frames; ++i) bumped = updatePlayer(p, in, w, dt) || bumped;
        return bumped;
    };

    // ① 不动就不该挪
    {
        Player p;
        run(p, InputState{}, 120);
        checkClose(p.feet.x, 0.0f, 1e-5f, "玩家：不按键就不动（x）");
        checkClose(p.feet.z, 4.5f, 1e-5f, "玩家：不按键就不动（z）");
    }

    // ② 按 W 一秒的位移：起步有加速段，所以是 2.9~3.2 米之间，但绝不该超过满速。
    //    这条同时钉住两件事：人真的在走（不是没动），以及没有"加速反而更快"的鬼故事。
    {
        Player p;
        InputState in;
        in.moveForward = 1.0f;
        run(p, in, 60);
        const float travelled = 4.5f - p.feet.z;
        check(travelled > 2.9f && travelled < Player::kWalkSpeed,
              "玩家：按 W 一秒走 2.9~3.2 米（起步加速，不会超速）");
        checkClose(p.feet.x, 0.0f, 1e-4f, "玩家：直着走不会横飘");
    }

    // ③ 往桌子走会停在外沿（正是"没穿模"的定义），再顶两秒也不会往里挤
    const Entity* desk = w.entity("桌面");
    check(desk != nullptr, "玩家：找得到「桌面」");
    if (desk != nullptr) {
        Player p;
        InputState in;
        in.moveForward = 1.0f;
        const bool bumped = run(p, in, 300);
        check(bumped, "玩家：撞到桌子会报「撞了」");
        checkClose(p.feet.z, desk->aabbMax.z + Player::kRadius, 0.01f, "玩家：停在桌子外沿，没穿模");
        const float stopped = p.feet.z;
        run(p, in, 120);
        checkClose(p.feet.z, stopped, 1e-4f, "玩家：顶着桌子不会一点一点往里挤");
    }

    // ④ 往左走会停在左墙内表面
    const Entity* leftWall = w.entity("左墙");
    check(leftWall != nullptr, "玩家：找得到「左墙」");
    if (leftWall != nullptr) {
        Player p;
        InputState in;
        in.moveRight = -1.0f;
        run(p, in, 400);
        checkClose(p.feet.x, leftWall->aabbMax.x + Player::kRadius, 0.01f, "玩家：停在左墙内表面");

        // ⑤ 贴着墙斜着走要"滑过去"而不是"卡死"：x 被墙挡住，z 照走不误。
        //    第一人称最基本的一条手感，也是最容易写成"撞上就完全不动"的地方。
        Player q;
        InputState diag;
        diag.moveForward = 1.0f;
        diag.moveRight = -1.0f;
        run(q, diag, 400);
        checkClose(q.feet.x, leftWall->aabbMax.x + Player::kRadius, 0.02f, "玩家：斜着顶墙时 x 被挡住");
        check(q.feet.z < 0.0f, "玩家：斜着顶墙时 z 照样前进（贴墙滑行）");
    }

    // ⑥ 斜着按两个键不能更快 —— 少了归一化这一步，斜走会快 41%，而人在窗口里
    //    只会觉得"这游戏有点飘"，根本查不出来
    {
        Player a, b;
        InputState straight;
        straight.moveForward = 1.0f;
        InputState diagonal;
        diagonal.moveForward = 1.0f;
        diagonal.moveRight = 1.0f;
        run(a, straight, 60);
        run(b, diagonal, 60);
        // 出生点在 (0,0,4.5)：位移就是 (x, 0, z-4.5)
        const float dStraight = 4.5f - a.feet.z;
        const float dDiagonal = length(Vec3{b.feet.x, 0.0f, b.feet.z - 4.5f});
        checkClose(dDiagonal, dStraight, dStraight * 0.02f, "玩家：斜着走不快 1.41 倍（速度归一化）");
    }

    // ⑦ 鼠标视角：右移 = 右转、下移 = 低头，俯仰有上限，yaw 收在 (-pi, pi]
    {
        Player p;
        InputState in;
        in.lookDX = 100.0f;
        in.lookDY = 50.0f;
        updatePlayer(p, in, w, dt);
        checkClose(p.yaw, -100.0f * Player::kLookSpeed, 1e-6f, "玩家：鼠标右移 = 向右转");
        checkClose(p.pitch, -0.04f - 50.0f * Player::kLookSpeed, 1e-6f, "玩家：鼠标下移 = 低头");

        Player up;
        InputState ceiling;
        ceiling.lookDY = -1.0e6f;
        updatePlayer(up, ceiling, w, dt);
        checkClose(up.pitch, 1.5533f, 1e-3f, "玩家：抬头有上限（不会从头顶翻过去）");

        Player spin;
        InputState around;
        around.lookDX = 1.0e9f;
        updatePlayer(spin, around, w, dt);
        check(spin.yaw >= -kPi && spin.yaw <= kPi, "玩家：一直往一个方向转，yaw 也不会涨到溢出");
    }

    // ⑧ --noclip：调试和拍图要能穿墙
    {
        Player p;
        InputState in;
        in.moveForward = 1.0f;
        run(p, in, 300);
        const float stopped = p.feet.z;
        p.noclip = true;
        run(p, in, 60);
        check(p.feet.z < stopped - 0.5f, "玩家：--noclip 下能穿过桌子");
    }

    // ⑨ dt = 0（时钟没走）不该动，也不该除以零
    {
        Player p;
        InputState in;
        in.moveForward = 1.0f;
        updatePlayer(p, in, w, 0.0f);
        checkClose(p.feet.z, 4.5f, 1e-6f, "玩家：dt=0 时原地不动");
    }
}

// 视线射线：按 E 能不能选中东西，全看这里。写得宽松（kPad）是为了让 4cm 厚的
// 展板、8mm 厚的屏幕也能被选中；但"宽松"不能变成"隔墙也能拿东西"。
void testRaycast() {
    World w;
    buildWorkshop(w);

    // ① 真的"走"到桌前（不是瞬移），再看显示器 → 选中「终端」。
    //    先用物理走到够得着的地方，再验证视线 —— 于是"桌边停得住"和"看得到终端"
    //    变成一条链上的两件事，桌子往前挪 10cm 这条就会红。
    {
        Player p;
        InputState in;
        in.moveForward = 1.0f;
        for (int i = 0; i < 300; ++i) updatePlayer(p, in, w, 1.0f / 60.0f);
        p.setLookAt(Vec3{0.0f, 1.035f, -5.28f});
        const World::RayHit hit = w.castRay(p.eye(), p.forward(), Player::kReach);
        check(hit.entity == w.findEntity("终端"), "交互：走到桌前看显示器，选中「终端」");
        check(!hit.blocked, "交互：选中终端时不算被挡住");
    }

    // ② 出生点直视前方：3.2 米内什么都没有，就该什么都没有（不能乱报）
    {
        Player p;
        p.pitch = 0.0f;
        const World::RayHit hit = w.castRay(p.eye(), p.forward(), Player::kReach);
        check(hit.entity < 0 && !hit.blocked, "交互：够不到就返回「什么都没有」");
    }

    // ③ 隔着墙够不到展板：实心体先挡住射线
    {
        Player p;
        p.feet = Vec3{-8.0f, 0.0f, 1.2f};  // 屋子外面（左墙以西）
        p.setLookAt(Vec3{-5.94f, 1.9f, 1.2f});
        const World::RayHit hit = w.castRay(p.eye(), p.forward(), Player::kReach);
        check(hit.entity < 0 && hit.blocked, "交互：隔着墙够不到东西（先被实心体挡住）");
    }

    // ④ 走近了就能选中 4cm 厚的展板 —— 就是 kPad 存在的理由
    {
        Player p;
        p.feet = Vec3{-5.0f, 0.0f, 1.2f};
        p.setLookAt(Vec3{-5.94f, 1.9f, 1.2f});
        const World::RayHit hit = w.castRay(p.eye(), p.forward(), Player::kReach);
        check(hit.entity == w.findEntity("展板"), "交互：4cm 厚的展板也能被选中");
    }

    // ⑤ M3.4 的验收主线：走到展台前看着球按 E。球带的命令用的是 M3.3 就有的
    //    inspect，所以"交互 → 命令 → 控制台"这条链现在就能端到端验证。
    {
        Player p;
        p.feet = Vec3{2.4f, 0.0f, -1.2f};
        p.setLookAt(Vec3{3.6f, 1.4f, -1.2f});
        const World::RayHit hit = w.castRay(p.eye(), p.forward(), Player::kReach);
        check(hit.entity == w.findEntity("塑料球"), "交互：看着展台上的塑料球能选中它");
        if (hit.entity >= 0) {
            check(w.entities[size_t(hit.entity)].command == "inspect 塑料球",
                  "交互：塑料球带的命令是 inspect 塑料球");
        }

        // ⑥ 同一个位置，把手伸短到 0.2 米就够不着 —— 确认距离真的在起作用
        const World::RayHit tooFar = w.castRay(p.eye(), p.forward(), 0.2f);
        check(tooFar.entity < 0, "交互：超出 reach 就够不到（距离上限有效）");
    }

    // ⑦ 视线为零（不该发生，但不能崩、不能瞎报）
    {
        Player p;
        const World::RayHit hit = w.castRay(p.eye(), Vec3{0.0f, 0.0f, 0.0f}, Player::kReach);
        check(hit.entity < 0 && !hit.blocked, "交互：视线为零向量时安全返回");
    }
}

// content/*.txt 的自检。这里的重点是「报错质量」和「数据↔场景对得上号」——
// 改数据的同学没有调试器，他唯一的反馈就是这些报错文字。
// ---- T4.5：--all-levels --auto 用的"解答表" ----------------------------------
//
// 每关的解答写成 content/ 的语法，走**真实的解析 → 应用**那条路（学生按 R 走的就是
// 它），但**不落盘** —— 绝不能把学生的 content/*.txt 覆盖掉。
//
// 第 0 关故意没有条目。它的正解是改 game/shaders/lighting.cpp 里的 kAmbientStrength，
// 那是**编译期常数**，运行时改不了。想让它自动过关只能用别的路（比如在评委视线里
// 点一盏灯 —— 实测也能读到 100%），可那是学生不会走的路径；把它算进来，"四关都能
// 自动达成"这句话就注水了。宁可少一关，也不让验收指标虚高。
struct AutoSolution {
    int level;
    const char* what;   // 人话说明，打在输出里
    const char* patch;  // content/ 语法
};

const AutoSolution kAutoSolutions[] = {
    {1, "灯装回吊灯位、拧到 48，灯罩点起来",
     "light 0 { pos 2.20 2.90 1.20  intensity 48 }\n"
     "material lamp { emissive 4.2 3.8 3.0 }\n"},
    {2, "三个球改成样板值（只动出厂是错的那几个数，其余不碰）",
     "material chrome  { roughness 0.06  metallic 1.00 }\n"
     "material plastic { roughness 0.26 }\n"
     "material clay    { albedo 0.74 0.53 0.32 }\n"},
    {3, "地板平铺 4x4、repeat、bilinear",
     "material floor { uvscale 4 4  wrap repeat  filter bilinear }\n"},
};

const AutoSolution* autoSolutionFor(int level) {
    for (const AutoSolution& s : kAutoSolutions)
        if (s.level == level) return &s;
    return nullptr;
}

// 把某关的解答应用上去。返回 false = 这份解答没真正生效，调用方必须当失败处理 ——
// 这正是 T4.2 那条教训：注入式测量不检查解析结果，测的就是"退回代码默认值的世界"，
// 数字好看但没意义。所以这里解析失败、名字对不上、撞上锁，三种都算失败。
bool applyAutoSolution(World& world, int level, std::vector<std::string>& log) {
    const AutoSolution* s = autoSolutionFor(level);
    if (s == nullptr) return false;

    const ContentPatch patch = parseContent(s->patch, "<--auto>");
    if (!patch.ok()) {
        for (const std::string& e : patch.errors)
            std::fprintf(stderr, "[--auto] 第 %d 关的解答解析失败：%s\n", level, e.c_str());
        return false;
    }
    const ApplyStats st = applyContent(world, patch, &log);
    if (st.missing != 0 || st.locked != 0) {
        std::fprintf(stderr, "[--auto] 第 %d 关的解答没生效：对不上的名字 %d 个、撞锁 %d 个\n",
                     level, st.missing, st.locked);
        return false;
    }
    return true;
}


void testContent() {
    // ① 正常解析：注释、逗号当空格、同一行写完一个块
    const ContentPatch good = parseContent(
        "# 注释\n"
        "material chrome { albedo 0.95, 0.93, 0.90  roughness 0.06  metallic 1.0 }\n"
        "ambient 0.4 0.4 0.5\n",
        "t.txt");
    check(good.ok(), "content：合法输入不报错");
    check(good.materials.size() == 1 && good.lights.empty(), "content：解析出一个材质块");
    check(good.hasAmbient, "content：解析出 ambient");
    if (good.materials.size() == 1) {
        const MaterialPatch& m = good.materials[0];
        check(m.name == "chrome", "content：材质名解析正确");
        check(m.hasAlbedo && m.hasRoughness && m.hasMetallic && !m.hasEmissive,
              "content：只记下文件里写了的那几项（没写的保持原值）");
        checkClose(m.roughness, 0.06f, 1e-6f, "content：roughness 数值正确");
        checkClose(m.albedo.z, 0.90f, 1e-6f, "content：逗号分隔的 albedo 也认");
    }

    // ② 报错必须带「文件名:行号」，且拼错的键名要给出建议
    const ContentPatch typoField = parseContent("material x {\n    roudhness 0.5\n}\n", "content/materials.txt");
    check(!typoField.ok(), "content：拼错字段名会报错");
    if (!typoField.ok()) {
        check(typoField.errors[0].find("content/materials.txt:2:") == 0, "content：报错带文件名和行号（第 2 行）");
        check(typoField.errors[0].find("roughness") != std::string::npos, "content：报错给出正确拼写建议");
    }

    // ③ 各种写坏的写法：都要报错，且不能崩
    check(!parseContent("material a { albedo 1 1 }\n", "t").ok(), "content：数字个数不对会报错");
    check(!parseContent("material a { metallic nan }\n", "t").ok(), "content：nan 被拒绝（否则渲染出满屏雪花）");
    check(!parseContent("material a {\n albedo 1 1 1\n", "t").ok(), "content：块没闭合会报错");
    check(!parseContent("}\n", "t").ok(), "content：多出来的 } 会报错");
    check(!parseContent("metarial a { }\n", "t").ok(), "content：顶层关键字拼错会报错");
    check(!parseContent("material {\n}\n", "t").ok(), "content：material 后面缺名字会报错");
    check(!parseContent("material a albedo 1 1 1\n", "t").ok(), "content：缺 { 会报错");

    // 少写一个数字时，报错要「少而准」——不能把下一行的 roughness 也当成 albedo 的数
    const ContentPatch shortNums = parseContent("material a {\n albedo 0.5 0.5\n roughness 0.3\n}\n", "t");
    check(shortNums.errors.size() == 1, "content：少写一个数只报一处错（没连累下一行）");
    if (!shortNums.ok()) check(shortNums.errors[0].find("albedo") != std::string::npos, "content：报错指到出问题的那个字段");

    // ④ 应用到世界：值真的改了
    World w;
    buildWorkshop(w);
    const int chrome = w.findMaterial("chrome");
    const ContentPatch edit = parseContent("material chrome { roughness 0.99 }\nlight 0 { pos 1.5 2.0 3.5  intensity 7 }\n", "t");
    std::vector<std::string> log;
    ApplyStats st = applyContent(w, edit, &log);
    check(st.materials == 1 && st.lights == 1, "content：应用了一项材质和一项灯");
    check(log.empty(), "content：合法数据应用时没有任何警告");
    checkClose(w.materials[size_t(chrome)].roughness, 0.99f, 1e-6f, "content：改材质真的落到世界上");
    checkClose(w.lights[0].intensity, 7.0f, 1e-6f, "content：改灯真的落到世界上");
    // 灯的位置是第 1 关的题面，所以它单独占一项：改 pos 得真的把灯搬走，
    // 而且写 pos 的那一行不许顺手动到 color / radius（每一项都是独立的）。
    checkClose(w.lights[0].position.z, 3.5f, 1e-6f, "content：改灯的 pos 真的把灯搬到了世界上");
    check(edit.lights[0].hasPos && !edit.lights[0].hasColor && !edit.lights[0].hasRadius,
          "content：只写 pos 的那一行，只动 pos（不动 color / radius）");

    // ⑤ 名字写错 / 名字写对但上了锁：两种「写了却没生效」都要出声，不能悄悄吞掉。
    //    前者会把可用的名字列出来（学生最容易踩的坑），后者要点明「这是标准答案，改不动」。
    const ContentPatch typoName = parseContent("material 镜面球 { roughness 0.5 }\n", "t");
    std::vector<std::string> log2;
    st = applyContent(w, typoName, &log2);
    check(st.missing == 1 && st.materials == 0, "content：世界里没有的材质名记为「没对上号」");
    check(log2.size() == 1 && log2[0].find("chrome") != std::string::npos, "content：名字写错时列出可用的材质名");
    checkClose(w.materials[size_t(chrome)].roughness, 0.99f, 1e-6f, "content：名字写错不会误改到别的材质");

    // 样板是第 2 关的标准答案，上着锁。这条挡的是"把卷子抄了"那种过关：
    // 学生把样板也改成球现在的乱值，两边一样，第 2 关就白送 100%。
    const int refChrome = w.findMaterial("ref_chrome");
    check(refChrome >= 0, "content：世界里真的有 ref_chrome 这份样板材质");
    const ContentPatch tamper = parseContent("material ref_chrome { albedo 0.98 0.97 0.96  roughness 0.55  metallic 0.0 }\n", "t");
    std::vector<std::string> logLock;
    st = applyContent(w, tamper, &logLock);
    check(st.locked == 1 && st.materials == 0, "content：样板材质上着锁，写了不生效（也不算进「应用成功」）");
    check(logLock.size() == 1 && logLock[0].find("标准答案") != std::string::npos,
          "content：写到上锁的材质时，明说「这是标准答案，改不动」");
    checkClose(w.materials[size_t(refChrome)].roughness, 0.06f, 1e-6f, "content：样板被写了一把，数值一动没动");
    checkClose(w.materials[size_t(refChrome)].albedo.x, 0.95f, 1e-6f, "content：样板被写了一把，颜色也一动没动");

    // ⑥ 仓库里真正的那两个数据文件：语法必须干净，而且每一项都要在场景里对得上号。
    //    否则学生看到的就是「改了没反应」，而原因只是一条没人看的警告。
    World w2;
    buildWorkshop(w2);
    std::vector<std::string> log3;
    for (const std::string& file : contentFileList()) {
        const ContentPatch p = loadContentFile(file);
        check(p.ok(), (std::string("content：仓库里的 ") + file + " 没有语法错误").c_str());
        if (!p.ok()) {
            std::printf("[selftest]   %s\n", p.errors[0].c_str());
            continue;
        }
        applyContent(w2, p, &log3);
    }
    for (const std::string& line : log3) std::printf("[selftest]   %s\n", line.c_str());
    check(log3.empty(), "content：仓库里的数据和场景完全对得上（没有哪一项被跳过）");

    // ⑦ 按 R 不该让画面"跳一下"：同一份数据应用两遍，画面必须一像素不差。
    //    这条原来盯的是"数据文件和代码默认值一致"，可第 0 关偏偏要用数据把灯关掉 ——
    //    那条已经不成立了。真正会让学生骂人的现像是"我按了 R，画面自己变了"，
    //    那就直接测它：同一份数据多应用一遍，结果必须完全一样。
    World w2b;
    buildWorkshop(w2b);
    std::vector<std::string> log4;
    Judge judge;
    for (const std::string& file : contentFileList()) applyContent(w2b, loadContentFile(file), &log4);
    judge.evaluate(w2b, levelAt(0));
    const Framebuffer once = judge.frame();
    for (const std::string& file : contentFileList()) applyContent(w2b, loadContentFile(file), &log4);
    const LevelStatus again = judge.evaluate(w2b, levelAt(0));
    const Framebuffer twice = judge.frame();
    bool samePixels = once.color.size() == twice.color.size();
    for (size_t i = 0; samePixels && i < once.color.size(); ++i)
        samePixels = sameVec3(once.color[i], twice.color[i]);
    check(samePixels, "content：同一份数据应用两遍，画面逐像素一致（按 R 不会跳）");
    checkClose(again.luminance, judge.evaluate(w2b, levelAt(0)).luminance, 1e-9f,
               "关卡：评委机位同一场景连判两次，结果一模一样");

    // ⑧ wrap / filter 是"值是词"的字段（第 3 关的三个词里占了两个）。它们比数字字段
    //    多两条要求，两条都是给学生看的：词写错了要猜得到他想写哪个；写成编号
    //    （wrap 1）要说明"这里写的是词"——那是很自然的猜测，不说明的话他只会以为
    //    自己手滑。这两个词本身就是要教的东西，报错得教会人，不能只说"错的"。
    const ContentPatch texPatch = parseContent("material floor { wrap repeat  filter bilinear }\n", "t");
    check(texPatch.ok() && texPatch.materials.size() == 1 && texPatch.materials[0].hasWrap &&
              texPatch.materials[0].hasFilter,
          "content：wrap / filter 的值是词，能解析");
    check(texPatch.materials[0].wrapMode == kWrapRepeat && texPatch.materials[0].filterMode == kFilterBilinear,
          "content：repeat / bilinear 对应到世界里的那两个枚举值");
    const ContentPatch texPatch2 = parseContent("material floor { wrap clamp  filter nearest }\n", "t");
    check(texPatch2.ok() && texPatch2.materials[0].wrapMode == kWrapClamp &&
              texPatch2.materials[0].filterMode == kFilterNearest,
          "content：clamp / nearest 也认（四个词都认）");
    const ContentPatch typoWrap = parseContent("material floor { wrap repeta }\n", "t");
    check(!typoWrap.ok() && typoWrap.errors[0].find("repeat") != std::string::npos,
          "content：wrap 拼错时提示「是不是想写 repeat」");
    const ContentPatch numWrap = parseContent("material floor { wrap 1 }\n", "t");
    check(!numWrap.ok() && numWrap.errors[0].find("词") != std::string::npos,
          "content：wrap 写成编号（wrap 1）被挡回来，并说明这里要写词");
    const ContentPatch valGone = parseContent("material floor { wrap }\n", "t");
    // 值没写时不把 } 当值吃掉，否则后面还会再报一串假错误
    check(valGone.errors.size() == 1, "content：wrap 后面没写词只报一处错（没把 } 当成值吃掉）");
    // 样板材质上着锁：贴图设置也改不动（锁是全字段的，不是只管 albedo/roughness）
    const ContentPatch lockTex = parseContent("material ref_chrome { wrap clamp  filter nearest }\n", "t");
    st = applyContent(w, lockTex, &logLock);
    check(st.locked == 1 && w.materials[size_t(refChrome)].wrapMode == kWrapRepeat,
          "content：样板上了锁，贴图设置也一样改不动");
}

// 关卡自检。关卡表是「学生改完代码之后还会继续长大」的东西 —— 后面还要加三关，
// 每加一关都是往 kLevels 里塞一个指针，塞错了不该等到宣讲现场才发现。
//
// 这里刻意不检查「出厂时进度必须是 0%」：学生把第 0 关过了之后，他机器上的
// 场景本来就该是亮的、本来就该是 100% —— 自检要能在「没做」和「做完了」
// 两种状态下都通过，否则学生做完题一跑自检以为自己做错了。
void testLevel() {
    World w;
    buildWorkshop(w);
    std::vector<std::string> log;
    for (const std::string& file : contentFileList()) applyContent(w, loadContentFile(file), &log);

    // ① 关卡表的结构
    check(levelCount() >= 1, "关卡：至少注册了一关");
    check(findLevel("dark") == 0, "关卡：第 0 关的 id 是 dark（--level 和存档里记的都是它）");
    check(findLevel("根本没有这一关") == -1, "关卡：id 找不到时返回 -1（不是 0，也不是崩溃）");
    check(&levelAt(-1) == &levelAt(0), "关卡：下标 -1 被夹回第 0 关");
    check(&levelAt(99) == &levelAt(levelCount() - 1), "关卡：下标越界被夹到最后一关");

    Judge judge;
    for (int i = 0; i < levelCount(); ++i) {
        const Level& lv = levelAt(i);
        const std::string tag = "关卡 " + std::to_string(i) + "：";
        check(lv.id[0] != '\0' && lv.title[0] != '\0' && lv.goal[0] != '\0' && lv.hint[0] != '\0',
              (tag + "id / 标题 / 目标 / 提示，四样都得写").c_str());
        check(lv.progress != nullptr, (tag + "有判分函数（没有就永远过不了关）").c_str());
        check(findLevel(lv.id) == i, (tag + "id 在表里是唯一的").c_str());

        const LevelStatus st = judge.evaluate(w, lv);
        check(st.progress >= 0.0f && st.progress <= 1.0f, (tag + "进度永远落在 0~1 之间").c_str());
    }
    check(judge.frame().width == kJudgeWidth && judge.frame().height == kJudgeHeight,
          "关卡：评委相机拍的是固定的 96x54 小图（不管玩家站在哪、看哪）");

    // ①b 代码默认值不许白送过关。把 content/ 整个丢掉（= 数据文件被删掉 / 全写坏），
    //     每一关都得读 0 —— 这条挡的是"删个文件反而过了"。前面三关各有一处默认值是
    //     故意留黑的（灯、球、地板），但那是三处分散的实现细节；这里从外面把整件事
    //     钉住：以后再加关卡，忘了把默认值调成"没做完的样子"，自检当场会红。
    //    注意**从第 1 关开始查**：第 0 关的判定跟着着色器里的 kAmbientStrength 走，
    //    而那正是学生要亲手改的那一行 —— 他改对之后（房间本来就亮了），这一关当然
    //    不读 0。以前这里连第 0 关一起查，于是"学生照游戏说的做对了、一跑自检却报错"，
    //    一条会因为"做对了"而红的断言本身就不该存在。
    World wRaw;
    buildWorkshop(wRaw);
    int rawBad = -1;
    for (int i = 1; i < levelCount(); ++i) {
        if (judge.evaluate(wRaw, levelAt(i)).progress > 0.0f) rawBad = i;
    }
    std::string rawMsg = "关卡：content/ 一个文件都没有时，每一关都读 0%（丢文件不等于过关）";
    if (rawBad >= 0) rawMsg += " —— 第 " + std::to_string(rawBad) + " 关不为 0";
    check(rawBad < 0, rawMsg.c_str());

    //    第 0 关换成一条**永远成立**的断言：删掉 content/ 不会让它比有数据时更亮。
    //    这才是"删数据白送过关"对第 0 关的意义 —— 数据不该是它的开关，
    //    它的开关是学生自己改的那行代码。
    {
        // （loadFresh 定义在下面几行，这里就地写一遍那三行 —— 这一块比它早）
        World wFull0;
        buildWorkshop(wFull0);
        std::vector<std::string> logRaw;
        for (const std::string& file : contentFileList()) {
            applyContent(wFull0, loadContentFile(file), &logRaw);
        }
        const float pFull = judge.evaluate(wFull0, levelAt(0)).progress;
        const float pRaw = judge.evaluate(wRaw, levelAt(0)).progress;
        check(pRaw <= pFull + 1e-4f, "关卡：删掉 content/ 不会让第 0 关更亮（数据不是它的开关）");
    }

    // ①c 交互按关卡隔离。零基础的人进了游戏会把 E 按一圈 —— 第 0 关（一间黑屋子）
    //     按到材质球上会弹出一屏 "roughness / metallic"，他不知道那是什么、也不知道
    //     跟自己该干的事有什么关系。所以每件东西标了"这是哪一关的事"，
    //     不是这一关的不给开控制台（但会回一句"这是第 N 关的事"）。
    {
        World wg;
        buildWorkshop(wg);
        const Entity* lamp = wg.entity("吊灯");
        const Entity* term = wg.entity("终端");
        const Entity* board = wg.entity("展板");
        const Entity* clay = wg.entity("陶土球");
        check(lamp != nullptr && term != nullptr && board != nullptr && clay != nullptr,
              "关卡：吊灯/终端/展板/陶土球这几个可交互的东西都在");
        if (lamp != nullptr && term != nullptr && board != nullptr && clay != nullptr) {
            check(interactableNow(*lamp, 1), "关卡：第 1 关能看吊灯铭牌（那一关就靠它）");
            check(!interactableNow(*lamp, 0), "关卡：第 0 关看不了吊灯铭牌");
            check(!interactableNow(*lamp, 2), "关卡：第 2 关也看不了吊灯铭牌");
            check(interactableNow(*clay, 2), "关卡：材质球在第 2 关能按");
            check(!interactableNow(*clay, 0), "关卡：材质球在第 0 关不能按");
            check(interactableNow(*term, 0) && interactableNow(*term, 3),
                  "关卡：终端哪一关都能用（它是每关的公告板）");
            check(interactableNow(*board, 2), "关卡：展板哪一关都能看（它不是任何一关的作业）");

            // 准星提示也要跟着：用不上的东西**不提示**"按 E" ——
            // 提示了却不能按，比干脆不提示更让人困惑（"我按了怎么没反应"）。
            int lampIdx = -1;
            for (size_t i = 0; i < wg.entities.size(); ++i) {
                if (wg.entities[i].name == "吊灯") lampIdx = int(i);
            }
            check(lampIdx >= 0, "关卡：能按名字找到吊灯");
            check(!promptFor(wg, lampIdx, 1).empty(), "关卡：第 1 关准星提示「按 E 看吊灯铭牌」");
            check(promptFor(wg, lampIdx, 0).empty(), "关卡：第 0 关准星不提示吊灯");
        }
    }

    // ①d 过关收尾：推进到下一关 + 念下一关的题面。
    //     这条挡的是"两条路各写一半"那一类死角 —— 开场就发现过关、和循环里当场过关，
    //     必须走同一个函数。实测撞到过：开场那条只打印一句"你已经过了这一关"、
    //     不推进，人停在一个已完成的房间里，题面不更新、HUD 的"下一步"是空的。
    {
        Console c;
        Judge j2;
        World w2;
        buildWorkshop(w2);
        LevelRuntime r;
        r.index = 1;
        r.status = j2.evaluate(w2, levelAt(1));
        // 返回值就是"四关全通了没有"—— 入群二维码的解锁条件。这条不能散在别处：
        // 它错了的表现是"码该开的时候没开"，而那种错在自检里本来是看不出来的。
        check(!advanceAfterPass(c, w2, j2, r),
              "流程：还没到最后关，返回值 false（群码不该解锁）");
        check(r.index == 2, "流程：过关收尾把人送到下一关（不再停在已完成的房间里）");
        checkClose(r.status.progress, j2.evaluate(w2, levelAt(2)).progress, 1e-6f,
                   "流程：推进之后 rt.status 跟着重判（面板和题面说的才是同一关）");
        bool saidNext = false;
        for (int i = 0; i < c.lineCount(); ++i) {
            if (c.lineAt(i).find("下面是下一关") != std::string::npos) saidNext = true;
        }
        check(saidNext, "流程：过关收尾会把「下面是下一关」说出来");

        // 最后一关：没有下一关可去，但要说"四关都过了"，不能留一片空白
        Console c2;
        LevelRuntime r2;
        r2.index = levelCount() - 1;
        r2.status = j2.evaluate(w2, levelAt(levelCount() - 1));
        check(advanceAfterPass(c2, w2, j2, r2),
              "流程：过了最后一关，返回值 true（入群二维码就是靠它解锁的）");
        check(r2.index == levelCount() - 1, "流程：最后一关过了就停在那儿（本来也没有下一关）");
        bool saidDone = false;
        for (int i = 0; i < c2.lineCount(); ++i) {
            if (c2.lineAt(i).find("四关都过了") != std::string::npos) saidDone = true;
        }
        check(saidDone, "流程：最后一关过了会说「四关都过了」，不留空白");
    }

    // ② 判分跟着世界走：同一场景，把灯打开，评委机位必须更亮、进度只能升不能降。
    //    这条盯的是「判分方向没写反」——把变亮判成变暗是这一块最容易犯的错。
    const Level& lv0 = levelAt(0);
    const LevelStatus dark = judge.evaluate(w, lv0);
    applyContent(w, parseContent("light 0 { intensity 48 }\nlight 1 { intensity 26 }\n", "t"), &log);
    const LevelStatus lit = judge.evaluate(w, lv0);
    check(lit.luminance > dark.luminance, "关卡：把两盏灯打开，评委机位确实更亮了");
    check(lit.progress >= dark.progress, "关卡：更亮的场景进度不会更低（判分方向是对的）");

    // 判分要拍两张（开着灯 / 灯全关）才算得出"灯自己贡献了多少亮度"，而最后留在
    // framebuffer 里的必须是"开着灯"的那张 —— 它就是关卡在 view.frame 里拿到的那张。
    // 顺序一旦写反，关卡想自己量画面某个角落，量到的会是一张全黑的假图，
    // 而且这种错在纯黑房间里看不出来（两张一样黑）。这里用开着灯的场景钉住它。
    check(std::fabs(judge.frame().meanLuminance() - lit.luminance) < 1e-6f,
          "关卡：交到关卡手里的 frame 是开着灯的那张真画面（不是「灯全关」那张）");

    // ③ 存档里的进度位掩码。它决定"下次进门要不要再欢呼一次"，所以读写都得对；
    //    而移位一旦越过 31 位在 C++ 里是未定义行为 —— 越界下标必须被拦住。
    check(!levelDone(0, 0) && levelDone(markLevelDone(0, 0), 0), "关卡：过关位掩码写进去、读得出来");
    const uint32_t two = markLevelDone(markLevelDone(0, 3), 5);
    check(levelDone(two, 3) && levelDone(two, 5) && !levelDone(two, 4),
          "关卡：写第 3、5 关，只有这两位亮（第 4 关不受影响）");
    check(markLevelDone(0, -1) == 0 && markLevelDone(0, kMaxTrackedLevels) == 0,
          "关卡：越界的关卡号写不进掩码（也踩不到未定义行为）");
    check(!levelDone(0xFFFFFFFFu, -1) && !levelDone(0xFFFFFFFFu, kMaxTrackedLevels) && !levelDone(0xFFFFFFFFu, 999),
          "关卡：越界的关卡号读出来永远是「没过」");

    // ④ 「进度条上的 100%」和「真的过关了」必须是同一件事。
    //    这条是拿真实 bug 换来的：达标线曾经正好压在实测亮度上，于是学生照着提示
    //    改完代码，屏幕写着 100%，程序却判定"没过"（progress = 0.9999）——
    //    那一刻他只会觉得自己被骗了。这里把整个 0~1 扫一遍，两个口径不许有一处对不上。
    bool pctHonest = true;
    for (int i = 0; i <= 20000 && pctHonest; ++i) {
        LevelStatus s;
        s.progress = float(i) / 20000.0f;
        if ((progressPercent(s) == 100) != s.passed()) pctHonest = false;
    }
    check(pctHonest, "关卡：进度条写「100%」和「判定过关」永远同时发生");
    check(progressPercent(LevelStatus{0.9999f, 0.0f}) == 99, "关卡：差一点点过关时，进度条老实写 99%");

    // ⑤ 第 1 关是「改数据」关，判分里有一半读的是数据而不是画面 —— 必须验一下
    //    这两条都不是摆设。理由很实在：第 1 关的画面判定可以靠调亮度蒙过去
    //    （把 intensity 拧到 500，房间照样亮），要是没有数据那两条，学生就学会了
    //    「不用理解题，把数字调大就行」——这比不做题还糟。
    const Level& lv1 = levelAt(1);
    World w3;
    buildWorkshop(w3);
    std::vector<std::string> log5;
    for (const std::string& file : contentFileList()) applyContent(w3, loadContentFile(file), &log5);
    applyContent(w3, parseContent("material lamp { emissive 0 0 0 }\nlight 0 { pos 2.2 2.9 1.2  intensity 500 }\n", "t"), &log5);
    check(!judge.evaluate(w3, lv1).passed(), "关卡 1：灯罩不亮，灯拧到 500 也过不了关（判分读的是数据）");

    applyContent(w3, parseContent("material lamp { emissive 4.2 3.8 3.0 }\nlight 0 { pos 0 1.1 4.6 }\n", "t"), &log5);
    check(!judge.evaluate(w3, lv1).passed(), "关卡 1：灯罩亮了、光却还留在门口 —— 一样过不了关");

    // ⑥ 第 2 关是「照着样板把材质改回来」：三个球各占三分之一，判定读的是九个数。
    //    这一节把「1/3、2/3、3/3」的每一档都钉住，尤其是"三个球抄成同一个值"这条
    //    最省事的歪路 —— 它必须只值三分之一，不能白送过关。
    const Level& lv2 = levelAt(2);
    // 注意：World 不能按值往外传 —— 地板的材质里存着指向 world.textures 的指针
    // （Material::albedoTexture），拷贝一份世界会让那个指针指到已经析构的纹理上。
    // 所以这里往调用者自己的世界里建，而不是 return 一个 World。
    auto loadFresh = [&](World& dest) {
        dest = World{};
        buildWorkshop(dest);
        std::vector<std::string> l;
        for (const std::string& file : contentFileList()) applyContent(dest, loadContentFile(file), &l);
    };
    std::vector<std::string> log6;
    World w4;
    loadFresh(w4);
    checkClose(judge.evaluate(w4, lv2).progress, 0.0f, 1e-6f, "关卡 2：出厂状态读 0%（一个球都没改对）");

    applyContent(w4, parseContent("material chrome { albedo 0.95 0.93 0.90  roughness 0.06  metallic 1.0 }\n", "t"), &log6);
    const LevelStatus one2 = judge.evaluate(w4, lv2);
    checkClose(one2.progress, 1.0f / 3.0f, 1e-6f, "关卡 2：只把镜面球改对 = 三分之一");
    check(progressPercent(one2) == 33, "关卡 2：改对一个球，进度条老实写 33%（不四舍五入成 33.333）");

    applyContent(w4, parseContent("material plastic { albedo 0.82 0.13 0.11  roughness 0.26  metallic 0.0 }\n"
                                  "material clay { albedo 0.74 0.53 0.32  roughness 0.92  metallic 0.0 }\n", "t"), &log6);
    const LevelStatus solved2 = judge.evaluate(w4, lv2);
    check(solved2.passed() && progressPercent(solved2) == 100, "关卡 2：三个球都照着样板改对了才过关");

    // 判定读数据、不读画面 —— 把灯全关掉，已经改对的还是 100%，没改对的还是 0%。
    // 这条是这一关"摸黑也能过"那句话的凭据：学生跳过第 1 关直接进来，判定照样成立。
    applyContent(w4, parseContent("light 0 { intensity 0 }\nlight 1 { intensity 0 }\n", "t"), &log6);
    check(judge.evaluate(w4, lv2).passed(), "关卡 2：房间全黑，改对的就是改对了（判定读的是数据不是画面）");
    World w5;
    loadFresh(w5);
    applyContent(w5, parseContent("light 0 { intensity 0 }\nlight 1 { intensity 0 }\n", "t"), &log6);
    checkClose(judge.evaluate(w5, lv2).progress, 0.0f, 1e-6f, "关卡 2：房间全黑，没改对的也还是 0%（黑暗帮不上忙）");

    // 歪路一：三个球抄成同一个值（比如都抄成样板镜面的数）。这不是"改对"，是"改成一样"。
    World w6;
    loadFresh(w6);
    applyContent(w6, parseContent("material chrome  { albedo 0.95 0.93 0.90  roughness 0.06  metallic 1.0 }\n"
                                  "material plastic { albedo 0.95 0.93 0.90  roughness 0.06  metallic 1.0 }\n"
                                  "material clay    { albedo 0.95 0.93 0.90  roughness 0.06  metallic 1.0 }\n", "t"), &log6);
    const LevelStatus same2 = judge.evaluate(w6, lv2);
    check(!same2.passed() && progressPercent(same2) == 33,
          "关卡 2：三个球抄成同一个值只值三分之一（判定是球 vs 自己的样板，不是三球互比）");

    // 歪路二：把样板改成球现在的样子（改考卷）。样板上了锁，写不进去 —— 进度还是 0%。
    // 这条要是红了，说明"答案不在数据文件里"那句话是假的。
    World w7;
    loadFresh(w7);
    applyContent(w7, parseContent("material ref_chrome { albedo 0.98 0.97 0.96  roughness 0.55  metallic 0.0 }\n"
                                  "material ref_clay   { albedo 0.30 0.32 0.36 }\n", "t"), &log6);
    checkClose(judge.evaluate(w7, lv2).progress, 0.0f, 1e-6f, "关卡 2：改样板（改考卷）不算过关，进度还是 0%");

    // ⑦ 第 3 关是「一张贴图怎么贴」：平铺 4x4、repeat、bilinear 三件事各占一份。
    //    每一档都钉住，尤其是两条歪路 —— "只改那两个词、平铺不动"（画面看着变干净了，
    //    可地砖还是一块 1.5 米），和"把平铺拧到 100"（多铺几次总没错？题面是"铺几次"）。
    const Level& lv3 = levelAt(3);
    World w8;
    loadFresh(w8);
    checkClose(judge.evaluate(w8, lv3).progress, 0.0f, 1e-6f, "关卡 3：出厂状态读 0%（三件都没做）");

    applyContent(w8, parseContent("material floor { uvscale 4 4 }\n", "t"), &log6);
    const LevelStatus scaleOnly = judge.evaluate(w8, lv3);
    checkClose(scaleOnly.progress, 0.4f, 1e-6f, "关卡 3：只把平铺改成 4x4 = 40%");
    check(progressPercent(scaleOnly) == 40, "关卡 3：进度条老实写 40%");

    applyContent(w8, parseContent("material floor { wrap repeat }\n", "t"), &log6);
    checkClose(judge.evaluate(w8, lv3).progress, 0.7f, 1e-6f, "关卡 3：平铺 + 寻址 = 70%");

    applyContent(w8, parseContent("material floor { filter bilinear }\n", "t"), &log6);
    check(judge.evaluate(w8, lv3).passed(), "关卡 3：三件都改对才过关（100%）");

    // 歪路一：只把 repeat / bilinear 改对，平铺还是 1 1 —— 地板砖还是老大一块
    World w9;
    loadFresh(w9);
    applyContent(w9, parseContent("material floor { wrap repeat  filter bilinear }\n", "t"), &log6);
    check(!judge.evaluate(w9, lv3).passed(), "关卡 3：光把寻址和滤波改对、平铺不动 —— 不过关");

    // 歪路二：把平铺拧到 100（"多铺几次总没错"）。题面是"铺几次"，不是"越密越好"。
    World w10;
    loadFresh(w10);
    applyContent(w10, parseContent("material floor { uvscale 100 100  wrap repeat  filter bilinear }\n", "t"), &log6);
    check(!judge.evaluate(w10, lv3).passed(), "关卡 3：平铺拧到 100（地板糊成一片噪点）不算过关");

    // 判定读数据、不读画面：房间全黑也照样判 —— 跳着关玩的人不会卡在"还没点灯"上。
    applyContent(w10, parseContent("light 0 { intensity 0 }\nlight 1 { intensity 0 }\n", "t"), &log6);
    applyContent(w10, parseContent("material floor { uvscale 4 4 }\n", "t"), &log6);
    check(judge.evaluate(w10, lv3).passed(), "关卡 3：房间全黑，改对的就是改对了（判定读的是数据不是画面）");

    // ⑧ 第 3 关动的是地板，而地板是第 1 关评委画面里最大的一块受光面 —— 两关很容易
    //    互相拖累（第 3 关的题改对了，第 1 关的亮度却掉下达标线）。这里钉两条：
    //    地板还是被调乱的样子时、和地砖铺好之后，第 1 关都得照过不误。
    //    这两条就是"第 1 关达标线怎么定的"那件事的长期看门人：以后谁再动地板、
    //    动灯、动材质，只要把第 1 关的余量吃掉了，自检当场会红。
    World w11;
    loadFresh(w11);
    applyContent(w11, parseContent("light 0 { pos 2.20 2.90 1.20  intensity 48 }\n"
                                   "material lamp { emissive 4.2 3.8 3.0 }\n", "t"), &log6);
    check(judge.evaluate(w11, lv1).passed(), "关卡 1：地板还是被调乱的样子，灯那三件做对了照样过关");
    applyContent(w11, parseContent("material floor { uvscale 4 4  wrap repeat  filter bilinear }\n", "t"), &log6);
    check(judge.evaluate(w11, lv1).passed(), "关卡 1：第 3 关把地板修好之后，第 1 关还是过关（两关不互相拖累）");

    // ⑨ --all-levels --auto 用的那份解答表，每一关都必须**真的能过**。
    //    钉的是"冒烟测试的答案本身"：解答写错了、或者哪一关的判定漂了，自检当场就红，
    //    不用等谁想起来跑一次 --all-levels --auto。第 0 关没有条目（运行时无解，
    //    原因写在那份表头上），这里自然跳过。
    for (int i = 0; i < levelCount(); ++i) {
        if (autoSolutionFor(i) == nullptr) continue;
        World wa;
        loadFresh(wa);
        std::vector<std::string> logAuto;
        const std::string tag = "--auto：第 " + std::to_string(i) + " 关的解答";
        check(applyAutoSolution(wa, i, logAuto), (tag + "能应用上去（解析 / 名字 / 锁都没问题）").c_str());
        check(judge.evaluate(wa, levelAt(i)).passed(), (tag + "真的能过关").c_str());
    }
}

// 控制台自检。控制台是「学生唯一能对着画面打字的地方」，它的每一条交互都是承诺：
// 字符进得去、退格删得掉、回车一定有回音、面板遮不住世界、缺字模也不白屏。
void testConsole() {
    // ① 输入编辑：只收 ASCII 可见字符（见 console.h 文件头 ①）
    Console con;
    con.typeChar('a');
    con.typeChar(' ');
    con.typeChar('1');
    check(con.input() == "a 1", "控制台：ASCII 字符能进输入行");
    con.typeChar('\n');
    con.typeChar('\t');
    con.typeChar(char(0x80));
    check(con.input() == "a 1", "控制台：换行/制表/高位字节被挡在键盘外（不碰输入法也打不乱）");
    con.backspace();
    check(con.input() == "a ", "控制台：退格删掉最后一个字符");
    con.backspace();
    con.backspace();
    con.backspace();
    check(con.input().empty(), "控制台：连按退格删空之后不崩");

    // ② 回车：先把「> 你敲的那行」写进历史，再交给命令处理器
    Console c2;
    std::string got;
    int calls = 0;
    c2.setHandler([&](const std::string& line) {
        got = line;
        ++calls;
    });
    for (char ch : std::string("ambient 0.5")) c2.typeChar(ch);
    c2.submit();
    check(calls == 1 && got == "ambient 0.5", "控制台：回车把整行交给命令处理器");
    check(c2.lineCount() == 1 && c2.lineAt(0) == "> ambient 0.5", "控制台：回车先把「> 你敲的那行」写进历史");
    check(c2.input().empty(), "控制台：回车后输入行清空");
    c2.submit();
    check(calls == 1 && c2.lineCount() == 1, "控制台：空行回车什么都不做（不报错、不回声）");

    // ③ 命令的输出排在回声下面 —— 顺序就是因果顺序，学生一眼看清「我做了什么 / 发生了什么」
    Console c3;
    c3.setHandler([&](const std::string&) { c3.printOk("环境光 = 0.5 0.5 0.5"); });
    c3.run("ambient 0.5");
    check(c3.lineCount() == 2 && c3.lineAt(1).rfind("环境光", 0) == 0, "控制台：命令的输出排在「> 命令」下面");

    // ④ run() == 敲完回车：--cmd 注入和真人打字必须走同一条路（截图里看得到的就是现场按得出来的）
    Console c4;
    int calls4 = 0;
    c4.setHandler([&](const std::string&) { ++calls4; });
    for (char ch : std::string("light 60")) c4.typeChar(ch);
    c4.submit();
    Console c5;
    c5.setHandler([&](const std::string&) { ++calls4; });
    c5.run("light 60");
    check(calls4 == 2 && c4.lineCount() == c5.lineCount() && c4.lineAt(0) == c5.lineAt(0),
          "控制台：--cmd 的 run() 和真人敲回车产生完全一样的历史");

    // ⑤ cls 清屏
    Console c6;
    c6.print("一");
    c6.print("二");
    check(c6.lineCount() == 2, "控制台：输出进历史");
    c6.clear();
    check(c6.lineCount() == 0, "控制台：cls 清空历史");

    // ⑥ 滚动缓冲封顶：玩一晚上也不能无限吃内存
    Console c7;
    for (int i = 0; i < Console::kMaxScrollback + 50; ++i) c7.print("第 " + std::to_string(i) + " 行");
    check(c7.lineCount() == Console::kMaxScrollback, "控制台：历史行数封顶");
    check(c7.lineAt(0).find("50 行") != std::string::npos, "控制台：封顶后丢的是最老的行");

    // ⑦ 折行：中文没有空格可断，只能按「字」断
    Font font;
    const bool fontOk = font.loadFromFile("assets/font/pixel12.bin");
    if (fontOk) {
        const std::string longZh = "环境光太暗的话整个房间都看不清";  // 纯汉字，没有空格可断
        const int maxW = 100;
        const std::vector<std::string> rows = font.wrap(longZh, maxW);
        check(rows.size() >= 2, "控制台：长中文行会折成多行");
        bool allFit = true;
        for (const std::string& r : rows)
            if (font.measureLine(r) > maxW) allFit = false;
        check(allFit, "控制台：折行后每一行都不超过面板宽度");
        std::string joined;
        for (const std::string& r : rows) joined += r;
        check(joined == longZh, "控制台：折行不丢字、不切坏 UTF-8（拼回去和原文一模一样）");

        // 面板窄到放不下一个字：也必须出得来（否则 draw 会原地打转）
        size_t cpCount = 0;
        for (size_t i = 0; i < longZh.size(); ++cpCount) utf8Next(longZh, i);
        check(font.wrap(longZh, 3).size() == cpCount, "控制台：面板窄到放不下一个字时，一行一个字（不死循环）");
        const std::vector<std::string> twoLn = font.wrap("第一行\n第二行", maxW);
        check(twoLn.size() == 2 && twoLn[0] == "第一行", "控制台：原文里的换行会被保留");
        check(font.wrap("", maxW).size() == 1, "控制台：空串折行后仍有一行（光标得有地方待）");
    }

    // ⑧ 面板：隐藏 = 一个像素都不碰；显示 = 只压暗底部，上半屏照常看得见世界
    Framebuffer fb;
    fb.resize(200, 200);
    fb.clear(Vec3{1.0f, 1.0f, 1.0f});
    const std::vector<Vec3> before = fb.color;
    Console hidden;
    hidden.print("测试");
    hidden.draw(fb, font);
    bool untouched = true;
    for (size_t i = 0; i < fb.color.size(); ++i)
        if (fb.color[i].x != before[i].x) untouched = false;
    check(untouched, "控制台：隐藏时一个像素都不碰（不影响离屏逐像素比对）");

    Console shown;
    shown.setVisible(true);
    shown.print("环境光 = 0.5 0.5 0.5 —— 面板只占下半屏，世界还在上面");
    shown.draw(fb, font);
    // 面板最高 = 7 行字 + 上下留白，所以这条线以上绝不该被动过
    const int worstPanelTop = fb.height - (font.lineHeight() * Console::kVisibleRows + Console::kPadY * 2);
    bool upperUntouched = true;
    for (int y = 0; y < worstPanelTop; ++y)
        for (int x = 0; x < fb.width; ++x)
            if (fb.color[size_t(y) * size_t(fb.width) + size_t(x)].x != 1.0f) upperUntouched = false;
    check(upperUntouched, "控制台：面板只压暗底部，上半屏的世界一点没动");
    bool bottomLit = false;
    bool bottomDarker = false;
    for (int y = fb.height - 20; y < fb.height; ++y)
        for (int x = 0; x < fb.width; ++x) {
            const float v = fb.color[size_t(y) * size_t(fb.width) + size_t(x)].x;
            if (v != 1.0f) bottomLit = true;
            if (v < 0.5f) bottomDarker = true;  // 背景板 alpha 0.72 压过白色 → 0.29 左右
        }
    check(bottomLit, "控制台：面板真的画在画面底部");
    check(bottomDarker, "控制台：面板底下是半透明的（世界被压暗，不是糊一块不透明色块）");

    // ⑨ panelHeight() 是给 HUD 让位用的，必须和 draw() 真画出来的高度一模一样 ——
    //    这里用「最高被改动的行」反推实际高度来对账（曾经真的叠在一起过）
    auto topChangedRow = [](const Framebuffer& f) {
        for (int y = 0; y < f.height; ++y)
            for (int x = 0; x < f.width; ++x)
                if (f.color[size_t(y) * size_t(f.width) + size_t(x)].x != 1.0f) return y;
        return -1;
    };
    Console shortCon;
    shortCon.setVisible(true);
    shortCon.print("一行");
    Framebuffer fb3;
    fb3.resize(240, 200);
    fb3.clear(Vec3{1.0f, 1.0f, 1.0f});
    shortCon.draw(fb3, font);
    check(topChangedRow(fb3) == fb3.height - shortCon.panelHeight(font, fb3.width, fb3.height),
          "控制台：panelHeight() 和实际画出来的高度一致（HUD 才能正确让位，短面板）");

    Console fullCon;
    fullCon.setVisible(true);
    for (int i = 0; i < 30; ++i) fullCon.print("第 " + std::to_string(i) + " 行");
    Framebuffer fb4;
    fb4.resize(240, 200);
    fb4.clear(Vec3{1.0f, 1.0f, 1.0f});
    fullCon.draw(fb4, font);
    check(topChangedRow(fb4) == fb4.height - fullCon.panelHeight(font, fb4.width, fb4.height),
          "控制台：panelHeight() 和实际画出来的高度一致（历史堆满、面板顶到上限）");
    // ⑩ 滚动：面板显示不下的行不是丢了，是视口钉在末尾 —— 关卡的提示是编号步骤，
    //    "看不到第一步"等于没有提示。这里钉的是滚动本身（绘制效果靠出图看）。
    {
        Console sc;
        sc.setVisible(true);
        for (int i = 0; i < 30; ++i) sc.print("第 " + std::to_string(i) + " 行");
        Framebuffer fs;
        fs.resize(240, 200);
        fs.clear(Vec3{1.0f, 1.0f, 1.0f});
        sc.draw(fs, font);
        check(sc.atBottom(), "控制台：默认贴着最新一行（跟真终端一样）");

        sc.scrollBy(3);
        check(!sc.atBottom() && sc.scroll() == 3, "控制台：往上滚 3 行");

        // 滚过头：draw() 里要把它夹回合法范围，否则"再往下按"会看着没反应
        sc.scrollBy(1000);
        sc.draw(fs, font);
        const int clamped = sc.scroll();
        check(clamped > 0 && clamped < 1000, "控制台：滚过头被夹回合法范围（不会越滚越远）");

        sc.scrollBy(1000);
        sc.draw(fs, font);
        check(sc.scroll() == clamped, "控制台：已经到顶之后再往上滚，位置不动");

        sc.scrollToBottom();
        check(sc.atBottom(), "控制台：能滚回最新一行");

        // 换个小窗口重新开：滚动位置要复位（上次翻到一半关掉，下次打开还停在半路很莫名其妙）
        sc.scrollBy(5);
        sc.setVisible(false);
        sc.setVisible(true);
        check(sc.atBottom(), "控制台：重新打开时回到最新一行");
    }

    check(fullCon.panelHeight(font, fb4.width, fb4.height) <= font.lineHeight() * Console::kVisibleRows + Console::kPadY * 2,
          "控制台：面板高度有上限（历史再多也最多占 kVisibleRows 行字）");

    // ⑩ 字模整个没加载成功时也要能画（红块占位），绝不崩 —— 招新现场少一个文件不能白屏
    Framebuffer fb5;
    fb5.resize(200, 200);
    fb5.clear(Vec3{1.0f, 1.0f, 1.0f});
    Font noFont;
    Console orphan;
    orphan.setVisible(true);
    orphan.print("字模没加载");
    orphan.draw(fb5, noFont);
    bool orphanPainted = false;
    for (const Vec3& c : fb5.color)
        if (c.x != 1.0f) orphanPainted = true;
    check(orphanPainted, "控制台：字模没加载时也画得出（红块占位），不是白屏也不是崩溃");
}

void testSave() {
    // 存到 build/ 里，绝不碰玩家真正的 saved/save.bin —— 跑一次自检把人家进度清了
    // 是最难被原谅的一种"测试"
    const std::string path = "build/selftest_save.bin";
    std::remove(path.c_str());

    // ① 全新的存档点：文件不存在 = 没有存档，不是错误
    check(!loadSave(path).loaded, "存档：文件不存在时安静地返回「没有存档」");

    // ② 存一轮读一轮：每个字段都得原样回来（定长二进制不走文本，浮点也不该掉精度）
    SaveData out;
    out.level = 3;
    out.feet = Vec3{1.5f, -0.25f, 7.25f};
    out.yaw = 0.75f;
    out.pitch = 1.0f;
    out.goals = 0b1011u;
    check(writeSave(out, path), "存档：写 build/selftest_save.bin 成功");
    const SaveData in = loadSave(path);
    check(in.loaded, "存档：刚写下的文件读得回来");
    check(in.level == 3, "存档：关卡号原样回来");
    check(sameVec3(in.feet, out.feet), "存档：脚底坐标原样回来");
    check(in.yaw == out.yaw && in.pitch == out.pitch, "存档：朝向原样回来（浮点按位存，没掉精度）");
    check(in.goals == out.goals, "存档：目标位掩码原样回来");

    // ③ 各种坏档：全都当"没有存档"，一个都不许崩、不许把 NaN 放进世界
    auto broken = [&](const char* what, const std::string& bytes) {
        std::FILE* f = std::fopen(path.c_str(), "wb");
        if (f != nullptr) {
            std::fwrite(bytes.data(), 1, bytes.size(), f);
            std::fclose(f);
        }
        check(!loadSave(path).loaded, what);
    };

    std::string good;
    good.append(kSaveMagic, 4);
    detail::pushU32(good, kSaveVersion);
    detail::pushU32(good, 0);
    detail::pushF32(good, 0.0f);
    detail::pushF32(good, 0.0f);
    detail::pushF32(good, 4.5f);
    detail::pushF32(good, 0.0f);
    detail::pushF32(good, -0.06f);
    detail::pushU32(good, 0);

    broken("存档：magic 不对 = 没有存档", "XXXX" + good.substr(4));
    broken("存档：版本号不认识 = 没有存档", good.substr(0, 4) + std::string("\x09\x00\x00\x00", 4) + good.substr(8));
    broken("存档：少一个字节 = 没有存档", good.substr(0, good.size() - 1));

    // 坐标是 NaN 的档：不拦住的话人会瞬移到 NaN，然后整个世界从屏幕上消失
    std::string nanSave = good;
    nanSave.replace(12, 4, [] {
        std::string t;
        detail::pushF32(t, std::nanf(""));
        return t;
    }());
    broken("存档：脚底坐标是 NaN = 没有存档", nanSave);

    // 关卡号超范围：将来关卡数据换了，旧档不该把人塞进一个不存在的关
    std::string badLevel = good;
    badLevel.replace(8, 4, [] {
        std::string t;
        detail::pushU32(t, 999u);
        return t;
    }());
    broken("存档：关卡号超范围 = 没有存档", badLevel);

    // ④ 只有俯仰角越界这一种"半坏"要救得回来：夹到 ±89°，而不是整档作废
    std::string wildPitch = good;
    wildPitch.replace(28, 4, [] {
        std::string t;
        detail::pushF32(t, 3.0f);
        return t;
    }());
    std::FILE* f = std::fopen(path.c_str(), "wb");
    std::fwrite(wildPitch.data(), 1, wildPitch.size(), f);
    std::fclose(f);
    const SaveData fixed = loadSave(path);
    check(fixed.loaded, "存档：俯仰角越界仍然读得出来（只夹角度，不作废整档）");
    checkClose(fixed.pitch, 1.5533f, 1e-4f, "存档：越界的俯仰角被夹到 ±89°");

    // ⑤ --reset 的底座：删掉之后必须真的当没存档
    check(clearSave(path), "存档：--reset 删档成功");
    check(!loadSave(path).loaded, "存档：删档之后读出来是「没有存档」");
}

// 暂停菜单的自检。只测能自动化的那部分：**命中判定**。
// 绘制效果自动化不了，靠出图肉眼看（见计划里 Task 3 的第 3 步）。
void testMenu() {
    Font font;
    font.loadFromFile("assets/font/pixel12.bin");

    const int fbW = 1280, fbH = 720;
    const Menu::Layout L = Menu::computeLayout(fbW, fbH, font);

    // ① 每一行的中点都命中它自己
    bool allRowsHit = true;
    for (int r = 0; r < Menu::kRowCount; ++r) {
        const float mx = float(L.panelX + L.panelW / 2);
        const float my = float(Menu::rowTop(L, r) + L.rowH / 2);
        if (Menu::rowAt(mx, my, fbW, fbH, font) != r) allRowsHit = false;
    }
    check(allRowsHit, "菜单：1280x720 下每一行的中点都命中自己");

    // ② 面板外 → -1
    check(Menu::rowAt(5.0f, 5.0f, fbW, fbH, font) == -1, "菜单：点在面板外返回 -1");
    check(Menu::rowAt(float(fbW) - 2.0f, float(fbH) - 2.0f, fbW, fbH, font) == -1,
          "菜单：点在最右下角返回 -1（面板居中，角上不是它）");

    // ③ 换到最小那档分辨率，命中仍然对 —— 排版是按 framebuffer 现算的
    bool smallOk = true;
    const Menu::Layout S = Menu::computeLayout(854, 480, font);
    for (int r = 0; r < Menu::kRowCount; ++r) {
        const float mx = float(S.panelX + S.panelW / 2);
        const float my = float(Menu::rowTop(S, r) + S.rowH / 2);
        if (Menu::rowAt(mx, my, 854, 480, font) != r) smallOk = false;
    }
    check(smallOk, "菜单：854x480 下每一行也命中自己（排版随 framebuffer 现算）");

    // ④ 所有行都落在面板里 —— 不然会出现"点得到、看不见"
    check(Menu::rowTop(L, 0) >= L.panelY &&
              Menu::rowTop(L, Menu::kRowCount - 1) + L.rowH <= L.panelY + L.panelH,
          "菜单：所有行都落在面板内（点得到也看得见）");

    // ⑤ ◀ ▶ 的左右命中与回绕
    {
        int prevX = 0, nextX = 0, aw = 0;
        Menu::arrowRects(L, Menu::kRowResolution, fbW, prevX, nextX, aw);
        const float y = float(Menu::rowTop(L, Menu::kRowResolution) + L.rowH / 2);
        FrameInput in;
        in.mousePressed = true;
        in.mouseY = y;
        Menu m;
        m.setVisible(true);

        m.setDisplay(1, WindowMode::Windowed);
        in.mouseX = float(nextX + aw / 2);
        MenuAction a = m.update(in, fbW, fbH, font);
        check(a.kind == MenuAction::SetResolution && a.value == 2, "菜单：点 > 进到下一档分辨率");

        m.setDisplay(2, WindowMode::Windowed);
        in.mouseX = float(prevX + aw / 2);
        a = m.update(in, fbW, fbH, font);
        check(a.kind == MenuAction::SetResolution && a.value == 1, "菜单：点 < 退回上一档分辨率");

        m.setDisplay(0, WindowMode::Windowed);
        in.mouseX = float(prevX + aw / 2);
        a = m.update(in, fbW, fbH, font);
        check(a.kind == MenuAction::SetResolution && a.value == kResolutionCount - 1,
              "菜单：在第一档点 < 回绕到最后一档");
    }

    // ⑥ 窗口模式那一行同理
    {
        int prevX = 0, nextX = 0, aw = 0;
        Menu::arrowRects(L, Menu::kRowMode, fbW, prevX, nextX, aw);
        FrameInput in;
        in.mousePressed = true;
        in.mouseY = float(Menu::rowTop(L, Menu::kRowMode) + L.rowH / 2);
        Menu m;
        m.setVisible(true);

        m.setDisplay(2, WindowMode::Windowed);
        in.mouseX = float(nextX + aw / 2);
        MenuAction a = m.update(in, fbW, fbH, font);
        check(a.kind == MenuAction::SetMode && a.value == int(WindowMode::Borderless),
              "菜单：点 > 切到无边框");

        m.setDisplay(2, WindowMode::Fullscreen);
        in.mouseX = float(prevX + aw / 2);
        a = m.update(in, fbW, fbH, font);
        check(a.kind == MenuAction::SetMode && a.value == int(WindowMode::Borderless),
              "菜单：全屏点 < 回绕到无边框");
    }

    // ⑦ 继续 / 退出
    {
        FrameInput in;
        in.mousePressed = true;
        in.mouseX = float(L.panelX + L.panelW / 2);
        Menu m;
        m.setVisible(true);
        in.mouseY = float(Menu::rowTop(L, Menu::kRowResume) + L.rowH / 2);
        check(m.update(in, fbW, fbH, font).kind == MenuAction::Resume, "菜单：点「继续游戏」返回 Resume");
        in.mouseY = float(Menu::rowTop(L, Menu::kRowQuit) + L.rowH / 2);
        check(m.update(in, fbW, fbH, font).kind == MenuAction::Quit, "菜单：点「退出游戏」返回 Quit");
    }

    // ⑧ 只读行（分隔线、关卡标题、目标、进度条）点了不该有反应
    {
        FrameInput in;
        in.mousePressed = true;
        in.mouseX = float(L.panelX + L.panelW / 2);
        Menu m;
        m.setVisible(true);
        const int readonlyRows[] = {Menu::kRowBlank1, Menu::kRowTitle, Menu::kRowGoal,
                                    Menu::kRowProgress, Menu::kRowBlank2};
        bool quiet = true;
        for (int r : readonlyRows) {
            in.mouseY = float(Menu::rowTop(L, r) + L.rowH / 2);
            if (m.update(in, fbW, fbH, font).kind != MenuAction::None) quiet = false;
        }
        check(quiet, "菜单：只读行点了没反应（别让人以为点坏了）");
    }

    // ⑨ 菜单关着的时候，点了什么都不该发生
    {
        FrameInput in;
        in.mousePressed = true;
        in.mouseX = float(L.panelX + L.panelW / 2);
        in.mouseY = float(Menu::rowTop(L, Menu::kRowResume) + L.rowH / 2);
        Menu m;
        m.setVisible(false);
        check(m.update(in, fbW, fbH, font).kind == MenuAction::None, "菜单：关着的时候点了没反应");
    }

    // ⑩ 鼠标坐标换算。鼠标是**客户区**坐标，菜单画在 **framebuffer** 上 ——
    //    窗口一被拉大（最大化）或 --scale，这两套就不是一回事了。不换算的话只有
    //    左上角那一块能点中，看着就是"菜单大部分按不了"（实测报过这个）。
    //    这条是纯算术，所以在自检里钉住，不靠开窗点。
    {
        // 最大化：客户区 2560x1369，渲染还是 1280x720。
        // 点的位置**从布局算**，不写死坐标 —— 菜单插一行就会失效（这次加「从头开始」
        // 就被这条抓到了：写死的那个 y 落到了新行上，"点退出游戏"自然就不中了）。
        const Menu::Layout LM = Menu::computeLayout(1280, 720, font);
        const float fbX = float(LM.panelX + LM.panelW / 2);
        const float fbY = float(Menu::rowTop(LM, Menu::kRowQuit) + LM.rowH / 2);
        // 在客户区里点同一个位置，换算回来应当还落在那一行
        float mx = fbX * 2560.0f / 1280.0f;
        float my = fbY * 1369.0f / 720.0f;
        mouseToFramebuffer(mx, my, 2560, 1369, 1280, 720);
        checkClose(mx, fbX, 0.5f, "菜单：客户区 X 坐标换算到 framebuffer");
        checkClose(my, fbY, 1.0f, "菜单：客户区 Y 坐标换算到 framebuffer");

        FrameInput in;
        in.mousePressed = true;
        in.mouseX = mx;
        in.mouseY = my;
        Menu m;
        m.setVisible(true);
        check(m.update(in, 1280, 720, font).kind == MenuAction::Quit,
              "菜单：最大化窗口下点「退出游戏」能命中（先换算再判）");

        // 窗口尺寸和渲染分辨率相同时，坐标应当原样不动
        float sx = 640.0f, sy = 497.0f;
        mouseToFramebuffer(sx, sy, 1280, 720, 1280, 720);
        checkClose(sx, 640.0f, 0.01f, "菜单：窗口尺寸等于渲染分辨率时坐标不动");

        // 退化输入（客户区还没量到 / 为 0）不许除零
        float zx = 5.0f, zy = 7.0f;
        mouseToFramebuffer(zx, zy, 0, 0, 1280, 720);
        checkClose(zx, 5.0f, 0.01f, "菜单：客户区尺寸为 0 时坐标不动（不除零）");
        checkClose(zy, 7.0f, 0.01f, "菜单：客户区尺寸为 0 时 Y 也不动");
    }

    // ⑪ 「从头开始」是破坏性操作（清进度 + 还原所有文件），所以要点两次。
    //    这条守的是"手滑一下就把学生的东西全抹了" —— 那种事出一次就够劝退的。
    {
        const Menu::Layout L2 = Menu::computeLayout(1280, 720, font);
        FrameInput in;
        in.mousePressed = true;
        in.mouseX = float(L2.panelX + L2.panelW / 2);
        in.mouseY = float(Menu::rowTop(L2, Menu::kRowRestart) + L2.rowH / 2);

        Menu m;
        m.setVisible(true);
        check(m.update(in, 1280, 720, font).kind == MenuAction::None,
              "菜单：「从头开始」第一次点只是待命，不执行");
        check(m.restartArmed(), "菜单：点过之后待命状态立起来了（标签会变、会变红）");
        check(m.update(in, 1280, 720, font).kind == MenuAction::Restart,
              "菜单：「从头开始」第二次点才真的执行");
        check(!m.restartArmed(), "菜单：执行之后待命清掉（不会连着又触发一次）");

        // 点别的行取消待命 —— 免得"点一下、去做点别的、过会儿又点回来"变成误清空
        Menu m2;
        m2.setVisible(true);
        m2.update(in, 1280, 720, font);
        in.mouseY = float(Menu::rowTop(L2, Menu::kRowResume) + L2.rowH / 2);
        m2.update(in, 1280, 720, font);
        check(!m2.restartArmed(), "菜单：点了别的行就取消待命");

        // 关掉菜单再打开，待命也该是干净的
        Menu m3;
        m3.setVisible(true);
        in.mouseY = float(Menu::rowTop(L2, Menu::kRowRestart) + L2.rowH / 2);
        m3.update(in, 1280, 720, font);
        m3.setVisible(false);
        m3.setVisible(true);
        check(!m3.restartArmed(), "菜单：关掉再打开时待命是干净的（不会一进来点一下就清空）");
    }
}

// 入群二维码的自检。
//
// 重点全落在**"这张码还扫得出来吗"**上 —— 因为它出错时在程序里看不出任何异常：
// 图照画、不报错，只是手机扫不出来。所以把"能扫"的几条硬指标在这里钉死。
void testRecruit() {
    RecruitQr q;
    check(q.loadFromFile(kRecruitQrPath), "群码：素材读得出来（assets/recruit/qr.bin）");
    if (!q.ready()) {
        check(false, "群码：素材没读出来，后面的检查没有意义，全部跳过");
        return;
    }

    const int n = q.modulesW;
    check(n == q.modulesH, "群码：是正方形");
    check(n >= 21 && n <= 177 && n % 2 == 1, "群码：模块数是合法的二维码尺寸");
    check(int(q.bits.size()) == (n + 7) / 8 * n, "群码：数据长度和尺寸对得上");

    // 三个定位角必须是标准 7×7。**这一条最有分量**：定位角摆在固定的几个位置上，
    // 它对得上就同时说明了两件事 —— 生成脚本量出来的格网是对的，而且 XOR 密钥和
    // 脚本那边一致（密钥错了整张点阵就是噪声，第一个定位角就对不上）。
    static const int kFinder[7][7] = {
        {1, 1, 1, 1, 1, 1, 1}, {1, 0, 0, 0, 0, 0, 1}, {1, 0, 1, 1, 1, 0, 1},
        {1, 0, 1, 1, 1, 0, 1}, {1, 0, 1, 1, 1, 0, 1}, {1, 0, 0, 0, 0, 0, 1},
        {1, 1, 1, 1, 1, 1, 1},
    };
    const char* kCorner[3] = {"左上", "右上", "左下"};
    for (int k = 0; k < 3; ++k) {
        const int ox = (k == 1) ? n - 7 : 0;
        const int oy = (k == 2) ? n - 7 : 0;
        bool ok = true;
        for (int r = 0; r < 7; ++r)
            for (int c = 0; c < 7; ++c)
                if (q.dark(ox + c, oy + r) != (kFinder[r][c] != 0)) ok = false;
        check(ok, (std::string("群码：") + kCorner[k] + "定位角是标准 7×7").c_str());
    }

    int darkCount = 0;
    for (int r = 0; r < n; ++r)
        for (int c = 0; c < n; ++c)
            if (q.dark(c, r)) ++darkCount;
    check(darkCount > n * n / 4 && darkCount < n * n * 3 / 4, "群码：黑白分布像一张真的二维码");

    // 越界当白：drawRecruitQr 拿它当静默区（规范要求四周留 4 格白边，缺了有的读取器
    // 认不出来）。这一条顺带钉住了"越界不许读越界内存"。
    check(!q.dark(-1, 0) && !q.dark(0, -1) && !q.dark(n, 0) && !q.dark(0, n),
          "群码：越界当白（静默区靠它）");

    // ---- 画出来的东西
    Font font;
    font.loadFromFile("assets/font/pixel12.bin");
    const Vec3 paper{2.6f, 2.6f, 2.6f};
    Framebuffer fb;
    fb.resize(401, 401);
    fb.clear(Vec3{0.0f, 0.0f, 0.0f});
    drawRecruitQr(fb, font, q, true);

    // 逐行找最长的连续亮像素段 —— 托盘是画面里唯一的**实心**亮块（标题文字很窄，
    // 抢不过它）。先求宽度，再定托盘的行范围。
    auto longestBrightRun = [&](int y, int& start) {
        int best = 0, run = 0, s = 0;
        for (int x = 0; x < fb.width; ++x) {
            const Vec3 c = fb.color[size_t(y) * size_t(fb.width) + size_t(x)];
            if (c.x >= paper.x - 0.1f && c.y >= paper.y - 0.1f) {
                if (run == 0) s = x;
                if (++run > best) {
                    best = run;
                    start = s;
                }
            } else {
                run = 0;
            }
        }
        return best;
    };

    int plate = 0, plateL = 0;
    for (int y = 0; y < fb.height; ++y) {
        int s = 0;
        const int r = longestBrightRun(y, s);
        if (r > plate) {
            plate = r;
            plateL = s;
        }
    }
    const int unit = n + 8;  // 模块数 + 两侧各 4 格静默区
    // **必须是整数倍** —— 不是整数倍就会出现半像素宽的格线，手机扫不出来。
    // 这一条挡的是"谁把 scale 改成了浮点除法"。
    check(plate > 0 && plate % unit == 0, "群码：托盘边长是（模块数 + 静默区）的整数倍");

    if (plate > 0 && plate % unit == 0) {
        int top = -1, bot = -1;
        for (int y = 0; y < fb.height; ++y) {
            int s = 0;
            if (longestBrightRun(y, s) >= plate) {
                if (top < 0) top = y;
                bot = y;
            }
        }
        check(bot - top + 1 == plate, "群码：托盘是正方形（高 = 宽）");
        // 对比度拉满：托盘里必须**同时**有纯白和纯黑，中间调不算数 ——
        // 手机认的就是对比度，把纸色调暗一点"看着柔和些"就足以把这张码扫废。
        int white = 0, black = 0;
        for (int y = top; y <= bot; ++y)
            for (int x = plateL; x < plateL + plate; ++x) {
                const float v = fb.color[size_t(y) * size_t(fb.width) + size_t(x)].x;
                if (v >= paper.x - 0.1f) ++white;
                if (v <= 0.0001f) ++black;
            }
        check(white > 0 && black > 0, "群码：托盘里有纯白也有纯黑（对比度拉满）");
        // 黑格差不多占一半。太低说明点阵被写坏了（比如密钥只对上了一段）。
        check(black > plate * plate / 8, "群码：黑格占比不低于八分之一（不是空图）");
    }
}

// 画面设置的自检。重点全落在"坏输入不许把游戏弄打不开"上 ——
// 一个设置文件坏了就不让人进游戏，是比没有设置更糟的事。
void testSettings() {
    const std::string path = "build/selftest_settings.txt";

    // ① 往返：写出去再读回来，三个字段一个不差
    Settings a;
    a.mode = WindowMode::Fullscreen;
    a.width = 1280;
    a.height = 720;
    check(saveSettings(a, path), "设置：能写出 saved/settings.txt 那个格式");
    Settings b;
    check(loadSettings(path, b), "设置：写出去的能读回来");
    check(b.mode == WindowMode::Fullscreen && b.width == 1280 && b.height == 720,
          "设置：往返之后 mode/width/height 一模一样");

    // ② 文件不存在 → 回完整默认值，返回 false（不是崩，也不是半个默认）
    Settings c;
    check(!loadSettings("build/这个文件不存在.txt", c), "设置：文件不在时返回 false");
    check(c.mode == WindowMode::Windowed && c.width == 0 && c.height == 0,
          "设置：文件不在时给的是完整默认值");

    // ③ 乱码文件 → 回默认值，不打不开游戏
    {
        std::FILE* f = std::fopen(path.c_str(), "wb");
        if (f != nullptr) {
            std::fwrite("\x01\x02 not a settings file at all\n", 1, 31, f);
            std::fclose(f);
        }
        Settings d;
        check(!loadSettings(path, d), "设置：乱码文件返回 false");
        check(d.mode == WindowMode::Windowed, "设置：乱码文件回默认模式（不崩）");
    }

    // ④ 认不出的键 → 算读失败，但已认出来的字段照样生效
    {
        std::FILE* f = std::fopen(path.c_str(), "wb");
        if (f != nullptr) {
            std::fprintf(f, "mode fullscreen\nbogus 3\n");
            std::fclose(f);
        }
        Settings e;
        check(!loadSettings(path, e), "设置：认不出的键算读失败");
        check(e.mode == WindowMode::Fullscreen, "设置：认不出的键不影响已认出来的字段");
    }

    // ⑤ 分辨率表：三档、约等于 16:9、封顶 720p
    //    注意是"约等于"：854x480 是约定俗成的 480p 宽度，可它的宽高比是 1.7792，
    //    和精确的 16:9（1.7778）差 0.08% —— 拉到 2560 宽的屏上也就差 2 像素，
    //    肉眼看不出来。要求精确相等就会把一个标准分辨率挡在门外，所以给 1% 容差。
    check(kResolutionCount == 3, "设置：分辨率表是三档");
    for (int i = 0; i < kResolutionCount; ++i) {
        const Resolution& r = kResolutions[i];
        check(r.h <= 720, "设置：没有一档超过 720p（软渲染撑不住）");
        const double aspect = double(r.w) / double(r.h);
        check(aspect > 1.760 && aspect < 1.796, "设置：每一档都约等于 16:9（不然全屏拉伸会变形）");
    }

    // ⑥ 收档：任意尺寸都能收进表里，而且就近
    check(nearestResolutionIndex(1600, 900) == 2, "设置：1600x900 收进 1280x720（启动默认封顶）");
    check(nearestResolutionIndex(1000, 600) == 1, "设置：1000x600 收进 960x540");
    check(nearestResolutionIndex(400, 300) == 0, "设置：太小的话收到最小那档");

    std::remove(path.c_str());
}

int runSelfTest() {
    // 命令行默认值也得钉：离屏出图的默认分辨率不许低于 480p（明确要求，见 Args 里
    // width/height 那段注释）。这条挡的是"谁为了快又把它悄悄调回 480x270"。
    {
        char a0[] = "dreamlab";
        char a1[] = "--shot";
        char a2[] = "build/tmp_selftest_default.png";
        char* av[] = {a0, a1, a2};
        const Args def = parseArgs(3, av);
        check(def.width >= 854 && def.height >= 480, "命令行：--shot 的默认分辨率不低于 480p（854x480）");
    }

    testMath();
    testRasterizer();
    testFont();
    testWorld();
    testPlayer();
    testRaycast();
    testContent();
    testLevel();
    testConsole();
    testSave();
    testSettings();
    testMenu();
    testRecruit();
    if (g_failures == 0) {
        std::printf("[selftest] %d 项检查全部通过\n", g_checks);
        return 0;
    }
    std::printf("[selftest] %d/%d 项失败\n", g_failures, g_checks);
    return 1;
}

// 程序里所有资源路径（assets/ 字模、content/、saved/）都是相对仓库根写的。
// 从仓库根敲 `build/dreamlab.exe` 没问题，但**双击 exe** 时工作目录是 build/，
// 于是字模整份加载不到 —— 每个字都画成实心红块（core/font.h 里 count_ == 0 那条
// 占位路径），content/ 也全部读不到。招新页写着"拉下来就能跑"，双击是必然动作，
// 所以这里把工作目录摆正：
//   ① 当前目录能解开就不动 —— 脚本（build/tmp_*.py）一直是这么跑的，别改它的语义；
//   ② 解不开就从 exe 所在目录往上找带字模的那一层，找到就切过去。
// 找不到就什么都不做：后面各自会报"打不开"，总比乱猜一个目录强。
void lockToRepoRoot() {
    const char* kMarker = "assets/font/pixel12.bin";
    if (std::FILE* f = std::fopen(kMarker, "rb")) {
        std::fclose(f);
        return;  // 已经在仓库根，行为与以前完全一致
    }

    std::string dir = executableDir();
    for (int up = 0; up < 4 && !dir.empty(); ++up) {
        std::string probe = dir + "/" + kMarker;
        if (std::FILE* f = std::fopen(probe.c_str(), "rb")) {
            std::fclose(f);
            if (setCurrentDir(dir)) std::printf("[路径] 当前目录不是仓库根，已切到 %s\n", dir.c_str());
            return;
        }
        const size_t cut = dir.find_last_of("/\\");
        if (cut == std::string::npos) break;
        dir = dir.substr(0, cut);  // 上一级
    }
}

}  // namespace

int main(int argc, char** argv) {
    lockToRepoRoot();  // 必须最早：后面的字模、content/、saved/ 都靠它
    const Args args = parseArgs(argc, argv);
    if (args.help) {
        printUsage();
        return 0;
    }

    if (args.selftest) return runSelfTest();
    if (args.preview) return runPreview(args.previewText, 1);

    Scene scene;
    buildScene(scene);
    // --cam 给的是"眼睛"的位置（人一米六二高），玩家存的是脚底 —— 差一个身高
    if (args.hasCam) scene.player.feet = args.cam - Vec3{0.0f, Player::kEyeHeight, 0.0f};
    if (args.hasLook) scene.player.setLookAt(args.look);
    if (args.fovDeg > 0.0f) scene.camera.fovY = radians(args.fovDeg);
    scene.player.noclip = args.noclip;
    syncCamera(scene);

    // content/ 是世界的「最后一句话」：先搭场景，再让数据覆盖上去。
    // 这样不管谁（关卡代码、玩家、上一局留下的状态）把值改成了什么，只要文件里写着，
    // 画面出来就一定是文件说的样子 —— 「改数据一定生效」是数据热重载的全部承诺。
    ContentWatcher watcher(contentFileList());
    watcher.prime(scene.world);

    // 控制台：面板本身完全不知道世界是什么，命令通过 handler 走出去（见 console.h 文件头的边界说明）。
    // --cmd 注入走 con.run()，和真人敲回车是同一条路径 —— 离屏截图里能看到的，宣讲现场一定按得出来。
    Console con;
    con.setVisible(args.console);
    con.print("数媒组工作室 · 控制台。敲 help 看全部命令，R 键重新读 content/。");
    con.print("这个世界由 content/*.txt 和 game/shaders/ 决定：改一处，画面当场变。");

    // 存档：只有开窗模式（= 真的在玩）才读。离屏出图必须每次从同一个出生点开始，
    // 不然"昨天的图和今天逐像素对不上"，而逐像素比对正是本项目的验证主手段。
    // --all-levels 也是一种离屏模式：它是冒烟测试，跑完就退出，不该开窗。
    // （漏了这一条的话，不给 --shot 的 --all-levels 会掉进开窗路径 —— 参数被整个
    //   忽略、窗口开着一直跑，看起来就像"命令没反应"。）
    const bool windowed = !args.hasShot && !args.allLevels;

    // ---- 画面设置（暂停菜单那一层）
    // 读得到就用上次存的；读不到就用 --width/--height（也就是"按核数自适应"算出来的那个）。
    // **无论走哪条路，都过一遍 nearestResolutionIndex 收进 kResolutions** ——
    // 那张表封顶 720p，所以这台机器上按核数算出来的 1600x900 会变成 1280x720。
    // 离屏那条路完全不读它（截图比对脚本指着精确的 --width/--height）。
    Settings settings;
    if (!loadSettings(kSettingsPath, settings)) settings.mode = WindowMode::Windowed;
    if (windowed) {
        const int wantW = settings.width > 0 ? settings.width : args.width;
        const int wantH = settings.height > 0 ? settings.height : args.height;
        const int idx = nearestResolutionIndex(wantW, wantH);
        settings.width = kResolutions[idx].w;
        settings.height = kResolutions[idx].h;
    }
    int resIndex = nearestResolutionIndex(settings.width, settings.height);

    if (args.reset) {
        clearSave();
        std::printf("[存档] 已清空 %s\n", kSavePath);
    }
    // 关卡进度就从存档里恢复：上次走到第几关，这次还站第几关（M3 的存档里 level 恒为 0）
    LevelRuntime rt;
    if (windowed && !args.reset) {
        const SaveData sv = loadSave();
        if (sv.loaded) {
            // 出生点先留个底：存档里的位置要是站不住，得能退回这里
            const Player spawn = scene.player;
            scene.player.feet = sv.feet;
            scene.player.yaw = sv.yaw;
            scene.player.pitch = sv.pitch;
            rt.index = sv.level;
            rt.goals = sv.goals;
            // 读到的是"上次离开的地方"，但世界可能已经变了（改了 content/ 的家具位置，
            // 存档点就可能在墙里）。站不住就老老实实回出生点 —— 卡在实体里出不来
            // 是最让人以为"游戏坏了"的一种坏法。
            if (!playerFits(scene.world, scene.player)) {
                scene.player = spawn;
                std::printf("[存档] 存档里的位置已经被东西占了 → 回出生点（想彻底重来用 --reset）\n");
                con.print("上次离开的位置现在被东西占了，先回出生点。想彻底重来：加 --reset 启动。");
            } else {
                std::printf("[存档] 继续上次：站在 (%.2f, %.2f, %.2f)（想从头开始用 --reset）\n",
                            double(sv.feet.x), double(sv.feet.y), double(sv.feet.z));
                con.print("继续上次的位置。想从出生点重来：加 --reset 启动。");
            }
        }
    }
    // --level 压过存档：验收某一关、以及"我直接跳过去看看"都用它，不必先去删存档
    if (args.level >= 0) rt.index = args.level;
    if (rt.index < 0 || rt.index >= levelCount()) {
        std::printf("[关卡] 要找第 %d 关，可现在总共只有 %d 关 —— 从第 0 关开始\n", rt.index, levelCount());
        rt.index = 0;
    }

    // 评委机位先判一次。开场白要用它，--cmd 注入的命令（例如 level）也要用它 ——
    // 不然"刚进游戏查一下进度"会查到一片 0。
    Judge judge;
    {
        const Level& lv = levelAt(rt.index);
        rt.status = judge.evaluate(scene.world, lv);
        std::printf("[关卡] %s · 目标「%s」· 进度 %d%%（评委机位亮度 %.4f，其中灯贡献 %.4f）\n", lv.title,
                    lv.goal, progressPercent(rt.status), double(rt.status.luminance),
                    double(rt.status.lightLuminance));
        if (rt.status.passed()) {
            // 开场就已经过关，有两种情形，要分开：
            //   · 存档说这是头一回 → 学生多半是刚关掉游戏改完代码/改完数据、回来的。
            //     第 0 关的高光时刻就在这一下（房间亮着，而他上次看到的是全黑），
            //     不能因为"判定发生在第 0 帧之前"就把它吞掉。这里只立旗子、不出声 ——
            //     "喊一声"统一由 runWindow 做（那儿才是玩家看得见的地方），
            //     而且它会顺手把人送到下一关；
            //   · 存档记着"这关以前就过了" → 别再欢呼，但**一样得往下走**。
            //     停在一个"已经完成"的房间里没有任何指引：题面不更新、HUD 的"下一步"
            //     是空的（过了关的关卡不再说下一步），人只能对着一个 100% 的进度条发呆。
            //     这个死角实测被撞到过 —— "第一关提示已经过关了，但下一关的信息始终没出现"。
            // **先问再标记** —— 顺序反了就永远问不出"是不是头一回"
            const bool wasDone = levelDone(rt.goals, rt.index);
            rt.goals = markLevelDone(rt.goals, rt.index);
            if (!wasDone) {
                rt.freshWin = true;  // 头一回：交给 runWindow 喊一声 + 推进
            } else {
                con.printOk(std::string(lv.title) + "：你已经过了这一关。");
                // 早过过了：不欢呼，但**照样往下走** —— 别把人留在已完成的房间里
                advanceAfterPass(con, scene.world, judge, rt);
            }
        } else {
            con.print(std::string(lv.title) + " —— 目标：" + lv.goal);
            con.print("走到桌子前的终端，看着它按 E：它会告诉你该改哪个文件、改哪一行。");
        }
    }

    // --watch：没有窗口也能验证热重载。先应用一次 content，然后最多等 N 毫秒，
    // 等到文件内容变化就重新应用，再走正常渲染出图 —— 和游戏里按 R 是同一条代码路径。
    if (args.watchMs > 0) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(args.watchMs);
        bool got = false;
        while (std::chrono::steady_clock::now() < deadline) {
            if (!watcher.poll(scene.world).empty()) {
                got = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        std::printf("[reload] %s（轮询 %d 次）\n",
                    got ? "content 有变化，已重新应用" : "等待超时，content 没有变化", watcher.polls());
    }

    // --scale 只在开窗时有意义：离屏出图的像素数必须严格等于 --width/--height
    // （截图比对脚本都指着这个），所以渲染分辨率按模式定下来。
    // 开窗时渲染分辨率来自**设置里那一档**（封顶 720p），--scale 再降采样。
    const int renderW = windowed ? settings.width / args.scale : args.width;
    const int renderH = windowed ? settings.height / args.scale : args.height;
    Rasterizer rz(args.threads);
    rz.resize(renderW, renderH);

    Toast toast;
    int shotIndex = 0;
    auto takeShot = [&]() {
        // 先按「此刻的世界」重绘一帧再存。这样「刚改完数据就拍」拍到的一定是新样子，
        // 而不是上一帧的旧画面 —— 离屏模式（--cmd）里更必须：那时一帧都还没渲染过。
        rz.framebuffer().clear(kClearColor);
        renderFrame(rz, scene, 0.0f);
        // 故意画在面板/准星之前：存的是「干净的世界」，UI 挡住的底部也拍得全。
        char path[64];
        std::snprintf(path, sizeof(path), "shots/console_%02d.png", ++shotIndex);
        const std::vector<uint8_t> rgb = rz.framebuffer().toRGB8(args.exposure, true);
        if (writePNG(path, rz.framebuffer().width, rz.framebuffer().height, rgb.data())) {
            con.printOk(std::string("已存 ") + path);
            toast.show(std::string("已存 ") + path, 0.0, 1.6);
        } else {
            con.printError(std::string("写 PNG 失败：") + path + "（shots/ 目录在不在？）");
            toast.show("写 PNG 失败，看看 shots/ 目录在不在", 0.0, 2.4);
        }
    };
    // ---- 入群二维码：四关全通之后才解锁的东西（见 engine/recruit.h）
    RecruitState recruit;
    recruit.qr.loadFromFile(kRecruitQrPath);  // 读不出来会自己打印修复提示，不崩
    recruit.open = args.qrPreview;            // --qr-preview：离屏核对这张码画得对不对
    // 按 Q 和敲 qr 命令走的是**同一条路**。没解锁就返回 false，不给后门 ——
    // "通关才能看"是这个功能的全部意义，留个后门等于没做。
    auto showRecruit = [&]() -> bool {
        if (!recruit.unlocked && !args.qrPreview) return false;
        recruit.open = true;
        con.setVisible(false);  // 覆盖层会盖住控制台，索性关掉，免得 Esc 关掉码又冒出个控制台
        return true;
    };
    con.setHandler([&](const std::string& line) {
        runCommand(line, con, scene.world, watcher, takeShot, showRecruit, judge, rt);
    });

    // 窗口模式：没有 --shot 就开窗。打不开（没桌面、远程会话…）就老老实实退回离屏，
    // 而不是报个错什么都看不着 —— 这个项目的第一课是"几条命令就能跑起来"。
    if (windowed) {
        Window win;
        if (win.open(settings.width, settings.height, kWindowTitle)) {
            // 开局就按上次存的模式摆好（open() 只开一个普通窗口，模式在这里补上）
            if (settings.mode != WindowMode::Windowed) {
                win.setWindowMode(settings.mode, settings.width, settings.height);
            }
            // --cmd 在开窗模式下也在进场前跑一遍：离屏那边一直是这样，两边保持一致，
            // "脚本按得出来的"和"宣讲现场真人按得出来的"才是同一条路。
            for (const std::string& c : args.cmds) con.run(c);
            const int rc =
                runWindow(args, win, scene, rz, con, takeShot, showRecruit, toast, judge, rt, recruit,
                          settings, resIndex, watcher);
            // --level N 是**临时覆盖**（--help 里写着"直接站在第 N 关，不看存档"），
            // 所以它也不该往存档里写 —— 对称。
            //
            // 这个坑真踩过：量帧率时跑了一批开窗的 `--level 3`，把存档的关卡号从第 1 关
            // 顶到了第 3 关，下次启动人就莫名其妙站在第 3 关了。调试参数不该有副作用。
            if (args.level >= 0) {
                std::printf("[存档] 这次带了 --level %d（临时覆盖），不写存档 —— 你的进度没被动过\n",
                            args.level);
                return rc;
            }
            // 人一按 ESC / 点叉就存一次档：不搞"找到存档点才能存"，那套仪式感是给
            // 长流程 RPG 的。这里存档的意义只有一条 —— 下次打开还站在昨天那个位置、
            // 昨天改过的 content/ 也还在。存的是「离开那一刻」的玩家位姿。
            SaveData sv;
            sv.level = rt.index;  // 走到哪一关了
            sv.goals = rt.goals;  // 哪几关过了（决定下次进门要不要再欢呼一次）
            sv.feet = scene.player.feet;
            sv.yaw = scene.player.yaw;
            sv.pitch = scene.player.pitch;
            if (writeSave(sv)) {
                std::printf("[存档] 已存 %s：站在 (%.2f, %.2f, %.2f)，第 %d 关\n", kSavePath,
                            double(sv.feet.x), double(sv.feet.y), double(sv.feet.z), sv.level);
            } else {
                std::fprintf(stderr, "[存档] 写 %s 失败 —— 这次的位置没能记下（下次还是从老地方开始）\n",
                             kSavePath);
            }
            return rc;
        }
        std::fprintf(stderr, "[窗口] 开不了窗 —— 这次改用离屏出图（--shot %s）。\n",
                     args.shot.c_str());
    }

    for (const std::string& c : args.cmds) con.run(c);
    if (!args.cmds.empty()) {
        for (int i = 0; i < con.lineCount(); ++i) std::printf("[console] %s\n", con.lineAt(i).c_str());
    }

    std::printf("[dreamlab-rt] 渲染 %dx%d，%d 帧，线程 %s\n", renderW, renderH, args.frames,
                args.threads > 0 ? std::to_string(args.threads).c_str() : "自动");

    // 离屏的走动：固定 1/60 秒一步。验收要的是"每次都一样"，不是"这台机器多快"。
    const std::vector<WalkStep> walk = parseWalk(args.walk);
    const float fixedDt = 1.0f / 60.0f;

    // ---- 跑一关：渲染 frames 帧 → 判分 →（可选）叠文字层 →（可选）写 PNG
    // 抽成 lambda 是为了 --all-levels：四关走的是同一段代码，不能各写一遍 ——
    // 写两遍迟早会分叉成"单关能跑、串联跑出来的是另一回事"。
    auto runOneLevel = [&](int levelIndex, const std::string& shotPath) {
        rt.index = levelIndex;

        double totalMs = 0.0;
        for (int f = 0; f < args.frames; ++f) {
            const float t = float(f) / 60.0f;
            const InputState in = walkInputAt(walk, f);
            updatePlayer(scene.player, in, scene.world, fixedDt);
            syncCamera(scene);
            if (in.interact) interact(scene, con, toast, 0.0, rt.index);

            const auto t0 = std::chrono::steady_clock::now();
            rz.framebuffer().clear(kClearColor);
            renderFrame(rz, scene, t);
            const auto t1 = std::chrono::steady_clock::now();
            totalMs += std::chrono::duration<double, std::milli>(t1 - t0).count();

            if (args.trace) {
                std::printf("[trace] f=%d pos=(%.3f,%.3f,%.3f) yaw=%.3f pitch=%.3f\n", f,
                            double(scene.player.feet.x), double(scene.player.feet.y),
                            double(scene.player.feet.z), double(scene.player.yaw),
                            double(scene.player.pitch));
            }
        }

        // 走动会往控制台里写东西（按 E 的交互），所以帧跑完再打一遍，离屏也能"看见"交互结果
        if (!walk.empty()) {
            for (int i = 0; i < con.lineCount(); ++i)
                std::printf("[console] %s\n", con.lineAt(i).c_str());
        }

        const double avgMs = totalMs / double(args.frames);
        std::printf("[dreamlab-rt] 三角形 %lld 个（剔除 %lld），着色 %lld 像素\n", rz.drawnTriangles(),
                    rz.culledTriangles(), rz.shadedPixels());
        std::printf("[dreamlab-rt] 平均每帧 %.2f ms（%.0f FPS），光栅化 %.2f ms\n", avgMs,
                    avgMs > 0.0 ? 1000.0 / avgMs : 0.0, rz.lastRasterMs());
        std::printf("[dreamlab-rt] 画面平均亮度 %.4f\n", rz.framebuffer().meanLuminance());

        // 关掉引擎重判一次：这几十帧里世界可能被 --walk 走、被 --cmd 改、被 --watch 重载过。
        // 判定的"收卷"必须发生在这一切之后，否则截的图和量出来的进度说的不是同一件事。
        const Level& lv = levelAt(rt.index);
        rt.status = judge.evaluate(scene.world, lv);
        std::printf("[关卡] %s · 目标「%s」· 进度 %d%%（评委机位亮度 %.4f，其中灯贡献 %.4f）\n",
                    lv.title, lv.goal, progressPercent(rt.status), double(rt.status.luminance),
                    double(rt.status.lightLuminance));

        // 文字层（HUD / 控制台）是最后一步叠上去的：不进深度测试、不参与光照，
        // 但和 3D 走同一条 ACES → sRGB 出口 —— 所以 UI 的颜色也得给线性 HDR 值。
        if (args.hud || con.visible() || args.qrPreview) {
            // 故意不检查返回值：字模加载失败时 Font 会画红块占位（绝不白屏），
            // 修复提示已经由 loadFromFile 打到 stderr 上了。
            Font font;
            font.loadFromFile("assets/font/pixel12.bin");
            const int inset = con.panelHeight(font, renderW, renderH);
            GoalPanel goal;
            goal.title = lv.title;
            goal.goal = lv.goal;
            goal.progress = rt.status.progress;
            goal.passed = rt.status.passed();
            // 离屏出图也带上"下一步"（截图是拿去当素材/验收的，和玩家看到的应当一致）
            goal.nextStep = nextStepFor(lv, makeView(scene.world, judge, rt.status), rt.status.passed());
            if (args.hud)
                drawHud(rz.framebuffer(), font, float(avgMs > 0.0 ? 1000.0 / avgMs : 0.0), inset,
                        kWindowHint, goal);
            if (con.visible()) con.draw(rz.framebuffer(), font);
            // --qr-preview：把"通关之后才看得到"的那张码也画出来，好核对画得对不对。
            // 这里**故意不看关卡进度** —— 它是出图验证用的开关（见 --help），不是游戏里的
            // 解锁；游戏里那条解锁的路在 runWindow，只有真把四关打完才走得到。
            if (args.qrPreview) {
                RecruitQr recruit;
                recruit.loadFromFile(kRecruitQrPath);
                drawRecruitQr(rz.framebuffer(), font, recruit, recruit.ready());
            }
        }

        if (shotPath.empty()) return true;  // --all-levels 不给 --shot 时只判分，不出图
        const std::vector<uint8_t> rgb = rz.framebuffer().toRGB8(args.exposure, true);
        if (!writePNG(shotPath.c_str(), renderW, renderH, rgb.data())) {
            std::fprintf(stderr, "[错误] 写 PNG 失败: %s\n", shotPath.c_str());
            return false;
        }
        std::printf("[dreamlab-rt] 已写出 %s\n", shotPath.c_str());
        return true;
    };

    if (!args.allLevels) {
        if (!runOneLevel(rt.index, args.shot)) return 1;
        return 0;
    }

    // ---- --all-levels：四关依次走一遍做冒烟 ----
    //
    // 判"通过"的标准不是"四关都过关"（出厂状态下本来就该都不过），而是：
    //   · 不喂解答时：四关都判出合法分数（不崩、不读越界），这就是冒烟
    //   · 喂了 --auto 时：**每关有解答的都必须真的过关** —— 没过就是解答写错了、
    //     或者关卡判定漂了，两种都要当场红
    // 第 0 关在 --auto 下算"跳过"，不算失败（原因见 kAutoSolutions 上面的说明）。
    int passed = 0;
    int skipped = 0;
    int failed = 0;
    const int total = levelCount();

    for (int i = 0; i < total; ++i) {
        std::printf("\n===== 第 %d 关 / 共 %d 关 =====\n", i, total);
        const AutoSolution* sol = autoSolutionFor(i);
        bool expectPass = false;

        if (args.autoSolve) {
            if (sol == nullptr) {
                ++skipped;
                std::printf("[--auto] 第 %d 关跳过，不予自动达成。\n", i);
                std::printf("[--auto]   它的正解是改 game/shaders/lighting.cpp 的 kAmbientStrength\n");
                std::printf("[--auto]   （编译期常数）再重新编译 —— 运行时改不了。\n");
                std::printf("[--auto]   能过它的另一条路是点一盏灯照进评委画面，但那是学生不会\n");
                std::printf("[--auto]   走的路径；算进来，四关可自动达成这句话就注水了。\n");
            } else {
                std::vector<std::string> log;
                if (!applyAutoSolution(scene.world, i, log)) {
                    ++failed;
                    std::printf("[--auto] 第 %d 关的解答**没生效**（详见上面的报错）\n", i);
                } else {
                    expectPass = true;
                    std::printf("[--auto] 已喂入第 %d 关的解答：%s\n", i, sol->what);
                }
            }
        }

        // --all-levels 出图时，把关号插到扩展名前面：build/l.png → build/l_level2.png
        std::string shot;
        if (args.hasShot) {
            shot = args.shot;
            const size_t dot = shot.find_last_of('.');
            const size_t slash = shot.find_last_of("/\\");
            if (dot != std::string::npos && (slash == std::string::npos || dot > slash)) {
                shot = shot.substr(0, dot) + "_level" + std::to_string(i) + shot.substr(dot);
            } else {
                shot += "_level" + std::to_string(i);
            }
        }

        if (!runOneLevel(i, shot)) return 1;

        if (rt.status.passed()) {
            ++passed;
        } else if (expectPass) {
            ++failed;
            std::printf("[--all-levels] ✗ 第 %d 关喂了解答却没过关（进度 %d%%）—— 解答或判定有问题\n", i,
                        progressPercent(rt.status));
        }
    }

    std::printf("\n[--all-levels] 四关走完：过关 %d 关", passed);
    if (skipped > 0) std::printf("，跳过 %d 关（运行时无解答）", skipped);
    if (failed > 0) std::printf("，**失败 %d 关**", failed);
    std::printf("\n");
    return failed == 0 ? 0 : 1;
    return 0;
}
