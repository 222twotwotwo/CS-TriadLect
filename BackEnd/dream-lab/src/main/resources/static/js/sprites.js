// ============================================================
//  sprites.js —— 世界里的每一样东西，都在这儿"画"出来
//
//  大学实验室陈设。三个约定（与角色设计稿一致）：
//    1. 先描边，再分三层明暗
//    2. 细节对齐到 2 像素网格
//    3. 光源统一：天花板灯管打冷白光，右侧窗户打进暖白光
//
//  这一层只负责"怎么画"，不关心"放在哪"——摆放与动画在 world.js
// ============================================================

import { C, shaded, seams, glow } from './palette.js';

/* ---------- 像素画小工具：字符串就是画布，字母就是颜色 ---------- */

export function pixelSprite(rows, palette, px = 3) {
  const g = new PIXI.Graphics();
  rows.forEach((row, y) => {
    [...row].forEach((ch, x) => {
      const c = palette[ch];
      if (c !== undefined) g.rect(x * px, y * px, px, px).fill(c);
    });
  });
  return g;
}

/* ---------- 房间的三个面 ---------- */

/** 天花板：吊顶方板 + 灯管 + 通风管道 + 喷淋头 */
export function drawCeiling(g, w, h) {
  g.rect(0, 0, w, h).fill(C.ceilTile);
  for (let x = 0; x < w; x += 48) g.rect(x, 0, 2, h - 4).fill(C.grout);
  for (let y = 4; y < h - 8; y += 20) g.rect(0, y, w, 2).fill(C.grout);
  g.rect(0, h - 4, w, 4).fill(C.wallShade);

  g.rect(0, 6, w, 18).fill(C.inkDeep);
  g.rect(0, 8, w, 14).fill(C.steel);
  g.rect(0, 8, w, 4).fill(C.steelLight);
  g.rect(0, 18, w, 2).fill(C.ink);
  for (let x = 60; x < w; x += 150) {
    g.rect(x, 4, 10, 22).fill(C.inkDeep);
    g.rect(x + 2, 6, 6, 18).fill(C.steelLight);
  }

  for (const lx of [150, 470, 790]) {
    g.rect(lx - 58, h - 28, 116, 14).fill(C.inkDeep);
    g.rect(lx - 56, h - 26, 112, 10).fill(C.steel);
    g.rect(lx - 52, h - 24, 104, 6).fill(C.steelLight);
    g.rect(lx - 48, h - 22, 96, 4).fill(0xfdffff);
    glow(g, lx, h - 10, 70, 0xffffff, 0.07);
  }

  for (const sx of [250, 570, 870]) {
    g.rect(sx, h - 16, 8, 8).fill(C.steel);
    g.rect(sx + 2, h - 14, 4, 4).fill(C.inkDeep);
    g.rect(sx + 3, h - 8, 2, 6).fill(C.steelLight);
  }
}

/** 墙面：冷白 + 瓷砖墙裙 + 走线槽 + 插座 */
export function drawWall(g, w, wallY0, wallY1) {
  g.rect(0, wallY0, w, wallY1 - wallY0).fill(C.wall);
  const skirtH = 52;
  const skirtY = wallY1 - skirtH;
  g.rect(0, skirtY, w, skirtH).fill(C.wainscot);
  g.rect(0, skirtY, w, 4).fill(C.steelLight);
  for (let x = 0; x < w; x += 24) {
    g.rect(x, skirtY + 6, 2, skirtH - 6).fill(C.grout);
  }
  for (let y = skirtY + 18; y < wallY1; y += 16) {
    g.rect(0, y, w, 2).fill(C.grout);
  }
  g.rect(0, wallY1 - 6, w, 6).fill(C.wallShade);

  for (const x of [246, 668]) {
    g.rect(x, wallY0, 10, wallY1 - wallY0 - skirtH).fill(C.wallShade);
    g.rect(x + 2, wallY0, 4, wallY1 - wallY0 - skirtH).fill(C.panel);
    for (let y = wallY0 + 16; y < skirtY; y += 28) {
      g.rect(x, y, 10, 4).fill(C.steel);
    }
  }

  for (const [sx, sy] of [[300, 236], [706, 236]]) {
    shaded(g, sx, sy, 22, 16, C.panelLight, C.wainscotHi, C.panel, C.ink);
    g.rect(sx + 4, sy + 5, 4, 6).fill(C.inkDeep);
    g.rect(sx + 14, sy + 5, 4, 6).fill(C.inkDeep);
  }
}

