// ============================================================
//  ui.js —— 对话框、终端、面板、Toast：所有"人机界面"
//
//  小约定：凡是返回 Promise 的函数，都是"演完这一段再往下走"，
//  游戏剧本（chapters.js）就靠它们串成一场戏。
// ============================================================

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* ---------------- 对话框 ---------------- */

const speakerClass = { '星野凛': 'rin', '咕博士': 'grant', '旁白': 'narrator' };

/** 角色立绘：每位角色一套表情，都取自各自的设计稿
 *  星野凛（少女）：normal 正常 / happy 笑眯眯 / sad 难过 / cry 哭泣 / blush 脸红 / talk 说话
 *  咕博士（企鹅玩偶服）：happy 开心 / angry 生气 / surprised 惊讶 / sad 难过 / blush 害羞
 */
export const FACES = {
  '星野凛': {
    normal: 'assets/rin_emo_normal.png',
    happy:  'assets/rin_emo_happy.png',
    sad:    'assets/rin_emo_sad.png',
    cry:    'assets/rin_emo_cry.png',
    blush:  'assets/rin_emo_blush.png',
    talk:   'assets/rin_emo_talk.png',
  },
  '咕博士': {
    normal:    'assets/grant_idle_1.png',
    happy:     'assets/grant_emo_happy.png',
    angry:     'assets/grant_emo_angry.png',
    surprised: 'assets/grant_emo_surprised.png',
    sad:       'assets/grant_emo_sad.png',
    blush:     'assets/grant_emo_blush.png',
  },
};

/** 谁说话，就亮出谁的脸；旁白这种"没有脸"的说话人就不出立绘 */
function setFace(speaker, face) {
  const img = $('#dialog-face');
  const set = FACES[speaker];
  if (!set) { img.classList.add('hidden'); return; }
  const src = set[face] || set.normal;
  if (!img.getAttribute('src') || !img.getAttribute('src').endsWith(src)) img.src = src;
  img.classList.remove('hidden');
}

/* ---------------- 对话框 ---------------- */

let typeTimer = null;

function dialogShow() { $('#dialog').classList.remove('hidden'); }

function typingInField() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
}

function overlayOpen() {
  return !$('#overlay').classList.contains('hidden')
    || !$('#interstitial').classList.contains('hidden');
}

/** 打字机念一句台词；点击或空格 = 快进，再一次 = 下一段 */
function say(speaker, text, face) {
  return new Promise(resolve => {
    dialogShow();
    const sp = $('#dialog-speaker');
    sp.textContent = speaker;
    sp.className = speakerClass[speaker] || '';
    setFace(speaker, face);
    $('#dialog-choices').innerHTML = '';
    $('#dialog-next').classList.add('hidden');

    const target = $('#dialog-text');
    target.textContent = '';
    let i = 0;
    const finish = () => {
      $('#dialog').removeEventListener('click', onAdvance);
      document.removeEventListener('keydown', onAdvance);
      resolve();
    };
    const onAdvance = (e) => {
      if (e && e.type === 'keydown') {
        if (e.code !== 'Space' && e.key !== ' ') return;
        if (e.repeat || typingInField() || overlayOpen()) return;
        e.preventDefault();
      }
      if (i < text.length) {
        i = text.length;                       // 第一击：快进
      } else {
        finish();
      }
    };
    $('#dialog').addEventListener('click', onAdvance);
    document.addEventListener('keydown', onAdvance);

    clearInterval(typeTimer);
    typeTimer = setInterval(() => {
      target.textContent = text.slice(0, ++i);
      target.scrollTop = target.scrollHeight;   // 让最新打出来的字始终可见
      if (i >= text.length) {
        clearInterval(typeTimer);
        $('#dialog-next').classList.remove('hidden');
      }
    }, 30);
  });
}

let choicesOff = null;

function cancelChoices() {
  if (choicesOff) { choicesOff(); choicesOff = null; }
}

