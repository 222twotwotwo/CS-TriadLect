// 4x4 矩阵与相机变换。列主序存储：m[列 * 4 + 行]，与 OpenGL 习惯一致。
// 变换链：模型矩阵 → 视图矩阵 → 投影矩阵，顶点最终落到 NDC（xyz ∈ [-1,1]，w>0 表示在相机前方）。
#pragma once

#include <cmath>

#include "vec.h"

namespace dlab {

struct Mat4 {
    float m[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};

    float& at(int row, int col) { return m[col * 4 + row]; }
    float at(int row, int col) const { return m[col * 4 + row]; }

    Vec3 column3(int col) const { return {m[col * 4], m[col * 4 + 1], m[col * 4 + 2]}; }
};

inline Mat4 operator*(const Mat4& a, const Mat4& b) {
    Mat4 r;
    for (int c = 0; c < 4; ++c) {
        for (int row = 0; row < 4; ++row) {
            float sum = 0.0f;
            for (int k = 0; k < 4; ++k) sum += a.at(row, k) * b.at(k, c);
            r.at(row, c) = sum;
        }
    }
    return r;
}

inline Vec4 operator*(const Mat4& a, Vec4 v) {
    return {a.at(0, 0) * v.x + a.at(0, 1) * v.y + a.at(0, 2) * v.z + a.at(0, 3) * v.w,
            a.at(1, 0) * v.x + a.at(1, 1) * v.y + a.at(1, 2) * v.z + a.at(1, 3) * v.w,
            a.at(2, 0) * v.x + a.at(2, 1) * v.y + a.at(2, 2) * v.z + a.at(2, 3) * v.w,
            a.at(3, 0) * v.x + a.at(3, 1) * v.y + a.at(3, 2) * v.z + a.at(3, 3) * v.w};
}

// 变换"点"（受平移影响）与"方向"（不受平移影响）
inline Vec3 transformPoint(const Mat4& a, Vec3 p) {
    const Vec4 r = a * Vec4(p, 1.0f);
    return {r.x, r.y, r.z};
}
inline Vec3 transformDir(const Mat4& a, Vec3 d) {
    const Vec4 r = a * Vec4(d, 0.0f);
    return {r.x, r.y, r.z};
}

inline Mat4 translation(Vec3 t) {
    Mat4 r;
    r.at(0, 3) = t.x;
    r.at(1, 3) = t.y;
    r.at(2, 3) = t.z;
    return r;
}

inline Mat4 scaling(Vec3 s) {
    Mat4 r;
    r.at(0, 0) = s.x;
    r.at(1, 1) = s.y;
    r.at(2, 2) = s.z;
    return r;
}

inline Mat4 rotationX(float rad) {
    const float c = std::cos(rad), s = std::sin(rad);
    Mat4 r;
    r.at(1, 1) = c;  r.at(1, 2) = -s;
    r.at(2, 1) = s;  r.at(2, 2) = c;
    return r;
}

inline Mat4 rotationY(float rad) {
    const float c = std::cos(rad), s = std::sin(rad);
    Mat4 r;
    r.at(0, 0) = c;  r.at(0, 2) = s;
    r.at(2, 0) = -s; r.at(2, 2) = c;
    return r;
}

inline Mat4 rotationZ(float rad) {
    const float c = std::cos(rad), s = std::sin(rad);
    Mat4 r;
    r.at(0, 0) = c;  r.at(0, 1) = -s;
    r.at(1, 0) = s;  r.at(1, 1) = c;
    return r;
}

inline Mat4 transpose(const Mat4& a) {
    Mat4 r;
    for (int i = 0; i < 4; ++i)
        for (int j = 0; j < 4; ++j) r.at(i, j) = a.at(j, i);
    return r;
}

// 透视投影：fovy 为竖直视场角（弧度），右手系，NDC 的 z 落在 [-1, 1]
inline Mat4 perspective(float fovy, float aspect, float zNear, float zFar) {
    const float f = 1.0f / std::tan(fovy * 0.5f);
    Mat4 r;
    for (float& v : r.m) v = 0.0f;
    r.at(0, 0) = f / aspect;
    r.at(1, 1) = f;
    r.at(2, 2) = (zFar + zNear) / (zNear - zFar);
    r.at(2, 3) = (2.0f * zFar * zNear) / (zNear - zFar);
    r.at(3, 2) = -1.0f;
    return r;
}

// 观察矩阵：把世界搬到"相机在原点、看向 -z"的空间
inline Mat4 lookAt(Vec3 eye, Vec3 target, Vec3 up) {
    const Vec3 f = normalize(target - eye);
    const Vec3 s = normalize(cross(f, up));
    const Vec3 u = cross(s, f);
    Mat4 r;
    r.at(0, 0) = s.x; r.at(0, 1) = s.y; r.at(0, 2) = s.z; r.at(0, 3) = -dot(s, eye);
    r.at(1, 0) = u.x; r.at(1, 1) = u.y; r.at(1, 2) = u.z; r.at(1, 3) = -dot(u, eye);
    r.at(2, 0) = -f.x; r.at(2, 1) = -f.y; r.at(2, 2) = -f.z; r.at(2, 3) = dot(f, eye);
    r.at(3, 0) = 0.0f; r.at(3, 1) = 0.0f; r.at(3, 2) = 0.0f; r.at(3, 3) = 1.0f;
    return r;
}

// 通用 4x4 求逆（路径追踪求相机射线、法线矩阵都要用）
inline Mat4 inverse(const Mat4& a) {
    const float* m = a.m;
    float inv[16];

    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] +
             m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] -
             m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] +
             m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] -
              m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] -
             m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] +
             m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] -
             m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] +
              m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] +
             m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] -
             m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] +
              m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] -
              m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] -
             m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] +
             m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] -
              m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] +
              m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];

    const float det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
    Mat4 r;
    if (std::fabs(det) < 1e-12f) return r;  // 奇异矩阵退化为单位阵
    const float invDet = 1.0f / det;
    for (int i = 0; i < 16; ++i) r.m[i] = inv[i] * invDet;
    return r;
}

// 法线矩阵：模型矩阵左上 3x3 的逆转置（非等比缩放下法线不能直接用模型矩阵变换）
inline Mat4 normalMatrix(const Mat4& model) {
    Mat4 upper;
    for (int c = 0; c < 3; ++c)
        for (int r = 0; r < 3; ++r) upper.at(r, c) = model.at(r, c);
    return transpose(inverse(upper));
}

}  // namespace dlab