/** 地板：棋盘瓷砖 + 砖缝 + 窗外投进来的天光 */
export function drawFloor(g, w, y0, h) {
  const tile = 32;
  for (let yy = y0; yy < y0 + h; yy += tile) {
    for (let xx = 0; xx < w; xx += tile) {
      const alt = ((xx / tile) + (yy / tile)) % 2 === 0;
      g.rect(xx, yy, tile, tile).fill(alt ? C.tile : C.tileAlt);
    }
  }
  seams(g, 0, y0, w, h, tile, C.tileLine, true);
  for (let x = 0; x < w; x += tile) g.rect(x, y0, 2, h).fill(C.tileLine);

  g.rect(0, y0, w, 18).fill({ color: C.tileDark, alpha: 0.45 });
  g.rect(176, y0, 588, 6).fill(C.inkDeep);
  g.rect(176, y0 + 6, 588, 4).fill({ color: C.steel, alpha: 0.55 });

  g.rect(620, y0 + 10, 28, h - 20).fill({ color: C.sunbeam, alpha: 0.16 });
  g.rect(668, y0 + 12, 40, h - 28).fill({ color: C.sunbeam, alpha: 0.22 });
  g.rect(730, y0 + 14, 70, h - 36).fill({ color: C.sunbeam, alpha: 0.30 });
  g.rect(800, y0 + 14, 110, h - 40).fill({ color: C.sunbeam, alpha: 0.38 });
  g.rect(556, y0, 8, h).fill({ color: C.inkDeep, alpha: 0.06 });
}

/* ---------- 右侧的大窗户（实验室最亮的地方） ---------- */

export function drawWindow(g, x, y, w, h) {
  g.rect(x - 10, y - 12, w + 20, h + 24).fill(C.panel);
  g.rect(x - 6, y - 8, w + 12, h + 16).fill(C.steel);
  g.rect(x - 4, y - 6, w + 8, h + 12).fill(C.steelLight);

  g.rect(x + 8, y + h - 62, 40, 62).fill({ color: C.cityFar, alpha: 0.55 });
  g.rect(x + 52, y + h - 86, 28, 86).fill({ color: C.cityFar, alpha: 0.5 });
  g.rect(x + 108, y + h - 70, 36, 70).fill({ color: C.cityFar, alpha: 0.45 });
  g.rect(x + 14, y + h - 46, 34, 46).fill(C.cityNear);
  g.rect(x + 50, y + h - 68, 22, 68).fill(C.cityNear);
  g.rect(x + 92, y + h - 42, 40, 42).fill({ color: C.cityNear, alpha: 0.95 });
  g.rect(x + 20, y + h - 38, 6, 8).fill({ color: C.hazard, alpha: 0.7 });
  g.rect(x + 54, y + h - 52, 6, 8).fill({ color: C.ice, alpha: 0.55 });
  g.rect(x + 100, y + h - 30, 6, 8).fill({ color: C.hazard, alpha: 0.55 });
  g.circle(x + 128, y + h - 28, 16).fill({ color: C.plant, alpha: 0.55 });
  g.rect(x + 124, y + h - 16, 8, 16).fill(C.plantDark);

  g.rect(x + w / 2 - 4, y, 8, h).fill(C.steelLight);
  g.rect(x, y + h / 2 - 4, w, 8).fill(C.steelLight);
  g.rect(x - 6, y - 6, w + 12, 8).fill(C.steel);
  g.rect(x - 6, y + h - 2, w + 12, 8).fill(C.steel);

  for (let i = 0; i < 5; i++) {
    g.rect(x + 4, y + 6 + i * 8, w - 8, 4).fill({ color: C.panelLight, alpha: 0.28 - i * 0.04 });
  }
  return { skyX: x, skyY: y, skyW: w, skyH: h };
}

/* ---------- 四个"站点" ---------- */

