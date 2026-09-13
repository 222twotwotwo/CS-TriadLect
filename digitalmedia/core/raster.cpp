// 光栅化器的实现。按执行顺序读下去，就是一条完整的 GPU 固定管线。
#include "raster.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <thread>

namespace dlab {
namespace {

constexpr int kTileSize = 32;      // tile 越大分桶开销越小、负载越粗
constexpr float kClipEps = 1e-5f;  // 近平面判定阈值（裁剪空间）
constexpr float kEdgeEps = 1e-2f;  // 边函数容差：宁可多画一点，也不要三角形之间出现裂缝

int mini(int a, int b) { return a < b ? a : b; }
int maxi(int a, int b) { return a > b ? a : b; }

// ---------------------------------------------------------------- 裁剪空间顶点

struct ClipVertex {
    Vec4 clip;    // 齐次坐标，w 是"到相机的距离"
    Vec3 world;   // 世界坐标（着色要用）
    Vec3 normal;
    Vec2 uv;
    Vec3 color;
};

ClipVertex lerpClip(const ClipVertex& a, const ClipVertex& b, float t) {
    ClipVertex r;
    r.clip = Vec4{a.clip.x + (b.clip.x - a.clip.x) * t, a.clip.y + (b.clip.y - a.clip.y) * t,
                  a.clip.z + (b.clip.z - a.clip.z) * t, a.clip.w + (b.clip.w - a.clip.w) * t};
    r.world = lerp(a.world, b.world, t);
    r.normal = lerp(a.normal, b.normal, t);
    r.uv = Vec2{a.uv.x + (b.uv.x - a.uv.x) * t, a.uv.y + (b.uv.y - a.uv.y) * t};
    r.color = lerp(a.color, b.color, t);
    return r;
}

// Sutherland–Hodgman：只对近平面裁剪。远平面交给深度测试白送。
// 返回 0/3/4 个顶点（4 个要拆成两个三角形）。
int clipAgainstNear(const ClipVertex in[3], ClipVertex out[4]) {
    int count = 0;
    for (int i = 0; i < 3; ++i) {
        const ClipVertex& cur = in[i];
        const ClipVertex& nxt = in[(i + 1) % 3];
        const float dCur = cur.clip.z + cur.clip.w;  // > 0 表示在近平面前方
        const float dNxt = nxt.clip.z + nxt.clip.w;
        const bool inCur = dCur > kClipEps;
        const bool inNxt = dNxt > kClipEps;
        if (inCur) out[count++] = cur;
        if (inCur != inNxt) out[count++] = lerpClip(cur, nxt, dCur / (dCur - dNxt));
    }
    return count;
}

// 边函数：>0 表示 p 在 a→b 的左侧。三个边函数同为正 ⇔ 在三角形内。
inline float edgeFunction(float ax, float ay, float bx, float by, float px, float py) {
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax);
}

}  // namespace

void Rasterizer::resize(int w, int h) {
    fb_.resize(w, h);
    tileW_ = (fb_.width + kTileSize - 1) / kTileSize;
    tileH_ = (fb_.height + kTileSize - 1) / kTileSize;
    bins_.assign(size_t(tileW_) * size_t(tileH_), std::vector<int>());
}

void Rasterizer::begin(const Mat4& view, const Mat4& projection, Vec3 cameraPos, const ShadeEnv& env) {
    viewProj_ = projection * view;
    cameraPos_ = cameraPos;
    env_ = env;
    env_.cameraPos = cameraPos;
    tris_.clear();
    statDrawn_ = 0;
    statCulled_ = 0;
    statPixels_ = 0;
}

void Rasterizer::draw(const Mesh& mesh, const Mat4& model, const Material& material) {
    const Mat4 nrm = normalMatrix(model);
    for (size_t i = 0; i + 2 < mesh.indices.size(); i += 3) {
        const Vertex& a = mesh.vertices[mesh.indices[i + 0]];
        const Vertex& b = mesh.vertices[mesh.indices[i + 1]];
        const Vertex& c = mesh.vertices[mesh.indices[i + 2]];
        submitTriangle(a, b, c, model, nrm, &material);
    }
}