/** 出选项：鼠标点，或左右键挑、回车选定 */
function choices(options) {
  cancelChoices();
  return new Promise(resolve => {
    $('#dialog-next').classList.add('hidden');
    const box = $('#dialog-choices');
    box.innerHTML = '';
    const buttons = [];
    let idx = Math.max(0, options.findIndex(o => o.gold));
    const paint = () => buttons.forEach((b, i) => b.classList.toggle('picked', i === idx));
    const finish = (value) => {
      cancelChoices();
      resolve(value);
    };
    const onKey = (e) => {
      if (typingInField() || overlayOpen()) return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        idx = e.key === 'ArrowLeft'
          ? (idx - 1 + options.length) % options.length
          : (idx + 1) % options.length;
        paint();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        finish(options[idx].value);
      }
    };
    for (const opt of options) {
      const btn = el('button', {
        class: 'pixel-btn' + (opt.gold ? ' gold' : ''),
        onclick: () => finish(opt.value),
      }, opt.label);
      box.append(btn);
      buttons.push(btn);
    }
    document.addEventListener('keydown', onKey);
    choicesOff = () => {
      document.removeEventListener('keydown', onKey);
      box.innerHTML = '';
      choicesOff = null;
    };
    paint();
  });
}

/** 在对话框里放一块自定义内容（比如悄悄话输入框） */
function custom(speaker, text, build, face) {
  return new Promise(resolve => {
    dialogShow();
    const sp = $('#dialog-speaker');
    sp.textContent = speaker;
    sp.className = speakerClass[speaker] || '';
    setFace(speaker, face);
    $('#dialog-text').textContent = text;
    $('#dialog-next').classList.add('hidden');
    const box = $('#dialog-choices');
    box.innerHTML = '';
    build(box, resolve);
  });
}

function dialogHide() { $('#dialog').classList.add('hidden'); }

/* ---------------- Toast ---------------- */

function toast(msg, good = false) {
  const item = el('div', { class: 'toast-item' + (good ? ' good' : '') }, msg);
  $('#toast').append(item);
  setTimeout(() => { item.style.opacity = '0'; item.style.transition = 'opacity .4s'; }, 4200);
  setTimeout(() => item.remove(), 4700);
}

/* ---------------- HUD ---------------- */

function hudInit() {
  document.addEventListener('pointerdown', startBgm);
  $('#hud').classList.remove('hidden');
  $('#topbtns').classList.remove('hidden');
  $('#bottombtns').classList.remove('hidden');
  $('#shards').innerHTML = '';
  for (let i = 1; i <= 7; i++) {
    $('#shards').append(el('div', { class: 'shard', id: 'shard-' + i, title: '碎片 ' + i }));
  }
}

function hudUpdate(state) {
  // 碎片点亮看具体编号（玩家可能按任意顺序找回——服务器说了算）
  const ids = new Set(state.foundFragments.map(f => f.id));
  for (let i = 1; i <= 7; i++) $('#shard-' + i).classList.toggle('lit', ids.has(i));
  $('#shards-count').textContent = `${state.foundCount} / ${state.totalCount}`;
  // 稳定度条的真实数值由 chapters.js 从 /world/status 喂进来
}

/** 世界稳定度仪表（数值来自服务器的 /world/status） */
function setStabilityBar(pct) {
  $('#stability-num').textContent = pct + '%';
  const fill = $('#stability-fill');
  fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
  fill.classList.toggle('ok', pct >= 100);
}

/* ---------------- 终端 ---------------- */

function consoleToggle(open) {
  const c = $('#console');
  const willOpen = open === undefined ? c.classList.contains('hidden') : open;
  c.classList.toggle('hidden', !willOpen);
  // 终端打开时，把右下角那个"终端"按钮压暗（它此时被终端盖住了）
  $('#console-toggle')?.classList.toggle('while-open', willOpen);
  if (willOpen) $('#console-input').focus();
}

/** 往终端里记一次请求：方法徽标 + 路径 + 状态码（按状态分色） */
function conLog(method, path, status) {
  const cls = status >= 500 || status >= 400 ? 'err' : status >= 300 ? 'warn' : 'ok';
  const box = $('#console-log');
  box.append(el('div', { class: 'con-line' },
    el('span', { class: 'con-method' + (method === 'POST' ? ' m-post' : '') }, method),
    el('span', { class: 'con-path' }, path),
    el('span', { class: 'con-status ' + cls }, String(status))));
  box.scrollTop = 1e9;
}

