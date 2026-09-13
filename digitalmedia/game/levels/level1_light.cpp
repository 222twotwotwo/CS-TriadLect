// 第 1 关「第一束光」—— 这一关教"灯光师"这份活儿的第一课。
//
//  第 0 关改的是 C++（着色器里的系数），要重编译才生效。
//  第 1 关改的全是 content/ 下的数据：存盘 → 按 R → 当场就变。同一件事的两种做法，
//  先后顺序是设计过的 —— 先体会到"改代码有多重"，才知道数据层为什么值得存在。
//
//  为什么过关要同时满足三件事？因为它们对应三个不同的知识点，少一个都学不到：
//    ① 灯罩自己得亮（emissive）—— 自发光只照亮自己，照不亮别人；
//    ② 灯得装回吊灯里（pos）    —— 位置比强度重要，光斑跟着位置走；
//    ③ 强度得拧上去（intensity）—— 平方反比：远一倍的暗四倍。
//  只做 ① 学生会以为"灯罩亮了房间就该亮"；只做 ③ 会看到一团光打在地上，
//  于是自己发现"位置不对"—— 这就是这一关最想让人撞见的那一下。
#include "../level.h"

namespace dlab {

namespace {

// 主光 = content/lights.txt 里的 light 0。这个编号是给学生的路标（文件里、
// 提示里、终端里说的都是"light 0"），所以关卡也认它，不做"哪盏灯都算"的宽容 ——
// 宽容会让"提示说的和我做的不一样"这种事变得可能。
constexpr int kMainLight = 0;

// 灯罩的 emissive 大于这个数才算"点亮了"（0.5 是个很暗的值：
// 只要学生把 emissive 从 0.00 改成一个真的会亮的数，就一定超过它）
constexpr float kShadeOn = 0.5f;

// 光离吊灯挂点多远算"装回灯里了"（米）。0.35 = 差不多一个灯罩的尺寸：
// 学生照着数字填就一定在范围里，但也不会"随手放个大概"就蒙混过去。
constexpr float kHangTolerance = 0.35f;

// 判定线。这里量的是 view.lightLuminance（灯的贡献），不是整张图的亮度 ——
// 所以达标线只有一个数，不用再担心"达标线是按哪种环境光底子定的"。
//
// 实测（出厂 lights.txt + 出厂 materials.txt，光按提示装回吊灯）：
//   灯的贡献      intensity 0  → 0.0000        → 出厂态干净地读 0%
//                intensity 48 → 0.1931        → 100%
//   强度扫描：32→0.1287、40→0.1609、44→0.1770、48→0.1931
//   （每 1 点强度值 0.0040，是干净的线性 —— 这条灯路没有自动曝光、没有人眼适应）
//   光留在门口 @48 时灯的贡献只有 0.0525：灯照的是门口，评委机位这一头基本没沾到。
//
// kBright 取 0.1650，落在"得把强度拧到 42 上下"的位置：照提示写 48 稳过，
// 只拧到 32 会停在 87% —— 差的那一点正好逼学生去读一眼"平方反比"。
//
// 为什么不再用绝对亮度：第一版是拿"第 0 关做完（ambient 0.6）"那间屋子的
// 0.2415 定的 0.2100，结果直接跳进第 1 关的人（ambient 还是出厂的 0）把三件事
// 全做对也只读 96%；就算把线压到两种底子都够得着，后面几关一改材质（第 2 关
// 就是要改材质）这个数还会再漂一次。改成量"灯的贡献"以后，环境光和别的关卡
// 怎么改都不影响这里 —— 教训的完整版写在 level_registry.cpp 里。
constexpr float kBrightLuminance = 0.1650f;

// 吊灯挂在哪儿：问场景要，不写死坐标 —— 改了 workshop.cpp 里吊灯的位置，
// 这一关的"正确位置"和灯罩的铭牌会跟着一起动，不会悄悄对不上。
Vec3 lampMount(const World& w) {
    const Entity* lamp = w.entity("吊灯");
    return lamp != nullptr ? lamp->position : Vec3{2.2f, 2.9f, 1.2f};
}

// 灯罩亮不亮：读材质，不读画面。
// 为什么不量图？灯罩是块 0.84m 的板，挂在 96x54 的小图边缘时只占几个像素 ——
// 而"emissive 是不是 0"在数据里是个非黑即白的事实，量它零风险。
// （对比第 0 关：那一关量的是"整间屋子的亮度"，没有对应的数据可读，只能看图。）
float shadeGlow(const World& w) {
    const int idx = w.findMaterial("lamp");
    if (idx < 0) return 0.0f;
    const Vec3 e = w.materials[size_t(idx)].emissive;
    return maxf(maxf(e.x, e.y), e.z);
}

float hangDistance(const World& w) {
    if (w.lightCount <= kMainLight) return 1.0e9f;
    return length(w.lights[kMainLight].position - lampMount(w));
}

float progressFirstLight(const LevelView& view) {
    const World& w = view.world;

    const float shade = shadeGlow(w) >= kShadeOn ? 1.0f : 0.0f;            // ① 灯罩亮了
    const float hang = hangDistance(w) <= kHangTolerance ? 1.0f : 0.0f;    // ② 光装回灯里了
    // ③ 展台真的被照亮了（连续量：光挪近一点、拧亮一点，进度条就往前走一点）。
    //    量的是 view.lightLuminance（灯的贡献），不是整张图的亮度 —— 后者会被
    //    环境光和别的关卡改的材质牵着走。理由和教训写在 level_registry.cpp 里。
    const float bright = clampf(view.lightLuminance / kBrightLuminance, 0.0f, 1.0f);

    const float p = shade * 0.25f + hang * 0.35f + bright * 0.40f;
    // 三件事都做到 = 1.0，明明白白地写出来。不靠 0.25+0.35+0.40 的浮点和正好凑到 1
    // —— 那种"差最后一位浮点就永远过不了关"的坑，第 0 关已经踩过一次了。
    if (shade > 0.0f && hang > 0.0f && bright >= 1.0f) return 1.0f;
    return p;
}

// 「现在差哪一步」。这一关有三件事，进度条只会告诉你"走到 40% 了"，
// 但零基础的人需要知道**是哪一件没做完**。这里把判定拆出来的三个条件依次问一遍，
// 谁没过就报谁 —— 而且给出具体数字，不用他回头翻提示。
//
// focusEntity = "吊灯"：这三步全都围着吊灯转（灯罩挂在它上面、光要装回它里面），
// 所以整关都给它描边，等于在黑暗里指"看那儿"。
NextStep nextStepFirstLight(const LevelView& view) {
    const World& w = view.world;

    if (shadeGlow(w) < kShadeOn) {
        return NextStep{"改 content/materials.txt 里 material lamp 的 emissive："
                        "0 0 0 → 4.2 3.8 3.0（灯罩得自己会亮）",
                        "吊灯"};
    }
    if (hangDistance(w) > kHangTolerance) {
        return NextStep{"灯罩亮了。现在把光挪回吊灯里：content/lights.txt 里 light 0 的 pos "
                        "改成 2.20 2.90 1.20（它现在还在门口）",
                        "吊灯"};
    }
    if (view.lightLuminance < kBrightLuminance) {
        return NextStep{"位置对了，就是还不够亮：把 light 0 的 intensity 从 0 拧到 48",
                        "吊灯"};
    }
    return NextStep{};
}

// 评委机位：站在展厅这一头，正对三个展台。
//   · 只有 +x 一个方向，三个展台沿 z 排开 —— 画面里是"三个球并排"，不是叠在一起；
//   · 视野收到 42 度（比玩家的 62 度窄），把墙和天花板的占比压下去，
//     画面上"亮"的部分尽可能都是展台附近的，量出来的数才敏感；
//   · 吊灯不在画面里：灯罩的状态是读材质数据的，不靠这张图（见 shadeGlow）。
Camera judgeCamera() {
    Camera cam;
    cam.fovY = radians(42.0f);
    aimCamera(cam, Vec3{-0.6f, 1.60f, -1.20f}, Vec3{3.60f, 1.20f, -1.20f});
    return cam;
}

const char* kHint =
    "这间屋子还差一盏灯。吊灯挂在天花板上（桌子右上方），但它现在不亮。\n"
    "要动两个文件，都是纯文本，记事本就能改：\n"
    "\n"
    " 1. 用记事本打开 content/lights.txt，找到 light 0 那一段，有两行要改：\n"
    "        pos       2.20 2.90 1.20    <- 灯挂在哪儿（现在被人挪到了门口）\n"
    "        intensity 48               <- 灯多亮（现在是 0，等于没开）\n"
    " 2. 再用记事本打开 content/materials.txt，找到 material lamp 那一段，\n"
    "    把 emissive 那行点亮：\n"
    "        emissive  4.20 3.80 3.00    （现在是 0 0 0，所以灯罩是个黑盒子）\n"
    " 3. 两个文件都存盘（Ctrl+S），切回游戏，按 R\n"
    "\n"
    "这次不用重新编译 —— 你改的是数据、不是代码，按 R 当场就生效。\n"
    "\n"
    "为什么位置比强度重要：亮度按「距离的平方」衰减，距离翻一倍只剩四分之一。\n"
    "把灯挪回吊灯底下，比把它拧到 500 有用得多。\n"
    "拿不准就走到吊灯底下抬头按 E，它会告诉你灯现在挂在哪儿。";

const Level& make() {
    static const Level lv = [] {
        Level l;
        l.id = "first-light";
        l.title = "第 1 关 · 第一束光";
        l.goal = "把主光装回吊灯里并点亮，让展台亮起来";
        l.hint = kHint;
        l.passNote =
            "记住这一个：把灯挪回吊灯，比把它拧到 500 有用得多 —— 亮度按距离平方衰减，"
            "距离翻一倍就只剩四分之一。位置比强度重要，这是灯光这行的第一课。";
        l.judge = judgeCamera();
        l.progress = progressFirstLight;
        l.nextStep = nextStepFirstLight;
        return l;
    }();
    return lv;
}

}  // namespace

const Level& levelFirstLight() { return make(); }

}  // namespace dlab
