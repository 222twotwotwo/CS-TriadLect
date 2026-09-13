// ============================================================
//  world.js —— 把这个世界"摆"出来
//
//  分工：
//    palette.js → 颜色与画法规范（实验室配色）
//    sprites.js → 每样东西怎么画
//    world.js   → 摆在哪儿、怎么动、这一关该看哪儿（就是这里）
//
//  这一版的舞台是**大学实验室**：天花板灯管与通风管、冷白的墙、
//  蓝灰瓷砖地、贴着墙的一长条实验台、右侧一扇大窗（实验室最亮的地方）。
//  每一关都有一个"站点"物件，当前该去的那一个会浮起一枚蓝色菱形提示。
// ============================================================

import { C, outlined, shaded, glow } from './palette.js';
import * as S from './sprites.js';

const W = 960, H = 540;
const CEIL_H = 64;               // 天花板
const FLOOR_Y = 300;             // 墙脚线
const BENCH_Y = 300;             // 实验台台面

/* ---------- 场景状态 ---------- */
let app, world;
let rin, rinGlow, grant, mascot, sky, skyNight, stars, clouds, doorGlow;
let stormOverlay, scanline, marker, markerAt = null;
let rain, bubbles, lampFlicker;
const debris = [], bursts = [], pools = [];
let stability = 0, t = 0, doorLit = false;
let rinBounceUntil = -9999, grantBounceUntil = -9999;
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const RIN_FRAMES = 9;            // 星野凛（少女）：9 帧待机
const RIN_HEIGHT = 121;          // 素材原高（已裁掉底部的帧号），1:1 显示最清晰
const RIN_GROUND_Y = 350;        // 精灵中心（脚底约 400：与格兰特站在同一条地平线上）
const GRANT_FRAMES = 8;          // 格兰特（企鹅玩偶服）：8 帧待机
const GRANT_HEIGHT = 120;
const GRANT_CX = 620, GRANT_CY = 340;   // 脚底约 400：与星野凛站在同一条地平线上
const MASCOT_FRAMES = 12;        // 摆设：捧腹大笑的黄色小家伙（12 帧）
const MASCOT_HEIGHT = 118;
let grantBaseY = GRANT_CY, mascotBaseY = 241;

const STATIONS = {               // 每一关该看的"站点"
  1: [430, 272], 2: [555, 84], 3: [430, 272], 4: [355, 96],
  5: [650, 200], 6: [723, 138], 7: [430, 272], 8: [80, 140],
};

export async function initWorld() {
  app = new PIXI.Application();
  await app.init({ background: C.wall, resizeTo: window, antialias: false });
  document.querySelector('#stage').appendChild(app.canvas);

  world = new PIXI.Container();
  app.stage.addChild(world);

  drawRoom();
  drawWallDecor();
  drawBenchRow();
  // 接地阴影（画在角色之下）：没有它，角色会像贴纸一样浮在地板上
  const shadows = new PIXI.Graphics();
  shadows.ellipse(430, 404, 34, 9).fill({ color: C.inkDeep, alpha: 0.16 });
  shadows.ellipse(620, 404, 40, 10).fill({ color: C.inkDeep, alpha: 0.16 });
  shadows.ellipse(226, BENCH_Y + 2, 46, 7).fill({ color: C.inkDeep, alpha: 0.10 });
  world.addChild(shadows);

  rin = await buildRin();
  world.addChild(rin.container);
  grant = await buildGrant();
  world.addChild(grant);
  mascot = await buildMascot();
  world.addChild(mascot);

  marker = buildMarker();
  world.addChild(marker);

  app.stage.addChild(buildStormFx());
  app.stage.addChild(buildScanline());

  app.ticker.add((tk) => tick(tk.deltaMS));
  window.addEventListener('resize', fit);
  fit();
  setStability(0);
  setChapter(1);

  // 按 F12 打开控制台，输入 __tavern 看看这个世界由什么组成
  window.__tavern = { app, world, setStability, celebrate, setChapter };
}

