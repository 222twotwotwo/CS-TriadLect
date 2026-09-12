#include "mesh.h"

#include <cmath>

namespace dlab {
namespace {

Vertex makeVertex(Vec3 p, Vec3 n, Vec2 uv, Vec3 color = Vec3{1.0f, 1.0f, 1.0f}) {
    Vertex v;
    v.position = p;
    v.normal = n;
    v.uv = uv;
    v.color = color;
    return v;
}

void pushQuad(Mesh& mesh, uint32_t base) {
    // 逆时针绕序：(0,1,2) + (0,2,3)
    mesh.indices.push_back(base + 0);
    mesh.indices.push_back(base + 1);
    mesh.indices.push_back(base + 2);
    mesh.indices.push_back(base + 0);
    mesh.indices.push_back(base + 2);
    mesh.indices.push_back(base + 3);
}

}  // namespace

Mesh makePlane(float size, float uvTiles) {
    const float h = size * 0.5f;
    Mesh m;
    m.vertices.push_back(makeVertex({-h, 0.0f, -h}, {0, 1, 0}, {0.0f, 0.0f}));
    m.vertices.push_back(makeVertex({h, 0.0f, -h}, {0, 1, 0}, {uvTiles, 0.0f}));
    m.vertices.push_back(makeVertex({h, 0.0f, h}, {0, 1, 0}, {uvTiles, uvTiles}));
    m.vertices.push_back(makeVertex({-h, 0.0f, h}, {0, 1, 0}, {0.0f, uvTiles}));
    pushQuad(m, 0);
    return m;
}

Mesh makeBox(Vec3 halfExtents, float uvTiles) {
    const Vec3 axis[3] = {{1.0f, 0.0f, 0.0f}, {0.0f, 1.0f, 0.0f}, {0.0f, 0.0f, 1.0f}};
    const float he[3] = {halfExtents.x, halfExtents.y, halfExtents.z};

    Mesh m;
    for (int a = 0; a < 3; ++a) {
        const int iu = (a + 1) % 3;
        const int iv = (a + 2) % 3;
        for (int side = -1; side <= 1; side += 2) {
            const Vec3 n = axis[a] * float(side);
            const Vec3 center = n * he[a];
            const Vec3 du = axis[iu] * he[iu];
            const Vec3 dv = axis[iv] * he[iv];
            const uint32_t base = uint32_t(m.vertices.size());
            m.vertices.push_back(makeVertex(center - du - dv, n, {0.0f, 0.0f}));
            m.vertices.push_back(makeVertex(center + du - dv, n, {uvTiles, 0.0f}));
            m.vertices.push_back(makeVertex(center + du + dv, n, {uvTiles, uvTiles}));
            m.vertices.push_back(makeVertex(center - du + dv, n, {0.0f, uvTiles}));
            pushQuad(m, base);
        }
    }
    return m;
}

Mesh makeSphere(float radius, int segments, int rings) {
    if (segments < 3) segments = 3;
    if (rings < 2) rings = 2;

    Mesh m;
    m.vertices.reserve(size_t(rings + 1) * size_t(segments + 1));
    for (int r = 0; r <= rings; ++r) {
        const float phi = kPi * float(r) / float(rings);  // 0（北极）→ π（南极）
        const float sp = std::sin(phi);
        const float cp = std::cos(phi);
        for (int s = 0; s <= segments; ++s) {
            const float theta = 2.0f * kPi * float(s) / float(segments);
            const Vec3 n{sp * std::cos(theta), cp, sp * std::sin(theta)};
            m.vertices.push_back(makeVertex(n * radius, n,
                                            {float(s) / float(segments), float(r) / float(rings)}));
        }
    }

    const int stride = segments + 1;
    for (int r = 0; r < rings; ++r) {
        for (int s = 0; s < segments; ++s) {
            const uint32_t i0 = uint32_t(r * stride + s);
            const uint32_t i1 = i0 + 1;
            const uint32_t i2 = i0 + uint32_t(stride);
            const uint32_t i3 = i2 + 1;
            m.indices.push_back(i0); m.indices.push_back(i2); m.indices.push_back(i1);
            m.indices.push_back(i1); m.indices.push_back(i2); m.indices.push_back(i3);
        }
    }
    return m;
}

Mesh makeCylinder(float radius, float height, int segments) {
    if (segments < 3) segments = 3;
    const float hh = height * 0.5f;

    Mesh m;
    // 侧面：每个分段两个顶点（下、上），法线沿径向
    for (int s = 0; s <= segments; ++s) {
        const float theta = 2.0f * kPi * float(s) / float(segments);
        const float ct = std::cos(theta);
        const float st = std::sin(theta);
        const Vec3 n{ct, 0.0f, st};
        const float u = float(s) / float(segments);
        m.vertices.push_back(makeVertex({ct * radius, -hh, st * radius}, n, {u, 0.0f}));
        m.vertices.push_back(makeVertex({ct * radius, hh, st * radius}, n, {u, 1.0f}));
    }
    for (int s = 0; s < segments; ++s) {
        const uint32_t b0 = uint32_t(s * 2);
        const uint32_t t0 = b0 + 1;
        const uint32_t b1 = b0 + 2;
        const uint32_t t1 = b0 + 3;
        m.indices.push_back(b0); m.indices.push_back(t0); m.indices.push_back(t1);
        m.indices.push_back(b0); m.indices.push_back(t1); m.indices.push_back(b1);
    }

    // 上下盖：各自一圈独立顶点（法线不同，不能和侧面共用）
    for (int side = 0; side < 2; ++side) {
        const float y = side == 0 ? hh : -hh;
        const Vec3 n{0.0f, side == 0 ? 1.0f : -1.0f, 0.0f};
        const uint32_t center = uint32_t(m.vertices.size());
        m.vertices.push_back(makeVertex({0.0f, y, 0.0f}, n, {0.5f, 0.5f}));
        for (int s = 0; s <= segments; ++s) {
            const float theta = 2.0f * kPi * float(s) / float(segments);
            const float ct = std::cos(theta);
            const float st = std::sin(theta);
            m.vertices.push_back(makeVertex({ct * radius, y, st * radius}, n,
                                            {0.5f + 0.5f * ct, 0.5f + 0.5f * st}));
        }
        for (int s = 0; s < segments; ++s) {
            const uint32_t a = center + 1 + uint32_t(s);
            const uint32_t b = center + 1 + uint32_t(s + 1);
            m.indices.push_back(center);
            if (side == 0) {
                m.indices.push_back(a);
                m.indices.push_back(b);
            } else {
                m.indices.push_back(b);
                m.indices.push_back(a);
            }
        }
    }
    return m;
}

Bounds meshBounds(const Mesh& mesh) {
    Bounds b;
    if (mesh.vertices.empty()) return b;
    b.min = mesh.vertices[0].position;
    b.max = mesh.vertices[0].position;
    for (const Vertex& v : mesh.vertices) {
        b.min = minv(b.min, v.position);
        b.max = maxv(b.max, v.position);
    }
    return b;
}

}  // namespace dlab
