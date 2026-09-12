#include "workshop.h"

#include "../core/texture.h"

namespace dlab {

namespace {

Entity makeEntity(const char* name, int mesh, int material, Vec3 position) {
    Entity e;
    e.name = name;
    e.mesh = mesh;
    e.material = material;
    e.position = position;
    return e;
}

}  // namespace

Workshop buildWorkshop(World& w) {
    Workshop ws;

    // ---------------------------------------------------------------- 网格库
    // 每种形状只造一份，靠实体的位置/缩放摆出整间屋子
    const int mPlane = w.addMesh("plane", makePlane(12.0f));
    const int mWall = w.addMesh("wall", makeBox(Vec3{6.0f, 1.7f, 0.15f}));
    const int mSideWall = w.addMesh("sideWall", makeBox(Vec3{0.15f, 1.7f, 6.0f}));
    const int mTop = w.addMesh("deskTop", makeBox(Vec3{1.6f, 0.045f, 0.5f}));
    const int mLeg = w.addMesh("deskLeg", makeBox(Vec3{0.05f, 0.39f, 0.45f}));
    const int mMonitor = w.addMesh("monitor", makeBox(Vec3{0.32f, 0.21f, 0.03f}));
    const int mScreen = w.addMesh("screen", makeBox(Vec3{0.27f, 0.16f, 0.008f}));
    const int mKeyboard = w.addMesh("keyboard", makeBox(Vec3{0.3f, 0.012f, 0.11f}));
    const int mPedestal = w.addMesh("pedestal", makeCylinder(0.55f, 0.95f, 28));
    const int mOrb = w.addMesh("orb", makeSphere(0.45f, 36, 18));
    const int mRefStand = w.addMesh("refStand", makeCylinder(0.13f, 1.2f, 20));  // 细柱：顶着材质样板
    const int mRefOrb = w.addMesh("refOrb", makeSphere(0.20f, 28, 14));
    const int mLampBody = w.addMesh("lampBody", makeBox(Vec3{0.42f, 0.06f, 0.42f}));
    const int mBoard = w.addMesh("board", makeBox(Vec3{0.04f, 0.7f, 1.1f}));
    const int mShelf = w.addMesh("shelf", makeBox(Vec3{0.3f, 0.9f, 1.2f}));
    const int mCrate = w.addMesh("crate", makeBox(Vec3{0.4f, 0.4f, 0.4f}));

    // ---------------------------------------------------------------- 材质表
    // 名字会被 content/materials.txt 按同名覆盖 —— 这就是"数据热重载"的接口
    const Texture& checker = w.addTexture(makeChecker(128, Vec3{0.055f, 0.062f, 0.075f},
                                                     Vec3{0.70f, 0.72f, 0.76f}, 4));

    Material floor;
    floor.albedo = Vec3{1.0f, 1.0f, 1.0f};
    floor.roughness = 0.30f;
    floor.albedoTexture = &checker;
    // 贴图的三个设置出厂是错的 —— 第 3 关「纹理」要从这里改起：
    //   uvscale 1 1 = 整块地板只铺一张贴图（格子被拉伸成 1.5 米一块）；
    //   clamp       = 铺出去的部分不重复，拉成边缘那一条颜色；
    //   nearest     = 最近邻采样，格线硬，远处一格一格地闪。
    // 和灯、材质两处一样：数据文件被写坏/删掉时退回代码默认，退回的也必须是
    // "被调乱的样子"，不能白送过关。第 3 关的题面就是这个文件加 content/textures.txt。
    floor.uvScale = Vec2{1.0f, 1.0f};
    floor.wrapMode = kWrapClamp;
    floor.filterMode = kFilterNearest;
    ws.matFloor = w.addMaterial("floor", floor);

    Material wall;
    wall.albedo = Vec3{0.30f, 0.32f, 0.36f};
    wall.roughness = 0.88f;
    ws.matWall = w.addMaterial("wall", wall);

    Material ceiling;
    ceiling.albedo = Vec3{0.16f, 0.17f, 0.20f};
    ceiling.roughness = 0.95f;
    ws.matCeiling = w.addMaterial("ceiling", ceiling);

    Material wood;
    wood.albedo = Vec3{0.52f, 0.33f, 0.16f};
    wood.roughness = 0.55f;
    ws.matWood = w.addMaterial("wood", wood);

    Material chrome;
    chrome.albedo = Vec3{0.95f, 0.93f, 0.90f};
    chrome.roughness = 0.55f;   // 出厂是错的 —— 第 2 关「材质」要从这里改起，见下面 refChrome
    chrome.metallic = 0.0f;     // 同上：出厂是错的
    ws.matChrome = w.addMaterial("chrome", chrome);

    Material plastic;
    plastic.albedo = Vec3{0.82f, 0.13f, 0.11f};
    plastic.roughness = 0.95f;  // 出厂是错的
    plastic.metallic = 0.0f;
    ws.matPlastic = w.addMaterial("plastic", plastic);

    Material clay;
    clay.albedo = Vec3{0.30f, 0.32f, 0.36f};  // 出厂是错的：陶土该是红棕色
    clay.roughness = 0.92f;
    clay.metallic = 0.0f;
    ws.matClay = w.addMaterial("clay", clay);

    // 材质样板（第 2 关的标准答案）。和上面三个球是同一套参数，只是这三个是对的。
    // 为什么放在代码里、而不是 content/materials.txt 里：样板是"标准答案"，
    // 学生要改的是展台上那三个球；把答案和题面放进同一个文件，改错一个字母
    // 就可能把答案也改掉，然后"照着样板改"这件事就不成立了。
    // locked 是这件事的最后一道门：光放在代码里还不够 —— content/ 能覆盖世界上
    // 任何一份已有材质，学生（或者一次手滑）往 materials.txt 里写一段 ref_chrome，
    // 样板就跟着变了。上锁之后那种写法会被挡回来，并告诉他"这是答案，改不动"。
    // 三个球的代码默认值也故意是错的，和数据文件里一致 —— 数据文件被删掉/写坏时
    // 退回代码默认，退回的必须还是"三个做错的球"，而不是白送过关（和第 1 关同理）。
    Material refChrome;
    refChrome.albedo = Vec3{0.95f, 0.93f, 0.90f};
    refChrome.roughness = 0.06f;
    refChrome.metallic = 1.0f;
    refChrome.locked = true;
    ws.matRefChrome = w.addMaterial("ref_chrome", refChrome);

    Material refPlastic;
    refPlastic.albedo = Vec3{0.82f, 0.13f, 0.11f};
    refPlastic.roughness = 0.26f;
    refPlastic.metallic = 0.0f;
    refPlastic.locked = true;
    ws.matRefPlastic = w.addMaterial("ref_plastic", refPlastic);

    Material refClay;
    refClay.albedo = Vec3{0.74f, 0.53f, 0.32f};
    refClay.roughness = 0.92f;
    refClay.metallic = 0.0f;
    refClay.locked = true;
    ws.matRefClay = w.addMaterial("ref_clay", refClay);

    Material stone;
    stone.albedo = Vec3{0.52f, 0.53f, 0.55f};
    stone.roughness = 0.62f;
    ws.matStone = w.addMaterial("stone", stone);

    // 灯罩：挂在桌子右上方的天花板上。它自己会发光（= 灯亮着的时候那个亮块），
    // 但它照不亮别人 —— 照亮房间的是 content/lights.txt 里的 light 0，
    // 两者的位置必须一致，不然会出现"灯罩挂在天上、光却打在别处"这种一眼假的画面。
    // emissive 的代码默认也是 0（和数据文件一致）：世界出厂就是黑的，
    // 什么亮、什么不亮全由 content/ 决定 —— 数据文件写坏了退回代码默认，
    // 退回的也是"一间黑屋子"，而不是白送一个亮着的灯罩。
    Material lampMat;
    lampMat.albedo = Vec3{0.08f, 0.08f, 0.09f};  // 深灰铁壳，暗房间里也看得出轮廓
    lampMat.emissive = Vec3{0.0f, 0.0f, 0.0f};
    lampMat.roughness = 0.5f;
    ws.matLamp = w.addMaterial("lamp", lampMat);

    // 屏幕：暗房间里唯一亮着的东西 —— 它会把人引到终端面前
    Material screen;
    screen.albedo = Vec3{0.0f, 0.0f, 0.0f};
    screen.emissive = Vec3{0.35f, 1.35f, 1.05f};
    screen.roughness = 0.4f;
    ws.matScreen = w.addMaterial("screen", screen);

    Material board;
    board.albedo = Vec3{0.68f, 0.66f, 0.60f};
    board.roughness = 0.72f;
    ws.matBoard = w.addMaterial("board", board);

    // ---------------------------------------------------------------- 实体
    // 地板 / 天花板
    w.addEntity(makeEntity("地板", mPlane, ws.matFloor, Vec3{0.0f, 0.0f, 0.0f}));
    w.addEntity(makeEntity("天花板", mPlane, ws.matCeiling, Vec3{0.0f, 3.4f, 0.0f}));

    // 四面墙（内表面正好贴在 ±6 上）
    w.addEntity(makeEntity("后墙", mWall, ws.matWall, Vec3{0.0f, 1.7f, -6.15f}));
    w.addEntity(makeEntity("前墙", mWall, ws.matWall, Vec3{0.0f, 1.7f, 6.15f}));
    w.addEntity(makeEntity("左墙", mSideWall, ws.matWall, Vec3{-6.15f, 1.7f, 0.0f}));
    w.addEntity(makeEntity("右墙", mSideWall, ws.matWall, Vec3{6.15f, 1.7f, 0.0f}));

    // 靠后墙的桌子
    w.addEntity(makeEntity("桌面", mTop, ws.matWood, Vec3{0.0f, 0.78f, -5.0f}));
    w.addEntity(makeEntity("桌腿左", mLeg, ws.matWood, Vec3{-1.5f, 0.39f, -5.0f}));
    w.addEntity(makeEntity("桌腿右", mLeg, ws.matWood, Vec3{1.5f, 0.39f, -5.0f}));
    w.addEntity(makeEntity("键盘", mKeyboard, ws.matStone, Vec3{0.0f, 0.837f, -4.65f}));

    // 终端：显示器 + 屏幕。玩家走到它面前按 E，就是第 0 关的开场
    Entity terminal = makeEntity("终端", mMonitor, ws.matStone, Vec3{0.0f, 1.035f, -5.28f});
    terminal.prompt = "按 E 使用终端（控制台在这里）";
    terminal.command = "use terminal";
    ws.entTerminal = w.addEntity(terminal);

    Entity screenEnt = makeEntity("屏幕", mScreen, ws.matScreen, Vec3{0.0f, 1.035f, -5.245f});
    screenEnt.solid = false;
    ws.entScreen = w.addEntity(screenEnt);

    // 吊灯：主光在这里。挂在离天花板 0.5m 的位置，灯罩本体刚好挡住天花板上
    // 那个"距离 0.5m 的平方反比热点"，不然会糊成一大片白。
    // 它能被"看"（look lamp）：铭牌上写着它的挂点和此刻主光实际在哪儿 ——
    // 第 1 关的学生就是靠这块铭牌把灯装回去的。
    Entity lamp = makeEntity("吊灯", mLampBody, ws.matLamp, Vec3{2.2f, 2.9f, 1.2f});
    lamp.solid = false;
    lamp.prompt = "按 E 看吊灯铭牌";
    lamp.command = "look lamp";
    lamp.forLevel = 1;  // 吊灯铭牌是第 1 关的事（那一关就是靠它把灯装回去的）
    ws.entLamp = w.addEntity(lamp);

    // 左墙展板
    Entity boardEnt = makeEntity("展板", mBoard, ws.matBoard, Vec3{-5.94f, 1.9f, 1.2f});
    boardEnt.solid = false;
    boardEnt.prompt = "按 E 看展板";
    boardEnt.command = "look board";
    ws.entBoard = w.addEntity(boardEnt);

    // 右墙书柜 + 左前方的木箱（斜着摆，顺便验证 AABB 对任意角度都对）
    w.addEntity(makeEntity("书柜", mShelf, ws.matWood, Vec3{5.7f, 0.9f, 0.5f}));
    Entity crate = makeEntity("木箱", mCrate, ws.matWood, Vec3{-3.6f, 0.4f, 2.4f});
    crate.rotationY = radians(24.0f);
    w.addEntity(crate);

    // 三个展台 + 三个球：镜面 / 塑料 / 陶土。第 2 关「材质」就发生在这里。
    // 摆成右侧一列 —— 出生点正前方要留给桌子和终端，中间不能有东西挡视线。
    // 每个球前面一米立着一根细柱、顶上顶着一个小球：那是"材质样板"，
    // 这个球该长什么样，看它。球和样板一样高（1.4），比较的时候不用低头抬头。
    const float pedZ[3] = {-3.0f, -1.2f, 0.6f};
    const int orbMat[3] = {ws.matChrome, ws.matPlastic, ws.matClay};
    const char* orbName[3] = {"镜面球", "塑料球", "陶土球"};
    const int refMat[3] = {ws.matRefChrome, ws.matRefPlastic, ws.matRefClay};
    const char* refName[3] = {"样板镜面", "样板塑料", "样板陶土"};
    for (int i = 0; i < 3; ++i) {
        Entity ped = makeEntity("展台", mPedestal, ws.matStone, Vec3{3.6f, 0.475f, pedZ[i]});
        ped.name = std::string("展台") + char('A' + i);
        ws.entPedestals[i] = w.addEntity(ped);

        Entity orb = makeEntity(orbName[i], mOrb, orbMat[i], Vec3{3.6f, 1.4f, pedZ[i]});
        orb.prompt = "按 E 观察材质";
        orb.command = std::string("inspect ") + orbName[i];
        orb.forLevel = 2;  // 展台上这三个球是第 2 关（材质）的作业
        ws.entOrbs[i] = w.addEntity(orb);

        // 样板柱很细，不挡路（solid = false）；样板球挂在与球同高的位置
        Entity stand = makeEntity("样板柱", mRefStand, ws.matStone, Vec3{3.6f, 0.6f, pedZ[i] + 1.0f});
        stand.name = std::string("样板柱") + char('A' + i);
        stand.solid = false;
        w.addEntity(stand);

        Entity ref = makeEntity(refName[i], mRefOrb, refMat[i], Vec3{3.6f, 1.4f, pedZ[i] + 1.0f});
        ref.solid = false;
        ref.prompt = "按 E 读样板数值";
        ref.command = std::string("inspect ") + refName[i];
        ref.forLevel = 2;  // 样板也是第 2 关的（看答案用的）
        ws.entRefOrbs[i] = w.addEntity(ref);
    }

    // ---------------------------------------------------------------- 光照
    // 代码里这两盏灯一律是关着的：世界出厂的状态就是"一间黑屋子"，
    // 哪盏灯开着、吊在哪，由 content/lights.txt 决定（数据覆盖代码）。
    // 这样即使数据文件被写坏、整份不生效，退回代码默认也还是黑屋子 ——
    // 不会出现"改错了反而把房间点亮、看起来像过关了"。
    //
    // light 0 的代码默认位置也留在门口（0, 1.1, 4.6），和 content/lights.txt 的出厂值
    // 一样是"被搬乱了的样子"。为什么不默认放在灯罩底下（2.2, 2.9, 1.2）：那样一旦
    // 数据文件被删掉/写坏，第 1 关的"光装回灯里了"这一项就白送 35% —— 屏幕写着
    // 35%，可他什么都没做。出厂默认必须和题面一样是"没做完的样子"，第 2 关的球、
    // 第 3 关的地板都是照这条来的，这一处也补上。
    // radius 是灯的物理尺寸：吊灯是块 0.84m 的发光板，所以给它 0.9 ——
    // 相当于一盏"软盒灯"，近距离不会数值爆炸（这就是 shader 里那行 dist2 + radius）
    w.lights[0].position = Vec3{0.0f, 1.1f, 4.6f};
    w.lights[0].color = Vec3{1.0f, 0.93f, 0.82f};
    w.lights[0].intensity = 0.0f;
    w.lights[0].radius = 0.9f;

    w.lights[1].position = Vec3{-4.2f, 2.6f, 1.6f};  // 冷色补光，把暗部从纯黑里拉回来
    w.lights[1].color = Vec3{0.45f, 0.62f, 1.0f};
    w.lights[1].intensity = 0.0f;
    w.lights[1].radius = 0.45f;
    w.lightCount = 2;

    w.ambient = Vec3{0.42f, 0.44f, 0.50f};
    w.skyColor = Vec3{0.46f, 0.56f, 0.78f};
    w.groundColor = Vec3{0.24f, 0.20f, 0.17f};

    w.refreshBounds();
    return ws;
}

}  // namespace dlab
