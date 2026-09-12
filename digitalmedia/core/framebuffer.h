// 颜色缓冲 + 深度缓冲。
// 颜色存"线性空间浮点"（HDR），最后统一做曝光 + 色调映射再转 8 位 —— 这是现代渲染管线的标准做法，
// 也是为什么"直接把亮度值当像素颜色"会在亮部糊成一片白。
#pragma once

#include <atomic>
#include <cstdint>
#include <thread>
#include <vector>

#include "vec.h"

namespace dlab {

struct Framebuffer {
    int width = 0;
    int height = 0;
    std::vector<Vec3> color;   // 线性 RGB
    std::vector<float> depth;  // NDC z（-1 近 ~ 1 远），越小越近

    void resize(int w, int h) {
        width = w > 0 ? w : 1;
        height = h > 0 ? h : 1;
        color.assign(size_t(width) * size_t(height), Vec3{0.0f, 0.0f, 0.0f});
        depth.assign(size_t(width) * size_t(height), 1.0f);
    }

    void clear(Vec3 c = Vec3{0.0f, 0.0f, 0.0f}) {
        for (Vec3& px : color) px = c;
        for (float& d : depth) d = 1.0f;
    }

    bool inBounds(int x, int y) const { return x >= 0 && y >= 0 && x < width && y < height; }

    // 直接写入（用于 UI 叠加层，不参与深度测试）
    void setPixel(int x, int y, Vec3 c) {
        if (inBounds(x, y)) color[size_t(y) * size_t(width) + size_t(x)] = c;
    }

    // alpha 混合写入（控制台面板、文字都要用）
    void blendPixel(int x, int y, Vec3 c, float alpha) {
        if (!inBounds(x, y)) return;
        Vec3& dst = color[size_t(y) * size_t(width) + size_t(x)];
        dst = lerp(dst, c, alpha);
    }

    // 截图里的"画面平均亮度"：关卡判定直接用它（例如"把这间屋子点亮"）
    float meanLuminance() const {
        if (color.empty()) return 0.0f;
        double sum = 0.0;
        for (const Vec3& c : color) sum += double(luminance(c));
        return float(sum / double(color.size()));
    }

    // ACES 近似曲线（Narkowicz）：把 HDR 亮度压进 [0,1] 且保留高光层次
    static float tonemapACES(float x) {
        const float a = 2.51f, b = 0.03f, c = 2.43f, d = 0.59f, e = 0.14f;
        const float num = x * (a * x + b);
        const float den = x * (c * x + d) + e;
        return clampf(num / den, 0.0f, 1.0f);
    }

    // 线性 → sRGB 传输曲线
    static float linearToSRGB(float v) {
        const float c = clampf(v, 0.0f, 1.0f);
        return c <= 0.0031308f ? c * 12.92f : 1.055f * std::pow(c, 1.0f / 2.4f) - 0.055f;
    }

    // 曝光 + 色调映射 + sRGB 编码，结果写进调用方给的缓冲 —— 缓冲复用，别每帧
    // 再分配一个 width*height*3 的 vector（1600x900 就是 4.3MB，每帧 malloc/free 一次）。
    //
    // 这一步原来还是单线程的：1600x900 每帧要跑 4.3 百万次 std::pow（sRGB 传输曲线），
    // 实测占了"推屏"开销的大头。它是纯逐像素计算、各像素互不相干，所以按行切块并行。
    // 并行只切工作量，不改每个像素的算术 —— 出图与单线程版本逐像素完全相同。
    // threads：0 = 按核数自动。传 --threads 的值进来，是为了让"低配模拟"诚实 ——
    // 否则 tone map 这一段照样吃满所有核，M5.2 拿 --threads 4 量出来的数字会比
    // 真正的 4 核学生机好看。
    void toRGB8Into(std::vector<uint8_t>& out, float exposure = 1.0f, bool filmic = true,
                    int threads = 0) const {
        const int w = width;
        const int h = height;
        out.resize(size_t(w) * size_t(h) * 3);
        if (w <= 0 || h <= 0) return;

        auto row = [&](int y) {
            const size_t base = size_t(y) * size_t(w);
            for (int x = 0; x < w; ++x) {
                const size_t i = base + size_t(x);
                Vec3 c = color[i] * exposure;
                if (filmic) c = Vec3{tonemapACES(c.x), tonemapACES(c.y), tonemapACES(c.z)};
                out[i * 3 + 0] = uint8_t(linearToSRGB(c.x) * 255.0f + 0.5f);
                out[i * 3 + 1] = uint8_t(linearToSRGB(c.y) * 255.0f + 0.5f);
                out[i * 3 + 2] = uint8_t(linearToSRGB(c.z) * 255.0f + 0.5f);
            }
        };

        int workerCount = threads;
        if (workerCount <= 0) {
            workerCount = int(std::thread::hardware_concurrency());
            if (workerCount < 1) workerCount = 1;
        }
        if (workerCount > h) workerCount = h;

        if (workerCount <= 1 || h < 8) {  // 太窄的图不值当起线程
            for (int y = 0; y < h; ++y) row(y);
            return;
        }

        std::atomic<int> nextRow{0};
        auto worker = [&]() {
            for (;;) {
                const int y = nextRow.fetch_add(1, std::memory_order_relaxed);
                if (y >= h) break;
                row(y);
            }
        };
        std::vector<std::thread> pool;
        pool.reserve(size_t(workerCount - 1));
        for (int i = 1; i < workerCount; ++i) pool.emplace_back(worker);
        worker();
        for (std::thread& t : pool) t.join();
    }

    // 一次性用（离屏出图、自检）：自己拿一个新缓冲。
    // 逐帧的热路径请用 toRGB8Into + 常驻缓冲，别走这个。
    std::vector<uint8_t> toRGB8(float exposure = 1.0f, bool filmic = true) const {
        std::vector<uint8_t> out;
        toRGB8Into(out, exposure, filmic);
        return out;
    }
};

}  // namespace dlab
