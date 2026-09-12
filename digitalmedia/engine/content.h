// content/*.txt —— 数据层：材质 / 光照 / 环境。
//
// 三条「学生改动面」里最轻的一条：改数字 → 存盘 → 按 R，不重启、不编译（决策 D1）。
// 因此这个解析器有两个硬要求：
//   ① 报错要写清「哪个文件、第几行、是不是想写 X」。改数据的人往往没写过代码，
//      一句 "unknown key" 会让他直接放弃，而 "第 12 行：是不是想写 roughness？" 会让他继续。
//   ② 一个文件里只要有错，这个文件就一条都不许应用。半套新数据混着旧数据渲染出来的画面，
//      比一句报错难查一百倍。
//
// 语法（P0）：
//     # 井号到行尾是注释
//     material chrome { albedo 0.95 0.93 0.90  roughness 0.06  metallic 1.0 }   ← materials.txt
//     material floor  { uvscale 4 4  wrap repeat  filter bilinear }              ← textures.txt
//     light 0         { pos 2.2 2.9 1.2  color 1.0 0.93 0.82  intensity 48  radius 0.9 }
//     ambient 0.42 0.44 0.50                                                    ← lighting.txt
//     sky     0.46 0.56 0.78
//     ground  0.24 0.20 0.17
// 花括号可以换行写，逗号当空格用（0.5, 0.2, 0.2 也认），大小写敏感。
// wrap / filter 的值是词不是数字：wrap repeat|clamp、filter nearest|bilinear。
// 这两个词本身就是要教的东西（第 3 关），让学生敲 0/1 是在考他记编号。
// 一个文件里能写哪几类块不限 —— 现在 materials.txt 放 material，lights.txt 放 light，
// lighting.txt 放环境色，textures.txt 放地板的贴图设置。分文件只是为了
// "一次只开一个、别改错地方"，语法是同一套。
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "../core/mat.h"
#include "world.h"

