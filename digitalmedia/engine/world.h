// 世界 = 网格库 + 材质表 + 实体表 + 光照 + 环境。
//
// 所有东西都按"下标"互相引用（实体 → 网格下标 / 材质下标），
// 好处有两个：热重载材质只要改数组里的值；关卡换主题不用重新加载任何文件。
#pragma once

#include <deque>
#include <string>
#include <vector>

#include "../core/mat.h"
#include "../core/material.h"
#include "../core/raster.h"
#include "../core/texture.h"
#include "../engine/entity.h"
#include "../engine/mesh.h"

namespace dlab {

// 第一人称相机：位置 + 偏航/俯仰。用欧拉角而不是矩阵，是因为玩家输入天然就是这两个角度。
struct Camera {
    Vec3 position{0.0f, 1.62f, 4.5f};
    float yaw = 0.0f;     // 弧度，0 = 朝 -z 看
    float pitch = 0.0f;   // 抬头为正
    float fovY = radians(62.0f);

    Vec3 forward() const {
        const float cp = std::cos(pitch);
        return Vec3{-std::sin(yaw) * cp, std::sin(pitch), -std::cos(yaw) * cp};
    }
    Vec3 right() const { return Vec3{std::cos(yaw), 0.0f, -std::sin(yaw)}; }
    Vec3 up() const { return cross(right(), forward()); }
    Mat4 view() const { return lookAt(position, position + forward(), Vec3{0.0f, 1.0f, 0.0f}); }
};

// 按"站在 eye、看向 target"摆一台相机。手算 yaw/pitch 太容易差一个正负号，
// 而关卡里的评委机位是照着场景摆的 —— 那里用"看哪"描述，比用角度自然得多。
inline void aimCamera(Camera& cam, const Vec3& eye, const Vec3& target) {
    cam.position = eye;
    const Vec3 d = normalize(target - eye);
    cam.yaw = std::atan2(-d.x, -d.z);  // 0 = 朝 -z 看，和 Player 同一套约定
    cam.pitch = clampf(std::asin(clampf(d.y, -1.0f, 1.0f)), -1.5533f, 1.5533f);
}

class World {
public:
    static constexpr int kMaxLights = 8;

    std::vector<Mesh> meshes;
    std::vector<std::string> meshNames;
    // 纹理用 deque 而不是 vector：材质里存的是 Texture*，deque 在 push_back 时
    // 不会搬动已有元素，指针永远有效（vector 扩容会把它们全变成野指针）。
    std::deque<Texture> textures;
    std::vector<Material> materials;
    std::vector<std::string> materialNames;
    std::vector<Entity> entities;

    Light lights[kMaxLights];
    int lightCount = 0;

    // 环境：ambient 是"整个世界的底光"，第 0 关就是从 0 开始把它加回来
    Vec3 ambient{0.0f, 0.0f, 0.0f};
    Vec3 skyColor{0.46f, 0.56f, 0.78f};
    Vec3 groundColor{0.22f, 0.18f, 0.15f};

    int addMesh(const std::string& name, Mesh mesh);
    int addMaterial(const std::string& name, const Material& mat);
    int addEntity(Entity e);
    // 加完纹理再建材质，把返回的引用取地址存进 Material::albedoTexture
    Texture& addTexture(Texture tex);

    int findMesh(const std::string& name) const;
    int findMaterial(const std::string& name) const;
    int findEntity(const std::string& name) const;

    Entity* entity(const std::string& name);
    const Entity* entity(const std::string& name) const;

    // 按网格包围盒 + 实体变换，重算所有世界空间 AABB
    void refreshBounds();

    void fillEnv(ShadeEnv& env, const Vec3& cameraPos) const;

    // 整个场景画一帧（不 clear，也不做色调映射 —— 那是调用方的事）
    //
    // viewLight 是"只画给玩家看"的一盏补光（随身微光），默认不给。
    // 评委拍照（Judge::evaluate）必须是默认值 —— 多一盏灯就会把四关的标定垫高。
    //
    void render(Rasterizer& rz, const Camera& cam, const Light* viewLight = nullptr) const;

    // 相机能不能站在这里（用于出生点校验和关卡提示）
    bool overlapsSolid(const Vec3& point, float radius) const;

    // 玩家是个盒子（半径 + 身高），不能只用"点 + 半径"测：点半径会把 0.95m 高的
    // 展台整个漏过去（眼睛在 1.62，半径 0.32 根本够不着台面），人会直接从台子里穿过去。
    bool overlapsBox(const Vec3& lo, const Vec3& hi) const;

    // 视线射线：从 origin 沿 dir 步进，返回第一个"有交互的实体"。
    // 撞到不能穿过的实体就停 —— 隔着墙按 E 不该有反应，否则学生会以为命令坏了。
    struct RayHit {
        int entity = -1;       // 碰到的实体下标（-1 = 什么都没碰到）
        bool blocked = false;  // true = 先撞到的是挡路的实体（没有交互可言）
        float distance = 0.0f;
        Vec3 point{0.0f, 0.0f, 0.0f};
    };
    RayHit castRay(const Vec3& origin, const Vec3& dir, float maxDist, float step = 0.04f) const;
};

}  // namespace dlab