void Rasterizer::submitTriangle(const Vertex& a, const Vertex& b, const Vertex& c, const Mat4& model,
                                const Mat4& nrmMat, const Material* material) {
    // 1) 变换：mvp 用来定位置，model 用来拿世界坐标（着色必须用世界坐标），nrmMat 用来摆正法线
    const Mat4 mvp = viewProj_ * model;
    const Vertex* src[3] = {&a, &b, &c};
    ClipVertex in[3];
    for (int i = 0; i < 3; ++i) {
        in[i].clip = mvp * Vec4(src[i]->position, 1.0f);
        in[i].world = transformPoint(model, src[i]->position);
        in[i].normal = normalize(transformDir(nrmMat, src[i]->normal));
        in[i].uv = src[i]->uv;
        in[i].color = src[i]->color;
    }

    // 2) 近平面裁剪
    ClipVertex poly[4];
    const int n = clipAgainstNear(in, poly);
    if (n < 3) {
        ++statCulled_;
        return;
    }

    // 3) 透视除法 + 视口变换
    raster_detail::ScreenVertex sv[4];
    bool anyVisible = false;
    for (int i = 0; i < n; ++i) {
        const float invW = 1.0f / poly[i].clip.w;
        const float ndcX = poly[i].clip.x * invW;
        const float ndcY = poly[i].clip.y * invW;
        const float ndcZ = poly[i].clip.z * invW;
        raster_detail::ScreenVertex& s = sv[i];
        s.x = (ndcX * 0.5f + 0.5f) * float(fb_.width);
        s.y = (0.5f - ndcY * 0.5f) * float(fb_.height);  // 屏幕 y 向下
        s.depth = ndcZ * 0.5f + 0.5f;
        s.invW = invW;
        s.world = poly[i].world * invW;
        s.normal = poly[i].normal * invW;
        s.uv = Vec2{poly[i].uv.x * invW, poly[i].uv.y * invW};
        s.color = poly[i].color * invW;
        if (s.depth >= 0.0f && s.depth <= 1.0f) anyVisible = true;
    }
    if (!anyVisible) {
        ++statCulled_;
        return;
    }

    // 4) 扇形拆分成 1~2 个三角形，逐个登记
    const int fanCount = n - 2;
    for (int f = 0; f < fanCount; ++f) {
        const raster_detail::ScreenVertex& v0 = sv[0];
        const raster_detail::ScreenVertex& v1 = sv[f + 1];
        const raster_detail::ScreenVertex& v2 = sv[f + 2];

        // 屏幕 y 翻转会反绕组：统一整理成逆时针，扫描转换就不用考虑正反面
        const float area = edgeFunction(v0.x, v0.y, v1.x, v1.y, v2.x, v2.y);
        if (std::fabs(area) < 1e-4f) {
            ++statCulled_;
            continue;
        }

        raster_detail::ScreenTriangle tri;
        tri.material = material;
        tri.v[0] = v0;
        tri.v[1] = area > 0.0f ? v1 : v2;
        tri.v[2] = area > 0.0f ? v2 : v1;

        float minX = tri.v[0].x, maxX = tri.v[0].x;
        float minY = tri.v[0].y, maxY = tri.v[0].y;
        for (int i = 1; i < 3; ++i) {
            minX = minf(minX, tri.v[i].x);
            maxX = maxf(maxX, tri.v[i].x);
            minY = minf(minY, tri.v[i].y);
            maxY = maxf(maxY, tri.v[i].y);
        }
        // 完全在屏幕外（含近/远裁剪面）直接丢
        if (maxX < 0.0f || minX > float(fb_.width) || maxY < 0.0f || minY > float(fb_.height)) {
            ++statCulled_;
            continue;
        }
        tri.minX = maxf(minX, 0.0f);
        tri.minY = maxf(minY, 0.0f);
        tri.maxX = minf(maxX, float(fb_.width - 1));
        tri.maxY = minf(maxY, float(fb_.height - 1));

        // 5) 分桶：算一遍覆盖到哪些 tile，之后同一 tile 的像素只碰自己那批三角形
        const int t0x = maxi(0, int(tri.minX) / kTileSize);
        const int t1x = mini(tileW_ - 1, int(tri.maxX) / kTileSize);
        const int t0y = maxi(0, int(tri.minY) / kTileSize);
        const int t1y = mini(tileH_ - 1, int(tri.maxY) / kTileSize);

        const int index = int(tris_.size());
        tris_.push_back(tri);
        ++statDrawn_;
        for (int ty = t0y; ty <= t1y; ++ty)
            for (int tx = t0x; tx <= t1x; ++tx) bins_[size_t(ty) * size_t(tileW_) + size_t(tx)].push_back(index);
    }
}