/** 白板（第 2 关：读代码）—— 管理员留下的笔记 */
export function drawWhiteboard(g, x, y, w, h) {
  g.rect(x - 8, y - 8, w + 16, h + 20).fill(C.steel);
  g.rect(x - 4, y - 4, w + 8, h + 8).fill(C.panel);
  g.rect(x, y, w, h).fill(C.board);
  g.rect(x, y, w, 4).fill(0xffffff);
  g.rect(x + 4, y + 4, 4, h - 8).fill({ color: 0xffffff, alpha: 0.55 });

  g.rect(x + 22, y + 22, 48, 4).fill(C.marker);
  g.rect(x + 22, y + 34, 86, 4).fill(C.marker);
  g.rect(x + 22, y + 46, 62, 4).fill({ color: C.marker, alpha: 0.7 });
  g.rect(x + 148, y + 20, 36, 36).fill({ color: C.marker, alpha: 0.22 });
  g.rect(x + 148, y + 20, 36, 4).fill(C.marker);
  g.rect(x + 196, y + 32, 44, 4).fill(C.marker);
  for (let i = 0; i < 5; i++) g.rect(x + 24 + i * 20, y + 78 - i * 8, 6, 8 + i * 8).fill(C.signBlue);
  g.rect(x + 22, y + 86, 108, 4).fill(C.marker);
  g.rect(x + 148, y + 84, 58, 4).fill({ color: C.warning, alpha: 0.7 });
  g.rect(x + 214, y + 76, 40, 4).fill(C.marker);
  g.rect(x + 22, y + 102, 70, 4).fill({ color: C.ice, alpha: 0.7 });

  g.rect(x - 2, y + h + 2, w + 4, 10).fill(C.steel);
  g.rect(x + 12, y + h + 4, 28, 6).fill(0xdce6f4);
  g.rect(x + w - 86, y + h + 4, 8, 6).fill(C.signBlue);
  g.rect(x + w - 72, y + h + 4, 8, 6).fill(C.warning);
  g.rect(x + w - 58, y + h + 4, 8, 6).fill(C.life);
}

/** 玻璃样品柜（第 4 关：样本库） */
export function drawSampleCabinet(g, x, y, w, h) {
  shaded(g, x, y, w, h, C.panelLight, C.wall, C.panel, C.inkDeep);
  g.rect(x + 4, y + 4, w - 8, 14).fill(C.machine);
  g.rect(x + 10, y + 8, 36, 6).fill(C.steelLight);
  g.rect(x + w - 22, y + 8, 8, 6).fill(C.warning);
  g.rect(x + 6, y + 20, w - 12, h - 26).fill(C.inkDeep);

  const rows = 3, rh = (h - 36) / rows;
  const bottleCols = [C.flame, C.glass, C.life, C.signBlue, C.warning];
  for (let r = 0; r < rows; r++) {
    const ry = y + 24 + r * rh;
    g.rect(x + 10, ry, w - 20, rh - 6).fill(0xe8f0fa);
    g.rect(x + 12, ry + 2, 8, rh - 14).fill({ color: 0xffffff, alpha: 0.45 });
    let i = 0;
    for (let bx = x + 20; bx < x + w - 30; bx += 22, i++) {
      const bh = 14 + (i % 3) * 6;
      const col = bottleCols[(i + r) % 5];
      g.rect(bx, ry + rh - 12 - bh, 12, bh).fill(col);
      g.rect(bx, ry + rh - 12 - bh, 12, 3).fill(0xffffff);
      g.rect(bx + 3, ry + rh - 16 - bh, 6, 5).fill(C.steel);
      g.rect(bx + 2, ry + rh - 18, 8, 4).fill({ color: 0xffffff, alpha: 0.35 });
    }
    g.rect(x + 8, ry + rh - 8, w - 16, 4).fill(C.steelLight);
  }
  g.rect(x + 8, y + 22, w - 16, h - 30).stroke({ width: 2, color: C.steel, alpha: 0.55 });
  g.rect(x + w / 2 - 2, y + 22, 4, h - 32).fill({ color: C.steel, alpha: 0.7 });
  g.rect(x + w / 2 - 16, y + h / 2 - 6, 6, 16).fill(C.steelLight);
  g.rect(x + w / 2 + 10, y + h / 2 - 6, 6, 16).fill(C.steelLight);
}

/** 实验台：深色台面 + 浅蓝灰柜体（整间实验室的骨架） */
export function drawBench(g, x, y, w, h) {
  shaded(g, x, y, w, 18, C.bench, C.benchEdge, C.inkDeep, C.inkDeep);
  g.rect(x + 6, y + 4, w - 80, 4).fill({ color: C.steelLight, alpha: 0.35 });
  g.rect(x + 4, y + 18, w - 8, 4).fill(C.inkDeep);
  shaded(g, x + 6, y + 22, w - 12, h - 22, C.panelLight, C.wall, C.panel);
  for (let dx = x + 18; dx < x + w - 70; dx += 76) {
    g.rect(dx, y + 34, 62, 22).fill(C.wall);
    g.rect(dx, y + 34, 62, 2).fill(C.steelLight);
    g.rect(dx + 22, y + 42, 16, 4).fill(C.steel);
    g.rect(dx, y + h - 40, 62, 26).fill(C.wall);
    g.rect(dx, y + h - 40, 62, 2).fill(C.steelLight);
    g.rect(dx + 22, y + h - 28, 16, 4).fill(C.steel);
  }
}

