// ============================================================
//  chapters.js —— 游戏剧本：七个碎片，一场冒险
//
//  规则只有一条：【以服务器为准】。
//  玩家刷新页面、甚至重启服务器，只要回来问一句
//  /game/state，剧情就能从正确的位置继续。
//
//  每一关 = 等玩家发出正确的请求 = waitFragment(n) 返回。
//  无论是网页按钮、终端输入框，还是玩家自己开的真终端 curl，
//  剧情都会被推动——因为推动世界的从来不是界面，而是请求。
// ============================================================

import * as api from './api.js';
import { LAB_VIDEO_URL } from './config.js';
import * as ui from './ui.js';
import * as world from './world.js';

let story = null;
let opts = { qrUrl: '' };
let S = null;                 // 最近一次 /game/state 的快照

/* ---------- 服务器状态轮询 ---------- */

async function refresh() {
  try {
    const [st, w] = await Promise.all([api.get('/game/state'), api.get('/world/status')]);
    if (!st.ok) return false;
    S = st.data;
    if (w.ok && typeof w.data.stability === 'number') {
      ui.setStabilityBar(w.data.stability);
      world.setStability(w.data.stability);
    }
    ui.hudUpdate(S);
    return true;
  } catch {
    return false;               // 服务器重启中，稍后再问
  }
}

/** 一直等到第 n 片碎片被找回（不管是谁、用什么方式找回来的） */
async function waitFragment(n) {
  for (;;) {
    if (await refresh()) {
      if (S.foundCount >= n) return;
    } else {
      ui.toast('和服务器断开连接了……如果刚才在重启，几秒后会自动重连 ⏳');
      await ui.sleep(2500);
      continue;
    }
    await ui.sleep(500);
  }
}

/* ---------- 请求执行器（终端 / 按钮都走这里） ---------- */

export async function exec(method, path, body) {
  let r;
  try {
    r = await api.request(method, path, body);
  } catch {
    ui.toast('服务器失联了……如果刚才在重启，稍等几秒再发一次。');
    return null;
  }
  ui.conLog(method, path, r.status);
  if (r.ok && r.data) ui.conPrint(JSON.stringify(r.data, null, 2));
  if (!r.ok && r.data && r.data.message) ui.toast(r.data.message);  // 403 的嘲讽从这里飘出
  if (r.ok) await refresh();
  return r;
}

/* ---------- 样本库视图（可反复打开） ---------- */

async function openArchive() {
  const r = await exec('GET', '/archive/journey');
  if (!r || !r.ok) return;
  const d = r.data;
  const table = ui.el('table', { class: 'journey' });
  const thead = ui.el('tr', {}, ...d.columns.map(c => ui.el('th', {}, c)));
  table.append(ui.el('thead', {}, thead));
  const tbody = ui.el('tbody');
  for (const row of d.rows) {
    tbody.append(ui.el('tr', {},
      ui.el('td', {}, String(row.id)),
      ui.el('td', {}, row.time.replace('T', ' ').slice(0, 19)),
      ui.el('td', {}, `${row.method} ${row.path}${row.query ? '?' + row.query : ''}`),
      ui.el('td', { class: 'status-' + String(row.status)[0] }, String(row.status))));
  }
  table.append(tbody);
  ui.panelOpen('📖 ' + d.title,
    ui.el('p', {}, d.intro),
    ui.el('p', { class: 'hint' }, d.bookLine || ''),
    table,
    ui.el('p', { class: 'hint' }, d.peep || '每一行，都是你发出的一条 HTTP 请求。'));
}

/* ---------- 监控中心视图 ---------- */

let selectedLog = null;

