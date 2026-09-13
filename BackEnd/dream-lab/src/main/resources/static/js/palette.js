// ============================================================
//  palette.js —— 全世界的颜色，都从这里取
//
//  大学实验室：冷白墙、蓝灰瓷砖、深蓝台面、一盏天蓝作为全场强调
//  规矩：
//    1. 亮面冷白，暗面蓝灰，不要脏灰
//    2. 深色只撑三处：台面、设备、文字
//    3. 暖色是稀客：阳光、木头、试剂
// ============================================================

export const C = {
  // —— 从设计图采下来的官方色 ——
  ink:        0x3d4f6b,
  inkDeep:    0x2a3242,
  wall:       0xe4eaf4,
  wallShade:  0xd0d9e8,
  panel:      0xbfcbe0,
  panelLight: 0xd8e1f0,

  // —— 地板：棋盘瓷砖 + 天光 ——
  tile:       0xc5d0e4,
  tileAlt:    0xb4c0d8,
  tileDark:   0xa8b6cf,
  tileLine:   0x93a3be,
  sunbeam:    0xfaf0dc,

  // —— 墙裙瓷砖 ——
  wainscot:   0xdce5f2,
  wainscotHi: 0xeef3fa,
  grout:      0xb7c4d8,

  // —— 设备与台面 ——
  bench:      0x3a4560,
  benchEdge:  0x4d5a78,
  steel:      0x8fa2c0,
  steelLight: 0xaab8d0,
  machine:    0x2f3a52,
  glass:      0xcfe0f2,
  ceilTile:   0xd7deea,

  // —— 窗与天光 ——
  ice:        0x66ccff,
  snow:       0xeeffff,
  sky:        0x8fc4ea,
  skyLight:   0xcfe6f8,
  cloud:      0xf4f9ff,
  cityFar:    0xb8c8dc,
  cityNear:   0x8ea0b8,
  night:      0x2a3a5c,
  stormSky:   0x3a4a68,

  // —— 标识与点缀 ——
  signBlue:   0x3f6ea8,
  signBlueDeep: 0x2f4a6b,
  board:      0xf8fafd,
  marker:     0x5b7fa8,
  plant:      0x5f9e63,
  plantDark:  0x3d7a4a,
  warning:    0xd9534f,
  hazard:     0xe6c14c,

  // —— 角色用色（与角色设计稿一致，这里不改人物素材）——
  hair:       0x2b2b3d,
  hairLight:  0x445577,
  coat:       0xeef2ff,
  coatShade:  0xd7dcf2,
  skin:       0xffe9e4,
  flame:      0xffcd75,

  // —— 世界状态 ——
  life:       0x38b764,
  danger:     0xb13e53,
  storm:      0x432c54,
  crt:        0x3ad47a,
};

/** 世界越不稳定，画面越往"故障紫"偏 */
export function glitchTint(color, instability) {
  if (instability <= 0.02) return color;
  const r = (color >> 16) & 0xff, g = (color >> 8) & 0xff, b = color & 0xff;
  const mix = (v, target) => Math.round(v + (target - v) * instability * 0.5);
  return (mix(r, 0xb1) << 16) | (mix(g, 0x3e) << 8) | mix(b, 0x53);
}

/* ---------- 绘制规范：描边 + 三层明暗 ---------- */

/** 描边矩形：像素画的"筋骨" */
export function outlined(g, x, y, w, h, fill, ink = C.ink) {
  g.rect(x - 2, y - 2, w + 4, h + 4).fill(ink);
  g.rect(x, y, w, h).fill(fill);
  return g;
}

/** 带明暗的立体箱体：受光面 → 主体 → 背光面 → 描边 */
export function shaded(g, x, y, w, h, base, light, dark, outline = C.ink, px = 2) {
  g.rect(x - px, y - px, w + px * 2, h + px * 2).fill(outline);
  g.rect(x, y, w, h).fill(base);
  g.rect(x, y, w, px).fill(light);
  g.rect(x, y, px, h).fill(light);
  g.rect(x + w - px, y + px, px, h - px).fill(dark);
  g.rect(x + px, y + h - px, w - px, px).fill(dark);
  return g;
}

/** 板缝 / 瓷砖缝 */
export function seams(g, x, y, w, h, step, color, horizontal = false) {
  if (horizontal) {
    for (let yy = y + step; yy < y + h; yy += step) g.rect(x, yy, w, 2).fill(color);
  } else {
    for (let xx = x + step; xx < x + w; xx += step) g.rect(xx, y, 2, h).fill(color);
  }
  return g;
}

/** 一团柔和的光（灯下、屏幕辉光、门缝光） */
export function glow(g, x, y, r, color, alpha) {
  for (let i = 4; i >= 1; i--) {
    g.circle(x, y, r * i / 4).fill({ color, alpha: alpha / i });
  }
  return g;
}

export function hex(s) {
  return parseInt(s.replace('#', ''), 16);
}
