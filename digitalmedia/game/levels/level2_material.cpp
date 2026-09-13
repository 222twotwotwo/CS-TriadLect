// 第 2 关「材质」—— 这一关教"技术美术"每天在调的那三个数。
//
//  第 1 关把灯点亮了，三个球终于看得见了。看得见之后，问题就变成"它们看起来对不对"：
//  展台上的三个球被人改乱了，每个球旁边的细柱子上顶着一个小球 —— 那是它的材质样板，
//  也就是标准答案。这一关要做的事只有一件：**照着一模一样改回来**。
//
//  为什么判定读数据、不读画面？因为这一关的答案本来就是数据。画面上"像不像镜面"
//  是一个连续量（近似的 roughness 也能看出点金属味），而"是不是照样板改的"是个事实：
//  albedo / roughness / metallic 九个数，对得上就是对得上。读数据还顺带修掉一个
//  隐患 —— 这一关的判定不会被"房间亮不亮"影响，学生摸黑直接跳进来也能过。
//
//  为什么容差不是 0：这一关要教的是"看起来一样"，不是"一字不差"。0.92 写成 0.9
//  眼睛根本分不出来，却要判错，那是拿浮点在为难人。容差取到"明显不一样的数
//  一定被挡住、手抖一位小数一定放行"的位置。
#include <cstdio>

#include "../level.h"

