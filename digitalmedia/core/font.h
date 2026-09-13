// 像素字模 —— 中文和 ASCII 共用同一张 1-bit 图集。
//
// 为什么不像纹理那样用 PNG 存字模：PNG 要 inflate 解码器，而 P0 不想为了读
// 字模就背上一个 DEFLATE 实现（那是「压缩」那一课的伏笔）。自定义二进制格式
// 反而更简单，加载 = fread 整个文件 + 对码点表二分查找，零第三方依赖：
//
//   offset 0   magic "DLF1"
//   offset 4   u8 格宽 | u8 格高 | u8 每字字节数 | u8 标志(bit0=有步进表)
//   offset 8   u32 字数 N
//   之后       N × u32 码点（严格升序）| N × u8 步进宽度 | N × 每字字节数 位图
//
// 全部小端。位图按行 MSB 优先打包，行与行之间不按字节对齐（12×12 = 18 字节/字）。
// 字体与生成脚本见 tools/gen_font_atlas.py。
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "framebuffer.h"
#include "vec.h"

namespace dlab {

// 取一个 UTF-8 码点，i 前进到下一个字符。非法序列返回 U+FFFD 且只前进一个字节，
// 保证任何输入都不会让解码原地打转。
inline uint32_t utf8Next(const std::string& utf8, size_t& i) {
    if (i >= utf8.size()) return 0;
    const uint8_t b0 = uint8_t(utf8[i]);
    if (b0 < 0x80u) {
        ++i;
        return b0;
    }
    int extra = 0;
    uint32_t cp = 0;
    if ((b0 & 0xE0u) == 0xC0u) {
        extra = 1;
        cp = b0 & 0x1Fu;
    } else if ((b0 & 0xF0u) == 0xE0u) {
        extra = 2;
        cp = b0 & 0x0Fu;
    } else if ((b0 & 0xF8u) == 0xF0u) {
        extra = 3;
        cp = b0 & 0x07u;
    } else {
        ++i;
        return 0xFFFDu;
    }
    size_t k = i + 1;
    for (int n = 0; n < extra; ++n, ++k) {
        if (k >= utf8.size()) {
            ++i;
            return 0xFFFDu;
        }
        const uint8_t b = uint8_t(utf8[k]);
        if ((b & 0xC0u) != 0x80u) {
            ++i;
            return 0xFFFDu;
        }
        cp = (cp << 6) | (b & 0x3Fu);
    }
    i = k;
    return cp;
}

class Font {
public:
    static constexpr int kMaxGlyphW = 16;
    static constexpr int kMaxGlyphH = 16;
    static constexpr int kLineGap = 4;  // 12px 字 + 4px 行距 = 16px 行高

    // 整文件读入内存（161 KB 左右），解析失败返回 false 并打印修复提示
    bool loadFromFile(const std::string& path) {
        path_ = path;
        count_ = 0;
        missing_ = 0;
        warned_ = false;
        data_.clear();

        std::FILE* fh = std::fopen(path.c_str(), "rb");
        if (fh == nullptr) {
            std::fprintf(stderr,
                         "[font] 打不开字模 %s —— 文字会画成红块。"
                         "修复：python tools/gen_font_atlas.py\n",
                         path.c_str());
            return false;
        }
        std::fseek(fh, 0, SEEK_END);
        const long size = std::ftell(fh);
        std::fseek(fh, 0, SEEK_SET);
        if (size < 12) {
            std::fclose(fh);
            std::fprintf(stderr, "[font] 字模 %s 太小（%ld 字节），不是有效文件\n", path.c_str(), size);
            return false;
        }
        data_.resize(size_t(size));
        const size_t got = std::fread(data_.data(), 1, data_.size(), fh);
        std::fclose(fh);
        if (got != data_.size()) {
            std::fprintf(stderr, "[font] 字模 %s 只读到 %zu/%zu 字节\n", path.c_str(), got, data_.size());
            data_.clear();
            return false;
        }

        if (std::memcmp(data_.data(), "DLF1", 4) != 0) {
            std::fprintf(stderr, "[font] 字模 %s 的 magic 不是 DLF1（版本不匹配？重新跑生成脚本）\n", path.c_str());
            data_.clear();
            return false;
        }
        glyphW_ = data_[4];
        glyphH_ = data_[5];
        bytesPerGlyph_ = data_[6];
        const uint8_t flags = data_[7];
        const uint32_t n = readU32(data_.data() + 8);
        const int minBytes = (int(glyphW_) * int(glyphH_) + 7) / 8;
        if (glyphW_ < 1 || glyphW_ > kMaxGlyphW || glyphH_ < 1 || glyphH_ > kMaxGlyphH ||
            bytesPerGlyph_ < minBytes || (flags & 0x01u) == 0u) {
            std::fprintf(stderr, "[font] 字模 %s 头部非法（%dx%d，%d 字节/字，标志 0x%02X）\n", path.c_str(),
                         int(glyphW_), int(glyphH_), int(bytesPerGlyph_), int(flags));
            data_.clear();
            return false;
        }
        const size_t need = 12 + size_t(n) * (4 + 1 + size_t(bytesPerGlyph_));
        if (data_.size() != need) {
            std::fprintf(stderr, "[font] 字模 %s 长度对不上（应有 %zu 字节，实际 %zu）\n", path.c_str(), need,
                         data_.size());
            data_.clear();
            return false;
        }
        count_ = int(n);
        cpOffset_ = 12;
        advOffset_ = size_t(cpOffset_) + size_t(count_) * 4;
        bmOffset_ = advOffset_ + size_t(count_);
        return true;
    }