function fit() {
  const scale = Math.min(window.innerWidth / W, window.innerHeight / H);
  app.stage.scale.set(scale);
  app.stage.x = (window.innerWidth - W * scale) / 2;
  app.stage.y = (window.innerHeight - H * scale) / 2;
}

/* ---------- 房间 ---------- */

function drawRoom() {
  const room = new PIXI.Graphics();
  S.drawCeiling(room, W, CEIL_H);
  S.drawWall(room, W, CEIL_H, FLOOR_Y);
  S.drawFloor(room, W, FLOOR_Y, H - FLOOR_Y);
  world.addChild(room);

  // 窗外的天空：两层（风暴 / 晴空），按世界状态切换
  const { skyX, skyY, skyW, skyH } = { skyX: 790, skyY: 90, skyW: 150, skyH: 180 };
  sky = new PIXI.Graphics();
  sky.rect(skyX, skyY, skyW, skyH).fill(C.stormSky);
  skyNight = new PIXI.Graphics();
  skyNight.rect(skyX, skyY, skyW, skyH).fill(C.sky);
  skyNight.rect(skyX + 18, skyY + 10, 22, 22).fill(0xffe7a8);
  skyNight.rect(skyX + 22, skyY + 14, 14, 14).fill(0xfff4cc);
  skyNight.visible = false;
  clouds = new PIXI.Graphics();
  for (let i = 0; i < 3; i++) {
    const cx = skyX + 6 + i * 44, cy = skyY + 16 + (i % 2) * 30;
    clouds.rect(cx, cy, 52, 12).fill(C.cloud);
    clouds.rect(cx + 10, cy - 6, 30, 8).fill(C.cloud);
  }
  stars = new PIXI.Graphics();
  for (let i = 0; i < 24; i++) {
    stars.rect(skyX + 6 + (i * 41) % (skyW - 10), skyY + 6 + (i * 29) % (skyH - 12), 2, 2)
      .fill({ color: 0xffffff, alpha: 0.35 + (i % 5) * 0.13 });
  }
  stars.visible = false;
  rain = new PIXI.Graphics();
  rain.visible = false;
  world.addChild(sky, skyNight, clouds, stars, rain);

  // 窗框压住天空
  const winFrame = new PIXI.Graphics();
  S.drawWindow(winFrame, 790, 90, 150, 180);
  world.addChild(winFrame);

  // 天花板灯管投在地上的光池
  for (const lx of [150, 470, 790]) {
    const pool = new PIXI.Graphics();
    pool.ellipse(lx, 400, 140, 56).fill({ color: 0xffffff, alpha: 0.05 });
    pools.push(pool);
    world.addChildAt(pool, world.children.indexOf(winFrame));
  }
}

function drawWallDecor() {
  // 门缝的暖光（终章点亮，所以单独一层压在最上面）
  doorGlow = new PIXI.Graphics();
  doorGlow.rect(26, 152, 108, 174).fill({ color: C.flame, alpha: 0 });
  world.addChild(doorGlow);

  const g = new PIXI.Graphics();
  S.drawDoor(g, 34, 160, 92, 158);                    // 站点 8：终章之门
  S.drawClock(g, 170, 96);
  S.drawPoster(g, 200, 120, 70, 140, 'atom');
  S.drawExtinguisher(g, 132, 214);
  S.drawFirstAid(g, 168, 214);
  S.drawSampleCabinet(g, 290, 110, 130, 190);         // 站点 4：样本库
  S.drawWhiteboard(g, 440, 96, 230, 150);             // 站点 2：读代码
  S.drawPanel(g, 688, 150, 70, 128);                  // 站点 6：世界规则柜
  world.addChild(g);
}

