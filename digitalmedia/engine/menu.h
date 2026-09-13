// 暂停菜单。和 engine/console.h 一个分工：**只管绘制与命中判定，不碰任何平台细节**，
// 显示什么由外面喂进来（MenuModel），点了什么交出去（MenuAction）。
//
// 布局全部按 framebuffer 尺寸现算，不缓存 —— 这样切分辨率之后排版自动跟上，
// 不会出现"改了分辨率菜单跑到屏幕外面去"。
#pragma once

#include <string>

#include "../core/font.h"
#include "../core/framebuffer.h"
#include "settings.h"

namespace dlab {

// 菜单要显示的、来自游戏的那部分（只读）
struct MenuModel {
    const char* levelTitle = "";
    const char* levelGoal = "";
    int progressPercent = 0;
    bool passed = false;
};

// 这一帧用户点了什么。没有动作就是 None。
struct MenuAction {
    enum Kind { None, Resume, Quit, Restart, SetResolution, SetMode };
    Kind kind = None;
    int value = 0;  // SetResolution: 分辨率档位下标；SetMode: int(WindowMode)
};

class Menu {
public:
    // 行序是固定的。空行（分隔线）也占一行，这样坐标换算简单、命中不会错位。
    static constexpr int kRowResume = 0;
    static constexpr int kRowResolution = 1;
    static constexpr int kRowMode = 2;
    static constexpr int kRowBlank1 = 3;
    static constexpr int kRowTitle = 4;
    static constexpr int kRowGoal = 5;
    static constexpr int kRowProgress = 6;
    static constexpr int kRowBlank2 = 7;
    static constexpr int kRowRestart = 8;
    static constexpr int kRowQuit = 9;
    static constexpr int kRowCount = 10;

    // 面板与行的几何。抽出来是为了让**绘制和命中用同一套坐标** ——
    // 各算各的迟早会出现"看着点到了、其实没中"。
    struct Layout {
        int panelX = 0;
        int panelY = 0;
        int panelW = 0;
        int panelH = 0;
        int rowH = 0;
        int padX = 0;
        int topPad = 0;    // 面板顶到"暂停"标题之间
        int titleH = 0;    // "暂停"是 2 倍字号，高度要单独留
        int titleGap = 0;  // 标题到第一行之间
        int hintGap = 0;   // 最后一行到"按 Esc 回到游戏"之间
        int hintH = 0;
        int botPad = 0;
    };
    static Layout computeLayout(int fbW, int fbH, const Font& font);
    static int rowTop(const Layout& L, int row);
    static int hintTop(const Layout& L);

    // 点 (mx,my) 落在第几行；没点中返回 -1。纯函数，可自检。
    static int rowAt(float mx, float my, int fbW, int fbH, const Font& font);

    bool visible() const { return visible_; }
    // 关掉菜单就把「从头开始」的待命撤掉：下次打开时它是干净的，
    // 不会出现"一进菜单随手点一下就真清空了"。
    void setVisible(bool on) {
        visible_ = on;
        if (!on) armedRestart_ = false;
    }
    void setModel(const MenuModel& m) { model_ = m; }
    void setDisplay(int resIndex, WindowMode mode) {
        resIndex_ = resIndex;
        mode_ = mode;
    }
    // 当前鼠标悬停在第几行（画高亮用）；-1 = 没悬停
    int hoverRow() const { return hoverRow_; }
    // 「从头开始」是否已经点过一次（待命中）—— 自检要看它
    bool restartArmed() const { return armedRestart_; }

    MenuAction update(const FrameInput& in, int fbW, int fbH, const Font& font);
    void draw(Framebuffer& fb, const Font& font) const;

