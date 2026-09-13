// 入群二维码：四关全通之后才解锁的那张图。
//
// 格式（由 tools/gen_recruit_qr.py 生成，magic = DLQ1）：
//     magic  4 字节  "DLQ1"
//     ver    u32     格式版本，现在是 1
//     w,h    u32     模块数（不是像素数！）
//     数据           逐行的 1-bit，行尾补到整字节，MSB 在前，**XOR 混淆过**
//
// 为什么是"模块数×模块数"而不是原图：一格的边长叫一个 module，原图里一格占
// 十几个像素，全存下来纯属浪费 —— 缩到一格一个 bit，七十多 KB 变成二百多字节。
// 更要紧的是画质：按整数倍放大时每格正好是 N×N 个屏幕像素，边缘绝对干净。
// 直接缩原图的话格子会落在半个像素上、边缘发虚，手机就扫不出来了。
//
// 关于"解密"这两个字：**这是混淆，不是加密。** 密钥就写在本文件里，
// 谁真想解都能解。它挡的只是"在 GitHub 上点开 qr.bin 看一眼"的人；
// 真正的门槛是"你得先把四关打完"——那是个游戏设计，不是安全边界。
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace dlab {

// 必须和 tools/gen_recruit_qr.py 里的 KEY **逐字节一致**。
// 改一边不改另一边，游戏里会显示一堆雪花 —— 而且不报错，只是扫不出来。
inline constexpr char kRecruitMask[] = "dreamlab-rt/recruit-qr/v1";
inline constexpr const char* kRecruitQrPath = "assets/recruit/qr.bin";

struct RecruitQr {
    int modulesW = 0;
    int modulesH = 0;
    std::vector<uint8_t> bits;  // 1 bit / 模块

    bool ready() const { return modulesW > 0 && modulesH > 0 && !bits.empty(); }

    int stride() const { return (modulesW + 7) / 8; }

    // 第 (x,y) 个模块是不是黑的。越界当白 —— 画的时候正好当静默区用。
    bool dark(int x, int y) const {
        if (x < 0 || y < 0 || x >= modulesW || y >= modulesH) return false;
        const size_t i = size_t(y) * size_t(stride()) + size_t(x / 8);
        if (i >= bits.size()) return false;
        return (bits[i] & uint8_t(0x80u >> (x % 8))) != 0;
    }

    bool loadFromFile(const std::string& path);
};

// 读不出来就返回 false 并打印修复提示 —— 和字模一样，**不崩**：
// 二维码显示不出来是小事，因为加载失败而打不开游戏才是大事。
inline bool RecruitQr::loadFromFile(const std::string& path) {
    modulesW = 0;
    modulesH = 0;
    bits.clear();

    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        std::fprintf(stderr,
                     "[qr] 打不开入群二维码 %s —— 通关后看不到群码。"
                     "修复：python tools/gen_recruit_qr.py --src <二维码图>\n",
                     path.c_str());
        return false;
    }
    std::fseek(f, 0, SEEK_END);
    const long size = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (size < 16) {
        std::fclose(f);
        std::fprintf(stderr, "[qr] %s 只有 %ld 字节，不是有效文件\n", path.c_str(), size);
        return false;
    }
    // 先声明再 resize，不要写成 `raw(size_t(size))` —— 那一行会被解析成**函数声明**
    // （most vexing parse），后面所有对 raw 的用法都会莫名其妙地报错。
    std::vector<uint8_t> raw;
    raw.resize(size_t(size));
    const size_t got = std::fread(raw.data(), 1, raw.size(), f);
    std::fclose(f);
    if (got != raw.size()) {
        std::fprintf(stderr, "[qr] %s 只读到 %zu/%zu 字节\n", path.c_str(), got, raw.size());
        return false;
    }

    if (std::memcmp(raw.data(), "DLQ1", 4) != 0) {
        std::fprintf(stderr, "[qr] %s 的 magic 不是 DLQ1（版本不匹配？重新跑生成脚本）\n",
                     path.c_str());
        return false;
    }
    auto u32 = [&](size_t off) {
        return uint32_t(raw[off]) | (uint32_t(raw[off + 1]) << 8) | (uint32_t(raw[off + 2]) << 16) |
               (uint32_t(raw[off + 3]) << 24);
    };
    if (u32(4) != 1) {
        std::fprintf(stderr, "[qr] %s 的格式版本是 %u，本程序只认 1\n", path.c_str(), u32(4));
        return false;
    }
    const int w = int(u32(8));
    const int h = int(u32(12));
    // 二维码实际存在的版本是 1..40（21x21 .. 177x177）。超过这个范围说明文件坏了，
    // 不能拿它去分配内存 —— 一个坏文件不该能把程序撑爆。
    if (w < 21 || h < 21 || w > 177 || h > 177) {
        std::fprintf(stderr, "[qr] %s 的尺寸 %dx%d 不是合法的二维码模块数\n", path.c_str(), w, h);
        return false;
    }
    const size_t need = size_t((w + 7) / 8) * size_t(h);
    if (raw.size() != 16 + need) {
        std::fprintf(stderr, "[qr] %s 的数据长度对不上（应 %zu 字节，实际 %zu）\n", path.c_str(),
                     16 + need, raw.size());
        return false;
    }

    const size_t keyLen = std::strlen(kRecruitMask);
    modulesW = w;
    modulesH = h;
    bits.resize(need);
    for (size_t i = 0; i < need; ++i) {
        bits[i] = uint8_t(raw[16 + i] ^ uint8_t(kRecruitMask[i % keyLen]));
    }
    return true;
}

}  // namespace dlab
