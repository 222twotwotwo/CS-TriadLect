// 玩家控制器：走位、视角、碰撞。
//
// 设计上的两个刻意选择：
//
// ① **输入被"翻译"过再进来**（InputState 里只有"前后 / 左右 / 视角增量 / 按 E"）。
//    键盘、鼠标、--walk 脚本喂的都是这个结构，所以"离屏跑 300 帧模拟一直按 W"
//    和玩家在窗口里按着 W 走的是同一条代码、同一堵墙。碰撞这种"多一厘米就穿模"
//    的事，只有在离屏下能自动化验证时才敢说它是对的。
//
// ② **P0 不做重力、不做跳跃**（设计文档定的）。地是平的、人不掉下去，
//    于是"走路"这件事只剩水平面两轴 —— 学生读得完，也改得动（想加重力？
//    kGravity 加一行，y 轴上多一个 tryMove 而已，方向是敞开的）。
//
// 碰撞用"轴分离 + 二分退回"：先单独试 x 轴，撞了就二分找到贴边位置；
// z 轴再做一遍。好处是贴着墙斜着走不会被卡死 —— 另一个轴始终是自由的，
// 手感是"沿着墙滑过去"，这是第一人称最基本的一条手感。
#pragma once

#include "../core/vec.h"
#include "../engine/world.h"

namespace dlab {

// 一帧的输入意图（不问它从哪来）
struct InputState {
    float moveForward = 0.0f;  // +1 前 / -1 后
    float moveRight = 0.0f;    // +1 右 / -1 左
    float lookDX = 0.0f;       // 鼠标横向位移（像素）
    float lookDY = 0.0f;       // 纵向位移
    bool interact = false;     // E：这一帧按下了
};

struct Player {
    Vec3 feet{0.0f, 0.0f, 4.5f};  // 脚底中心（不是眼睛）—— 碰撞盒就贴着脚算
    float yaw = 0.0f;             // 弧度，0 = 朝 -z 看，和 Camera 同一套约定
    float pitch = -0.04f;
    Vec3 velocity{0.0f, 0.0f, 0.0f};  // 水平速度，只用来做起步/停步的平滑
    bool noclip = false;              // --noclip：穿墙看场景（调试和拍图用）

    // 都是"人"的常识值：学生改这里能立刻感觉到差别，这是留给他们的旋钮之一
    static constexpr float kEyeHeight = 1.62f;
    static constexpr float kHeight = 1.75f;    // 碰撞盒高度（比眼睛高一点，头顶有肉）
    static constexpr float kRadius = 0.30f;    // 碰撞盒半径（肩宽的一半）
    static constexpr float kWalkSpeed = 3.2f;  // m/s，正常快走
    static constexpr float kAccel = 14.0f;     // 加速度：起步不打滑，停步不飘
    static constexpr float kLookSpeed = 0.0024f;  // 鼠标像素 → 弧度
    static constexpr float kReach = 3.2f;         // 按 E 的有效距离

    Vec3 eye() const { return feet + Vec3{0.0f, kEyeHeight, 0.0f}; }

    Vec3 forward() const {
        const float cp = std::cos(pitch);
        return Vec3{-std::sin(yaw) * cp, std::sin(pitch), -std::cos(yaw) * cp};
    }
    // 走路永远在水平面上：视线抬起来也不会往天上飞
    Vec3 forwardFlat() const { return Vec3{-std::sin(yaw), 0.0f, -std::cos(yaw)}; }
    Vec3 rightFlat() const { return Vec3{std::cos(yaw), 0.0f, -std::sin(yaw)}; }

    void setLookAt(Vec3 target) {
        const Vec3 d = normalize(target - eye());
        yaw = std::atan2(-d.x, -d.z);
        pitch = clampf(std::asin(clampf(d.y, -1.0f, 1.0f)), -1.5533f, 1.5533f);
    }
};

// 走一步。dt 秒。返回这次有没有撞到东西（关卡/调试用）
bool updatePlayer(Player& p, const InputState& in, const World& world, float dt);

// 这个人站在当前位置合不合规（不跟任何实体打架）。存档回读、出生点校验用：
// "存档里的位置被墙占了"必须能被发现，否则人会卡在实体里出不来。
bool playerFits(const World& world, const Player& p);

}  // namespace dlab
