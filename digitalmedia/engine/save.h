// 存档：把"打到第几关、站在哪儿、做完哪些目标"写成一个 36 字节的小文件。
//
// 三个刻意选择：
//
// ① **手写定长二进制，不是 JSON**。存档是程序自己的私有状态，人不读它，也不该靠
//    改它来玩（想改东西？content/ 才是留给人改的地方）。字段顺序就写在下面的
//    writeSave 里 —— 学生想看懂一眼看完，想用十六进制编辑器改也改得动。
//
// ② **坏档一律当作"没有存档"**。magic 不对、版本不对、长度不对、坐标是 NaN ——
//    全部静默回到出生点。一个示例项目绝不能因为存档坏了就打不开。
//
// ③ **只有开窗模式读档写档**（判断在 main.cpp）。离屏出图要的是"每次都一样"：
//    如果 --shot 也吃存档，昨天截的图和今天的对不上，而逐像素比对正是这个项目
//    的验证主手段 —— 那才是把工具搞坏了。
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "../core/vec.h"

#if defined(_WIN32)
#include <direct.h>  // _mkdir
#else
#include <sys/stat.h>  // mkdir
#include <sys/types.h>
#endif

namespace dlab {

// 存档路径。放在 saved/ 里（.gitignore 已忽略）：这是"我这台机器上的进度"，
// 不该跟着仓库走 —— 不然 clone 下来的第一件事是继承别人的跑图记录。
constexpr const char* kSavePath = "saved/save.bin";
constexpr uint32_t kSaveVersion = 1;
constexpr const char* kSaveMagic = "DLSV";  // DreamLab SaVe

struct SaveData {
    bool loaded = false;  // true = 这一份是从文件里读出来的；false = 新开（或文件坏了）
    int level = 0;        // 关卡号（M4 起有意义；现在恒为 0 = 自由参观）
    Vec3 feet{0.0f, 0.0f, 4.5f};
    float yaw = 0.0f;
    float pitch = -0.06f;
    uint32_t goals = 0;  // 位掩码：第 i 位 = 第 i 关的目标已完成
};

namespace detail {

inline void pushU32(std::string& b, uint32_t v) {
    b.push_back(char(v & 0xFFu));
    b.push_back(char((v >> 8) & 0xFFu));
    b.push_back(char((v >> 16) & 0xFFu));
    b.push_back(char((v >> 24) & 0xFFu));
}

inline void pushF32(std::string& b, float v) {
    uint32_t bits = 0;
    std::memcpy(&bits, &v, sizeof(bits));  // 按位搬，别用 union / 别做数值转换
    pushU32(b, bits);
}

inline uint32_t takeU32(const std::string& b, size_t off) {
    return uint32_t(uint8_t(b[off])) | (uint32_t(uint8_t(b[off + 1])) << 8) |
           (uint32_t(uint8_t(b[off + 2])) << 16) | (uint32_t(uint8_t(b[off + 3])) << 24);
}

inline float takeF32(const std::string& b, size_t off) {
    const uint32_t bits = takeU32(b, off);
    float v = 0.0f;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}

inline bool sanePose(const Vec3& feet) {
    if (!std::isfinite(feet.x) || !std::isfinite(feet.y) || !std::isfinite(feet.z)) return false;
    // 屋子只有 12×12 米。留足余量到 ±500：既拦住 NaN / 天文数字，
    // 又不至于把将来"更大的地图"提前掐死。
    return std::fabs(feet.x) < 500.0f && std::fabs(feet.z) < 500.0f && feet.y > -50.0f &&
           feet.y < 500.0f;
}

}  // namespace detail

// 文件不存在 / 坏了 / 版本不认识 → 返回默认值（loaded=false），不报错、不抛异常
inline SaveData loadSave(const std::string& path = kSavePath) {
    SaveData s;
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) return s;
    std::string b;
    char buf[64];
    size_t n = 0;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) b.append(buf, n);
    std::fclose(f);

    const size_t kSize = 4 + 4 + 4 + 12 + 8 + 4;  // magic + 版本 + 关卡 + 脚底 + 朝向 + 目标
    if (b.size() != kSize) return s;
    if (b.compare(0, 4, kSaveMagic) != 0) return s;
    if (detail::takeU32(b, 4) != kSaveVersion) return s;

    const int level = int(detail::takeU32(b, 8));
    const Vec3 feet{detail::takeF32(b, 12), detail::takeF32(b, 16), detail::takeF32(b, 20)};
    const float yaw = detail::takeF32(b, 24);
    const float pitch = detail::takeF32(b, 28);
    if (level < 0 || level > 64) return s;
    if (!detail::sanePose(feet)) return s;
    if (!std::isfinite(yaw) || !std::isfinite(pitch)) return s;

    s.loaded = true;
    s.level = level;
    s.goals = detail::takeU32(b, 32);
    s.feet = feet;
    s.yaw = yaw;
    s.pitch = clampf(pitch, -1.5533f, 1.5533f);  // 坏档里的俯仰角也不能把头拧断
    return s;
}

inline bool writeSave(const SaveData& s, const std::string& path = kSavePath) {
    // 目录不在就建一个。建不出来（只读目录、权限不对）就安静地放弃存盘：
    // 玩不了存档是小事，因为存不了档而报错退出才是大事。
#if defined(_WIN32)
    _mkdir("saved");
#else
    mkdir("saved", 0755);
#endif

    std::string b;
    b.append(kSaveMagic, 4);
    detail::pushU32(b, kSaveVersion);
    detail::pushU32(b, uint32_t(s.level));
    detail::pushF32(b, s.feet.x);
    detail::pushF32(b, s.feet.y);
    detail::pushF32(b, s.feet.z);
    detail::pushF32(b, s.yaw);
    detail::pushF32(b, s.pitch);
    detail::pushU32(b, s.goals);

    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (f == nullptr) return false;
    const size_t wrote = std::fwrite(b.data(), 1, b.size(), f);
    std::fclose(f);
    return wrote == b.size();
}

// --reset：把存档删掉。删不掉（本来就没有 / 权限不够）也算成功 —— 目的达到了
inline bool clearSave(const std::string& path = kSavePath) { return std::remove(path.c_str()) == 0; }

}  // namespace dlab
