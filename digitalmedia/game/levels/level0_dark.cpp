// 第 0 关「黑暗」—— 游戏从这里开始，在一片全黑的工作室里醒来。
//
//  为什么黑？不是没灯，是整个房间的底光被一个系数乘没了。那个系数在
//  game/shaders/lighting.cpp 里，叫 kAmbientStrength，出厂值是 0.0f。
//  这一关要学生亲手把它改回来、重新编译、然后发现"存档把我放回了原来的位置"。
//
//  这一关同时也是这套判定方式的样板：目标不写在代码里，而是"评委相机看到的
//  那张图有多亮"。学生改的是着色器，量出来的是画面 —— 中间没有作弊的余地。
#include "../level.h"

namespace dlab {

namespace {

// 评委机位：站在屋子右前方，看前左角。
// 这个机位是挑出来的，挑法很具体：画面里必须一个自发光的东西都没有
// （屏幕、灯罩都在镜头背后），这样出厂状态下这张小图是纯黑 0.0000 ——
// 房间里有几分光，它就如数报几分，不会被两块亮斑垫出一个虚高的底。
Camera judgeCamera() {
    Camera cam;
    aimCamera(cam, Vec3{4.4f, 1.75f, 3.2f}, Vec3{-4.0f, 1.0f, 5.4f});
    return cam;
}

// 两个实测值（都是 96x54 的评委小图量出来的）：
//   出厂（kAmbientStrength = 0.0f）         → 0.0000
//   照提示改成 0.6f、重新编译之后           → 0.043895
// 环境光在着色器里是按这个系数线性缩放的，所以进度条基本是线性的：
// 拧到 0.2 大约走 1/3，拧到 0.6 正好走满。
constexpr float kDarkLuminance = 0.0000f;
constexpr float kLitLuminance = 0.043895f;

// 达标线比建议值低一截（0.95 → 亮度到 0.0417 就算满，大约对应旋钮拧到 0.57）。
// 为什么要留余量：学生照着提示把 0.6f 敲进去，得到的进度是 1.0000x —— 要是
// 达标线正好压在实测值上，浮点最后一位的抖动就能让它变成 0.9999，于是
// 「进度条写着 100%、但不算过关」。这种"我明明做对了"的假阴性，比判分松一点
// 危险得多。松一点顶多让 0.58 也过 —— 反正房间是真的亮起来了。
constexpr float kPassMargin = 0.95f;

float progressDark(const LevelView& view) {
    const float bar = (kLitLuminance - kDarkLuminance) * kPassMargin;
    return (view.luminance - kDarkLuminance) / bar;
}

// 这一关只有一步，所以"下一步"没什么分支 —— 房间亮了就没事了。
// 要动的是**代码文件**、不在场景里，所以没有要描边的实体。
NextStep nextStepDark(const LevelView& view) {
    if (progressDark(view) >= 1.0f) return NextStep{};
    return NextStep{"改 game/shaders/lighting.cpp 里的 kAmbientStrength："
                    "把 0.0f 改成 0.6f，存盘 → 双击 build.bat → 再运行 dreamlab.exe",
                    ""};
}

// 提示是写给**零基础的人**看的 —— 他们不知道 .cpp 是什么、不知道用什么打开它，
// 也不知道"存盘"和"重新编译"具体是哪个动作。所以宁可啰嗦，也要写成能照着做的步骤。
// 注意别写死行号（"第 40 行"会漂），教他们搜关键词反而更耐用。
const char* kHint =
    "这间屋子本来是亮的，现在全黑 —— 有人把灯的总开关拧到了零。\n"
    "这一步不需要会写代码，照着做就行：\n"
    "\n"
    " 1. 打开 game/shaders/ 这个文件夹，用记事本打开里面的 lighting.cpp\n"
    "    （右键它 -> 打开方式 -> 记事本。装过 VS Code 的话用那个更好）\n"
    " 2. 按 Ctrl+F，搜 kAmbientStrength\n"
    " 3. 你会看到这么一行：\n"
    "        constexpr float kAmbientStrength = 0.0f;\n"
    "    把里面的 0.0f 改成 0.6f\n"
    " 4. 按 Ctrl+S 存盘\n"
    " 5. 回到仓库那个文件夹，双击 build.bat，等十几秒\n"
    " 6. 再运行 build\\dreamlab.exe —— 房间就亮了，而你还站在这里\n"
    "\n"
    "（第 6 步那句话不是安慰：改代码不用怕丢进度，游戏会把你放回原位。）";

}  // namespace

const Level& levelDark() {
    // 函数里的 static：第一次有人问起它的时候才构造（Camera 得算一下角度），
    // 之后每次拿到的都是同一个 —— 判定必须稳定，不能每帧换一台相机。
    static const Level lv = [] {
        Level l;
        l.id = "dark";
        l.title = "第 0 关 · 黑暗";
        l.goal = "让房间重新亮起来";
        l.hint = kHint;
        l.passNote =
            "而且注意一件事：你刚才改的是 C++、还重新编译了一次 —— 可回到游戏里，"
            "你还站在离开时的那个位置。位置存在 saved/save.bin 里，这就是「存档续玩」。"
            "改代码不用怕丢进度。";
        l.judge = judgeCamera();
        l.progress = progressDark;
        l.nextStep = nextStepDark;
        return l;
    }();
    return lv;
}

}  // namespace dlab