/** 打印服务器返回的内容：JSON 会带上左边那道竖线 */
function conPrint(text, cls = 'con-res') {
  const isJson = cls === 'con-res' && /^[\[{]/.test(String(text).trim());
  const box = $('#console-log');
  box.append(el('div', { class: isJson ? 'con-json' : cls }, text));
  box.scrollTop = 1e9;
}

/** 把输入框里的 "GET /path" 或 "POST /path {json}" 拆开 */
function parseCommand(raw) {
  const m = raw.trim().match(/^(GET|POST|PUT|DELETE)\s+(\S+)(?:\s+([\s\S]+))?$/i);
  if (!m) return null;
  let body;
  if (m[3]) {
    try { body = JSON.parse(m[3]); } catch { return { error: '请求体不是合法 JSON：' + m[3] }; }
  }
  return { method: m[1].toUpperCase(), path: m[2], body };
}

function consoleInit(onCommand) {
  const input = $('#console-input');
  const history = [];        // 敲过的命令，用 ↑ ↓ 翻
  let cursor = -1;

  $('#console-toggle').addEventListener('click', () => consoleToggle());
  $('#console-close').addEventListener('click', () => consoleToggle(false));
  $('#console-clear').addEventListener('click', () => { $('#console-log').innerHTML = ''; });

  input.addEventListener('keydown', async (e) => {
    // ↑ ↓ 翻历史：终端该有的手感
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault();
      if (!history.length) return;
      if (e.key === 'ArrowUp') cursor = cursor < 0 ? history.length - 1 : Math.max(0, cursor - 1);
      else cursor = cursor < 0 ? -1 : Math.min(history.length - 1, cursor + 1);
      input.value = cursor < 0 ? '' : history[cursor];
      return;
    }
    if (e.key !== 'Enter') return;
    const raw = input.value;
    if (!raw.trim()) return;                       // 空命令：什么都不做
    history.push(raw.trim());
    cursor = -1;
    input.value = '';
    const cmd = parseCommand(raw);
    if (!cmd) { conPrint('念不出这样的咒语。格式：GET /路径  或  POST /路径 {"键":"值"}', 'con-err'); input.focus(); return; }
    if (cmd.error) { conPrint(cmd.error, 'con-err'); input.focus(); return; }
    await onCommand(cmd.method, cmd.path, cmd.body); // 真正执行交给外面
    input.focus();                                 // 发完保持焦点，好接着敲下一条
  });

  // 拖动终端：按住标题栏拖到任意位置（用 left/top 定位，并限制在窗口内）
  (() => {
    const head = $('#console-head');
    const box = $('#console');
    let drag = null;
    head.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;      // 别把"清空/关闭"当拖动手柄
      const r = box.getBoundingClientRect();
      box.style.left = r.left + 'px';
      box.style.top = r.top + 'px';
      box.style.right = 'auto';
      box.style.bottom = 'auto';
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!drag) return;
      const w = box.offsetWidth, h = box.offsetHeight;
      const x = Math.min(Math.max(0, e.clientX - drag.dx), window.innerWidth - w);
      const y = Math.min(Math.max(0, e.clientY - drag.dy), window.innerHeight - h);
      box.style.left = x + 'px';
      box.style.top = y + 'px';
    });
    document.addEventListener('mouseup', () => { drag = null; });
  })();

  // 点终端面板任意处，焦点都回到输入行（省得去瞄准那个细长的输入框）
  $('#console').addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    input.focus();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') consoleToggle(false);
  });
}

function consolePrefill(text) {
  consoleToggle(true);
  const input = $('#console-input');
  input.value = text;
  input.focus();
  // 光标放到行尾，玩家按回车就能发
  input.setSelectionRange(text.length, text.length);
}

/* ---------------- 全屏黑页 ---------------- */

/**
 * 铺满屏幕的黑页：一段话 + 一个按钮（可带倒计时自动继续）。
 * 用于"世界修复完成""通关之门"，以及询问玩家要不要重开。
 * options: { title, lines: [], button, buttons: [{label, value, primary}], countdown, countdownNote, note }
 *   返回 { value } —— value 是按钮上的 value，或 'timeout'
 */
