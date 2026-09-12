// 程序化几何 —— 仓库里没有任何 .obj/.fbx，所有模型都是这几行数学"长"出来的。
// 这也是"建模"这件事最底层的样子：顶点 + 法线 + uv + 三角形索引。
#pragma once

#include "../core/raster.h"

namespace dlab {

// 水平地面（法线朝 +y），边长 size，uv 平铺 uvTiles 次
Mesh makePlane(float size, float uvTiles = 1.0f);

// 长方体，halfExtents 是三个方向的半边长（所以 (1,1,1) 得到的是 2x2x2 的立方体）
Mesh makeBox(Vec3 halfExtents, float uvTiles = 1.0f);

// 球：rings 是纬度圈数，segments 是经度分段。段数越高越圆、越费像素
Mesh makeSphere(float radius, int segments = 32, int rings = 16);

// 圆柱：侧面 + 上下盖，中心在原点，轴向为 y
Mesh makeCylinder(float radius, float height, int segments = 24);

// 轴对齐包围盒（局部空间）。碰撞、交互射线、展台对齐都靠它。
struct Bounds {
    Vec3 min{0.0f, 0.0f, 0.0f};
    Vec3 max{0.0f, 0.0f, 0.0f};
    Vec3 center() const { return (min + max) * 0.5f; }
    Vec3 size() const { return max - min; }
};

Bounds meshBounds(const Mesh& mesh);

}  // namespace dlab
