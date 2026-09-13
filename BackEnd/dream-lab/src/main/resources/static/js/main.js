// ============================================================
//  main.js —— 点火钥匙
//
//  加载顺序：连上服务器 → 拉取剧情 → 画出世界 → 开演。
//
//  ⚠ 发布前要改的地方都在 js/config.js 里（招新二维码、通关后跳转的链接）。
// ============================================================

import * as api from './api.js';
import * as ui from './ui.js';
import * as world from './world.js';
import * as chapters from './chapters.js';
import { LAB_QR_URL } from './config.js';



const STORY_PARTS = ['meta', 'intro', 'grant', 'rin', 'fragments', 'storm',
                     'archive', 'monitor', 'world', 'finale', 'certificate', 'pages'];

/**
 * 出师不利也别白屏：任何脚本错误都变成一张人话告示，
 * 附上浏览器控制台里能看到的那行错误——排错是从看得懂错误开始的。
 */
function showCrash(title, detail) {
  if (document.querySelector('#crash-box')) return;   // 同一个错误只报一次，别叠成一摞
  const box = document.createElement('div');
  box.id = 'crash-box';
  box.style.cssText = 'position:fixed;inset:0;z-index:999;display:grid;place-items:center;'
    + 'background:#1a1c2c;font-family:ui-monospace,monospace;color:#f4f4f4;padding:24px;text-align:center';
  box.innerHTML = '<div><div style="font-size:20px;color:#ef6735;margin-bottom:14px">'
    + title + '</div><pre style="white-space:pre-wrap;font-size:12px;color:#9b9b9b;line-height:1.8">'
    + String(detail).replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))
    + '</pre><div style="margin-top:16px;font-size:12px;color:#ffcd75">'
    + '按 F12 打开浏览器控制台可以看到更详细的报错。</div></div>';
  document.body.append(box);
}

window.addEventListener('error', (e) => showCrash('这个世界抖坏了（脚本出错）', e.message + '\n' + e.filename + ':' + e.lineno));
window.addEventListener('unhandledrejection', (e) => showCrash('这个世界抖坏了（异步出错）', (e.reason && (e.reason.stack || e.reason.message)) || e.reason));

async function boot() {
  // 1. 连接服务器（可能在重启，就多试几次）
  const story = {};
  for (;;) {
    try {
      for (const part of STORY_PARTS) {
        const r = await api.get('/story/' + part);
        if (!r.ok) throw new Error('story/' + part);
        story[part] = r.data;
      }
      break;
    } catch {
      await ui.say('旁白', '正在连接逐梦实验室……\n（如果刚重启了服务器，几秒后会自动连上）');
      await ui.sleep(2000);
    }
  }

  // 2. 画出像素世界
  await world.initWorld();
  document.title = story.meta.title + ' · ' + story.meta.subtitle;

  // 3. 点亮界面
  ui.hudInit();
  ui.panelInit();
  ui.consoleInit(chapters.exec);

  // 4. 开演！
  chapters.start(story, { qrUrl: LAB_QR_URL });
}

boot();
