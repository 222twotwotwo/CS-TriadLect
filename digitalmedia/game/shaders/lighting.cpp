// ============================================================================
//  dreamlab-rt · 学生着色器 #1：光照
// ----------------------------------------------------------------------------
//  这是整个项目里你第一个该动手改的文件。
//
//  光栅化器会把屏幕上的每一个像素，包装成一个 Surface（它的世界坐标、法线、
//  uv、材质）传进来，问一句："这个点应该是什么颜色？"
//  这个函数就是那句回答。
//
//  这里实现的是一个简化版的 PBR（基于物理的渲染）：
//      result = 自发光 + 环境光 + Σ(每盏灯的漫反射 + 高光)
//  漫反射用 Lambert，高光用 GGX 微表面模型 + Schlick 菲涅尔
//  —— 和 UE5 / Blender / 各大引擎里跑的是同一套数学，只是少了贴图和阴影。
//
//  改完存盘，重新编译运行，画面立刻不一样。
// ============================================================================

#include "../../core/material.h"
#include "../../core/vec.h"

namespace dlab {

// ============================================================================
//  ★ 改动点 #1：环境光系数 ★
//  环境光总量 = env.ambient（世界给的基础亮度） × kAmbientStrength（你在这里拧的旋钮）。
//
//  - 改成 0.0f：整个房间瞬间沉进黑暗。
//  - 改成 2.0f：整间屋子亮到发白。
//
//  两条路都能点亮世界，值得体会它们的区别：
//    · env.ambient 来自 content/ 下的数据文件 —— 存盘按 R 立刻生效，不用编译；
//    · kAmbientStrength 是你正在读的这个 C++ 文件 —— 改完要重新编译。
//  这就是"美术调参"和"引擎改代码"的分工。
//
//  TODO(第 0 关「黑暗」)：现在它是 0.0f，所以整个房间是黑的 —— 这就是出生时
//  你看到的那片黑暗的来源。把它改成一个大于 0 的数（先试试 0.6f），
//  存盘 → 关掉游戏 → 重新运行 build.bat → 再进来（你还站在原来的位置，
//  存档会把你放回去）。房间亮起来的那一刻，第 0 关就过了。
// ============================================================================
constexpr float kAmbientStrength = 0.0f;

// 菲涅尔：视线越"擦着"表面，反射越强（所以金属边缘总是更亮）
static Vec3 fresnelSchlick(float cosTheta, Vec3 f0) {
    const float m = clampf(1.0f - cosTheta, 0.0f, 1.0f);
    const float m2 = m * m;
    const float m5 = m2 * m2 * m;
    return f0 + (Vec3{1.0f, 1.0f, 1.0f} - f0) * m5;
}

// GGX 法线分布：粗糙度决定高光是"一个小亮点"还是"一大片散射"
static float distributionGGX(float nDotH, float roughness) {
    const float a = roughness * roughness;
    const float a2 = a * a;
    const float d = nDotH * nDotH * (a2 - 1.0f) + 1.0f;
    return a2 / (kPi * d * d + 1e-6f);
}

// 几何遮蔽：微表面之间会互相挡住光（粗糙表面尤其明显）
static float geometrySmith(float nDotV, float nDotL, float roughness) {
    const float r = roughness + 1.0f;
    const float k = (r * r) / 8.0f;
    const float gv = nDotV / (nDotV * (1.0f - k) + k);
    const float gl = nDotL / (nDotL * (1.0f - k) + k);
    return gv * gl;
}

// 环境光采样：给一个方向，返回"从那个方向看过来的环境色"。
// 上用天光、下用地面反弹光，中间平滑过渡 —— 真实引擎里这一步是查一张 HDR 环境贴图，
// 这里用一个方向渐变冒充，效果够用、代价几乎为零。
static Vec3 environmentColor(const ShadeEnv& env, Vec3 dir) {
    const float t = clampf(dir.y * 0.5f + 0.5f, 0.0f, 1.0f);
    return lerp(env.groundColor, env.skyColor, t);
}

// ---------------------------------------------------------------------------
//  每个像素调用一次。改这个函数 = 改整个世界的观感。
// ---------------------------------------------------------------------------
Vec3 shadeSurface(const Surface& surface, const ShadeEnv& env) {
    const Material& mat = *surface.material;

    Vec3 N = normalize(surface.normal);
    const Vec3 V = normalize(env.cameraPos - surface.position);
    if (dot(N, V) < 0.0f) N = N * -1.0f;  // 双面材质：从背面看时把法线翻过来

    const Vec3 albedo = mat.albedoAt(surface.uv) * surface.color;
    const float roughness = clampf(mat.roughness, 0.04f, 1.0f);
    // 绝缘体的基础反射率是 0.04，金属则直接用自己的颜色反射
    const Vec3 f0 = lerp(Vec3{0.04f, 0.04f, 0.04f}, albedo, mat.metallic);

    // 自发光：灯罩、屏幕、招牌 —— 不受光照影响，自己就是光源
    Vec3 result = mat.emissive;

    // ---- 环境光照（IBL 的极简替代）----
    const float ambientAmount = luminance(env.ambient) * kAmbientStrength;
    const float nDotV = maxf(dot(N, V), 1e-4f);
    const Vec3 reflectDir = reflect(-V, N);
    const Vec3 fEnv = fresnelSchlick(nDotV, f0);
    const Vec3 kdEnv = (Vec3{1.0f, 1.0f, 1.0f} - fEnv) * (1.0f - mat.metallic);
    // 漫反射看到的是朝上的那半天光；镜面反射看到的是 reflectDir 方向的环境
    const Vec3 diffuseEnv = environmentColor(env, N);
    const Vec3 specEnv = environmentColor(env, reflectDir);
    // 粗糙度越高，反射越糊：把反射色往"平均环境色"上拉
    const Vec3 specBlur = lerp(specEnv, (env.skyColor + env.groundColor) * 0.5f, roughness);
    result += (kdEnv * albedo * diffuseEnv + specBlur * fEnv) * ambientAmount;

    // ---- 逐灯累加 ----
    // 抽成一个小函数，是因为下面那盏"随身补光"要走一模一样的算式。
    auto addLight = [&](const Light& light) {
        const Vec3 toLight = light.position - surface.position;
        const float dist2 = dot(toLight, toLight);
        const float dist = std::sqrt(dist2);
        if (dist < 1e-5f) return;

        const Vec3 L = toLight / dist;
        const float nDotL = dot(N, L);
        if (nDotL <= 0.0f) return;  // 背对着灯，不用算

        const Vec3 H = normalize(L + V);
        const float nDotH = maxf(dot(N, H), 0.0f);
        const float vDotH = maxf(dot(V, H), 0.0f);

        // 高光三件套：D（法线分布）× G（几何遮蔽）× F（菲涅尔）
        const float D = distributionGGX(nDotH, roughness);
        const float G = geometrySmith(nDotV, nDotL, roughness);
        const Vec3 F = fresnelSchlick(vDotH, f0);
        const Vec3 specular = F * (D * G / (4.0f * nDotV * nDotL + 1e-4f));

        // 能量守恒：被镜面反射掉的那部分能量，不能再拿去漫反射
        const Vec3 kd = (Vec3{1.0f, 1.0f, 1.0f} - F) * (1.0f - mat.metallic);
        const Vec3 diffuse = kd * albedo * (1.0f / kPi);

        // 平方反比衰减；light.radius 是灯的物理尺寸，让近距离不至于数值爆炸
        const float attenuation = light.intensity / (dist2 + light.radius);

        result += (diffuse + specular) * light.color * (nDotL * attenuation);
    };

    for (int i = 0; i < env.lightCount; ++i) {
        // 关着的灯（intensity 0）直接跳过：衰减里乘的就是它，算出来恒等于 0，
        // 画面一模一样，但白算一整套 PBR。出厂状态下屋子里的灯全是关的，
        // 这一条省掉的是"每个像素对着几盏关着的灯做完整光照"。
        if (env.lights[i].intensity <= 0.0f) continue;
        addLight(env.lights[i]);
    }

    // 玩家随身的那点微光。评委拍照时这个指针是空的（见 core/material.h 的说明）——
    // 它只负责让"全黑的屋子"里看得见脚下的路，不参与任何一关的判分。
    if (env.viewLight != nullptr && env.viewLight->intensity > 0.0f) {
        addLight(*env.viewLight);
    }

    return result;  // 线性空间 HDR —— 后面统一做曝光 + 色调映射
}

}  // namespace dlab