    bool loaded() const { return count_ > 0; }
    const std::string& path() const { return path_; }
    int glyphCount() const { return count_; }
    int glyphW() const { return glyphW_; }
    int glyphH() const { return glyphH_; }
    int lineHeight() const { return glyphH_ + kLineGap; }

    // 第 i 个字（i 必须落在 [0, glyphCount)）
    uint32_t codepointAt(int i) const {
        return readU32(data_.data() + cpOffset_ + size_t(i) * 4);
    }
    int advanceAt(int i) const { return int(data_[advOffset_ + size_t(i)]); }

    // 码点在字模里的下标；没有返回 -1
    int indexOf(uint32_t cp) const {
        int lo = 0;
        int hi = count_ - 1;
        while (lo <= hi) {
            const int mid = lo + (hi - lo) / 2;
            const uint32_t v = codepointAt(mid);
            if (v == cp) return mid;
            if (v < cp) {
                lo = mid + 1;
            } else {
                hi = mid - 1;
            }
        }
        return -1;
    }

    // 某个字的某一位是不是墨迹（预览工具也用它，所以公开）
    bool inkAt(int index, int gx, int gy) const {
        const int bit = gy * int(glyphW_) + gx;
        const uint8_t byte =
            data_[bmOffset_ + size_t(index) * size_t(bytesPerGlyph_) + size_t(bit >> 3)];
        return ((byte >> (7 - (bit & 7))) & 1u) != 0u;
    }

    // 步进宽度（像素）：全角 12、半角按字模表，找不到的字按一格算
    int advanceOf(uint32_t cp) const {
        const int idx = indexOf(cp);
        return idx >= 0 ? advanceAt(idx) : int(glyphW_);
    }

    int measureLine(const std::string& utf8) const {
        int w = 0;
        size_t i = 0;
        while (i < utf8.size()) {
            const uint32_t cp = utf8Next(utf8, i);
            if (cp == '\n') break;
            w += advanceOf(cp);
        }
        return w;
    }

    // 多行文本里最宽的一行（居中排版用）
    int measureText(const std::string& utf8) const {
        int best = 0;
        size_t start = 0;
        for (size_t i = 0; i <= utf8.size(); ++i) {
            if (i == utf8.size() || utf8[i] == '\n') {
                best = best > measureLine(utf8.substr(start, i - start)) ? best
                                                                        : measureLine(utf8.substr(start, i - start));
                start = i + 1;
            }
        }
        return best;
    }

    // 画一行，返回推进的宽度。注意颜色是线性 HDR：UI 想显示纯白请给 2.0 左右，
    // 因为整张画面最后才统一走 ACES 色调映射（UI 和 3D 在同一条链上）。
    int drawLine(Framebuffer& fb, int x, int y, const std::string& utf8, Vec3 color, float alpha = 1.0f,
                 int scale = 1) const {
        int penX = x;
        size_t i = 0;
        while (i < utf8.size()) {
            const uint32_t cp = utf8Next(utf8, i);
            if (cp == '\n') break;
            penX += drawGlyph(fb, penX, y, cp, color, alpha, scale);
        }
        return penX - x;
    }

