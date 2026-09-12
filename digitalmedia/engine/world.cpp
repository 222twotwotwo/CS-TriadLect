#include "world.h"

#include <cmath>

namespace dlab {

int World::addMesh(const std::string& name, Mesh mesh) {
    meshes.push_back(std::move(mesh));
    meshNames.push_back(name);
    return int(meshes.size()) - 1;
}

int World::addMaterial(const std::string& name, const Material& mat) {
    materials.push_back(mat);
    materialNames.push_back(name);
    return int(materials.size()) - 1;
}

int World::addEntity(Entity e) {
    entities.push_back(std::move(e));
    return int(entities.size()) - 1;
}

Texture& World::addTexture(Texture tex) {
    textures.push_back(std::move(tex));
    return textures.back();
}

int World::findMesh(const std::string& name) const {
    for (size_t i = 0; i < meshNames.size(); ++i)
        if (meshNames[i] == name) return int(i);
    return -1;
}

int World::findMaterial(const std::string& name) const {
    for (size_t i = 0; i < materialNames.size(); ++i)
        if (materialNames[i] == name) return int(i);
    return -1;
}

int World::findEntity(const std::string& name) const {
    for (size_t i = 0; i < entities.size(); ++i)
        if (entities[i].name == name) return int(i);
    return -1;
}

Entity* World::entity(const std::string& name) {
    const int i = findEntity(name);
    return i >= 0 ? &entities[size_t(i)] : nullptr;
}

const Entity* World::entity(const std::string& name) const {
    const int i = findEntity(name);
    return i >= 0 ? &entities[size_t(i)] : nullptr;
}

void World::refreshBounds() {
    for (Entity& e : entities) {
        if (e.mesh < 0 || e.mesh >= int(meshes.size())) continue;
        const Bounds b = meshBounds(meshes[size_t(e.mesh)]);
        // 逐个变换包围盒的 8 个角点再取 min/max —— 对任意角度都是精确的 AABB，
        // 所以关卡想斜着摆东西也不用改这里。
        const Mat4 m = entityTransform(e);
        Vec3 lo = transformPoint(m, b.min);
        Vec3 hi = lo;
        for (int c = 0; c < 8; ++c) {
            const Vec3 corner{(c & 1) ? b.max.x : b.min.x, (c & 2) ? b.max.y : b.min.y,
                              (c & 4) ? b.max.z : b.min.z};
            const Vec3 p = transformPoint(m, corner);
            lo = minv(lo, p);
            hi = maxv(hi, p);
        }
        e.aabbMin = lo;
        e.aabbMax = hi;
    }
}

void World::fillEnv(ShadeEnv& env, const Vec3& cameraPos) const {
    env.ambient = ambient;
    env.skyColor = skyColor;
    env.groundColor = groundColor;
    // ShadeEnv 里的 lights 是只读指针：直接指向 World 自己的数组，一座灯都不拷
    env.lights = lights;
    env.lightCount = lightCount;
    env.cameraPos = cameraPos;
}

void World::render(Rasterizer& rz, const Camera& cam, const Light* viewLight) const {
    const int w = rz.framebuffer().width;
    const int h = rz.framebuffer().height;
    const Mat4 view = cam.view();
    const Mat4 proj = perspective(cam.fovY, float(w) / float(h), 0.05f, 80.0f);

    ShadeEnv env;
    fillEnv(env, cam.position);
    env.viewLight = viewLight;
    rz.begin(view, proj, cam.position, env);

    for (const Entity& e : entities) {
        if (!e.visible) continue;
        if (e.mesh < 0 || e.mesh >= int(meshes.size())) continue;
        if (e.material < 0 || e.material >= int(materials.size())) continue;
        rz.draw(meshes[size_t(e.mesh)], entityTransform(e), materials[size_t(e.material)]);
    }
    rz.flush();
}

bool World::overlapsSolid(const Vec3& point, float radius) const {
    for (const Entity& e : entities) {
        if (!e.solid) continue;
        if (point.x + radius < e.aabbMin.x || point.x - radius > e.aabbMax.x) continue;
        if (point.y + radius < e.aabbMin.y || point.y - radius > e.aabbMax.y) continue;
        if (point.z + radius < e.aabbMin.z || point.z - radius > e.aabbMax.z) continue;
        return true;
    }
    return false;
}

bool World::overlapsBox(const Vec3& lo, const Vec3& hi) const {
    for (const Entity& e : entities) {
        if (!e.solid) continue;
        if (hi.x < e.aabbMin.x || lo.x > e.aabbMax.x) continue;
        if (hi.y < e.aabbMin.y || lo.y > e.aabbMax.y) continue;
        if (hi.z < e.aabbMin.z || lo.z > e.aabbMax.z) continue;
        return true;
    }
    return false;
}

World::RayHit World::castRay(const Vec3& origin, const Vec3& dir, float maxDist, float step) const {
    RayHit hit;
    if (step <= 0.0f) step = 0.04f;
    const Vec3 d = normalize(dir);
    if (length(d) < 0.5f) return hit;  // 视线长度为零（不该发生），当作什么都没看

    // 交互判定给一点宽松量：展板只有 4cm 厚、屏幕 8mm，严格判"点在盒子里"
    // 会因为步长跳过它们 —— 学生看到的是"明明对着展板，按 E 却没反应"。
    const float kPad = 0.06f;

    for (float t = step; t <= maxDist; t += step) {
        const Vec3 p = origin + d * t;
        for (size_t i = 0; i < entities.size(); ++i) {
            const Entity& e = entities[i];
            if (e.visible && !e.prompt.empty() && containsPoint(e, p, kPad)) {
                hit.entity = int(i);
                hit.distance = t;
                hit.point = p;
                return hit;
            }
        }
        for (const Entity& e : entities) {
            if (!e.solid) continue;
            if (containsPoint(e, p, 0.0f)) {
                hit.blocked = true;  // 撞到墙/桌子：后面的东西都看不到了
                hit.distance = t;
                hit.point = p;
                return hit;
            }
        }
    }
    return hit;
}

}  // namespace dlab