async function openMonitor() {
  const r = await exec('GET', '/monitor/logs');
  if (!r || !r.ok) return;
  const d = r.data;
  selectedLog = null;

  const crt = ui.el('div', { class: 'crt' });
  crt.append(ui.el('div', {}, '── ' + d.caseTitle + ' ──'));
  crt.append(ui.el('div', { class: 'hint' }, d.caseIntro));
  crt.append(ui.el('div', {}, ''));
  for (const log of d.logs) {
    crt.append(ui.el('div', {
      class: 'crt-line',
      onclick: (e) => {
        selectedLog = log.id;
        crt.querySelectorAll('.crt-line').forEach(n => n.classList.remove('selected'));
        e.currentTarget.classList.add('selected');
        verdictInput.value = log.id;
      },
    },
      ui.el('span', { class: 'log-id' }, `[${log.id}] `),
      ui.el('span', { class: log.level }, `${log.time} ${log.level}  `),
      ui.el('span', {}, log.text)));
  }

  const verdictInput = ui.el('input', { placeholder: '点选一行，或手动输入编号' });
  const submit = ui.el('button', { class: 'pixel-btn gold' }, '🔒 锁定真相');
  submit.addEventListener('click', async () => {
    const rr = await exec('POST', '/monitor/verdict', { logId: verdictInput.value });
    if (rr && rr.ok) {
      ui.panelClose();
      if (rr.data.verdictOk) {
        await ui.say('旁白', rr.data.verdictOk);
        await ui.say('星野凛', rr.data.revealLine, 'cry');
        await ui.say('星野凛', rr.data.foundLine);
      } else {
        await ui.say('旁白', rr.data.message || '真相已经锁定了。');
      }
      await refresh();
    }
  });
  ui.panelOpen('📺 ' + d.caseTitle.replace('档案编号', '监控中心 · 档案编号'),
    crt,
    ui.el('div', { class: 'verdict-row' },
      ui.el('span', {}, '异常日志编号：'), verdictInput, submit),
    ui.el('p', { class: 'hint' }, '提示：正常的日志按时钟均匀运转。异常的那条，像一颗卡进齿轮的石子。'));
}

/* ---------- 开场与各章 ---------- */

async function intro() {
  await refresh();
  if (S.returning) { world.echoBounce(); await ui.say('星野凛', story.rin.returning, 'happy'); }
  if (S.foundCount === 0) {
    for (const line of story.intro) {
      if (line.speaker === '星野凛') world.echoBounce();
      await ui.say(line.speaker, line.text);
    }
  } else {
    await ui.say('星野凛', `欢迎回来，访客。碎片已找回 ${S.foundCount} / 7。我们从停下的地方继续。`);
  }
}

async function ch1() {
  world.setChapter(1);
  world.grantBounce();
  await ui.say('咕博士', story.grant.note, 'sad');
  for (;;) {
    const pick = await ui.choices([
      { label: '⌨ 打开终端，发出第一条请求', value: 'console', gold: true },
      { label: '「乱码……说的啥？」', value: 'again' },
    ]);
    if (pick === 'again') {
      await ui.say('星野凛', '他说：跟精灵说话要用她的方式——往服务器发一条 GET /rin/hello 。网页按钮和终端命令，殊途同归。');
      continue;
    }
    ui.consolePrefill('GET /rin/hello');
    await ui.say('旁白', '终端已经打开，命令都替你敲好了——按下回车，发出你人生第一条 HTTP 请求。（你也可以最小化网页，打开真的终端输入同样的命令）');
    break;
  }
  await waitFragment(1);
  await ui.say('星野凛', story.rin.foundLines[1], 'happy');
}

async function ch2() {
  world.setChapter(2);
  world.echoBounce();
  await ui.say('星野凛', story.fragments[2].taskLine);
  for (;;) {
    const pick = await ui.choices([
      { label: '🔍 我该打开哪个文件？', value: 'where', gold: true },
      { label: '「代码我看不懂啊」', value: 'hint' },
      { label: '⌨ 我已经在终端念出咒语了', value: 'done' },
    ]);
    if (pick === 'where') {
      await ui.say('星野凛', '用你自己的编辑器打开项目里的 src/main/java/lab_dream/controller/MemoryFragmentController.java —— 记事本、VS Code、IntelliJ 都行。\n\n以 // 开头的行，是历代管理员留下的批注。咒语清单就藏在里面：找到那个「还没有人召唤过的路由」。');
    } else if (pick === 'hint') {
      await ui.say('星野凛', story.fragments[2].hint);
    } else {
      break;
    }
    await refresh();
    if (S.foundCount >= 2) break;
  }
  await waitFragment(2);
  await ui.say('星野凛', story.rin.foundLines[2], 'happy');
}

/** 把那首藏头诗唱一遍（线索就在歌里，所以允许她再唱一次） */
/** 世界修好的那一刻，郑重地讲一遍。
    两条路径都要走到：正常流程（第 6 关改完配置收集到碎片）与
    "重启服务器后自动续上"（后端在你重启时就把碎片收了，页面直接进第 7 关）。 */