namespace dlab {

// ------------------------------------------------------------------ 数据结构

// has* 为 false = 文件里没写这一项 = 保持世界里的原值（覆盖式，不是替换式）
struct MaterialPatch {
    std::string name;
    bool hasAlbedo = false;     Vec3 albedo{};
    bool hasRoughness = false;  float roughness = 0.0f;
    bool hasMetallic = false;   float metallic = 0.0f;
    bool hasEmissive = false;   Vec3 emissive{};
    bool hasUvScale = false;    Vec2 uvScale{};
    bool hasWrap = false;       int wrapMode = 0;    // kWrapRepeat / kWrapClamp
    bool hasFilter = false;     int filterMode = 0;  // kFilterNearest / kFilterBilinear
    int line = 0;  // 块头行号，报「世界没这个材质」时指回文件用
};

struct LightPatch {
    int index = 0;
    bool hasPos = false;        Vec3 pos{};
    bool hasColor = false;      Vec3 color{};
    bool hasIntensity = false;  float intensity = 0.0f;
    bool hasRadius = false;     float radius = 0.0f;
    int line = 0;
};

struct ContentPatch {
    std::string file;
    std::vector<MaterialPatch> materials;
    std::vector<LightPatch> lights;
    bool hasAmbient = false;    Vec3 ambient{};
    bool hasSky = false;        Vec3 sky{};
    bool hasGround = false;     Vec3 ground{};
    std::vector<std::string> errors;  // 已格式化好："content/xxx.txt:12: 未知字段 ..."
    bool ok() const { return errors.empty(); }
};

// 被监视的 content 文件清单。M4 做关卡时按关卡换一批（所以这里返回的是 vector）。
inline std::vector<std::string> contentFileList() {
    return {"content/materials.txt", "content/lights.txt", "content/lighting.txt", "content/textures.txt"};
}

// ------------------------------------------------------------------ 接口

ContentPatch parseContent(const std::string& src, const std::string& fileName);
bool readTextFile(const std::string& path, std::string& out);
ContentPatch loadContentFile(const std::string& path);

struct ApplyStats {
    int materials = 0;
    int lights = 0;
    int env = 0;
    int missing = 0;  // 文件里写了、世界里却没有的名字
    int locked = 0;   // 文件里写了、但这份材质上了锁（关卡的标准答案）
};

// log 非空时把人类可读的行写进 log（游戏内控制台面板要用），否则直接打到 stdout/stderr。
ApplyStats applyContent(World& world, const ContentPatch& patch, std::vector<std::string>* log = nullptr);

// 把若干文件读一遍并应用。单个文件解析失败只跳过它自己，其它文件照常。
int applyContentFiles(World& world, const std::vector<std::string>& files, std::vector<std::string>* log = nullptr);

// ------------------------------------------------------------------ 实现

namespace detail {

struct Token {
    std::string text;
    int line = 1;
};

// 词法：{ } 各自成 token（所以 material chrome{ 也认），# 吃到行尾，逗号当空白。
// \r 必须当空白吃掉 —— Windows 上 git checkout 下来的是 CRLF 文本。
inline std::vector<Token> tokenize(const std::string& src) {
    std::vector<Token> out;
    std::string cur;
    int line = 1;
    auto flush = [&]() {
        if (!cur.empty()) {
            out.push_back(Token{cur, line});
            cur.clear();
        }
    };
    for (size_t i = 0; i < src.size(); ++i) {
        const char c = src[i];
        if (c == '\n') {
            flush();
            ++line;
        } else if (c == '{' || c == '}') {
            flush();
            out.push_back(Token{std::string(1, c), line});
        } else if (c == '#') {
            flush();
            while (i + 1 < src.size() && src[i + 1] != '\n') ++i;
        } else if (c == ' ' || c == '\t' || c == '\r' || c == ',') {
            flush();
        } else {
            cur += c;
        }
    }
    flush();
    return out;
}

// 允许写成 0.5 / .5 / 0.5f / 1 / 1e-3；inf 和 nan 一律拒绝（会渲染成满屏雪花）
inline bool parseNumber(const std::string& s, float& out) {
    if (s.empty()) return false;
    const char* begin = s.c_str();
    char* end = nullptr;
    const float v = std::strtof(begin, &end);
    if (end == begin) return false;
    if (*end == 'f' || *end == 'F') ++end;
    if (*end != '\0' || !std::isfinite(v)) return false;
    out = v;
    return true;
}

inline bool parseInt(const std::string& s, int& out) {
    if (s.empty()) return false;
    char* end = nullptr;
    const long v = std::strtol(s.c_str(), &end, 10);
    if (end == s.c_str() || *end != '\0' || v < 0 || v > 100000) return false;
    out = int(v);
    return true;
}

inline int editDistance(const std::string& a, const std::string& b) {
    std::vector<int> prev(b.size() + 1), cur(b.size() + 1);
    for (size_t j = 0; j <= b.size(); ++j) prev[j] = int(j);
    for (size_t i = 1; i <= a.size(); ++i) {
        cur[0] = int(i);
        for (size_t j = 1; j <= b.size(); ++j) {
            const int cost = (a[i - 1] == b[j - 1]) ? 0 : 1;
            cur[j] = std::min(std::min(prev[j] + 1, cur[j - 1] + 1), prev[j - 1] + cost);
        }
        prev = cur;
    }
    return prev[b.size()];
}

// 拼错的键名给一句「是不是想写 X」。一个字母之差，自己盯着看十分钟也未必发现。
inline std::string suggest(const std::string& word, const std::vector<std::string>& options) {
    std::string best;
    int bestDist = 1 << 30;
    for (const std::string& o : options) {
        const int d = editDistance(word, o);
        if (d < bestDist) {
            bestDist = d;
            best = o;
        }
    }
    const int limit = int(word.size()) / 2 + 1;
    return bestDist <= limit ? best : std::string();
}

// 一个字段吃几个数；名字和数量放在一起，报错和建议都从这张表来
struct FieldSpec {
    const char* name;
    int arity;
};

inline const FieldSpec* kMaterialFields() {
    static const FieldSpec f[] = {{"albedo", 3},  {"roughness", 1}, {"metallic", 1},
                                  {"emissive", 3}, {"uvscale", 2},  {"wrap", 1},
                                  {"filter", 1}};
    return f;
}
inline const int kMaterialFieldCount = 7;

inline const FieldSpec* kLightFields() {
    static const FieldSpec f[] = {{"pos", 3}, {"color", 3}, {"intensity", 1}, {"radius", 1}};
    return f;
}
inline const int kLightFieldCount = 4;

inline const FieldSpec* kTopFields() {
    static const FieldSpec f[] = {{"ambient", 3}, {"sky", 3}, {"ground", 3}};
    return f;
}
inline const int kTopFieldCount = 3;

inline std::vector<std::string> fieldNames(const FieldSpec* specs, int n) {
    std::vector<std::string> v;
    for (int i = 0; i < n; ++i) v.push_back(specs[i].name);
    return v;
}

inline int fieldArity(const FieldSpec* specs, int n, const std::string& name) {
    for (int i = 0; i < n; ++i)
        if (name == specs[i].name) return specs[i].arity;
    return -1;
}

// "值是词"的字段（wrap / filter）的取值表：词 + 它对应的枚举值 + 一句"这个词是干什么的"。
// 那句解释会被原样写进报错 —— 学生分不清 repeat 和 clamp 谁是谁的时候，
// 不用去翻文档，报错里就写着两个人各自是干什么的。
struct WordOption {
    const char* word;
    int value;
    const char* note;
};

inline const WordOption* wrapOptions(int& n) {
    static const WordOption o[] = {
        {"repeat", kWrapRepeat, "超出去的部分从头再来（= 平铺）"},
        {"clamp", kWrapClamp, "超出去的部分按边缘那一条颜色拉出去（铺超过一张时整块地板变成一个纯色）"},
    };
    n = 2;
    return o;
}

inline const WordOption* filterOptions(int& n) {
    static const WordOption o[] = {
        {"nearest", kFilterNearest, "最近邻：取最近的那一个像素，边缘硬、放大会看见方块"},
        {"bilinear", kFilterBilinear, "双线性：周围四个像素插值，边缘平滑"},
    };
    n = 2;
    return o;
}

// 把世界里的现状写成人话（inspect 要打"现在是 clamp 还是 repeat"）。
// 认不出来的值（世界被谁改坏了）原样报数字，不装作认识。
inline const char* wrapWord(int mode) {
    if (mode == kWrapRepeat) return "repeat";
    if (mode == kWrapClamp) return "clamp";
    return "（不认识）";
}

inline const char* filterWord(int mode) {
    if (mode == kFilterNearest) return "nearest";
    if (mode == kFilterBilinear) return "bilinear";
    return "（不认识）";
}

inline Vec3 toVec3(const std::vector<float>& v) { return Vec3{v[0], v[1], v[2]}; }

// 数字扫描的止步标志：碰到这些就说明"这一行写完了"，绝不能再往下吃 ——
// 否则 albedo 少写一个数，就会把下一行的 roughness 也当成它的数，报出一串假错误。
inline bool numberStop(const std::string& t) {
    return t == "}" || t == "{" || t == "material" || t == "light" || t == "ambient" || t == "sky" ||
           t == "ground";
}

// 把一行数写进材质 patch；返回 false = 这个字段名不认识（调用方已经报过错）
inline bool fillMaterial(MaterialPatch& mp, const std::string& key, const std::vector<float>& n) {
    if (key == "albedo") {
        mp.albedo = toVec3(n);
        mp.hasAlbedo = true;
    } else if (key == "roughness") {
        mp.roughness = n[0];
        mp.hasRoughness = true;
    } else if (key == "metallic") {
        mp.metallic = n[0];
        mp.hasMetallic = true;
    } else if (key == "emissive") {
        mp.emissive = toVec3(n);
        mp.hasEmissive = true;
    } else if (key == "uvscale") {
        mp.uvScale = Vec2{n[0], n[1]};
        mp.hasUvScale = true;
    } else {
        return false;
    }
    return true;
}

inline bool fillLight(LightPatch& lp, const std::string& key, const std::vector<float>& n) {
    if (key == "pos") {
        lp.pos = toVec3(n);
        lp.hasPos = true;
    } else if (key == "color") {
        lp.color = toVec3(n);
        lp.hasColor = true;
    } else if (key == "intensity") {
        lp.intensity = n[0];
        lp.hasIntensity = true;
    } else if (key == "radius") {
        lp.radius = n[0];
        lp.hasRadius = true;
    } else {
        return false;
    }
    return true;
}

// 解析器本体：拿着 token 流往前走，出错就记一行带行号的文字，然后尽量继续往下读，
// 这样学生一次能看到全部错误，而不是改一个跑一次、改一个跑一次。
class Parser {
public:
    Parser(const std::string& src, const std::string& file) : file_(file), toks_(tokenize(src)) {}

