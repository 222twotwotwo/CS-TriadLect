#include "player.h"

#include <cmath>

namespace dlab {

namespace {

// 人的碰撞体近似成一个盒子：底面比脚略高 2cm。
// 那 2cm 是给地板留的 —— 地板是一张零厚度的平面（aabbMin.y == aabbMax.y == 0），
// 盒子要是不抬起来一点，玩家从第一帧起就算"站在实体里"。
void playerBox(const Vec3& feet, Vec3& lo, Vec3& hi) {
    lo = Vec3{feet.x - Player::kRadius, feet.y + 0.02f, feet.z - Player::kRadius};
    hi = Vec3{feet.x + Player::kRadius, feet.y + Player::kHeight, feet.z + Player::kRadius};
}

bool blockedAt(const World& world, const Vec3& feet) {
    Vec3 lo, hi;
    playerBox(feet, lo, hi);
    return world.overlapsBox(lo, hi);
}

// from 是安全的、to 被挡住了 —— 二分 12 次找到"贴着障碍物站得住"的最后一个位置。
// 12 次把一帧 5cm 的位移压到 1e-5 m：既看不出一条缝，也不会陷进去。
Vec3 snapToWall(const World& world, const Vec3& from, const Vec3& to) {
    Vec3 safe = from;
    Vec3 hit = to;
    for (int i = 0; i < 12; ++i) {
        const Vec3 mid = (safe + hit) * 0.5f;
        if (blockedAt(world, mid)) hit = mid;
        else safe = mid;
    }
    return safe;
}

}  // namespace

// eye()/forward()/forwardFlat()/rightFlat()/setLookAt() 都写在头文件里（一行一个，
// 内联反而更好读）；这个 .cpp 只放真正需要"一步步算"的 updatePlayer。

// 这个人站在这里合不合规（不跟任何实体打架）。存档回读、关卡出生点校验要用：
// "存档里的位置已经被墙占了"必须能被发现，否则玩家会卡在实体里出不来。
bool playerFits(const World& world, const Player& p) { return !blockedAt(world, p.feet); }

bool updatePlayer(Player& p, const InputState& in, const World& world, float dt) {
    if (dt <= 0.0f) return false;

    // ---------------------------------------------------------------- 视角
    // 鼠标右移 = 向右转。本工程里"向右" = yaw 减小（yaw=0 朝 -z，+x 在右手边），
    // 所以这里是减号而不是加号 —— 符号搞反的话，第一次开窗就会觉得"鼠标反了"。
    p.yaw -= in.lookDX * Player::kLookSpeed;
    p.pitch -= in.lookDY * Player::kLookSpeed;
    p.pitch = clampf(p.pitch, -1.5533f, 1.5533f);  // ±89°：能抬头看灯，但不会翻过去
    // yaw 收进 (-pi, pi]：一直往一个方向转下去也不会掉精度。
    // 用 fmod 而不是「超了就减一圈」——鼠标狂甩一下，一帧就能转过好几圈。
    p.yaw = std::fmod(p.yaw + kPi, 2.0f * kPi);
    if (p.yaw < 0.0f) p.yaw += 2.0f * kPi;
    p.yaw -= kPi;

    // ---------------------------------------------------------------- 水平速度
    Vec3 wish = p.forwardFlat() * in.moveForward + p.rightFlat() * in.moveRight;
    const float wishLen = length(wish);
    // 斜着按 W+D 要归一化，不然斜走会快 √2 倍（约 1.41 倍）—— 这是新手最容易漏的一行
    wish = wishLen > 1e-4f ? wish / wishLen * Player::kWalkSpeed : Vec3{0.0f, 0.0f, 0.0f};
    // 起步/停步做一点平滑，不是瞬间满速。手感上"有点重量"，也不影响碰撞的正确性
    const float t = clampf(Player::kAccel * dt, 0.0f, 1.0f);
    p.velocity.x = lerpf(p.velocity.x, wish.x, t);
    p.velocity.z = lerpf(p.velocity.z, wish.z, t);
    p.velocity.y = 0.0f;  // P0 没有重力、没有跳跃

    const Vec3 step = p.velocity * dt;

    if (p.noclip) {  // 穿墙模式：调试和拍图用，绕开一切碰撞
        p.feet += step;
        return false;
    }

    // ---------------------------------------------------------------- 碰撞
    // 先赌一把"这一整步都不撞"。房间里绝大多数帧都是这种情况，赌对了就一条路走完。
    const Vec3 target = p.feet + step;
    if (!blockedAt(world, target)) {
        p.feet = target;
        return false;
    }

    // 撞了：拆成两个轴各走一遍。另一个轴永远是自由的，所以贴着墙斜着走是"滑过去"
    // 而不是"卡死" —— 第一人称最基本的一条手感。
    bool bumped = false;

    if (step.x != 0.0f) {
        const Vec3 tryX{p.feet.x + step.x, p.feet.y, p.feet.z};
        if (blockedAt(world, tryX)) {
            p.feet = snapToWall(world, p.feet, tryX);
            p.velocity.x = 0.0f;
            bumped = true;
        } else {
            p.feet = tryX;
        }
    }

    if (step.z != 0.0f) {
        const Vec3 tryZ{p.feet.x, p.feet.y, p.feet.z + step.z};
        if (blockedAt(world, tryZ)) {
            p.feet = snapToWall(world, p.feet, tryZ);
            p.velocity.z = 0.0f;
            bumped = true;
        } else {
            p.feet = tryZ;
        }
    }

    return bumped;
}

}  // namespace dlab
