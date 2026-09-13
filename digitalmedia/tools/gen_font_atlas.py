#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成中文像素字模图集：assets/font/pixel12.bin

为什么不用 TrueType / 不用 PNG 存字模？
  · 直接在 C++ 里解析 TrueType 要写轮廓填充 + 抗锯齿，几千行，还容易出 bug；
  · 用 PNG 存字模就得在引擎里塞一个 inflate 解码器。
  所以这里的做法是：**在构建期**把字渲染成 1-bit 位图，存成自定义二进制格式；
  引擎侧只需要 fread + 二分查找，零解码依赖。

源字体：缝合像素字体 Fusion Pixel 12px（SIL OFL-1.1），字体文件不入库，
        脚本自动从 npm 镜像拉取并缓存到 tools/.cache/。

用法:
    python tools/gen_font_atlas.py            # 生成 assets/font/pixel12.bin
    python tools/gen_font_atlas.py --probe    # 只打印几个字的点阵，用来肉眼校验

依赖: Pillow, fonttools   (pip install pillow fonttools)
      —— 只有维护者需要跑这个脚本，学生拉下仓库直接用生成好的 .bin。
"""

import argparse
import io
import os
import struct
import sys
import tarfile
import urllib.request

FONT_PKG = "@fontsource/fusion-pixel-12px-proportional-sc"
FONT_VER = "5.3.0"
TARBALL_NAME = "fusion-pixel-12px-proportional-sc-{}.tgz".format(FONT_VER)
MIRRORS = [
    "https://registry.npmmirror.com/{pkg}/-/{name}",
    "https://registry.npmjs.org/{pkg}/-/{name}",
]

GLYPH_W = 12
GLYPH_H = 12
BYTES_PER_GLYPH = (GLYPH_W * GLYPH_H + 7) // 8  # = 18
MAGIC = b"DLF1"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE_DIR = os.path.join(HERE, ".cache")
OUT_BIN = os.path.join(ROOT, "assets", "font", "pixel12.bin")
OUT_LICENSE = os.path.join(ROOT, "assets", "font", "LICENSE")

# ASCII 之外额外补的符号：控制台提示、关卡目标里的箭头和标点
EXTRA_SYMBOLS = (
    "→←↑↓↔×÷°≈≠≤≥±∞√"
    "·—…※★☆♦♥✓✗"
    "“”‘’「」『』《》〈〉【】〔〕"
    "、。，；：？！（）－＿／＼｜"
    "①②③④⑤⑥⑦⑧⑨⑩"
)


# --------------------------------------------------------------------------- 取字体

def fetch_ttf():
    """下载并缓存源字体，转换成 ttf 后返回路径。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    ttf_path = os.path.join(CACHE_DIR, "fusion-pixel-12px-proportional-sc.ttf")
    if os.path.exists(ttf_path):
        print("[font] 复用缓存: {}".format(ttf_path))
        return ttf_path

    blob = None
    for template in MIRRORS:
        url = template.format(pkg=FONT_PKG, name=TARBALL_NAME)
        try:
            print("[font] 下载 {}".format(url))
            with urllib.request.urlopen(url, timeout=60) as resp:
                blob = resp.read()
            print("[font] 收到 {} 字节".format(len(blob)))
            break
        except Exception as exc:  # noqa: BLE001 - 镜像逐个回退，打印原因即可
            print("[font] 失败: {}".format(exc))
    if blob is None:
        sys.exit("[font] 所有镜像都拉不到字体，请手动下载后放到 tools/.cache/")

    woff_member = None
    license_text = None
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            base = os.path.basename(member.name)
            # woff1 不需要 brotli，woff2 才需要 —— 优先选 woff1
            if base.endswith(".woff") and woff_member is None:
                woff_member = member
            if base.upper().startswith("LICENSE") and license_text is None:
                extracted = tar.extractfile(member)
                if extracted is not None:
                    license_text = extracted.read().decode("utf-8", "replace")
        if woff_member is None:
            sys.exit("[font] tar 包里没找到 .woff 文件")

        woff_bytes = tar.extractfile(woff_member).read()
        print("[font] 取出 {}".format(woff_member.name))

    woff_path = os.path.join(CACHE_DIR, "source.woff")
    with open(woff_path, "wb") as fh:
        fh.write(woff_bytes)

    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        sys.exit("[font] 需要 fonttools：pip install fonttools")

    font = TTFont(woff_path)
    font.flavor = None  # 去掉 woff 包装，存成裸 ttf 给 FreeType 用
    font.save(ttf_path)
    print("[font] 已转换为 ttf: {}".format(ttf_path))

    if license_text:
        with open(os.path.join(CACHE_DIR, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(license_text)
    return ttf_path


# --------------------------------------------------------------------------- 字集

def build_codepoints(font_path):
    """ASCII + GB2312 全集 + 常用符号，最后按字体实际覆盖情况过滤。"""
    from fontTools.ttLib import TTFont

    wanted = set(range(0x20, 0x7F))
    for b1 in range(0xA1, 0xF8):
        for b2 in range(0xA1, 0xFF):
            try:
                wanted.add(ord(bytes([b1, b2]).decode("gb2312")))
            except UnicodeDecodeError:
                continue
    wanted.update(ord(c) for c in EXTRA_SYMBOLS)

    font = TTFont(font_path)
    available = set()
    for table in font["cmap"].tables:
        available.update(table.cmap.keys())
    font.close()

    missing = sorted(c for c in wanted if c not in available)
    kept = sorted(c for c in wanted if c in available)
    print("[font] 需要 {} 字，字体覆盖 {} 字，缺 {} 字（跳过）".format(
        len(wanted), len(kept), len(missing)))
    if missing:
        preview = "".join(chr(c) for c in missing[:24])
        print("[font] 缺失预览: {}".format(preview))
    return kept


# --------------------------------------------------------------------------- 渲染

def calibrate_baseline(font, sample="永国Agpy|_"):
    """
    在 12 行的格子里，基线该放在第几行？

    字体的 hhea/OS2 行高（ascent+descent）常常大于 12（本字体是 14+4=18），
    直接拿 ascent 当基线会把汉字的上半截和 ASCII 的下伸部全裁掉。
    所以这里不猜：把一撮代表性字符画到大画布上，量出真实墨迹相对基线的范围，
    再解出**刚好把整块墨迹塞进 12 行**的基线位置。
    """
    from PIL import Image, ImageDraw

    probe_base = 40
    img = Image.new("L", (GLYPH_W * 8, probe_base * 2), 0)
    ImageDraw.Draw(img).text((4, probe_base), sample, fill=255, font=font, anchor="ls")

    rows = [y for y in range(img.height)
            if any(img.getpixel((x, y)) >= 128 for x in range(img.width))]
    if not rows:
        return GLYPH_H - 2, 0, 0

    top = min(rows) - probe_base      # 负数：最高墨迹在基线上方多少行
    bottom = max(rows) - probe_base   # 正数：最低墨迹在基线下方多少行
    baseline = GLYPH_H - 1 - bottom
    return baseline, top, bottom


def render_glyphs(ttf_path, codepoints):
    """把每个码点渲染成 12x12 的 1-bit 点阵。"""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(ttf_path, GLYPH_H)
    baseline, top, bottom = calibrate_baseline(font)
    ink_rows = bottom - top + 1
    print("[font] 字号 {}px，基线第 {} 行，墨迹纵向范围 {}..{}（共 {} 行，格子 {} 行）".format(
        GLYPH_H, baseline, top, bottom, ink_rows, GLYPH_H))
    if ink_rows > GLYPH_H:
        print("[font] 警告：墨迹比格子还高，会裁掉 {} 行".format(ink_rows - GLYPH_H))

    advances = []
    bitmaps = []
    blank = []
    clipped = 0
    for code in codepoints:
        ch = chr(code)
        img = Image.new("L", (GLYPH_W, GLYPH_H), 0)
        # 像素字体必须在设计尺寸直接渲染 —— 放大再缩小会让 hinting 失效、笔画发糊。
        ImageDraw.Draw(img).text((0, baseline), ch, fill=255, font=font, anchor="ls")

        advance = int(round(font.getlength(ch)))
        if advance < 1:
            advance = GLYPH_W // 2
        if advance > GLYPH_W:
            advance = GLYPH_W
        advances.append(advance)

        rows = []
        empty = True
        for y in range(GLYPH_H):
            bits = 0
            for x in range(GLYPH_W):
                if img.getpixel((x, y)) >= 128:
                    bits |= 1 << (GLYPH_W - 1 - x)
                    empty = False
            rows.append(bits)
        if empty:
            blank.append(code)
        bitmaps.append(pack_rows(rows))

    if blank:
        preview = "".join(chr(c) for c in blank[:20])
        print("[font] 警告：{} 个码点渲染为空（可能是空格类字符）: {}".format(len(blank), preview))
    return advances, bitmaps


def validate(codepoints, advances, bitmaps):
    """生成后自检：汉字是否全宽、位图是否有全空/超宽。"""
    from collections import Counter

    half = [c for c, a in zip(codepoints, advances) if 0x4E00 <= c <= 0x9FFF and a != GLYPH_W]
    if half:
        preview = "".join(chr(c) for c in half[:20])
        print("[font] 警告：{} 个汉字不是全宽（应为 {}px）: {}".format(len(half), GLYPH_W, preview))

    wide = [c for c, a in zip(codepoints, advances) if a > GLYPH_W]
    if wide:
        print("[font] 警告：{} 个字步进超过格子宽度".format(len(wide)))

    hist = Counter(advances)
    common = ", ".join("{}px×{}".format(w, n) for w, n in sorted(hist.items()))
    print("[font] 步进宽度分布: {}".format(common))


def pack_rows(rows):
    """把 12 行 x 12 位的点阵按 MSB 优先打包成 18 字节（行不按字节对齐）。"""
    out = bytearray(BYTES_PER_GLYPH)
    bit_pos = 0
    for bits in rows:
        for i in range(GLYPH_W):
            if bits & (1 << (GLYPH_W - 1 - i)):
                out[bit_pos >> 3] |= 1 << (7 - (bit_pos & 7))
            bit_pos += 1
    return bytes(out)


# --------------------------------------------------------------------------- 输出

def write_atlas(path, codepoints, advances, bitmaps):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<BBBB", GLYPH_W, GLYPH_H, BYTES_PER_GLYPH, 0x01))
        fh.write(struct.pack("<I", len(codepoints)))
        for code in codepoints:
            fh.write(struct.pack("<I", code))
        fh.write(bytes(advances))
        for blob in bitmaps:
            fh.write(blob)
    size = os.path.getsize(path)
    print("[font] 写出 {} （{} 字，{:.0f} KB）".format(path, len(codepoints), size / 1024.0))


def write_license():
    src = os.path.join(CACHE_DIR, "LICENSE.txt")
    os.makedirs(os.path.dirname(OUT_LICENSE), exist_ok=True)
    with open(OUT_LICENSE, "w", encoding="utf-8") as fh:
        if os.path.exists(src):
            with open(src, encoding="utf-8") as handle:
                fh.write(handle.read())
        else:
            fh.write(
                "本目录下的 pixel12.bin 由「缝合像素字体 Fusion Pixel 12px」生成。\n"
                "Fusion Pixel 由 TakWolf 开发，以 SIL Open Font License 1.1 授权。\n"
                "许可证全文：https://scripts.sil.org/OFL\n")
    print("[font] 写出 {}".format(OUT_LICENSE))


def probe(ttf_path):
    """打印几个字的点阵，用来肉眼确认渲染参数对不对。"""
    samples = "黑光材质Ag1"
    codepoints = build_codepoints(ttf_path)
    wanted = [ord(c) for c in samples]
    keep = [c for c in wanted if c in codepoints]
    advances, bitmaps = render_glyphs(ttf_path, keep)
    for idx, code in enumerate(keep):
        print("\n--- {} (U+{:04X}, 步进 {}px) ---".format(chr(code), code, advances[idx]))
        blob = bitmaps[idx]
        for y in range(GLYPH_H):
            line = ""
            for x in range(GLYPH_W):
                bit = y * GLYPH_W + x
                on = blob[bit >> 3] & (1 << (7 - (bit & 7)))
                line += "#" if on else "."
            print(line)


def main():
    parser = argparse.ArgumentParser(description="生成 pixel12.bin 中文点阵字模")
    parser.add_argument("--probe", action="store_true", help="只打印样例点阵，不写文件")
    args = parser.parse_args()

    ttf_path = fetch_ttf()
    if args.probe:
        probe(ttf_path)
        return 0

    codepoints = build_codepoints(ttf_path)
    advances, bitmaps = render_glyphs(ttf_path, codepoints)
    validate(codepoints, advances, bitmaps)
    write_atlas(OUT_BIN, codepoints, advances, bitmaps)
    write_license()
    return 0


if __name__ == "__main__":
    sys.exit(main())
