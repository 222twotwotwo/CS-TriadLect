#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成入群二维码素材：assets/recruit/qr.bin

为什么存成"模块数×模块数"的 1-bit 位图，而不是原图？
  · 二维码只有黑和白两种"像素"——它的一格叫一个 module，一格里有几百个图片像素
    是纯属浪费。把图缩到 **一格一个 bit**，752×752 的原图就变成 45×45 ≈ 253 字节。
  · 更重要的是**画质**：运行时按整数倍最近邻放大，每格 module 正好 N×N 个屏幕像素，
    边缘绝对干净。如果直接缩原图，格子会落在半个像素上，边缘发虚 —— 手机扫不出来。
    这条是这一整个工具存在的理由。

为什么要自动识别模块数，而不是写死？
  入群二维码 **会过期**（微信的群二维码有有效期），每次招新前都得换一张新的。
  新图未必是同尺寸、未必是同一个 module 数。所以这里从三个定位角反推出
  "一格几像素"，再算出模块数 —— 换图直接重跑，不用改代码。

为什么是 XOR 混淆而不是加密？
  **它不是加密，别当保密手段用。** 密钥必须编译进二进制里，谁真想解都能解。
  它挡的只是"在 GitHub 上点开 assets/recruit/qr.bin 看一眼"的人。
  真正的门槛是"你得先把四关打完"——那是个游戏设计，不是安全边界。

用法:
    python tools/gen_recruit_qr.py --src <二维码图>      # 生成 assets/recruit/qr.bin
    python tools/gen_recruit_qr.py --src <图> --probe    # 顺便把识别出的点阵打成 ASCII 图
    python tools/gen_recruit_qr.py --src <图> --crop x,y,w,h   # 自动识别不准时手动指定

依赖: Pillow   (python -m pip install pillow)
      只有维护者需要跑；学生拉下仓库直接用生成好的 .bin。
"""

import argparse
import os
import struct
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("[qr] 需要 Pillow：python -m pip install pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_BIN = os.path.join(ROOT, "assets", "recruit", "qr.bin")

MAGIC = b"DLQ1"
VERSION = 1

# 混淆密钥。再说一遍：这是 **混淆**，不是加密 —— 它就写在这份源码里。
# 换掉它会让旧的 qr.bin 失效，两边必须同时改（这边 + engine/recruit.h 的 kMask）。
KEY = b"dreamlab-rt/recruit-qr/v1"


# ----------------------------------------------------------------- 二值化

def binarize(img):
    """灰度 + Otsu 阈值 → 二维 bool 列表（True = 黑）。"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()

    # Otsu：把直方图分成两堆，让两堆之间的方差最大。二维码是纯黑白，
    # 直方图基本是双峰的，Otsu 一定找得到峰之间那道沟。
    hist = [0] * 256
    for y in range(h):
        for x in range(w):
            hist[px[x, y]] += 1
    total = w * h
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    w_b = 0
    best_t, best_var = 128, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    print("[qr] Otsu 阈值 = {}".format(best_t))

    return [[px[x, y] <= best_t for x in range(w)] for y in range(h)], w, h


# ------------------------------------------------- 定位角（1:1:3:1:1 游程）

def _runs(line):
    """把一条线压成游程 [(起, 长, 是否黑), ...]。"""
    out = []
    i = 0
    n = len(line)
    while i < n:
        b = line[i]
        j = i
        while j < n and line[j] == b:
            j += 1
        out.append((i, j - i, b))
        i = j
    return out


def _ratio_ok(lens, ms):
    """五个游程是不是 1:1:3:1:1（容差 0.6 格）。"""
    for got, want in zip(lens, (1, 1, 3, 1, 1)):
        if abs(got - want * ms) > ms * 0.6:
            return False
    return True


def _scan_line(line, limit):
    """在一条线上找 1:1:3:1:1，返回 [(中心, 单格像素), ...]。"""
    hits = []
    rs = _runs(line)
    for i in range(len(rs) - 4):
        a, b, c, d, e = rs[i:i + 5]
        # 必须是 黑 白 黑 白 黑
        if not (a[2] and not b[2] and c[2] and not d[2] and e[2]):
            continue
        total = a[1] + b[1] + c[1] + d[1] + e[1]
        ms = total / 7.0
        if ms < 1.0 or total > limit:
            continue
        if _ratio_ok((a[1], b[1], c[1], d[1], e[1]), ms):
            hits.append((a[0] + total / 2.0, ms))
    return hits