function drawBenchRow() {
  const g = new PIXI.Graphics();
  S.drawBench(g, 180, BENCH_Y, 580, 84);
  S.drawSink(g, 508, BENCH_Y - 16);
  S.drawBottleRack(g, 300, BENCH_Y - 34);
  S.drawFlasks(g, 390, BENCH_Y - 44);
  S.drawMonitor(g, 600, BENCH_Y - 86);                // 站点 5：监控中心
  S.drawMicroscope(g, 700, BENCH_Y - 54);
  S.drawStool(g, 96, 404);
  S.drawTrashBin(g, 858, 396);
  S.drawPlant(g, 60, 424);
  world.addChild(g);

  bubbles = new PIXI.Graphics();
  world.addChild(bubbles);
  lampFlicker = new PIXI.Graphics();
  lampFlicker.rect(0, 0, W, H).fill({ color: 0x1a2233, alpha: 0 });
  world.addChild(lampFlicker);
}

function buildMarker() {
  const c = new PIXI.Container();
  const g = new PIXI.Graphics();
  S.drawStationMarker(g, 0, 0);
  c.addChild(g);
  c.x = -999;
  return c;
}

/* ---------- 三位角色 ---------- */

/** 通用：把一串 PNG 帧做成循环动画 */
async function buildAnimation(prefix, frames, height, speed) {
  const paths = Array.from({ length: frames }, (_, i) => `assets/${prefix}${i + 1}.png`);
  const textures = await Promise.all(paths.map(p => PIXI.Assets.load(p)));
  textures.forEach(t => { t.source.scaleMode = 'nearest'; });
  const sp = new PIXI.AnimatedSprite(textures);
  sp.anchor.set(0.5, 0.5);
  sp.height = height;
  sp.width = height * (textures[0].width / textures[0].height);
  sp.animationSpeed = speed;
  sp.play();
  return sp;
}

function nameTag(label, y) {
  const t = new PIXI.Text({
    text: label,
    style: {
      fontFamily: '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
      fontSize: 11,
      fill: 0xffffff,
      stroke: { color: 0x2a3242, width: 3 },
      align: 'center',
    },
  });
  t.anchor.set(0.5, 1);
  t.y = y;
  t.zIndex = 2;
  return t;
}

function buildRinFallback() {
  const rows = [
    '....W....', '...WCW...', '..WCCCW..', '.WCCCCCW.',
    'WCCCICCCW', '.WCCCCCW.', '..WCCCW..', '...WCW...', '....W....',
  ];
  const g = S.pixelSprite(rows, { W: C.coatShade, C: C.ice, I: C.snow }, 4);
  g.x = -18; g.y = -18;
  return g;
}

async function buildRin() {
  const container = new PIXI.Container();
  rinGlow = new PIXI.Graphics();
  glow(rinGlow, 0, 0, 44, C.ice, 0.07);
  container.addChild(rinGlow);
  try {
    const sp = await buildAnimation('rin_idle_', RIN_FRAMES, RIN_HEIGHT, 0.1);
    container.addChild(sp);
  } catch (e) {
    container.addChild(buildRinFallback());
  }
  container.addChild(nameTag('星野凛', -RIN_HEIGHT / 2 - 6));
  container.x = 430; container.y = RIN_GROUND_Y;
  return { container };
}

async function buildGrant() {
  const c = new PIXI.Container();
  grantBaseY = GRANT_CY;
  c.x = GRANT_CX; c.y = GRANT_CY;
  try {
    const sp = await buildAnimation('grant_idle_', GRANT_FRAMES, GRANT_HEIGHT, 0.08);
    c.addChild(sp);
  } catch (e) {
    const g = S.pixelSprite(['..KK..', '.KSSK.', 'KSSSSK', 'KPPPPK', 'KKKKKK'], { K: C.ink, S: C.skin, P: C.bench }, 6);
    g.x = -18; g.y = 0;
    c.addChild(g);
  }
  c.addChild(nameTag('咕博士', -GRANT_HEIGHT / 2 - 6));
  return c;
}