    ContentPatch run() {
        while (pos_ < toks_.size()) {
            const Token t = toks_[pos_];
            if (t.text == "}") {
                err(t.line, "多了一个 }（前面没有对应的 material / light 块）");
                ++pos_;
            } else if (t.text == "material" || t.text == "light") {
                parseBlock(t.text == "material");
            } else if (fieldArity(kTopFields(), kTopFieldCount, t.text) >= 0) {
                parseTopScalar();
            } else {
                const std::string s = suggest(t.text, fieldNames(kTopFields(), kTopFieldCount));
                err(t.line, "不认识的顶层关键字 \"" + t.text + "\"" +
                                (s.empty() ? "（这里能写 material / light / ambient / sky / ground）"
                                           : "（是不是想写 " + s + "？）"));
                ++pos_;
            }
        }
        return std::move(patch_);
    }

private:
    void err(int line, const std::string& msg) {
        patch_.errors.push_back(file_ + ":" + std::to_string(line) + ": " + msg);
    }

    std::string tokText(size_t i) const { return i < toks_.size() ? toks_[i].text : std::string(); }
    int tokLine(size_t i) const { return i < toks_.size() ? toks_[i].line : lastLine(); }
    int lastLine() const { return toks_.empty() ? 1 : toks_.back().line; }