let repairedShown = false;
async function showRepairedOnce(story) {
  if (repairedShown) return;
  repairedShown = true;
  await ui.blackout({
    title: story.pages.repaired.title,
    lines: story.pages.repaired.lines,
    button: story.pages.repaired.button,
  });
}

async function singPoem() {
  await ui.say('星野凛', story.fragments[3].poemIntro);
  for (const line of story.fragments[3].poem) {
    world.echoBounce();
    await ui.say('星野凛', '♪ ' + line);
  }
  await ui.say('星野凛', story.fragments[3].poemOutro);
  // 听完歌，马上告诉她"答案该往哪儿填"——不然猜出来也不知道怎么用
  await ui.say('星野凛', story.fragments[3].howToLine);
}

async function ch3() {
  world.setChapter(3);
  world.echoBounce();
  await ui.say('星野凛', story.fragments[3].taskLine);
  await singPoem();
  ui.consolePrefill('GET /memory/fragment/3?spell=');
  const done3 = waitFragment(3);
  const guessStarted = Date.now();
  let showAnswer = false;
  while (!(S && S.foundCount >= 3)) {
    if (Date.now() - guessStarted >= 60000) showAnswer = true;
    const opts = [
      { label: '去终端试试咒语', value: 'console', gold: true },
      { label: '再唱一次', value: 'sing' },
      { label: '请求星野凛提示', value: 'hint' },
    ];
    if (showAnswer) opts.push({ label: '查看答案', value: 'answer' });
    const waiters = [done3.then(() => 'got'), ui.choices(opts)];
    if (!showAnswer) {
      waiters.push(ui.sleep(Math.max(0, 60000 - (Date.now() - guessStarted))).then(() => 'tick'));
    }
    const pick = await Promise.race(waiters);
    if (pick === 'got' || (S && S.foundCount >= 3)) break;
    if (pick === 'tick') {
      showAnswer = true;
      ui.cancelChoices();
      continue;
    }
    if (pick === 'answer') {
      const ans = atob('bHVtb3M=');
      await ui.say('旁白', `守门人要的咒语是 ${ans}。\n在终端里发送：GET /memory/fragment/3?spell=${ans}`);
      ui.consolePrefill('GET /memory/fragment/3?spell=' + ans);
    } else if (pick === 'sing') await singPoem();
    else if (pick === 'hint') await ui.say('星野凛', story.fragments[3].hint);
    else if (pick === 'console') ui.consolePrefill('GET /memory/fragment/3?spell=');
    await refresh();
  }
  ui.cancelChoices();
  await done3;
  await ui.say('星野凛', story.rin.foundLines[3], 'happy');
}

async function ch4() {
  world.setChapter(4);
  await ui.say('星野凛', story.fragments[4].taskLine);
  await ui.choices([{ label: '🚪 推开档案馆的门', value: 'go', gold: true }]);
  await openArchive();
  await waitFragment(4);
  await ui.say('星野凛', story.rin.foundLines[4], 'happy');
  await ui.say('旁白', '顺便：档案馆里的每一条记录，都来自数据库里的 journey 表。整个数据库就是项目目录下的 data/world.db ——你甚至可以用工具打开它亲眼看。');
}

async function ch5() {
  world.setChapter(5);
  await ui.say('星野凛', story.fragments[5].taskLine);
  await ui.choices([{ label: '📺 走进监控中心', value: 'go', gold: true }]);
  // 面板只打开一次，然后安心等玩家把真相锁出来。
  // （之前这里是个循环反复重开面板，会把玩家刚点中的日志和输入冲掉，导致要锁两次。）
  await openMonitor();
  await waitFragment(5);
  await ui.say('星野凛', story.rin.foundLines[5], 'sad');
  await ui.say('旁白', '现在你知道该怎么修复世界了——真相就在 config 文件里等着你。');
}

