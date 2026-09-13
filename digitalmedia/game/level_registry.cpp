// 关卡表 + 评委机位（"判定怎么算"这件事只在这里发生一次，所有关卡共用）。
//
//  加一关要做的事：
//    ① 在 game/levels/ 下新建一个 .cpp，实现一个返回 const Level& 的函数；
//    ② 在 level.h 里给它加一行声明；
//    ③ 把它填进下面的 kLevels 表。
//  build.bat 已经会把 game/levels/*.cpp 自动编进去，不用改构建脚本。
#include "level.h"

namespace dlab {

namespace {

const Level* const kLevels[] = {
    &levelDark(),        // 第 0 关
    &levelFirstLight(),  // 第 1 关
    &levelMaterial(),    // 第 2 关
    &levelTexture(),     // 第 3 关
};

constexpr int kLevelCount = int(sizeof(kLevels) / sizeof(kLevels[0]));

}  // namespace

int levelCount() { return kLevelCount; }

const Level& levelAt(int index) {
    if (index < 0) index = 0;
    if (index >= kLevelCount) index = kLevelCount - 1;
    return *kLevels[index];
}

int findLevel(const std::string& id) {
    for (int i = 0; i < kLevelCount; ++i) {
        if (id == kLevels[i]->id) return i;
    }
    return -1;
}

LevelStatus Judge::evaluate(World& world, const Level& lv) {
    if (rz_.framebuffer().width != kJudgeWidth || rz_.framebuffer().height != kJudgeHeight) {
        rz_.resize(kJudgeWidth, kJudgeHeight);
    }
    // 拍两张，顺序不能反。先拍"灯全关"的，再拍真的 —— 因为最后留在 framebuffer
    // 里的那张，就是关卡在 view.frame 里拿到的那张。要是反过来，关卡想自己量画面
    // 某个角落，量到的会是一张灯全关的假图。
    //
    // 评委相机拍的是"场景本身"：没东西挡着的地方就是纯黑，不铺底色。
    // 关卡问的是"屋里亮起来了吗"，不是"背景有多亮"—— 要是拿界面的底色当亮度，
    // 一台对着墙外的相机也能把进度条推满。
    //
    // 为什么要多渲染一张 96x54 的小图？因为绝对亮度会被一堆和关卡无关的东西
    // 牵着走：环境光调到多少、桌上的球换成什么材质、地板纹理平铺几遍…… 第 1 关
    // 就吃过这个亏 —— 达标线按"第 0 关做完"的那间屋子定的，结果直接跳关进来的
    // 人把一切做对也只读 96%（环境光还是出厂的 0）。后来发现就算把达标线压到
    // 两种底子都够得着，只要后面几关改动材质，这个数还会再漂一次。
    // 而"两张的差"只跟灯有关：环境光、材质怎么变，它都不动。
    // 代价是每帧多一次 96x54 的光栅化 —— 小图上量亮度，本来就是这个道理。
    float saved[World::kMaxLights] = {};
    const int n = world.lightCount < World::kMaxLights ? world.lightCount : World::kMaxLights;
    for (int i = 0; i < n; ++i) {
        saved[i] = world.lights[i].intensity;
        world.lights[i].intensity = 0.0f;
    }
    rz_.framebuffer().clear(Vec3{0.0f, 0.0f, 0.0f});
    world.render(rz_, lv.judge);
    const float lightsOff = rz_.framebuffer().meanLuminance();
    for (int i = 0; i < n; ++i) world.lights[i].intensity = saved[i];

    rz_.framebuffer().clear(Vec3{0.0f, 0.0f, 0.0f});
    world.render(rz_, lv.judge);
    const float lightsOn = rz_.framebuffer().meanLuminance();

    LevelStatus st;
    st.luminance = lightsOn;
    st.lightLuminance = lightsOn - lightsOff;
    if (lv.progress != nullptr) {
        const LevelView view{world, rz_.framebuffer(), lightsOn, st.lightLuminance};
        st.progress = clampf(lv.progress(view), 0.0f, 1.0f);
    }
    return st;
}

}  // namespace dlab