long long Rasterizer::rasterizeTile(int tileIndex) {
    long long shaded = 0;
    const int tx = tileIndex % tileW_;
    const int ty = tileIndex / tileW_;
    const int clipX0 = tx * kTileSize;
    const int clipY0 = ty * kTileSize;
    const int clipX1 = mini(clipX0 + kTileSize - 1, fb_.width - 1);
    const int clipY1 = mini(clipY0 + kTileSize - 1, fb_.height - 1);

    const std::vector<int>& list = bins_[size_t(tileIndex)];
    for (size_t k = 0; k < list.size(); ++k) {
        const raster_detail::ScreenTriangle& tri = tris_[size_t(list[k])];
        const raster_detail::ScreenVertex& a = tri.v[0];
        const raster_detail::ScreenVertex& b = tri.v[1];
        const raster_detail::ScreenVertex& c = tri.v[2];

        const int px0 = maxi(int(std::floor(tri.minX)), clipX0);
        const int px1 = mini(int(std::ceil(tri.maxX)), clipX1);
        const int py0 = maxi(int(std::floor(tri.minY)), clipY0);
        const int py1 = mini(int(std::ceil(tri.maxY)), clipY1);
        if (px0 > px1 || py0 > py1) continue;

        // 边函数 ax*px + ay*py + ac，沿 x 每步加 ax、沿 y 每步加 ay
        const float e0x = a.y - b.y, e0y = b.x - a.x;
        const float e0c = -(b.x - a.x) * a.y + (b.y - a.y) * a.x;
        const float e1x = b.y - c.y, e1y = c.x - b.x;
        const float e1c = -(c.x - b.x) * b.y + (c.y - b.y) * b.x;
        const float e2x = c.y - a.y, e2y = a.x - c.x;
        const float e2c = -(a.x - c.x) * c.y + (a.y - c.y) * c.x;

        const float area = e0x * c.x + e0y * c.y + e0c;
        const float invArea = 1.0f / area;

        for (int py = py0; py <= py1; ++py) {
            const float sy = float(py) + 0.5f;
            const float sx = float(px0) + 0.5f;
            float w0 = e0x * sx + e0y * sy + e0c;
            float w1 = e1x * sx + e1y * sy + e1c;
            float w2 = e2x * sx + e2y * sy + e2c;
            size_t rowOffset = size_t(py) * size_t(fb_.width);

            for (int px = px0; px <= px1; ++px, w0 += e0x, w1 += e1x, w2 += e2x) {
                // 容差比较：宁可重叠也不留缝（不透明物体重叠由深度测试兜底）
                if (w0 < -kEdgeEps || w1 < -kEdgeEps || w2 < -kEdgeEps) continue;

                const float l0 = w1 * invArea;  // 顶点 0 的权重
                const float l1 = w2 * invArea;  // 顶点 1
                const float l2 = w0 * invArea;  // 顶点 2

                const float depth = l0 * a.depth + l1 * b.depth + l2 * c.depth;
                const size_t index = rowOffset + size_t(px);
                if (depth >= fb_.depth[index]) continue;

                // 透视校正：先除 w 插值，再用插值结果除回来
                const float invW = l0 * a.invW + l1 * b.invW + l2 * c.invW;
                const float w = invW > 1e-9f ? 1.0f / invW : 0.0f;

                Surface s;
                s.position = (a.world * l0 + b.world * l1 + c.world * l2) * w;
                s.normal = normalize((a.normal * l0 + b.normal * l1 + c.normal * l2) * w);
                s.uv = Vec2{(a.uv.x * l0 + b.uv.x * l1 + c.uv.x * l2) * w,
                            (a.uv.y * l0 + b.uv.y * l1 + c.uv.y * l2) * w};
                s.color = (a.color * l0 + b.color * l1 + c.color * l2) * w;
                s.material = tri.material;

                fb_.depth[index] = depth;
                fb_.color[index] = shadeSurface(s, env_);
                ++shaded;
            }
        }
    }
    return shaded;
}

void Rasterizer::flush() {
    const auto t0 = std::chrono::steady_clock::now();

    // 只有非空 tile 才参与调度
    std::vector<int> active;
    active.reserve(bins_.size());
    for (size_t i = 0; i < bins_.size(); ++i)
        if (!bins_[i].empty()) active.push_back(int(i));

    if (!active.empty()) {
        int workerCount = threads_;
        if (workerCount <= 0) {
            const unsigned hw = std::thread::hardware_concurrency();
            workerCount = hw > 0 ? int(hw) : 4;
        }
        if (workerCount > int(active.size())) workerCount = int(active.size());

        std::atomic<size_t> next{0};
        std::atomic<long long> pixelCounter{0};
        auto worker = [&]() {
            for (;;) {
                const size_t i = next.fetch_add(1, std::memory_order_relaxed);
                if (i >= active.size()) break;
                pixelCounter.fetch_add(rasterizeTile(active[i]), std::memory_order_relaxed);
            }
        };

        if (workerCount <= 1) {
            worker();
        } else {
            std::vector<std::thread> pool;
            pool.reserve(size_t(workerCount - 1));
            for (int i = 1; i < workerCount; ++i) pool.emplace_back(worker);
            worker();
            for (std::thread& t : pool) t.join();
        }
        statPixels_ = pixelCounter.load();
    }

    const auto t1 = std::chrono::steady_clock::now();
    lastRasterMs_ = std::chrono::duration<double, std::milli>(t1 - t0).count();

    tris_.clear();
    for (int i : active) bins_[size_t(i)].clear();
}

}  // namespace dlab