/* ---------- 台面上的设备 ---------- */

export function drawMonitor(g, x, y) {
  g.rect(x + 24, y + 58, 12, 16).fill(C.machine);
  g.rect(x + 8, y + 72, 46, 8).fill(C.machine);
  shaded(g, x, y, 64, 58, C.machine, 0x46536f, 0x222a3c, C.inkDeep);
  g.rect(x + 6, y + 6, 52, 42).fill(C.inkDeep);
  g.rect(x + 8, y + 8, 48, 38).fill(0x123526);
  for (let i = 0; i < 6; i++) {
    g.rect(x + 12, y + 12 + i * 5, 28 - (i % 3) * 6, 3).fill({ color: C.crt, alpha: 0.85 });
  }
  g.rect(x + 8, y + 8, 48, 6).fill({ color: 0xffffff, alpha: 0.08 });
  g.rect(x + 4, y + 80, 56, 12).fill(C.machine);
  g.rect(x + 8, y + 84, 8, 4).fill(C.steelLight);
  g.rect(x + 20, y + 84, 28, 4).fill(C.inkDeep);
  glow(g, x + 32, y + 28, 22, C.crt, 0.06);
}

export function drawMicroscope(g, x, y) {
  g.rect(x + 4, y + 48, 44, 10).fill(C.inkDeep);
  g.rect(x + 8, y + 50, 36, 4).fill(C.steel);
  g.rect(x + 22, y + 18, 10, 32).fill(C.machine);
  g.rect(x + 10, y + 28, 20, 8).fill(C.machine);
  g.rect(x + 12, y + 36, 16, 12).fill(0x46536f);
  g.rect(x + 18, y + 10, 12, 12).fill(C.steelLight);
  g.rect(x + 20, y + 8, 8, 6).fill(C.glass);
  g.rect(x + 30, y + 38, 14, 8).fill(C.steel);
  g.rect(x + 34, y + 36, 8, 4).fill(C.ice);
}

export function drawBottleRack(g, x, y) {
  g.rect(x, y + 26, 68, 8).fill(C.inkDeep);
  g.rect(x + 2, y + 27, 64, 5).fill(C.panel);
  for (let i = 0; i < 4; i++) {
    const bx = x + 4 + i * 16;
    const ht = 18 + (i % 2) * 6;
    g.rect(bx, y + 26 - ht, 12, ht).fill([C.flame, C.glass, C.life, C.signBlue][i]);
    g.rect(bx, y + 26 - ht, 12, 3).fill(0xffffff);
    g.rect(bx + 3, y + 20 - ht, 6, 6).fill(C.steel);
  }
}

export function drawFlasks(g, x, y) {
  g.rect(x + 2, y + 20, 26, 22).fill(C.inkDeep);
  g.rect(x + 6, y + 18, 18, 24).fill({ color: C.glass, alpha: 0.45 });
  g.rect(x + 8, y + 28, 14, 12).fill(0x4aa3d8);
  g.rect(x + 8, y + 34, 14, 6).fill(0x2f7fb0);
  g.rect(x + 12, y + 8, 6, 12).fill({ color: C.glass, alpha: 0.55 });
  g.rect(x + 10, y + 6, 10, 4).fill(C.steel);
  for (let i = 0; i < 3; i++) {
    g.rect(x + 36 + i * 14, y + 14, 10, 28).fill({ color: C.glass, alpha: 0.4 });
    g.rect(x + 38 + i * 14, y + 26, 6, 14).fill([0xd9534f, 0x8fc4ea, 0x7fd4a8][i]);
    g.rect(x + 38 + i * 14, y + 12, 6, 4).fill(C.steel);
  }
  g.rect(x + 32, y + 40, 50, 6).fill(C.inkDeep);
}

export function drawSink(g, x, y) {
  g.rect(x, y, 54, 16).fill(C.steel);
  g.rect(x + 4, y + 4, 46, 10).fill(C.inkDeep);
  g.rect(x + 6, y + 6, 42, 4).fill({ color: C.ice, alpha: 0.25 });
  g.rect(x + 24, y - 16, 6, 16).fill(C.steelLight);
  g.rect(x + 18, y - 20, 18, 6).fill(C.steel);
  g.rect(x + 34, y - 14, 10, 4).fill(C.steelLight);
}