async function ch6() {
  world.setChapter(6);
  world.echoBounce();
  await ui.say('星野凛', story.fragments[6].taskLine);
  await ui.say('星野凛', story.fragments[6].hint);
  await ui.say('旁白', '温馨提示：重启服务器后，这个世界会自己发现配置被改好了——你只要回到这个页面，第六片碎片就自动归位。\n\n（你敲过的命令、找回的碎片都不会丢：它们持久化在数据库里了。）');
  document.querySelector('#btn-world').classList.remove('hidden');
  const done = waitFragment(6);
  while (!(S && S.foundCount >= 6)) {
    const pick = await Promise.race([
      done.then(() => 'got'),
      ui.choices([{ label: '查看问题', value: 'check' }]),
    ]);
    if (pick === 'got' || (S && S.foundCount >= 6)) break;
    await openWorldStatus();
    await refresh();
  }
  ui.cancelChoices();
  await done;
  document.querySelector('#btn-world').classList.add('hidden');
  await showRepairedOnce(story);          // 世界刚刚被修好
  await ui.say('星野凛', story.rin.foundLines[6], 'happy');
}

async function ch7() {
  world.setChapter(7);
  await ui.say('星野凛', story.fragments[7].taskLine, 'blush');
  await ui.custom('星野凛', '把想说的话写下来。它会作为 POST 请求的请求体，被写进数据库——重启也不会消失。', (box, resolve) => {
    const open = ui.el('button', { class: 'pixel-btn gold' }, '写给你面前的小精灵');
    open.addEventListener('click', () => openWhisper(resolve));
    box.append(open);
  });
  await waitFragment(7);
  await ui.say('星野凛', story.rin.foundLines[7], 'blush');
}

function growArea(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 240) + 'px';
}

function whisperCount(s) {
  return s.replace(/\r|\n/g, '').length;
}

function sanitizeWhisper(s) {
  s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  s = s.replace(/^\n+/, '');
  s = s.replace(/\n{2,}/g, '\n');
  return s;
}

function currentLine(ta) {
  const v = ta.value, i = ta.selectionStart ?? v.length;
  const from = v.lastIndexOf('\n', i - 1) + 1;
  return v.slice(from, i);
}

function openWhisper(resolve) {
  const ta = ui.el('textarea');
  const counter = ui.el('div', { class: 'counter' }, '0 / 200');
  const send = ui.el('button', { class: 'pixel-btn gold' }, 'POST 我的悄悄话');
  let sending = false;

  const sync = () => {
    const next = sanitizeWhisper(ta.value);
    if (next !== ta.value) {
      const pos = ta.selectionStart;
      ta.value = next;
      const p = Math.min(pos, next.length);
      ta.setSelectionRange(p, p);
    }
    counter.textContent = whisperCount(ta.value) + ' / 200';
    growArea(ta);
  };

  const submit = async () => {
    if (sending) return;
    const message = ta.value.trim();
    if (whisperCount(message) > 200) {
      ui.toast('最多说200字哦');
      return;
    }
    sending = true;
    const r = await exec('POST', '/memory/fragment/7', { message });
    sending = false;
    if (r && r.ok) {
      ui.panelClose();
      ui.dialogHide();
      resolve();
    }
  };

  ta.addEventListener('input', sync);
  ta.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key !== 'Enter') return;
    if (e.shiftKey) {
      if (!currentLine(ta).trim()) e.preventDefault();
      return;
    }
    e.preventDefault();
    submit();
  });
  send.addEventListener('click', submit);

  ui.panelOpen('悄悄话',
    ui.el('div', { class: 'whisper-box' },
      ta,
      ui.el('div', { class: 'whisper-row' }, counter, send)));
  document.querySelector('#panel').classList.add('whisper-panel');
  ta.focus();
}

async function finale() {
  world.setChapter(8);
  await refresh();
  const fin = (await exec('GET', '/finale')).data;
  await ui.say('旁白', fin.restored);
  world.grantBounce();
  await ui.say('咕博士', fin.grantLine, 'happy');
  world.celebrate();
  await ui.say('星野凛', fin.echoLine, 'happy');
  world.doorOpen();
  await ui.say('旁白', fin.doorLine);
  await ui.say('门', fin.recruitLine);

  for (;;) {
    const pick = await ui.choices([
      { label: '📜 查看我的修复者证书', value: 'cert', gold: true },
      { label: '☕ 再在实验室里逛逛', value: 'roam' },
    ]);
    if (pick === 'cert') await showCertificate();
    else break;
  }
  await ui.say('星野凛', fin.secondRun);

  // 通关之门：一扇通往真实世界的门（点按钮或等倒计时都会过去）
  const gate = story.pages.gate;
  await ui.blackout({
    title: gate.title,
    lines: gate.lines,
    button: gate.button,
    countdown: gate.countdown,
    countdownNote: gate.countdownNote,
  });
  window.location.href = LAB_VIDEO_URL;     // 门外是真实世界
}

