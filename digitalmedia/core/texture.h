// 纹理：一张线性空间的浮点图 + 采样器（寻址模式 / 滤波模式）。
// 采样器是"技术美术"最常打交道的概念之一：同一张贴图，repeat 还是 clamp、最近邻还是双线性，
// 观感完全不同 —— 这正是第 3 关要让学生亲手对比的东西。
#pragma once

#include <cmath>
#include <cstdint>
#include <vector>

#include "vec.h"

namespace dlab {

enum WrapMode { kWrapRepeat = 0, kWrapClamp = 1 };
enum FilterMode { kFilterNearest = 0, kFilterBilinear = 1 };

struct Texture {
    int width = 0;
    int height = 0;
    std::vector<Vec3> pixels;  // 线性颜色，行优先

    bool valid() const { return width > 0 && height > 0 && !pixels.empty(); }

    Vec3 texel(int x, int y) const {
        return pixels[size_t(y) * size_t(width) + size_t(x)];
    }

    // 按寻址模式把 uv 映射到 [0,1) 或 [0,1]
    Vec2 wrapUV(Vec2 uv, int wrap) const {
        if (wrap == kWrapClamp) {
            return {clampf(uv.x, 0.0f, 1.0f), clampf(uv.y, 0.0f, 1.0f)};
        }
        auto frac = [](float v) { return v - std::floor(v); };
        return {frac(uv.x), frac(uv.y)};
    }

    Vec3 sample(Vec2 uv, int wrap = kWrapRepeat, int filter = kFilterBilinear) const {
        if (!valid()) return {1.0f, 1.0f, 1.0f};
        uv = wrapUV(uv, wrap);

        if (filter == kFilterNearest) {
            const int x = int(clampf(uv.x * float(width), 0.0f, float(width - 1)));
            const int y = int(clampf(uv.y * float(height), 0.0f, float(height - 1)));
            return texel(x, y);
        }

        // 双线性：在像素中心对齐的坐标系里插值（-0.5 是关键，否则会整体偏移半个像素）
        const float fx = uv.x * float(width) - 0.5f;
        const float fy = uv.y * float(height) - 0.5f;
        const int x0 = int(std::floor(fx));
        const int y0 = int(std::floor(fy));
        const float tx = fx - float(x0);
        const float ty = fy - float(y0);
        auto fetch = [&](int x, int y) {
            if (wrap == kWrapRepeat) {
                x = ((x % width) + width) % width;
                y = ((y % height) + height) % height;
            } else {
                x = int(clampf(float(x), 0.0f, float(width - 1)));
                y = int(clampf(float(y), 0.0f, float(height - 1)));
            }
            return texel(x, y);
        };
        const Vec3 a = lerp(fetch(x0, y0), fetch(x0 + 1, y0), tx);
        const Vec3 b = lerp(fetch(x0, y0 + 1), fetch(x0 + 1, y0 + 1), tx);
        return lerp(a, b, ty);
    }
};

// ---------------------------------------------------------------- 程序化纹理

// 棋盘格：最经典的"一眼看出 UV 与采样问题"的图案
inline Texture makeChecker(int size, Vec3 c0, Vec3 c1, int tiles) {
    Texture t;
    t.width = size;
    t.height = size;
    t.pixels.resize(size_t(size) * size_t(size));
    for (int y = 0; y < size; ++y) {
        for (int x = 0; x < size; ++x) {
            const int cx = x * tiles / size;
            const int cy = y * tiles / size;
            t.pixels[size_t(y) * size_t(size) + size_t(x)] = ((cx + cy) & 1) ? c1 : c0;
        }
    }
    return t;
}

// 值噪声：给"程序化纹理"一节用，也是 Houdini 那类工具最基础的积木
inline Texture makeValueNoise(int size, uint32_t seed, Vec3 c0, Vec3 c1) {
    auto hash = [seed](int x, int y) {
        uint32_t h = uint32_t(x * 374761393 + y * 668265263) ^ (seed * 2246822519u);
        h = (h ^ (h >> 13)) * 1274126177u;
        return float((h ^ (h >> 16)) & 0xFFFFu) / 65535.0f;
    };
    auto smooth = [](float t) { return t * t * (3.0f - 2.0f * t); };

    Texture t;
    t.width = size;
    t.height = size;
    t.pixels.resize(size_t(size) * size_t(size));
    const int cells = 8;
    for (int y = 0; y < size; ++y) {
        for (int x = 0; x < size; ++x) {
            const float u = float(x) / float(size) * float(cells);
            const float v = float(y) / float(size) * float(cells);
            const int x0 = int(u), y0 = int(v);
            const float tx = smooth(u - float(x0));
            const float ty = smooth(v - float(y0));
            const float a = lerpf(hash(x0, y0), hash(x0 + 1, y0), tx);
            const float b = lerpf(hash(x0, y0 + 1), hash(x0 + 1, y0 + 1), tx);
            const float n = lerpf(a, b, ty);
            t.pixels[size_t(y) * size_t(size) + size_t(x)] = lerp(c0, c1, n);
        }
    }
    return t;
}

}  // namespace dlab