    // material <名字> { ... }   /   light <灯号> { ... }
    void parseBlock(bool isMaterial) {
        const std::string what = isMaterial ? "material" : "light";
        const int headLine = toks_[pos_].line;

        if (pos_ + 1 >= toks_.size()) {
            err(headLine, what + " 后面缺内容（要写：" + (isMaterial ? "material 材质名 {" : "light 灯号 {") + "）");
            pos_ = toks_.size();
            return;
        }
        const Token nameTok = toks_[pos_ + 1];
        if (nameTok.text == "{") {
            err(headLine, what + " 后面缺" + (isMaterial ? "材质名" : "灯号") + "，例如 " +
                              (isMaterial ? "material chrome {" : "light 0 {"));
            pos_ += 2;
            return;
        }
        if (tokText(pos_ + 2) != "{") {
            err(headLine, what + " " + nameTok.text + " 后面要跟一个 { （花括号）");
            pos_ += 2;  // 只吃掉名字，让下面的字段按顶层关键字继续报错
            return;
        }
        pos_ += 3;

        MaterialPatch mp;
        LightPatch lp;
        bool ownerOk = true;
        if (isMaterial) {
            mp.name = nameTok.text;
            mp.line = headLine;
        } else {
            lp.line = headLine;
            if (!parseInt(nameTok.text, lp.index)) {
                err(nameTok.line, "灯的编号要是一个非负整数，例如 light 0 {（0 = 第一盏灯）");
                ownerOk = false;
            }
        }

        const FieldSpec* specs = isMaterial ? kMaterialFields() : kLightFields();
        const int specCount = isMaterial ? kMaterialFieldCount : kLightFieldCount;
        const std::vector<std::string> names = fieldNames(specs, specCount);
        auto stop = [&](const std::string& t) { return numberStop(t) || fieldArity(specs, specCount, t) >= 0; };

        while (pos_ < toks_.size()) {
            if (toks_[pos_].text == "}") {
                ++pos_;
                if (ownerOk) {
                    if (isMaterial) {
                        patch_.materials.push_back(mp);
                    } else {
                        patch_.lights.push_back(lp);
                    }
                }
                return;
            }
            // 忘了写 } 就直接开下一个块：在这里收尾并报错，避免后面全线崩
            if (toks_[pos_].text == "material" || toks_[pos_].text == "light") {
                err(toks_[pos_].line, "上一个 " + what + " 块没有用 } 闭合");
                if (ownerOk) {
                    if (isMaterial) {
                        patch_.materials.push_back(mp);
                    } else {
                        patch_.lights.push_back(lp);
                    }
                }
                return;  // 外层接着处理这个 token
            }

            const Token key = toks_[pos_];
            const int arity = fieldArity(specs, specCount, key.text);
            ++pos_;

            // wrap / filter 走单独一条路：它们的值是词（repeat/clamp、nearest/bilinear），
            // 不是数字。列进 kMaterialFields 只是为了两件事：拼错时能被 suggest 想到、
            // 数字扫描时被当成"新字段的开头"停下来。
            if (isMaterial && (key.text == "wrap" || key.text == "filter")) {
                parseTextureWord(mp, key, specs, specCount);
                continue;
            }

            if (arity < 0) {
                const std::string s = suggest(key.text, names);
                err(key.line, "未知字段 \"" + key.text + "\"" +
                                  (s.empty() ? "" : "（是不是想写 " + s + "？）"));
                // 把它后面跟着的数字一并吃掉：那些本来就是它的值。
                // 不吞掉的话这里会再报一句「未知字段 "0.26"」—— 多出来的假错误比少报更伤人。
                float ignored = 0.0f;
                while (pos_ < toks_.size() && !stop(toks_[pos_].text) &&
                       parseNumber(toks_[pos_].text, ignored))
                    ++pos_;
                continue;
            }

            std::vector<float> nums;
            bool badNumber = false;
            for (int k = 0; k < arity && pos_ < toks_.size();) {
                if (stop(toks_[pos_].text)) break;
                float v = 0.0f;
                if (!parseNumber(toks_[pos_].text, v)) {
                    err(toks_[pos_].line, "\"" + toks_[pos_].text + "\" 不是数字（" + key.text + " 的第 " +
                                              std::to_string(k + 1) + " 个数）");
                    ++pos_;
                    badNumber = true;
                    break;
                }
                nums.push_back(v);
                ++pos_;
                ++k;
            }
            if (badNumber) continue;
            if (int(nums.size()) != arity) {
                err(key.line, key.text + " 需要 " + std::to_string(arity) + " 个数字，这里给了 " +
                                  std::to_string(nums.size()) + " 个");
                continue;
            }
            if (isMaterial) {
                fillMaterial(mp, key.text, nums);
            } else {
                fillLight(lp, key.text, nums);
            }
        }
        err(headLine, what + " " + nameTok.text + " 没有用 } 闭合（文件在这里就结束了）");
    }

