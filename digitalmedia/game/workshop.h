// 「数媒组工作室」—— 招新项目的舞台。
//
// 所有几何都是 core/mesh.h 里那几个函数拼出来的（这个仓库里没有一个模型文件）。
// 房间坐标：地面 y=0，层高 3.4，x/z ∈ [-6, 6]。
#pragma once

#include "../engine/world.h"

namespace dlab {

// 建完之后把关键下标交出来，关卡和交互都用它，避免到处 findXXX("中文名")
struct Workshop {
    int matFloor = 0;
    int matWall = 0;
    int matCeiling = 0;
    int matWood = 0;
    int matChrome = 0;
    int matPlastic = 0;
    int matClay = 0;
    int matStone = 0;
    int matLamp = 0;
    int matScreen = 0;
    int matBoard = 0;
    // 材质样板（第 2 关的标准答案）：ref_chrome / ref_plastic / ref_clay。
    // 它们只在代码里定义 —— content/materials.txt 里没有这三项，改不着。
    int matRefChrome = 0;
    int matRefPlastic = 0;
    int matRefClay = 0;

    int entTerminal = -1;   // 桌上的终端（第 0 关的"第一束光"从这里来）
    int entScreen = -1;
    int entLamp = -1;       // 吊灯（自发光灯罩，也是主光的位置）
    int entBoard = -1;      // 墙上的展板
    int entPedestals[3] = {-1, -1, -1};
    int entOrbs[3] = {-1, -1, -1};     // 展台上那三个球：镜面 / 塑料 / 陶土
    int entRefOrbs[3] = {-1, -1, -1};  // 每个球旁边的材质样板（小球）
};

// 搭出整间屋子。调用前 World 必须是空的。
Workshop buildWorkshop(World& w);

}  // namespace dlab
