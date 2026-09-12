// 三角形光栅化管线 —— 这个项目最"硬核"的文件，也是整个引擎的心脏。
// 它做的事和 GPU 里的固定管线一模一样，只是全部用 CPU 写出来：
//
//   顶点 → 模型/视图/投影变换 → 近平面裁剪 → 透视除法 → 视口变换
//        → tile 分桶 → 多线程扫描转换 → 深度测试 → 逐像素调用着色器
//
// 学生不需要改这个文件（但如果读懂了，就已经超过 90% 的图形学入门课）。
#pragma once

#include <cstdint>
#include <vector>

#include "framebuffer.h"
#include "mat.h"
#include "material.h"
#include "vec.h"

namespace dlab {

// 一个顶点携带的全部信息。属性越少越快 —— 这里每一项都在逐像素插值。
struct Vertex {
    Vec3 position;
    Vec3 normal{0.0f, 1.0f, 0.0f};
    Vec2 uv{0.0f, 0.0f};
    Vec3 color{1.0f, 1.0f, 1.0f};
};

// 三角形索引网格（索引省内存，也让顶点法线/uv 在面之间共享）
struct Mesh {
    std::vector<Vertex> vertices;
    std::vector<uint32_t> indices;

    int triangleCount() const { return int(indices.size() / 3); }
};

// 着色函数：声明在 core（渲染器要调用它），实现在 game/shaders/lighting.cpp。
// 光栅化器把每个像素变成一个 Surface 递进来，拿回一个线性空间的颜色。
// 这就是"学生可改着色器"与"引擎"之间唯一的接口。
Vec3 shadeSurface(const Surface& surface, const ShadeEnv& env);

namespace raster_detail {

// 屏幕空间顶点：坐标是像素，depth 是 NDC z 映射到 [0,1]（0 近 1 远）。
// 除 invW 外的属性都已经乘过 invW，插值后统一除回来 —— 这就是"透视校正插值"。
struct ScreenVertex {
    float x = 0.0f;
    float y = 0.0f;
    float depth = 1.0f;
    float invW = 1.0f;
    Vec3 world;
    Vec3 normal;
    Vec2 uv;
    Vec3 color;
};

// 已经准备好被扫描转换的三角形
struct ScreenTriangle {
    const Material* material = nullptr;
    ScreenVertex v[3];
    float minX = 0.0f, minY = 0.0f, maxX = -1.0f, maxY = -1.0f;  // 已裁剪到屏幕内的包围盒
};

}  // namespace raster_detail

class Rasterizer {
public:
    explicit Rasterizer(int threads = 0) : threads_(threads) {}

    void resize(int w, int h);

    Framebuffer& framebuffer() { return fb_; }
    const Framebuffer& framebuffer() const { return fb_; }

    // 每帧开头：设定相机与光照环境，清空上一帧的三角形
    void begin(const Mat4& view, const Mat4& projection, Vec3 cameraPos, const ShadeEnv& env);

    // 提交一个网格。model 是模型矩阵（含平移/旋转/缩放）
    void draw(const Mesh& mesh, const Mat4& model, const Material& material);

    // 本帧结束：分桶 + 多线程光栅化，把结果写进 framebuffer()
    void flush();

    int threads() const { return threads_; }
    long long drawnTriangles() const { return statDrawn_; }
    long long culledTriangles() const { return statCulled_; }
    long long shadedPixels() const { return statPixels_; }
    double lastRasterMs() const { return lastRasterMs_; }

private:
    void submitTriangle(const Vertex& a, const Vertex& b, const Vertex& c, const Mat4& model,
                        const Mat4& nrmMat, const Material* material);
    long long rasterizeTile(int tileIndex);

    Framebuffer fb_;
    int threads_ = 0;
    Mat4 viewProj_;
    Vec3 cameraPos_;
    ShadeEnv env_;

    int tileW_ = 0;
    int tileH_ = 0;
    std::vector<raster_detail::ScreenTriangle> tris_;
    std::vector<std::vector<int>> bins_;  // 每个 tile 覆盖到的三角形索引

    long long statDrawn_ = 0;
    long long statCulled_ = 0;
    long long statPixels_ = 0;
    double lastRasterMs_ = 0.0;
};

}  // namespace dlab
