// ============================================================================
//  关卡 —— 这个项目的"课程表"
// ----------------------------------------------------------------------------
//  一关 = 一句"你现在该干什么" + 一台固定的评委相机 + 一个打分函数。
//
//  判定为什么不看玩家眼前的画面？因为那样的话"看着屏幕就过关、转过身就掉回
//  0%"—— 判定必须和玩家站在哪、朝哪看完全无关。所以每一关自带一台评委相机
//  （judge），引擎用它拍一张小图，把这张图交给这一关的打分函数。
//
//  一关自己住在 game/levels/ 下的一个文件里：改关卡 = 改那一个文件。
//  这里只写"关卡长什么样"，不写任何一关的具体内容。
// ============================================================================
#pragma once

#include <cstdint>
#include <string>

#include "../core/framebuffer.h"
#include "../core/raster.h"
#include "../engine/world.h"

namespace dlab {

// 评委相机拍完照，交到关卡手里的东西。
struct LevelView {
    const World& world;        // 此刻的世界：灯亮不亮、材质什么样，数据都在这里
    const Framebuffer& frame;  // 评委机位看到的那张小图 —— 想量哪个角落自己量
    float luminance;           // frame 的平均亮度（把环境光、自发光、灯全算在内）
    // 只看灯贡献的那部分亮度 = 「现在」减「把灯全关掉」。量两遍的原因见 level_registry.cpp：
    // 绝对亮度会被环境光和别的关卡改的材质牵着走，这个差不会 —— 要判"灯把屋子照亮了吗"，
    // 就该量它。
    float lightLuminance;
};

// "下一步该干什么" —— **关卡自己算**，因为它本来就拆过判定（灯罩亮没亮、位置对不对、
// 亮度够不够）。零基础的人光看进度条不知道下一步该动什么：进度条说"走到 35% 了"，
// 可那 35% 是"三件事里的哪一件"？只有关卡自己答得上来。
//
// 这不是通用客套话，是每关自己的分解动作 —— 所以它和 passNote 一样写在关卡里。
// 用 std::string 不用 const char*：第 2 关要**点名是哪个球、哪个数不对**
// （"「陶土球」还不对：albedo 该改成 0.74 0.53 0.32"），那是运行时拼出来的。
struct NextStep {
    std::string text;         // 一句话。HUD 上直接显示，终端也打一遍
    std::string focusEntity;  // 这一步要动的东西（世界里的实体名）。
                              // 画面里会给它描一圈边，指"看这儿"。
                              // 空 = 不描（比如第 0 关要改的是代码文件，不在场景里）
};

// 一关。全是"描述"，没有一个字是"流程"—— 流程在 main.cpp 里。
struct Level {
    const char* id = "";                          // "dark"：--level 和存档记的是它，不是中文标题
    const char* title = "";                       // "第 0 关 · 黑暗"
    const char* goal = "";                        // HUD 上那一行
    const char* hint = "";                        // 走到终端按 E，终端把这段话念给你听
    // 过关那一下补的一句。每关的"高光"不一样 —— 第 0 关是"你改的是代码、重编译过，
    // 可你还站在原地"，第 2 关是"你今天干的就是技术美术的活"。写在关卡里而不是
    // 写在流程里，是因为它是这一关的教学点，不是通用的客套话。
    const char* passNote = "";
    Camera judge;                                 // 评委机位
    float (*progress)(const LevelView&) = nullptr;  // 0 = 刚进门，1 = 过关
    NextStep (*nextStep)(const LevelView&) = nullptr;  // 现在差哪一步（见 NextStep）
};

// 一次判定的结果
struct LevelStatus {
    float progress = 0.0f;
    float luminance = 0.0f;
    // 只看灯贡献的那部分亮度（= 开着灯的绝对亮度 - 灯全关的绝对亮度）。
    // 同一个数会原样传给关卡的 progress（见 LevelView::lightLuminance）；
    // 放在这里是为了让它能被打印出来 —— 达标线是照它定的，定标的时候得看得见它。
    float lightLuminance = 0.0f;
    bool passed() const { return progress >= 1.0f; }
};

// 进度条上写的那个百分数。规矩只有一条，但必须钉死：
//   屏幕上写着「100%」的那一刻，= 真的过关了。反过来也一样。
// 为什么值得单独写个函数：显示是四舍五入（99.9% 会写成 100%），而过关是硬判
// （progress 得真到 1.0）。不把这两个口径对齐，就会出现「它说我 100% 了，却不
// 给我过关」—— 这不是小毛病，这是学生整个下午白干。
inline int progressPercent(const LevelStatus& st) {
    if (st.passed()) return 100;
    const int pct = int(st.progress * 100.0f + 0.5f);
    return pct >= 100 ? 99 : pct;  // 差一点点过关 → 老实写 99%，别替它宣布成功
}

// 这一局正在第几关 + 上一次判定是什么。main.cpp 每帧更新一次，
// 命令解释器和 HUD 都从它取数 —— 保证"屏幕上的进度"和"命令查到的进度"是同一个。
struct LevelRuntime {
    int index = 0;
    LevelStatus status;
    uint32_t goals = 0;      // 位掩码：第 i 位 = 第 i 关以前就过了（从存档里带回来的）
    bool freshWin = false;   // 一进门就已经过关，但存档说这是头一回 —— 该补喊一声"你过了"
};

// 存档里那个位掩码的读和写。移位超过 31 位在 C++ 里是未定义行为（不是"结果是 0"），
// 所以下标一律先夹进合法范围 —— 将来关卡加到第 32 关，这里也不会悄悄写出个 UB。
constexpr int kMaxTrackedLevels = 32;
inline bool levelDone(uint32_t goals, int index) {
    return index >= 0 && index < kMaxTrackedLevels && (goals & (1u << index)) != 0;
}
inline uint32_t markLevelDone(uint32_t goals, int index) {
    return index >= 0 && index < kMaxTrackedLevels ? (goals | (1u << index)) : goals;
}

// 评委小图的尺寸。判定只要一个数，所以用不着全分辨率：
// 96x54 和 640x360 量出来的平均亮度实测差不到 0.5%，代价却只是一个零头。
constexpr int kJudgeWidth = 96;
constexpr int kJudgeHeight = 54;

// 评委机位。不跟玩家走，也不参与玩家的画面 —— 它只负责"替关卡看一眼世界"。
class Judge {
public:
    // world 是能改的：判定要拍两张（开着灯 / 关掉灯）才能把"灯的贡献"分出来，
    // 拍第二张时会把灯临时关掉再放回去。见 level_registry.cpp 里的说明。
    LevelStatus evaluate(World& world, const Level& lv);
    const Framebuffer& frame() const { return rz_.framebuffer(); }

private:
    Rasterizer rz_{4};  // 小图，4 个线程够用；主渲染还在抢核，别再添乱
};

int levelCount();
const Level& levelAt(int index);       // 越界的下标会被夹到合法范围里
int findLevel(const std::string& id);  // 找不到返回 -1

// 每一关实现在 game/levels/ 下，在这里认个脸
const Level& levelDark();        // 第 0 关「黑暗」
const Level& levelFirstLight();  // 第 1 关「第一束光」
const Level& levelMaterial();    // 第 2 关「材质」
const Level& levelTexture();     // 第 3 关「纹理」

}  // namespace dlab