def find_finders(bits, w, h):
    """
    找三个定位角。返回 [(cx, cy, 单格像素), ...]，失败返回 None。

    先在每一行扫水平方向的 1:1:3:1:1，命中之后再沿着那一列**竖直方向**再验一次 ——
    只要一边像是不够的（数据区偶尔也会凑出这个比例），两边都像才算数。
    """
    cands = []
    for y in range(h):
        for cx, ms in _scan_line(bits[y], limit=w):
            col = [bits[yy][int(cx)] for yy in range(h)]
            for cy, ms2 in _scan_line(col, limit=h):
                if abs(cy - y) <= ms2 * 2.5 and abs(ms2 - ms) <= ms * 0.6:
                    cands.append((cx, cy, (ms + ms2) / 2.0))
    if not cands:
        return None

    # 同一个定位角会在很多行上被重复命中 —— 按"中心距离小于一格"聚成一堆。
    clusters = []
    for cx, cy, ms in cands:
        for cl in clusters:
            if abs(cl["x"] / cl["n"] - cx) < ms * 2 and abs(cl["y"] / cl["n"] - cy) < ms * 2:
                cl["x"] += cx
                cl["y"] += cy
                cl["ms"] += ms
                cl["n"] += 1
                break
        else:
            clusters.append({"x": cx, "y": cy, "ms": ms, "n": 1})

    clusters = [c for c in clusters if c["n"] >= 3]  # 太少的当噪声
    if len(clusters) < 3:
        return None
    clusters.sort(key=lambda c: -c["n"])
    return [(c["x"] / c["n"], c["y"] / c["n"], c["ms"] / c["n"]) for c in clusters[:3]]


def qr_geometry(finders):
    """
    三个定位角 → (左上角像素坐标, 单格像素, 模块数)。

    定位角的**中心**落在整个二维码的第 (3.5, 3.5) 格，所以往回退 3.5 格就是边界。
    模块数 = 两个定位角中心之间的距离 / 单格 + 7（两头各半个定位角）。
    """
    # 三个点里，和另外两个都离得远的那个是右上（或左下）；用"哪两个 y 最接近"分成顶行和左下。
    order = sorted(finders, key=lambda f: f[1])
    (y1, y2, y3) = order
    if abs(y1[1] - y2[1]) <= abs(y2[1] - y3[1]):
        top, sing = [y1, y2], y3
    else:
        top, sing = [y2, y3], y1
    top.sort(key=lambda f: f[0])          # 顶行里 x 小的 = 左上
    tl, tr = top[0], top[1]
    bl = sing

    ms = (tl[2] + tr[2] + bl[2]) / 3.0

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    # 顶行两个中心之间隔 (模块数 - 7) 格；取整到最近的奇数。
    n_from_top = dist(tl, tr) / ms + 7
    n_from_left = dist(tl, bl) / ms + 7
    n = int(round((n_from_top + n_from_left) / 2.0))
    if n % 2 == 0:
        n += 1

    x0 = tl[0] - 3.5 * ms
    y0 = tl[1] - 3.5 * ms
    return x0, y0, ms, n


# --------------------------------------------------------------- 采样/校验

def sample(bits, x0, y0, ms, n):
    """按格心采样成 n×n 的 1-bit。格心 = 边界 + (i+0.5) 格。"""
    out = []
    for r in range(n):
        row = []
        cy = int(round(y0 + (r + 0.5) * ms))
        for c in range(n):
            cx = int(round(x0 + (c + 0.5) * ms))
            row.append(bits[cy][cx])
        out.append(row)
    return out


