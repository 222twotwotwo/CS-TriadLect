// 材质与光源 —— 渲染器与"学生着色器"之间的数据契约。
// 这里的字段就是技术美术每天在引擎里调的那几个参数：albedo / roughness / metallic。
#pragma once

#include "texture.h"
#include "vec.h"

namespace dlab {

struct Material {
    Vec3 albedo{0.8f, 0.8f, 0.8f};  // 基础色（线性）
    float roughness = 0.35f;        // 粗糙度：0 = 镜面，1 = 完全漫反射
    float metallic = 0.0f;          // 金属度：0 = 绝缘体（塑料/木头），1 = 金属
    Vec3 emissive{0.0f, 0.0f, 0.0f};// 自发光（灯罩、屏幕、字标）
    const Texture* albedoTexture = nullptr;

    Vec2 uvScale{1.0f, 1.0f};
    Vec2 uvOffset{0.0f, 0.0f};
    int wrapMode = kWrapRepeat;
    int filterMode = kFilterBilinear;

    // 上锁 = content/*.txt 改不动它，改到它就报一行"这块被锁了"。
    // 用在哪：第 2 关「材质」的样板球。样板是那一关的标准答案，答案要是能跟着
    // 数据文件一起被改，学生把样板也改成乱的值就能"过关" —— 那不是过关，是把卷子抄了。
    bool locked = false;

    // 取某一个 uv 处的漫反射色（有贴图就采样，没有就用常量 albedo）
    Vec3 albedoAt(Vec2 uv) const {
        if (!albedoTexture || !albedoTexture->valid()) return albedo;
        const Vec2 scaled{uv.x * uvScale.x + uvOffset.x, uv.y * uvScale.y + uvOffset.y};
        return albedo * albedoTexture->sample(scaled, wrapMode, filterMode);
    }
};

struct Light {
    Vec3 position{0.0f, 2.0f, 0.0f};
    Vec3 color{1.0f, 1.0f, 1.0f};
    float intensity = 10.0f;
    float radius = 0.2f;  // 灯的物理尺寸，用于高光形状（软盒灯近似）
};

// 一个着色点的所有信息（由光栅化器插值好后递给学生写的着色函数）
struct Surface {
    Vec3 position;    // 世界空间坐标
    Vec3 normal;      // 世界空间法线（已归一化）
    Vec2 uv;
    Vec3 color{1.0f, 1.0f, 1.0f};  // 顶点色
    const Material* material = nullptr;
};

// 全局光照环境（逐像素都相同）
struct ShadeEnv {
    // 环境光基础亮度（由世界/关卡给出，来自 content/ 数据）。0 = 漆黑的房间。
    // 着色器会再乘一个自己的系数，学生两条路都能改。
    Vec3 ambient{0.0f, 0.0f, 0.0f};
    // 廉价的环境光照：用"上半球的天光 + 下半球的地面反弹光"替代一张真正的 HDR 环境贴图。
    // 别小看它 —— 金属之所以看起来像金属，靠的就是它反射出了周围的环境。
    Vec3 skyColor{0.46f, 0.56f, 0.78f};
    Vec3 groundColor{0.22f, 0.18f, 0.15f};

    const Light* lights = nullptr;
    int lightCount = 0;
    Vec3 cameraPos{0.0f, 0.0f, 0.0f};

    // 只进"玩家画面"的一盏补光（跟着相机走的小范围微光），主灯表之外单独算。
    // 为什么不塞进 lights 里：评委（Judge::evaluate）走的是 World::render 的同一条
    // 路，多一盏灯就会把四关的标定垫高 —— 关卡 1 的"灯贡献"是 0.2005 对 0.1650，
    // 关卡 0 的出厂亮度是精确的 0.0000，都经不起多一盏。所以评委那条路上它始终是
    // nullptr（见 game/level_registry.cpp），只有 game/main.cpp 画玩家那一帧才设。
    const Light* viewLight = nullptr;
};

}  // namespace dlab