    // wrap repeat / filter bilinear 这类"值是词"的字段。
    // 三条要求，一条都不能少：词要在取值表里；词写错要给"是不是想写 X"；
    // 值没写（下一个 token 已经是新字段或 }）要报"缺一个词"而不是把那个 token 吃掉
    // —— 吃掉的话，后面会连带报一串假错误，学生反而不知道错在哪。
    void parseTextureWord(MaterialPatch& mp, const Token& key, const FieldSpec* specs, int specCount) {
        const bool isWrap = key.text == "wrap";
        int optCount = 0;
        const WordOption* opts = isWrap ? wrapOptions(optCount) : filterOptions(optCount);
        const std::string what = isWrap ? "wrap" : "filter";
        auto allowed = [&]() {
            std::string s;
            for (int i = 0; i < optCount; ++i) {
                s += (i ? " 或 " : "");
                s += std::string(opts[i].word) + "（" + opts[i].note + "）";
            }
            return s;
        };

        if (pos_ >= toks_.size()) {
            err(key.line, what + " 后面缺一个词，要写 " + allowed());
            return;
        }
        const Token val = toks_[pos_];
        const bool structural = val.text == "{" || val.text == "}" || val.text == "material" ||
                                val.text == "light" || fieldArity(specs, specCount, val.text) >= 0;
        if (structural) {
            err(val.line, what + " 后面缺一个词，要写 " + allowed());
            return;
        }
        ++pos_;  // 到这里它一定是个"打算当值写"的 token，认不认得都收下

        float asNumber = 0.0f;
        if (parseNumber(val.text, asNumber)) {
            // 写成编号（wrap 1）是很自然的猜测 —— 单独给一句，别让他以为是自己手滑
            err(val.line, what + " 写的是词不是编号，要写 " + allowed() + "；这里是 \"" + val.text + "\"");
            return;
        }
        for (int i = 0; i < optCount; ++i) {
            if (val.text == opts[i].word) {
                if (isWrap) {
                    mp.wrapMode = opts[i].value;
                    mp.hasWrap = true;
                } else {
                    mp.filterMode = opts[i].value;
                    mp.hasFilter = true;
                }
                return;
            }
        }
        std::vector<std::string> words;
        for (int i = 0; i < optCount; ++i) words.push_back(opts[i].word);
        const std::string s = suggest(val.text, words);
        err(val.line, what + " 只认 " + allowed() + "；\"" + val.text + "\" 不认识" +
                          (s.empty() ? "" : "（是不是想写 " + s + "？）"));
    }