export function drawStool(g, x, y) {
  g.rect(x + 4, y, 36, 10).fill(C.inkDeep);
  g.rect(x + 6, y + 2, 32, 6).fill(0x8a5a32);
  g.rect(x + 8, y + 2, 28, 2).fill(0xb07a4a);
  g.rect(x + 18, y + 10, 8, 26).fill(C.steel);
  g.rect(x + 10, y + 34, 24, 5).fill(C.inkDeep);
  g.rect(x + 14, y + 38, 5, 8).fill(C.machine);
  g.rect(x + 25, y + 38, 5, 8).fill(C.machine);
}

export function drawTrashBin(g, x, y) {
  shaded(g, x, y, 40, 54, C.steel, C.steelLight, C.panel, C.ink);
  g.rect(x + 4, y + 6, 32, 4).fill(C.inkDeep);
  g.rect(x + 6, y + 14, 28, 20).fill({ color: C.wall, alpha: 0.45 });
  g.rect(x + 14, y + 2, 12, 6).fill(C.warning);
}

/* ---------- 墙面上的东西 ---------- */

/** 门（终章之门）—— 门牌写着实验室编号 */
export function drawDoor(g, x, y, w, h) {
  g.rect(x - 10, y - 10, w + 20, h + 20).fill(C.panel);
  shaded(g, x, y, w, h, C.wall, 0xf4f8fd, C.wallShade, C.ink);
  g.rect(x + 8, y + 12, w - 16, 70).fill(C.steelLight);
  g.rect(x + 12, y + 16, w - 24, 62).fill({ color: C.glass, alpha: 0.5 });
  g.rect(x + 12, y + 16, w - 24, 4).fill({ color: 0xffffff, alpha: 0.6 });
  g.rect(x + w / 2 - 2, y + 16, 4, 62).fill(C.steelLight);
  g.rect(x + 12, y + 44, w - 24, 4).fill(C.steelLight);
  g.rect(x + 6, y + h - 18, w - 12, 14).fill(C.steel);
  g.rect(x + w - 22, y + h / 2 + 28, 12, 22).fill(C.steelLight);
  g.rect(x + w - 20, y + h / 2 + 30, 8, 18).fill(C.ice);

  g.rect(x + 8, y - 36, 52, 18).fill(C.life);
  g.rect(x + 8, y - 36, 52, 3).fill(0x7fdd9a);
  g.rect(x + 64, y - 32, 22, 14).fill(C.machine);
  g.rect(x + 68, y - 28, 14, 6).fill(C.steelLight);
}

/** 海报：实验室的两句标语 */
export function drawPoster(g, x, y, w, h, kind) {
  g.rect(x - 4, y - 4, w + 8, h + 8).fill(C.steel);
  g.rect(x, y, w, h).fill(kind === 'atom' ? C.signBlue : 0x2f6b5a);
  g.rect(x, y, w, 4).fill(kind === 'atom' ? 0x6f9ed0 : 0x5fbf8a);
  const cx = x + w / 2;
  if (kind === 'atom') {
    g.circle(cx, y + h * 0.40, 18).stroke({ width: 3, color: 0xdce8f8 });
    g.ellipse(cx, y + h * 0.40, 28, 10).stroke({ width: 2, color: 0xdce8f8, alpha: 0.9 });
    g.circle(cx, y + h * 0.40, 4).fill(0xdce8f8);
    for (let i = 0; i < 3; i++) g.rect(cx - 20, y + h - 46 + i * 12, 40, 6).fill(0xdce8f8);
  } else {
    const cy = y + h * 0.38;
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2 - Math.PI / 2;
      g.circle(cx + Math.cos(a) * 18, cy + Math.sin(a) * 18, 4).fill(0xdce8f8);
    }
    g.circle(cx, cy, 18).stroke({ width: 2, color: 0xdce8f8, alpha: 0.9 });
    g.circle(cx, cy, 5).fill(0xdce8f8);
    g.rect(cx - 22, y + h - 40, 44, 6).fill(0xdce8f8);
    g.rect(cx - 14, y + h - 26, 28, 5).fill({ color: 0xdce8f8, alpha: 0.8 });
  }
}