    // 画多行（'\n' 分行），返回占用的高度
    int drawText(Framebuffer& fb, int x, int y, const std::string& utf8, Vec3 color, float alpha = 1.0f,
                 int scale = 1) const {
        int lineY = y;
        size_t start = 0;
        for (size_t i = 0; i <= utf8.size(); ++i) {
            if (i == utf8.size() || utf8[i] == '\n') {
                drawLine(fb, x, lineY, utf8.substr(start, i - start), color, alpha, scale);
                start = i + 1;
                lineY += lineHeight() * (scale < 1 ? 1 : scale);
            }
        }
        return (lineY - y) - kLineGap * (scale < 1 ? 1 : scale);
    }

    // 本次运行累计碰到多少个字集里没有的字（诊断用）
    int missingGlyphs() const { return missing_; }

    // 按像素宽度折行。中文没有空格可断，所以只能按"字"断，不能按词断。
    // 保证：每行的 measureLine() <= maxWidth（除非单个字本身就比 maxWidth 宽，
    // 那时那一行只有它自己 —— 否则会死循环）。
    std::vector<std::string> wrap(const std::string& utf8, int maxWidth) const {
        std::vector<std::string> lines;
        if (maxWidth < 1) maxWidth = 1;
        std::string cur;
        int curW = 0;
        size_t i = 0;
        while (i < utf8.size()) {
            if (utf8[i] == '\n') {
                lines.push_back(cur);
                cur.clear();
                curW = 0;
                ++i;
                continue;
            }
            const size_t start = i;
            const uint32_t cp = utf8Next(utf8, i);
            const int w = advanceOf(cp);
            if (curW + w > maxWidth && !cur.empty()) {
                lines.push_back(cur);
                cur.clear();
                curW = 0;
            }
            cur.append(utf8, start, i - start);
            curW += w;
        }
        if (!cur.empty() || lines.empty()) lines.push_back(cur);
        return lines;
    }

private:
    static uint32_t readU32(const uint8_t* p) {
        return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
    }

    int drawGlyph(Framebuffer& fb, int x, int y, uint32_t cp, Vec3 color, float alpha, int scale) const {
        if (scale < 1) scale = 1;
        if (count_ == 0) {
            // 字模整个没加载成功：画实心红块，让「缺文件」和「缺某个字」一眼可分。
            // 想让它过 ACES 之后还红得刺眼，线性值要给到 8 左右。
            fillRect(fb, x, y, glyphW_, glyphH_, scale, Vec3{8.0f, 0.05f, 0.05f}, alpha);
            return glyphW_ * scale;
        }
        const int idx = indexOf(cp);
        if (idx < 0) {
            ++missing_;
            if (!warned_) {
                warned_ = true;
                std::fprintf(stderr,
                             "[font] 字集里没有 U+%04X，用空框占位。"
                             "要加字：改 tools/gen_font_atlas.py 的 EXTRA_SYMBOLS 后重跑脚本\n",
                             cp);
            }
            strokeRect(fb, x, y, glyphW_, glyphH_, scale, color, alpha);
            return glyphW_ * scale;
        }
        for (int gy = 0; gy < int(glyphH_); ++gy)
            for (int gx = 0; gx < int(glyphW_); ++gx)
                if (inkAt(idx, gx, gy)) fillRect(fb, x + gx * scale, y + gy * scale, 1, 1, scale, color, alpha);
        return advanceAt(idx) * scale;
    }

    static void fillRect(Framebuffer& fb, int x, int y, int w, int h, int scale, Vec3 color, float alpha) {
        for (int gy = 0; gy < h * scale; ++gy)
            for (int gx = 0; gx < w * scale; ++gx) fb.blendPixel(x + gx, y + gy, color, alpha);
    }

    static void strokeRect(Framebuffer& fb, int x, int y, int w, int h, int scale, Vec3 color, float alpha) {
        const int pw = w * scale;
        const int ph = h * scale;
        for (int gy = 0; gy < ph; ++gy) {
            const bool hEdge = gy < scale || gy >= ph - scale;
            for (int gx = 0; gx < pw; ++gx) {
                if (hEdge || gx < scale || gx >= pw - scale) fb.blendPixel(x + gx, y + gy, color, alpha);
            }
        }
    }

    std::vector<uint8_t> data_;  // 整个字模文件
    std::string path_ = "assets/font/pixel12.bin";
    uint8_t glyphW_ = 12;
    uint8_t glyphH_ = 12;
    uint8_t bytesPerGlyph_ = 18;
    int count_ = 0;
    size_t cpOffset_ = 12;
    size_t advOffset_ = 12;
    size_t bmOffset_ = 12;
    mutable int missing_ = 0;
    mutable bool warned_ = false;
};

}  // namespace dlab