function blackout(options = {}) {
  return new Promise(resolve => {
    const box = $('#interstitial');
    $('#inter-title').textContent = options.title || '';
    $('#inter-lines').textContent = (options.lines || []).join('\n');
    const btn = $('#inter-btn');
    const row = $('#inter-btns');
    const note = $('#inter-note');
    const linesEl = $('#inter-lines');
    linesEl.classList.toggle('italic', !!options.italic);
    let timer = null;
    let onKey = null;
    let onClick = null;
    const finish = (value) => {
      if (timer) clearInterval(timer);
      if (onKey) document.removeEventListener('keydown', onKey);
      if (onClick) box.removeEventListener('click', onClick);
      box.classList.add('hidden');
      btn.onclick = null;
      row.innerHTML = '';
      linesEl.classList.remove('italic');
      resolve({ value });
    };
    row.innerHTML = '';
    if (options.buttons && options.buttons.length) {
      btn.classList.add('hidden');
      const nodes = [];
      let idx = 0;
      const paint = () => nodes.forEach((n, i) => n.classList.toggle('picked', i === idx));
      onKey = (e) => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          idx = e.key === 'ArrowLeft'
            ? (idx - 1 + nodes.length) % nodes.length
            : (idx + 1) % nodes.length;
          paint();
        } else if (e.key === 'Enter') {
          e.preventDefault();
          finish(options.buttons[idx].value);
        }
      };
      for (const b of options.buttons) {
        const node = el('button', {
          class: 'pixel-btn sky',
          onclick: () => finish(b.value),
        }, b.label);
        row.append(node);
        nodes.push(node);
      }
      document.addEventListener('keydown', onKey);
      paint();
    } else if (options.button) {
      btn.textContent = options.button;
      btn.classList.remove('hidden');
      btn.onclick = () => finish('button');
      onKey = (e) => {
        if (typingInField()) return;
        if (e.key === 'Enter' || e.code === 'Space' || e.key === ' ') {
          e.preventDefault();
          finish('button');
        }
      };
      document.addEventListener('keydown', onKey);
    } else if (options.clickToContinue) {
      btn.classList.add('hidden');
      onClick = () => finish('button');
      box.addEventListener('click', onClick);
      onKey = (e) => {
        if (typingInField()) return;
        if (e.key === 'Enter' || e.code === 'Space' || e.key === ' ') {
          e.preventDefault();
          finish('button');
        }
      };
      document.addEventListener('keydown', onKey);
    } else {
      btn.classList.add('hidden');
    }
    // 倒计时（通关之门用：不点也会自己走过去）
    if (options.countdown > 0) {
      let left = options.countdown;
      const render = () => {
        note.textContent = (options.countdownNote || '（{seconds} 秒后自动继续）')
          .replace('{seconds}', left);
      };
      render();
      timer = setInterval(() => {
        left -= 1;
        if (left <= 0) { finish('timeout'); return; }
        render();
      }, 1000);
    } else {
      note.textContent = options.note || '';
    }
    box.classList.remove('hidden');
  });
}

/* ---------------- 覆盖层面板 ---------------- */

function panelOpen(title, ...bodyNodes) {
  $('#panel').classList.remove('whisper-panel');
  $('#panel-title').textContent = title;
  const body = $('#panel-body');
  body.innerHTML = '';
  body.append(...bodyNodes);
  $('#overlay').classList.remove('hidden');
}
function panelClose() {
  $('#overlay').classList.add('hidden');
  $('#panel').classList.remove('whisper-panel');
}
function panelInit() { $('#panel-close').addEventListener('click', panelClose); }

/* ---------------- 背景音乐 ---------------- */

let bgm = null;

function ensureBgm() {
  if (bgm) return bgm;
  bgm = new Audio('assets/bgm.mp3');
  bgm.loop = true;
  bgm.volume = 0.42;
  return bgm;
}

function startBgm() {
  const a = ensureBgm();
  if (!a.paused) return;
  const p = a.play();
  if (p && p.catch) p.catch(() => {});
}

export {
  el, sleep, $, blackout,
  say, choices, custom, dialogHide, dialogShow,
  toast,
  hudInit, hudUpdate, setStabilityBar,
  consoleInit, consoleToggle, consolePrefill, conLog, conPrint,
  panelOpen, panelClose, panelInit,
  cancelChoices, startBgm,
};