/** 摆设：实验室里那只一直在捧腹大笑的小家伙（不参与剧情，只负责可爱） */
async function buildMascot() {
  const c = new PIXI.Container();
  c.x = 226; c.y = mascotBaseY;
  try {
    const sp = await buildAnimation('laugh_idle_', MASCOT_FRAMES, MASCOT_HEIGHT, 0.16);
    c.addChild(sp);
  } catch (e) {
    const g = new PIXI.Graphics();
    g.rect(-20, -20, 40, 40).fill(C.flame);
    c.addChild(g);
  }
  c.addChild(nameTag('奶先生', -MASCOT_HEIGHT / 2 - 6));
  return c;
}

/* ---------- 崩坏特效 ---------- */

function buildStormFx() {
  stormOverlay = new PIXI.Container();
  const red = new PIXI.Graphics();
  red.rect(0, 0, W, H).fill({ color: C.danger, alpha: 0 });
  red.name = 'redflash';
  stormOverlay.addChild(red);
  const colors = [C.warning, C.danger, C.flame, C.ice];
  for (let i = 0; i < 16; i++) {
    const d = new PIXI.Graphics();
    const s = 2 + (i % 3) * 2;
    d.rect(0, 0, s, s).fill(colors[i % 4]);
    d.x = Math.random() * W; d.y = Math.random() * H;
    d._vy = 0.4 + Math.random() * 1.2;
    d._vx = (Math.random() - 0.5) * 1.4;
    debris.push(d);
    stormOverlay.addChild(d);
  }
  try {
    stormOverlay._noise = new PIXI.NoiseFilter({ noise: 0.06 });
    world.filters = [stormOverlay._noise];
  } catch { /* 不支持就只靠抖动 */ }
  return stormOverlay;
}

function buildScanline() {
  scanline = new PIXI.Graphics();
  scanline.rect(0, 0, W, 3).fill({ color: 0xffffff, alpha: 0.06 });
  return scanline;
}

/* ---------- 每帧 ---------- */

function tick(dt) {
  t += dt;
  const unstable = 1 - stability / 100;

  // 角色站在固定的地平线上，不再自己上下跳动：
  // 画面的"震动"统一由世界容器承担（见下方 unstable 分支），角色跟着一起晃。
  rin.container.y = RIN_GROUND_Y;
  rinGlow.alpha = 0.5 + Math.sin(t / 340) * 0.25 + stability / 400;
  grant.y = grantBaseY;
  if (mascot) mascot.y = mascotBaseY;

  // 角色的体积始终不变：要"活"就靠逐帧动画本身（她本来就在呼吸、眨眼）

  if (markerAt) marker.y = markerAt[1] + (reduceMotion ? 0 : Math.sin(t / 420) * 5);
  for (const p of pools) p.alpha = 0.35 + (stability / 100) * 0.5;

  if (bubbles && !reduceMotion) {
    bubbles.clear();
    const flaskX = 398, flaskY = BENCH_Y - 16;
    for (let i = 0; i < 4; i++) {
      const phase = (t / 420 + i * 0.22) % 1;
      bubbles.rect(flaskX + (i % 2) * 6, flaskY - phase * 18, 2, 2)
        .fill({ color: 0xffffff, alpha: 0.7 * (1 - phase) });
    }
  }

  if (clouds && stability >= 100 && !reduceMotion) {
    clouds.x = Math.sin(t / 2800) * 10;
  }

  if (unstable > 0.02) {
    world.x = Math.sin(t / 45) * 8 * unstable + (Math.random() - 0.5) * 10 * unstable;
    world.y = Math.cos(t / 58) * 6 * unstable + (Math.random() - 0.5) * 8 * unstable;
    const flash = stormOverlay.getChildByName('redflash');
    flash.alpha = Math.random() < 0.04 ? 0.10 * unstable : flash.alpha * 0.9;
    for (const d of debris) {
      d.visible = true;
      d.y += d._vy * dt * 0.12 * (0.4 + unstable);
      d.x += d._vx * dt * 0.06;
      if (d.y > H) { d.y = -8; d.x = Math.random() * W; }
      if (d.x < -8 || d.x > W + 8) d.x = Math.random() * W;
    }
    if (stormOverlay._noise) stormOverlay._noise.noise = 0.02 + 0.06 * unstable;
    scanline.y = (t / 6) % H; scanline.visible = true;
    sky.visible = true; skyNight.visible = false;          // 窗外：乌云压城
    sky.alpha = 0.35 + 0.65 * unstable;
    clouds.visible = false; stars.visible = false;
    if (rain) {
      rain.visible = true;
      rain.clear();
      const skyX = 790, skyY = 90, skyW = 150, skyH = 180;
      for (let i = 0; i < 18; i++) {
        const rx = skyX + 6 + ((i * 37 + t / 8) % (skyW - 12));
        const ry = skyY + 8 + ((i * 53 + t / 4) % (skyH - 16));
        rain.rect(rx, ry, 2, 8).fill({ color: 0xcfe6f8, alpha: 0.35 + (i % 3) * 0.12 });
      }
    }
    if (lampFlicker && !reduceMotion) {
      lampFlicker.alpha = Math.random() < 0.08 * unstable ? 0.18 * unstable : lampFlicker.alpha * 0.72;
    }
  } else {
    world.x = 0; world.y = 0;
    for (const d of debris) d.visible = false;
    if (stormOverlay._noise) stormOverlay._noise.noise = 0;
    stormOverlay.getChildByName('redflash').alpha = 0;
    scanline.visible = false;
    sky.visible = false; skyNight.visible = true;          // 窗外：晴空
    clouds.visible = true; stars.visible = false;
    if (rain) rain.visible = false;
    if (lampFlicker) lampFlicker.alpha = 0;
  }

  doorGlow.alpha = doorLit ? 0.20 + Math.sin(t / 260) * 0.14 : 0;

  for (const b of bursts) b.step(dt);
  for (let i = bursts.length - 1; i >= 0; i--) if (bursts[i].dead) bursts.splice(i, 1);
}