    // ambient 0.42 0.44 0.50 这类顶层单行数据
    void parseTopScalar() {
        const Token key = toks_[pos_];
        const int arity = fieldArity(kTopFields(), kTopFieldCount, key.text);
        ++pos_;
        std::vector<float> nums;
        bool badNumber = false;
        for (int k = 0; k < arity && pos_ < toks_.size();) {
            if (numberStop(toks_[pos_].text)) break;
            float v = 0.0f;
            if (!parseNumber(toks_[pos_].text, v)) {
                err(toks_[pos_].line, "\"" + toks_[pos_].text + "\" 不是数字（" + key.text + " 的第 " +
                                          std::to_string(k + 1) + " 个数）");
                ++pos_;
                badNumber = true;
                break;
            }
            nums.push_back(v);
            ++pos_;
            ++k;
        }
        if (badNumber) return;
        if (int(nums.size()) != arity) {
            err(key.line, key.text + " 需要 " + std::to_string(arity) + " 个数字，这里给了 " +
                              std::to_string(nums.size()) + " 个");
            return;
        }
        if (key.text == "ambient") {
            patch_.ambient = toVec3(nums);
            patch_.hasAmbient = true;
        } else if (key.text == "sky") {
            patch_.sky = toVec3(nums);
            patch_.hasSky = true;
        } else {
            patch_.ground = toVec3(nums);
            patch_.hasGround = true;
        }
    }

    std::string file_;
    std::vector<Token> toks_;
    size_t pos_ = 0;
    ContentPatch patch_;
};

}  // namespace detail

// ------------------------------------------------------------------ 公开实现

inline ContentPatch parseContent(const std::string& src, const std::string& fileName) {
    detail::Parser p(src, fileName);
    ContentPatch patch = p.run();
    patch.file = fileName;
    return patch;
}

inline bool readTextFile(const std::string& path, std::string& out) {
    out.clear();
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) return false;
    char buf[4096];
    size_t n = 0;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) out.append(buf, n);
    std::fclose(f);
    return true;
}

inline ContentPatch loadContentFile(const std::string& path) {
    std::string src;
    if (!readTextFile(path, src)) {
        ContentPatch p;
        p.file = path;
        p.errors.push_back(path + ": 打不开这个文件（要在仓库根目录下运行程序，路径是相对仓库根写的）");
        return p;
    }
    return parseContent(src, path);
}

namespace detail {

inline void emit(std::vector<std::string>* log, bool toStderr, const std::string& line) {
    if (log != nullptr) {
        log->push_back(line);
        return;
    }
    std::fprintf(toStderr ? stderr : stdout, "%s\n", line.c_str());
}

}  // namespace detail

