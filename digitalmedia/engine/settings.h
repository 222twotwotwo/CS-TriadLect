// 画面设置（分辨率 / 窗口模式）。存在 saved/settings.txt。
//
// 为什么和 save.bin 分开存：--reset 的语义是"清进度"，不该顺手把人家调好的
// 窗口尺寸也清了。两件事各管各的。
//
// 文件是纯文本 key value，和 content/*.txt 一个味道 —— 人和机器都好读，
// 写坏了删掉就回默认，不会把游戏弄打不开。
#pragma once

#include <cstdio>
#include <cstdlib>
#include <string>

#include "../core/platform.h"

namespace dlab {

// 和 save.bin 放同一个目录，但是**两个文件** —— --reset 只清进度，不动显示设置。
constexpr const char* kSettingsPath = "saved/settings.txt";

// 分辨率只放 16:9 的三档，而且**封顶 720p**：
//   · 16:9 是因为全屏走的是"拉伸铺满"，非 16:9 会被拉变形；
//   · 720p 封顶是因为这是纯 CPU 软渲染，再往上帧率撑不住。
// 附带一个好处：在 2560x1440 的屏幕上，720p 是整齐的 2.00x、480p 是 3.00x，
// 最近邻放大出来每个像素一样大（540p 是 2.667x，有的像素 2 宽有的 3 宽）。
struct Resolution {
    int w;
    int h;
};

constexpr Resolution kResolutions[] = {{854, 480}, {960, 540}, {1280, 720}};
constexpr int kResolutionCount = int(sizeof(kResolutions) / sizeof(kResolutions[0]));

// 最接近 (w,h) 的那一档的下标。用来把"按核数自适应"算出来的尺寸收进这张表
// （比如 1600x900 会被收成 1280x720 —— 这就是"启动默认也封顶 720p"的落点）。
inline int nearestResolutionIndex(int w, int h) {
    int best = 0;
    long long bestDiff = -1;
    for (int i = 0; i < kResolutionCount; ++i) {
        const long long dw = kResolutions[i].w - w;
        const long long dh = kResolutions[i].h - h;
        const long long diff = dw * dw + dh * dh;
        if (bestDiff < 0 || diff < bestDiff) {
            bestDiff = diff;
            best = i;
        }
    }
    return best;
}

inline const char* windowModeWord(WindowMode m) {
    switch (m) {
        case WindowMode::Windowed: return "windowed";
        case WindowMode::Borderless: return "borderless";
        case WindowMode::Fullscreen: return "fullscreen";
    }
    return "windowed";
}

inline bool parseWindowMode(const std::string& s, WindowMode& out) {
    if (s == "windowed") { out = WindowMode::Windowed; return true; }
    if (s == "borderless") { out = WindowMode::Borderless; return true; }
    if (s == "fullscreen") { out = WindowMode::Fullscreen; return true; }
    return false;
}

struct Settings {
    WindowMode mode = WindowMode::Windowed;
    int width = 0;   // 0 = 让调用方按核数自适应（会被 nearestResolutionIndex 收进表里）
    int height = 0;
};

// 读。文件不在 / 读不动 / 有认不出的字段 —— 一律回默认值并返回 false。
// **绝不因为一个设置文件坏了就打不开游戏** —— 和 save.bin 同一条策略。
// 注意：认不出的字段算"读失败"，但已经认出来的字段照样生效（不让一个错字废掉整份）。
inline bool loadSettings(const std::string& path, Settings& out) {
    out = Settings{};
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) return false;

    bool ok = true;
    char line[256];
    while (std::fgets(line, sizeof(line), f) != nullptr) {
        std::string s(line);
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
        if (s.empty() || s[0] == '#') continue;
        const size_t sp = s.find(' ');
        if (sp == std::string::npos) {
            ok = false;  // 只有键没有值
            continue;
        }
        const std::string key = s.substr(0, sp);
        std::string val = s.substr(sp + 1);
        while (!val.empty() && val[0] == ' ') val.erase(val.begin());

        if (key == "mode") {
            if (!parseWindowMode(val, out.mode)) ok = false;
        } else if (key == "width") {
            out.width = std::atoi(val.c_str());
        } else if (key == "height") {
            out.height = std::atoi(val.c_str());
        } else {
            ok = false;  // 认不出的键
        }
    }
    std::fclose(f);
    return ok;
}

inline bool saveSettings(const Settings& s, const std::string& path) {
    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (f == nullptr) return false;
    std::fprintf(f, "# dreamlab-rt 的画面设置。删掉它 = 回到默认。\n");
    std::fprintf(f, "mode    %s\n", windowModeWord(s.mode));
    std::fprintf(f, "width   %d\n", s.width);
    std::fprintf(f, "height  %d\n", s.height);
    std::fclose(f);
    return true;
}

}  // namespace dlab
