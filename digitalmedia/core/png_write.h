// PNG 编码器 —— 零依赖，只用 C 标准库。
// 刻意不用 zlib：deflate 走 stored（未压缩）块，格式合法、实现可读，
// 编码图像格式本身就是数媒组要讲的一课（压缩留作进阶任务）。
#pragma once

// MSVC 会把 fopen 当成"不安全函数"报 C4996；build.bat 里已全局定义，
// 这里的 #ifndef 是为了让单独包含本头文件的用法也不报警告。
#if defined(_MSC_VER) && !defined(_CRT_SECURE_NO_WARNINGS)
#define _CRT_SECURE_NO_WARNINGS
#endif

#include <cstdint>
#include <cstdio>
#include <vector>

namespace dlab {

// ---------------------------------------------------------------- CRC32 / Adler32

inline uint32_t crc32Update(uint32_t crc, const uint8_t* data, size_t len) {
    static uint32_t table[256];
    static bool ready = false;
    if (!ready) {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        ready = true;
    }
    for (size_t i = 0; i < len; ++i) crc = table[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
    return crc;
}

inline uint32_t adler32(const uint8_t* data, size_t len) {
    uint32_t a = 1, b = 0;
    const uint32_t mod = 65521u;
    for (size_t i = 0; i < len; ++i) {
        a = (a + data[i]) % mod;
        b = (b + a) % mod;
    }
    return (b << 16) | a;
}

// ---------------------------------------------------------------- 写入辅助

inline void putU32BE(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(uint8_t(v >> 24));
    out.push_back(uint8_t(v >> 16));
    out.push_back(uint8_t(v >> 8));
    out.push_back(uint8_t(v));
}

inline void writeChunk(std::vector<uint8_t>& out, const char type[4], const std::vector<uint8_t>& data) {
    putU32BE(out, uint32_t(data.size()));
    std::vector<uint8_t> body;
    body.reserve(4 + data.size());
    for (int i = 0; i < 4; ++i) body.push_back(uint8_t(type[i]));
    body.insert(body.end(), data.begin(), data.end());
    out.insert(out.end(), body.begin(), body.end());
    putU32BE(out, crc32Update(0xFFFFFFFFu, body.data(), body.size()) ^ 0xFFFFFFFFu);
}

// ---------------------------------------------------------------- 主入口

// rgb: 长度必须为 w*h*3，行优先，每像素 R,G,B。
inline bool writePNG(const char* path, int w, int h, const uint8_t* rgb) {
    if (!path || w <= 0 || h <= 0 || !rgb) return false;

    // 原始数据：每行前面加一个 filter 字节（0 = None）
    std::vector<uint8_t> raw;
    raw.reserve(size_t(h) * (1 + size_t(w) * 3));
    for (int y = 0; y < h; ++y) {
        raw.push_back(0);
        const uint8_t* row = rgb + size_t(y) * size_t(w) * 3;
        raw.insert(raw.end(), row, row + size_t(w) * 3);
    }

    // zlib 流：2 字节头 + stored deflate 块 + 4 字节 adler32
    std::vector<uint8_t> z;
    z.reserve(raw.size() + raw.size() / 65535 * 5 + 16);
    z.push_back(0x78);
    z.push_back(0x01);
    size_t pos = 0;
    while (pos < raw.size()) {
        const size_t remain = raw.size() - pos;
        const uint16_t blockLen = uint16_t(remain > 65535 ? 65535 : remain);
        const bool last = (remain <= 65535);
        z.push_back(last ? 1 : 0);              // BFINAL, BTYPE=00 (stored)
        z.push_back(uint8_t(blockLen & 0xFF));  // LEN 小端
        z.push_back(uint8_t(blockLen >> 8));
        const uint16_t nlen = uint16_t(~blockLen);
        z.push_back(uint8_t(nlen & 0xFF));      // NLEN = ~LEN
        z.push_back(uint8_t(nlen >> 8));
        z.insert(z.end(), raw.begin() + pos, raw.begin() + pos + blockLen);
        pos += blockLen;
    }
    putU32BE(z, adler32(raw.data(), raw.size()));

    // PNG 文件
    std::vector<uint8_t> png = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};

    std::vector<uint8_t> ihdr;
    putU32BE(ihdr, uint32_t(w));
    putU32BE(ihdr, uint32_t(h));
    ihdr.push_back(8);  // 位深
    ihdr.push_back(2);  // 颜色类型 2 = truecolor RGB
    ihdr.push_back(0);  // 压缩方法
    ihdr.push_back(0);  // 滤波方法
    ihdr.push_back(0);  // 隔行扫描
    writeChunk(png, "IHDR", ihdr);

    // 单块 IDAT 过大时拆成多块（PNG 自身无 2GB 限制问题，但保持通用）
    const size_t maxIdat = 1024 * 1024;
    for (size_t off = 0; off < z.size(); off += maxIdat) {
        const size_t n = (z.size() - off < maxIdat) ? (z.size() - off) : maxIdat;
        writeChunk(png, "IDAT", std::vector<uint8_t>(z.begin() + off, z.begin() + off + n));
    }
    writeChunk(png, "IEND", {});

    std::FILE* fp = std::fopen(path, "wb");
    if (!fp) return false;
    const size_t written = std::fwrite(png.data(), 1, png.size(), fp);
    std::fclose(fp);
    return written == png.size();
}

}  // namespace dlab