/** 挂钟 */
export function drawClock(g, x, y) {
  g.circle(x, y, 28).fill(C.inkDeep);
  g.circle(x, y, 24).fill(0xf7fafd);
  g.circle(x, y, 22).fill(C.board);
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    g.rect(x + Math.cos(a) * 18 - 1, y + Math.sin(a) * 18 - 1, 3, 3).fill(C.ink);
  }
  g.rect(x - 1, y - 14, 3, 16).fill(C.inkDeep);
  g.rect(x, y - 1, 12, 3).fill(C.inkDeep);
  g.circle(x, y, 3).fill(C.warning);
}

/** 配电箱（第 6 关：世界规则柜） */
export function drawPanel(g, x, y, w, h) {
  for (let i = 0; i < 6; i++) {
    g.rect(x - 6, y - 8 + i * 8, 12, 6).fill(i % 2 ? C.hazard : C.inkDeep);
    g.rect(x + w - 6, y - 8 + i * 8, 12, 6).fill(i % 2 ? C.hazard : C.inkDeep);
  }
  shaded(g, x, y, w, h, C.steelLight, 0xf4f8fd, C.steel, C.inkDeep);
  g.rect(x + 8, y + 8, w - 16, 32).fill(C.machine);
  for (let i = 0; i < 3; i++) g.rect(x + 14, y + 14 + i * 8, w - 36, 4).fill({ color: 0x7fd4ff, alpha: 0.85 });
  g.circle(x + w - 22, y + 24, 6).fill(C.warning);
  g.circle(x + w - 22, y + 24, 3).fill(0xff8a86);
  g.rect(x + 12, y + 46, 16, 16).fill(C.inkDeep);
  g.rect(x + 14, y + 48, 12, 12).fill(C.life);
  g.rect(x + w / 2 - 8, y + 48, 16, 8).fill(C.inkDeep);
  g.rect(x + w / 2 - 4, y + 54, 8, 28).fill(C.steel);
  g.rect(x + w / 2 - 10, y + 80, 20, 8).fill(C.warning);
  g.rect(x + 10, y + h - 16, w - 20, 6).fill(C.inkDeep);
}

/** 灭火器：实验室墙上那一抹红 */
export function drawExtinguisher(g, x, y) {
  g.rect(x + 6, y, 16, 6).fill(C.steel);
  g.rect(x + 8, y + 6, 12, 40).fill(C.warning);
  g.rect(x + 8, y + 6, 12, 4).fill(0xff8a86);
  g.rect(x + 10, y + 14, 8, 4).fill(C.inkDeep);
  g.rect(x + 18, y + 4, 10, 4).fill(C.steelLight);
  g.rect(x + 4, y + 46, 20, 6).fill(C.inkDeep);
}

/** 急救箱 */
export function drawFirstAid(g, x, y) {
  shaded(g, x, y, 28, 22, C.wall, 0xf4f8fd, C.wallShade, C.warning, 2);
  g.rect(x + 12, y + 4, 4, 14).fill(C.warning);
  g.rect(x + 6, y + 9, 16, 4).fill(C.warning);
}

/** 站点提示：一枚悬浮的菱形指示物 */
export function drawStationMarker(g, x, y) {
  g.rect(x - 8, y - 2, 16, 4).fill(C.inkDeep);
  g.rect(x - 2, y - 8, 4, 16).fill(C.inkDeep);
  g.rect(x - 6, y - 6, 12, 12).fill(C.inkDeep);
  g.rect(x - 4, y - 4, 8, 8).fill(0x7fd4ff);
  g.rect(x - 2, y - 2, 4, 4).fill(0xffffff);
}

/* ---------- 绿植（实验室里唯一的"活物"） ---------- */

export function drawPlant(g, x, y, big = false) {
  const w = big ? 34 : 26, h = big ? 30 : 24;
  g.rect(x + 2, y + h, w - 4, 6).fill(C.inkDeep);
  g.rect(x + 4, y + h + 4, w - 8, big ? 26 : 20).fill(0xd8a06a);
  g.rect(x + 4, y + h + 4, w - 8, 4).fill(0xe8b684);
  const leaves = [[0, 0], [-8, 6], [8, 6], [-4, -8], [6, -6]];
  for (const [dx, dy] of leaves) {
    g.rect(x + w / 2 - 8 + dx, y + h - 8 + dy, 16, 10).fill(C.plant);
    g.rect(x + w / 2 - 8 + dx, y + h - 8 + dy, 16, 3).fill(0x7fbf7f);
  }
  g.rect(x + w / 2 - 1, y + 4, 3, h + 6).fill(C.plantDark);
}