    // 菜单里那两行"◀ 值 ▶"的左右箭头命中区。点击落在同一个行内时，
    // 靠这个再分一次左右 —— 命中区**就是画 ◀ ▶ 的位置**，和看到的一致。
    static void arrowRects(const Layout& L, int row, int fbW, int& prevX, int& nextX, int& arrowW);

private:
    bool visible_ = false;
    MenuModel model_;
    int resIndex_ = 2;                       // 默认停在 1280x720（封顶那档）
    WindowMode mode_ = WindowMode::Windowed;
    int hoverRow_ = -1;
    bool armedRestart_ = false;  // 「从头开始」是否已待命（见 update() 里那段）

    static void rect(Framebuffer& fb, int x, int y, int w, int h, Vec3 c, float alpha);
    static const char* modeLabel(WindowMode m);
};

// ---------------------------------------------------------------- 实现

inline Menu::Layout Menu::computeLayout(int fbW, int fbH, const Font& font) {
    Layout L;
    L.panelW = 460;
    if (L.panelW > fbW - 40) L.panelW = fbW - 40;   // 小窗口下别超出画面
    L.rowH = font.lineHeight() * 2;
    L.padX = 18;
    L.topPad = 12;
    L.titleH = font.lineHeight() * 2;  // "暂停"用 2 倍字号画
    L.titleGap = 12;
    L.hintGap = 10;
    L.hintH = font.lineHeight();
    L.botPad = 12;
    // 高度是**算出来的**，不是"行数 × 行高 + 一个拍脑袋的常数"——
    // 上一版就是后者，结果标题压在"继续游戏"上、底部提示压在"退出游戏"上。
    L.panelH = L.topPad + L.titleH + L.titleGap + L.rowH * kRowCount + L.hintGap + L.hintH +
               L.botPad;
    if (L.panelH > fbH - 20) L.panelH = fbH - 20;
    L.panelX = (fbW - L.panelW) / 2;
    L.panelY = (fbH - L.panelH) / 2;
    return L;
}

inline int Menu::rowTop(const Layout& L, int row) {
    return L.panelY + L.topPad + L.titleH + L.titleGap + row * L.rowH;
}

inline int Menu::hintTop(const Layout& L) {
    return L.panelY + L.panelH - L.botPad - L.hintH;
}

inline int Menu::rowAt(float mx, float my, int fbW, int fbH, const Font& font) {
    const Layout L = computeLayout(fbW, fbH, font);
    if (mx < float(L.panelX) || mx >= float(L.panelX + L.panelW)) return -1;
    if (my < float(L.panelY) || my >= float(L.panelY + L.panelH)) return -1;
    for (int r = 0; r < kRowCount; ++r) {
        const float top = float(rowTop(L, r));
        if (my >= top && my < top + float(L.rowH)) return r;
    }
    return -1;
}

inline void Menu::arrowRects(const Layout& L, int row, int fbW, int& prevX, int& nextX, int& arrowW) {
    (void)fbW;
    arrowW = 30;
    prevX = L.panelX + L.panelW - L.padX - 260;
    nextX = L.panelX + L.panelW - L.padX - arrowW;
}

inline const char* Menu::modeLabel(WindowMode m) {
    switch (m) {
        case WindowMode::Windowed: return "窗口";
        case WindowMode::Borderless: return "无边框";
        case WindowMode::Fullscreen: return "全屏";
    }
    return "窗口";
}

inline void Menu::rect(Framebuffer& fb, int x, int y, int w, int h, Vec3 c, float alpha) {
    for (int gy = 0; gy < h; ++gy)
        for (int gx = 0; gx < w; ++gx) fb.blendPixel(x + gx, y + gy, c, alpha);
}

inline MenuAction Menu::update(const FrameInput& in, int fbW, int fbH, const Font& font) {
    MenuAction act;
    if (!visible_) {
        hoverRow_ = -1;
        return act;
    }

    hoverRow_ = rowAt(in.mouseX, in.mouseY, fbW, fbH, font);
    if (!in.mousePressed || hoverRow_ < 0) return act;

    // 点了别的行就把「从头开始」的待命状态取消 —— 否则"点一下、去做点别的、
    // 过一会儿又点回来"会把一次误触变成真正的清空。
    if (hoverRow_ != kRowRestart) armedRestart_ = false;

    const Layout L = computeLayout(fbW, fbH, font);
    switch (hoverRow_) {
        case kRowResume:
            act.kind = MenuAction::Resume;
            break;
        case kRowQuit:
            act.kind = MenuAction::Quit;
            break;
        case kRowRestart:
            // 破坏性操作，两次点击才生效：第一次只是"待命"，标签会变、变红，
            // 第二次才真的做。一键抹掉所有改动不该是"手滑就到"的事。
            if (armedRestart_) {
                armedRestart_ = false;
                act.kind = MenuAction::Restart;
            } else {
                armedRestart_ = true;
            }
            break;
        case kRowResolution: {
            int prevX = 0, nextX = 0, aw = 0;
            arrowRects(L, kRowResolution, fbW, prevX, nextX, aw);
            const float mx = in.mouseX;
            if (mx >= float(prevX) && mx < float(prevX + aw)) {
                act.kind = MenuAction::SetResolution;
                act.value = (resIndex_ + kResolutionCount - 1) % kResolutionCount;
            } else if (mx >= float(nextX) && mx < float(nextX + aw)) {
                act.kind = MenuAction::SetResolution;
                act.value = (resIndex_ + 1) % kResolutionCount;
            }
            break;
        }
        case kRowMode: {
            int prevX = 0, nextX = 0, aw = 0;
            arrowRects(L, kRowMode, fbW, prevX, nextX, aw);
            const float mx = in.mouseX;
            const int cur = int(mode_);
            if (mx >= float(prevX) && mx < float(prevX + aw)) {
                act.kind = MenuAction::SetMode;
                act.value = (cur + 2) % 3;
            } else if (mx >= float(nextX) && mx < float(nextX + aw)) {
                act.kind = MenuAction::SetMode;
                act.value = (cur + 1) % 3;
            }
            break;
        }
        default:
            break;  // 分隔线与进度块不响应点击
    }
    return act;
}

inline void Menu::draw(Framebuffer& fb, const Font& font) const {
    if (!visible_) return;

    const Vec3 white{2.2f, 2.2f, 2.25f};
    const Vec3 dim{1.2f, 1.2f, 1.25f};
    const Vec3 strong{2.6f, 2.6f, 2.7f};
    const Vec3 panel{0.02f, 0.025f, 0.04f};
    const Vec3 hot{0.35f, 0.55f, 0.85f};

    const Layout L = computeLayout(fb.width, fb.height, font);

    // 整屏压暗一层，让"暂停"这件事看得出来
    rect(fb, 0, 0, fb.width, fb.height, Vec3{0.0f, 0.0f, 0.0f}, 0.55f);
    // 面板。**不透明** —— 留一点点透明度的话，正对终端站着时笔记本电脑的亮屏会
    // 从面板底下透出来一小块，正好落在面板中间，看着像画了个错东西。
    // 周围那一圈压暗已经足够表达"游戏暂停了"。
    rect(fb, L.panelX, L.panelY, L.panelW, L.panelH, panel, 1.0f);
    // 面板边框（细两条横线就够，别做花）
    rect(fb, L.panelX, L.panelY, L.panelW, 1, hot, 0.5f);
    rect(fb, L.panelX, L.panelY + L.panelH - 1, L.panelW, 1, hot, 0.5f);

    const int textX = L.panelX + L.padX;
    char buf[192];

    // 标题行
    font.drawLine(fb, textX, L.panelY + L.topPad, "暂停", strong, 1.0f, 2);

    auto rowColor = [&](int row) { return row == hoverRow_ ? strong : white; };
    auto highlight = [&](int row) {
        if (row != hoverRow_) return;
        rect(fb, L.panelX + 4, rowTop(L, row) - 2, L.panelW - 8, L.rowH - 2, hot, 0.16f);
    };

    // 0 继续游戏
    highlight(kRowResume);
    font.drawLine(fb, textX + 12, rowTop(L, kRowResume) + L.rowH / 3, "继续游戏", rowColor(kRowResume));

    // 1 分辨率
    highlight(kRowResolution);
    font.drawLine(fb, textX, rowTop(L, kRowResolution) + L.rowH / 3, "分辨率", dim);
    {
        int prevX = 0, nextX = 0, aw = 0;
        arrowRects(L, kRowResolution, fb.width, prevX, nextX, aw);
        const int y = rowTop(L, kRowResolution) + L.rowH / 3;
        font.drawLine(fb, prevX, y, "<", strong);
        std::snprintf(buf, sizeof(buf), "%d x %d", kResolutions[resIndex_].w, kResolutions[resIndex_].h);
        font.drawLine(fb, prevX + aw + 6, y, buf, rowColor(kRowResolution));
        font.drawLine(fb, nextX, y, ">", strong);
    }

    // 2 窗口模式
    highlight(kRowMode);
    font.drawLine(fb, textX, rowTop(L, kRowMode) + L.rowH / 3, "窗口模式", dim);
    {
        int prevX = 0, nextX = 0, aw = 0;
        arrowRects(L, kRowMode, fb.width, prevX, nextX, aw);
        const int y = rowTop(L, kRowMode) + L.rowH / 3;
        font.drawLine(fb, prevX, y, "<", strong);
        font.drawLine(fb, prevX + aw + 6, y, modeLabel(mode_), rowColor(kRowMode));
        font.drawLine(fb, nextX, y, ">", strong);
    }

    // 3 分隔线
    rect(fb, textX, rowTop(L, kRowBlank1) + L.rowH / 2, L.panelW - L.padX * 2, 1, dim, 0.35f);

    // 4/5 当前关卡与目标
    font.drawLine(fb, textX, rowTop(L, kRowTitle) + L.rowH / 3, model_.levelTitle, white);
    font.drawLine(fb, textX, rowTop(L, kRowGoal) + L.rowH / 3, model_.levelGoal, dim);

    // 6 进度条
    {
        const int y = rowTop(L, kRowProgress) + L.rowH / 3;
        const int barW = 220;
        const int barH = font.glyphH();
        const int filled = barW * model_.progressPercent / 100;
        rect(fb, textX, y + 2, barW, barH - 4, Vec3{0.10f, 0.10f, 0.12f}, 1.0f);
        if (filled > 0) rect(fb, textX, y + 2, filled, barH - 4, hot, 0.9f);
        std::snprintf(buf, sizeof(buf), "%d%%", model_.progressPercent);
        font.drawLine(fb, textX + barW + 12, y, buf, model_.passed ? strong : dim);
    }

    // 7 分隔线
    rect(fb, textX, rowTop(L, kRowBlank2) + L.rowH / 2, L.panelW - L.padX * 2, 1, dim, 0.35f);

    // 8 从头开始。破坏性操作，所以：待命之后**标签会变**（说清会清掉什么、要再点一次），
    //   颜色也从普通白变成警示色。一键抹掉所有改动不该是"手滑就到"的事。
    {
        const Vec3 warn{2.6f, 0.95f, 0.80f};
        highlight(kRowRestart);
        const char* label = armedRestart_ ? "再点一次确认：清空进度 + 还原所有改动"
                                          : "从头开始（清空进度和文件改动）";
        font.drawLine(fb, textX + 12, rowTop(L, kRowRestart) + L.rowH / 3, label,
                      armedRestart_ ? warn : rowColor(kRowRestart));
    }

    // 9 退出游戏
    highlight(kRowQuit);
    font.drawLine(fb, textX + 12, rowTop(L, kRowQuit) + L.rowH / 3, "退出游戏", rowColor(kRowQuit));

    // 底部一句提示，免得有人不知道怎么回游戏
    font.drawLine(fb, textX, hintTop(L), "按 Esc 回到游戏", dim);
}

}  // namespace dlab