inline ApplyStats applyContent(World& world, const ContentPatch& patch, std::vector<std::string>* log) {
    ApplyStats st;

    for (const MaterialPatch& mp : patch.materials) {
        const int idx = world.findMaterial(mp.name);
        if (idx < 0) {
            // 名字写错了：把可用的名字全列出来 —— 这正是学生最容易踩的坑
            std::string names;
            for (size_t i = 0; i < world.materialNames.size(); ++i)
                names += (i == 0 ? "" : " ") + world.materialNames[i];
            detail::emit(log, true, patch.file + ":" + std::to_string(mp.line) + ": 世界里没有叫 \"" + mp.name +
                                        "\" 的材质，这一块被跳过。可用的是：" + names);
            ++st.missing;
            continue;
        }
        Material& m = world.materials[size_t(idx)];
        if (m.locked) {
            // 上锁的材质：写了也不生效。这里必须出声 —— 悄悄忽略会让学生以为
            // "改动生效了但画面没变"，然后去怀疑渲染管线，而不是怀疑自己改错了地方。
            detail::emit(log, true, patch.file + ":" + std::to_string(mp.line) + ": \"" + mp.name +
                                        "\" 是关卡的标准答案，上着锁 —— content/ 改不动它。"
                                        "要改的是展台上那三个球（chrome / plastic / clay）。");
            ++st.locked;
            continue;
        }
        if (mp.hasAlbedo) m.albedo = mp.albedo;
        if (mp.hasRoughness) m.roughness = mp.roughness;
        if (mp.hasMetallic) m.metallic = mp.metallic;
        if (mp.hasEmissive) m.emissive = mp.emissive;
        if (mp.hasUvScale) m.uvScale = mp.uvScale;
        if (mp.hasWrap) m.wrapMode = mp.wrapMode;
        if (mp.hasFilter) m.filterMode = mp.filterMode;
        ++st.materials;
    }

    for (const LightPatch& lp : patch.lights) {
        if (lp.index >= world.lightCount) {
            detail::emit(log, true, patch.file + ":" + std::to_string(lp.line) + ": 场景里只有 " +
                                        std::to_string(world.lightCount) + " 盏灯，没有第 " +
                                        std::to_string(lp.index) + " 盏，这一块被跳过");
            ++st.missing;
            continue;
        }
        Light& l = world.lights[lp.index];
        if (lp.hasPos) l.position = lp.pos;
        if (lp.hasColor) l.color = lp.color;
        if (lp.hasIntensity) l.intensity = lp.intensity;
        if (lp.hasRadius) l.radius = lp.radius;
        ++st.lights;
    }

    bool env = false;
    if (patch.hasAmbient) {
        world.ambient = patch.ambient;
        env = true;
    }
    if (patch.hasSky) {
        world.skyColor = patch.sky;
        env = true;
    }
    if (patch.hasGround) {
        world.groundColor = patch.ground;
        env = true;
    }
    if (env) ++st.env;

    return st;
}

inline int applyContentFiles(World& world, const std::vector<std::string>& files, std::vector<std::string>* log) {
    int applied = 0;
    for (const std::string& path : files) {
        const ContentPatch patch = loadContentFile(path);
        if (!patch.ok()) {
            for (const std::string& e : patch.errors) detail::emit(log, true, e);
            detail::emit(log, true, "  → " + path + " 有 " + std::to_string(patch.errors.size()) +
                                        " 处错误，本次不做任何改动（其它文件照常生效）");
            continue;
        }
        const ApplyStats st = applyContent(world, patch, log);
        detail::emit(log, false, "[content] " + path + "：材质 " + std::to_string(st.materials) + " 项，灯 " +
                                     std::to_string(st.lights) + " 项" +
                                     (st.env ? "，环境色 3 项" : "") +
                                     (st.missing > 0 ? "，" + std::to_string(st.missing) + " 项没对上号" : ""));
        ++applied;
    }
    return applied;
}

}  // namespace dlab