/* ---------- 对外动作 ---------- */

export function setStability(pct) {
  stability = Math.max(0, Math.min(100, pct));
  document.body.classList.toggle('glitch', stability < 100);
}

/** 让画面"指向"这一关该去的站点 */
export function setChapter(n) {
  const at = STATIONS[n] || STATIONS[7];
  markerAt = at;
  marker.x = at[0]; marker.y = at[1];
}

export function echoBounce() { rinBounceUntil = t; }
export function grantBounce() { grantBounceUntil = t; }
export function doorOpen() { doorLit = true; }

export function celebrate() {
  const colors = [C.flame, C.warning, C.life, C.ice, C.snow, C.signBlue];
  let launched = 0;
  const spawn = () => {
    const cx = 140 + Math.random() * (W - 280);
    const cy = 70 + Math.random() * 160;
    const parts = Array.from({ length: 42 }, (_, i) => {
      const a = (i / 42) * Math.PI * 2;
      const v = 1.6 + Math.random() * 2.2;
      return { x: 0, y: 0, vx: Math.cos(a) * v, vy: Math.sin(a) * v };
    });
    const g = new PIXI.Graphics();
    world.addChild(g);
    const burst = {
      dead: false,
      step(dt) {
        let landed = true;
        for (const p of parts) {
          p.x += p.vx * dt * 0.09;
          p.y += p.vy * dt * 0.09;
          p.vy += dt * 0.006;
          if (p.y < 210) landed = false;
        }
        g.clear();
        parts.forEach((p, i) => {
          const alpha = p.y < 180 ? 1 : Math.max(0, 1 - (p.y - 180) / 60);
          g.rect(cx + p.x, cy + p.y, 3, 3).fill({ color: colors[i % colors.length], alpha });
        });
        if (landed) { burst.dead = true; g.destroy(); }
      },
    };
    bursts.push(burst);
  };
  const timer = setInterval(() => { spawn(); if (++launched >= 8) clearInterval(timer); }, 700);
}