namespace dlab {

namespace {

// 三对名字：展台上那个球（学生要改的）→ 旁边细柱子上的样板（标准答案）。
// 样板材质定义在 workshop.cpp 里，content/materials.txt 里没有它们 ——
// 那一份文件是给学生的"草稿纸"，这一关的答案不放在草稿纸上。
struct Pair {
    const char* ball;
    const char* ref;
    const char* cn;  // 中文名，用在自检的消息里
};

const Pair kPairs[3] = {
    {"chrome", "ref_chrome", "镜面球"},
    {"plastic", "ref_plastic", "塑料球"},
    {"clay", "ref_clay", "陶土球"},
};

constexpr float kAlbedoTol = 0.08f;  // 颜色：每个通道差这么点，眼睛分不出来
constexpr float kRoughTol = 0.10f;   // 粗糙度：0.92 和 0.85 看起来是一回事
constexpr float kMetalTol = 0.50f;   // 金属度：这个数不是 0 就是 1，判的是"选对了没"

bool nearf(float a, float b, float tol) {
    const float d = a - b;
    return d <= tol && d >= -tol;
}

// 这个球和它的样板对上了没有：三个参数全对上才算。
//
// 为什么按"球"给分，不按"参数"给分（9 个数各占 1/9）？因为出厂状态里
// 每个球只剩一个参数是错的（另外两个本来就是对的），按参数给分的话，
// 一进门进度条就停在 5/9 = 55% —— 学生什么都没做却被告知"过半了"。
// 按球给分，出厂干净地读 0%，改对一个球明确地跳三分之一：
// 三个球、三格，他看得见自己走到哪儿了。
bool ballMatches(const World& w, const Pair& p) {
    const int a = w.findMaterial(p.ball);
    const int b = w.findMaterial(p.ref);
    // 名字对不上（文件被改得面目全非）→ 这一球不算数。返回 false 而不是崩溃，
    // 也不能是"两个都找不到所以相等"—— 那会把改坏文件变成过关。
    if (a < 0 || b < 0) return false;
    const Material& m = w.materials[size_t(a)];
    const Material& r = w.materials[size_t(b)];
    const bool albedoOk = nearf(m.albedo.x, r.albedo.x, kAlbedoTol) &&
                          nearf(m.albedo.y, r.albedo.y, kAlbedoTol) &&
                          nearf(m.albedo.z, r.albedo.z, kAlbedoTol);
    return albedoOk && nearf(m.roughness, r.roughness, kRoughTol) &&
           nearf(m.metallic, r.metallic, kMetalTol);
}

float progressMaterial(const LevelView& view) {
    const World& w = view.world;
    int matched = 0;
    for (const Pair& p : kPairs) {
        if (ballMatches(w, p)) ++matched;
    }
    if (matched >= 3) return 1.0f;  // 满分明明白白写出来，不靠 3/3.0f 的浮点正好等于 1
    return float(matched) / 3.0f;
}

// 「现在差哪一步」—— 这一关**点名**：哪个球还不对、哪个数该改成什么。
// 零基础的人看到进度条停在 33% 只会问"是哪一个错了"，这里直接答，而且给出目标值，
// 省得他回头翻提示、或者把三个球挨个试一遍。
// 只报**第一个**不对的球：一次说一件，比一口气列三件好做。
NextStep nextStepMaterial(const LevelView& view) {
    const World& w = view.world;
    for (const Pair& p : kPairs) {
        if (ballMatches(w, p)) continue;

        const int a = w.findMaterial(p.ball);
        const int b = w.findMaterial(p.ref);
        if (a < 0 || b < 0) {
            return NextStep{std::string("content/materials.txt 里找不到「") + p.ball + "」那一段", ""};
        }
        const Material& m = w.materials[size_t(a)];
        const Material& r = w.materials[size_t(b)];
        char buf[200];
        if (!nearf(m.albedo.x, r.albedo.x, kAlbedoTol) ||
            !nearf(m.albedo.y, r.albedo.y, kAlbedoTol) ||
            !nearf(m.albedo.z, r.albedo.z, kAlbedoTol)) {
            std::snprintf(buf, sizeof(buf),
                          "「%s」的颜色不对：materials.txt 里把它的 albedo 改成 %.2f %.2f %.2f", p.cn,
                          double(r.albedo.x), double(r.albedo.y), double(r.albedo.z));
        } else if (!nearf(m.roughness, r.roughness, kRoughTol)) {
            std::snprintf(buf, sizeof(buf), "「%s」的表面还不对：把它的 roughness 改成 %.2f", p.cn,
                          double(r.roughness));
        } else {
            std::snprintf(buf, sizeof(buf), "「%s」的金属度还不对：把它的 metallic 改成 %.2f", p.cn,
                          double(r.metallic));
        }
        // focusEntity 用中文名 —— 那才是场景里那个实体的名字（orbName 那几个）
        return NextStep{buf, p.cn};
    }
    return NextStep{};
}

// 评委机位：站在展台正对面，一列六个球（三个球 + 三个样板）全进画面。
// 为什么站得比第 1 关远：这一关要一眼看全"球 vs 样板"，缺一个都对比不起来。
// 展台在 x=3.6 排成一列，z 从 -3.0（镜面）到 1.6（陶土样板）跨了 4.6 米，
// 加上展台底座的半径，横里要装下 5.35 米 —— 站在 6.2 米外才装得下。
// 视场角比第 1 关窄（36° 对 42°）：42° 站这么远，一列球只占画面中间四成，
// 缩到 36° 刚好把展台撑满八成的宽度，判定预览图里也一眼看得清。
// 为什么正对着看、不斜着看：斜视时"球面和样板面"的受光角度不一样，同一份材质
// 在画面上会显得一深一浅，学生就会怀疑自己改错了。
Camera judgeCamera() {
    Camera cam;
    cam.fovY = radians(36.0f);
    aimCamera(cam, Vec3{-2.60f, 1.72f, -0.87f}, Vec3{3.60f, 1.25f, -0.87f});
    return cam;
}

const char* kHint =
    "展台上这三个球被人改乱了。每个球旁边的细柱子上顶着一个小球 —— 那是它该有的样子\n"
    "（我们叫它「样板」）。你要做的，就是把三个球改回样板的样子。\n"
    "\n"
    " 1. 先看到答案：走到球跟前按 E，或者敲 inspect（不带名字会把所有材质一起列出来）\n"
    " 2. 用记事本打开 content/materials.txt\n"
    "    里面每个球一段（chrome / plastic / clay），照着样板把不一样的那几个数改掉\n"
    " 3. 存盘（Ctrl+S），切回游戏，按 R —— 三个球各占三分之一的进度\n"
    "\n"
    "三个数各管一件事：\n"
    "    albedo      颜色\n"
    "    roughness   表面多光滑（0 像镜子，1 像土）\n"
    "    metallic    算不算金属（金属会把整个房间的颜色反射上去）\n"
    "\n"
    "判定比的是「这个球像不像它自己的样板」，所以三个球抄成同一个值是不算的。\n"
    "小窍门：改 roughness 的时候盯着球面上的高光看 —— 它会从糊开的一大团，变成一个小亮点。";

const Level& make() {
    static const Level lv = [] {
        Level l;
        l.id = "material";
        l.title = "第 2 关 · 材质";
        l.goal = "照着样板，把三个球的材质改回来";
        l.hint = kHint;
        l.passNote =
            "你刚才干的就是技术美术的日常：别人给你一个目标结果（旁边那根样板），"
            "你要找出对应的那组数字。roughness 管高光糊不糊、metallic 管反不反射环境 —— "
            "这两个数你能凭画面反推出来了，这就是这份工作最核心的手感。";
        l.judge = judgeCamera();
        l.progress = progressMaterial;
        l.nextStep = nextStepMaterial;
        return l;
    }();
    return lv;
}

}  // namespace

const Level& levelMaterial() { return make(); }

}  // namespace dlab