async function showCertificate() {
  const r = await exec('GET', '/finale/certificate');
  if (!r || !r.ok) return;
  const d = r.data;
  const qr = opts.qrUrl
    ? ui.el('img', { class: 'qr-img', src: opts.qrUrl, alt: '实验室二维码' })
    : ui.el('div', { class: 'qr' }, '实验室二维码\n（发布前替换）');
  ui.panelOpen('📜 ' + d.title,
    ui.el('div', { class: 'certificate' },
      ui.el('h1', {}, d.title),
      ui.el('h2', {}, d.subtitle),
      ui.el('div', { class: 'seal' }, '✦'),
      ui.el('div', { class: 'lines' }, ...d.lines.map(l => ui.el('div', {}, l))),
      qr,
      ui.el('div', { class: 'footer' }, d.footer)));
}

/* ---------- 右上角按钮注册 ---------- */

async function openWorldStatus() {
  await ui.say('旁白', story.fragments[6].taskLine);
}

async function confirmReset() {
  const cancel = ui.el('button', { class: 'pixel-btn' }, '取消');
  const ok = ui.el('button', { class: 'pixel-btn gold' }, '确定重置');
  cancel.addEventListener('click', () => ui.panelClose());
  ok.addEventListener('click', async () => {
    await api.post('/game/restart');
    window.location.reload();
  });
  ui.panelOpen('重置进度',
    ui.el('p', {}, '七片碎片会重新散落，世界也会回到崩塌。确定吗？'),
    ui.el('div', { class: 'reset-actions' }, cancel, ok));
}

function registerButtons() {
  document.querySelector('#btn-archive').addEventListener('click', openArchive);
  document.querySelector('#btn-monitor').addEventListener('click', openMonitor);
  document.querySelector('#btn-world').addEventListener('click', openWorldStatus);
  document.querySelector('#btn-reset').addEventListener('click', confirmReset);
}

async function syncButtons() {
  const showArchive = S && S.foundCount >= 3;   // 第 3 片到手，档案馆的门才开
  const showMonitor = S && S.foundCount >= 4;
  document.querySelector('#btn-archive').classList.toggle('hidden', !showArchive);
  document.querySelector('#btn-monitor').classList.toggle('hidden', !showMonitor);
}

/* ---------- 主流程 ---------- */

export async function start(storyData, options = {}) {
  story = storyData;
  opts = options;
  registerButtons();
  await refresh();

  // 第一次进实验室、或「再来一次」清零之后：先黑屏提醒戴耳机，顺便解开浏览器的自动播放限制
  if (S.foundCount === 0) {
    await ui.blackout({
      title: '',
      lines: ['温馨提醒：带上耳机，效果更佳。'],
      italic: true,
      clickToContinue: true,
    });
  }
  ui.startBgm();

  // 上次已经把七片碎片全找回来了？那就在门口问一句
  if (S.foundCount >= S.totalCount) {
    const chooser = story.pages.chooser;
    const pick = await ui.blackout({
      title: chooser.title,
      lines: chooser.lines,
      buttons: [
        { label: chooser.continueLabel, value: 'continue', primary: true },
        { label: chooser.againLabel, value: 'again' },
      ],
    });
    if (pick.value === 'again') {
      await api.post('/game/restart');          // 清空进度 + 把世界规则写回崩塌
      const page = story.pages.restart;
      await ui.blackout({ title: page.title, lines: page.lines, button: page.button });
      window.location.reload();                 // 刷新后就是全新的一周目
      return;
    }
  }

  await intro();

  // 重启服务器后自动续上的情况：进页面时世界已经修好、第六片也到手，
  // 但这段"修复完成"的过场还没看过 —— 补上。
  if (S.foundCount >= 6 && S.nextFragmentId === 7) {
    await showRepairedOnce(story);
  }

  for (;;) {
    await syncButtons();
    const next = S.nextFragmentId;
    if (next == null) { await finale(); break; }
    const chapter = [ch1, ch2, ch3, ch4, ch5, ch6, ch7][next - 1];
    await chapter();
  }
}