def check_finders(grid):
    """
    采样出来的 45×45 里，三个角必须是标准的 7×7 定位角图形。
    对不上就说明模块数或边界猜错了 —— **宁可报错退出，也不能悄悄产出一个扫不出来的码**。
    """
    expected = [
        [1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1],
    ]
    n = len(grid)
    bad = []
    for name, ox, oy in (("左上", 0, 0), ("右上", n - 7, 0), ("左下", 0, n - 7)):
        for r in range(7):
            for c in range(7):
                if grid[oy + r][ox + c] != bool(expected[r][c]):
                    bad.append((name, r, c))
    return bad


# ------------------------------------------------------------------ 输出

def pack(grid):
    """n×n 的 1-bit → 逐行、行尾补整字节、MSB 在前。"""
    n = len(grid)
    stride = (n + 7) // 8
    blob = bytearray()
    for r in range(n):
        for byte_i in range(stride):
            v = 0
            for bit in range(8):
                c = byte_i * 8 + bit
                if c < n and grid[r][c]:
                    v |= 0x80 >> bit
            blob.append(v)
    return bytes(blob), stride


def mask(data):
    """XOR 混淆（不是加密，见文件头）。"""
    return bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="二维码图片（微信导出的群二维码，jpg/png 都行）")
    ap.add_argument("--out", default=OUT_BIN, help="输出路径，默认 assets/recruit/qr.bin")
    ap.add_argument("--crop", help="手动指定裁剪区 x,y,w,h（自动识别不准时用）")
    ap.add_argument("--probe", action="store_true", help="把识别出的点阵打成 ASCII 图")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        sys.exit("[qr] 找不到源图：{}".format(args.src))

    img = Image.open(args.src)
    print("[qr] 源图 {}  {}".format(args.src, img.size))

    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        img = img.crop((x, y, x + w, y + h))
        print("[qr] 手动裁剪 → {}".format(img.size))

    bits, w, h = binarize(img)
    print("[qr] 二值化 {}x{}".format(w, h))

    finders = find_finders(bits, w, h)
    if finders is None:
        sys.exit("[qr] 没找到三个定位角。用 --crop 手动指定二维码所在的方块，再跑一次。")
    for i, (cx, cy, ms) in enumerate(finders):
        print("[qr] 定位角 {} 中心 ({:.1f}, {:.1f})  单格 {:.2f}px".format(i, cx, cy, ms))

    x0, y0, ms, n = qr_geometry(finders)
    print("[qr] 二维码边界 ({:.1f}, {:.1f})  单格 {:.2f}px  →  {}×{} 模块".format(x0, y0, ms, n, n))

    grid = sample(bits, x0, y0, ms, n)
    bad = check_finders(grid)
    if bad:
        preview = ", ".join("{}{}/{}".format(name, r, c) for name, r, c in bad[:6])
        sys.exit("[qr] 三个定位角对不上（{} 处不符：{}...）。\n"
                 "[qr] 说明模块数或边界猜错了 —— 用 --crop 手动指定，或用 --probe 看识别结果。"
                 .format(len(bad), preview))

    holes = sum(1 for r in grid for v in r if v)
    print("[qr] 黑格 {} / {}（{:.0f}%）".format(holes, n * n, 100.0 * holes / (n * n)))
    if holes == 0 or holes == n * n:
        sys.exit("[qr] 整张图全黑或全白 —— 源图不对，或者阈值不对。")

    if args.probe:
        print("[qr] 点阵预览（██ = 黑）：")
        for row in grid:
            # 两个字符一格，终端里才是方的
            print("    " + "".join("██" if v else "  " for v in row))

    data, stride = pack(grid)
    body = struct.pack("<4sIII", MAGIC, VERSION, n, n) + mask(data)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(body)
    print("[qr] 写出 {}（{}×{} 模块，{} 字节，混淆后）".format(args.out, n, n, len(body)))

    # 立刻读回来解一遍，确认写出去的和算出来的是一致的 —— 这一步不做的话，
    # 混淆里写错一个下标，要等到游戏里看二维码才发现。
    with open(args.out, "rb") as f:
        back = f.read()
    magic, ver, bw, bh = struct.unpack("<4sIII", back[:16])
    assert magic == MAGIC and ver == VERSION and (bw, bh) == (n, n), "回读的文件头不对"
    assert mask(back[16:]) == data, "回读的数据对不上"
    print("[qr] 回读校验通过")


if __name__ == "__main__":
    main()
