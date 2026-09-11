/* ============================================================
 * 真实终端本地服务
 * ------------------------------------------------------------
 * 用法：
 *   在本文件所在目录（index.html 所在目录）执行：
 *     node server.cjs
 *   然后浏览器打开控制台打印的地址（默认 http://127.0.0.1:3210/）。
 *
 * 说明：
 *   - 页面里终端的命令会在这台电脑上【真实执行】，
 *     默认工作目录 = 本文件所在目录（即 index.html 所在目录）。
 *   - 服务只绑定 127.0.0.1，只供本机访问。
 *   - 自动挑选可用的真实 Shell：Git Bash > bash > PowerShell > cmd。
 * ============================================================ */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn, spawnSync } = require('child_process');

const ROOT = __dirname;              // index.html 所在目录 = 命令默认工作目录
const HOST = '127.0.0.1';
const PORT = Number(process.env.PORT) || 3210;
const CMD_TIMEOUT_MS = 60000;        // 单条命令超时上限

/* ---------------- 挑选可用的真实 Shell ---------------- */
function shellWorks(file, prefix) {
  try {
    const r = spawnSync(file, prefix.concat(['echo __SHELL_OK__']), {
      encoding: 'utf8', timeout: 4000, windowsHide: true
    });
    return r.status === 0 && /__SHELL_OK__/.test(r.stdout || '');
  } catch (e) { return false; }
}

function pickShell() {
  const cands = [];
  if (process.platform === 'win32') {
    const pf = process.env.ProgramFiles || 'C:\\Program Files';
    const pf86 = process.env['ProgramFiles(x86)'] || pf;
    cands.push({ name: 'Git Bash', file: path.join(pf, 'Git', 'bin', 'bash.exe'), prefix: ['-c'] });
    cands.push({ name: 'Git Bash(x86)', file: path.join(pf86, 'Git', 'bin', 'bash.exe'), prefix: ['-c'] });
    cands.push({ name: 'bash', file: 'bash.exe', prefix: ['-c'] });
    cands.push({ name: 'PowerShell 7', file: 'pwsh.exe', prefix: ['-NoProfile', '-NonInteractive', '-Command'] });
    cands.push({ name: 'Windows PowerShell', file: 'powershell.exe', prefix: ['-NoProfile', '-NonInteractive', '-Command'] });
    cands.push({ name: 'cmd', file: process.env.ComSpec || 'cmd.exe', prefix: ['/d', '/c'] });
  } else {
    cands.push({ name: 'bash', file: 'bash', prefix: ['-c'] });
    cands.push({ name: 'sh', file: 'sh', prefix: ['-c'] });
  }
  for (const c of cands) {
    const hasSep = c.file.indexOf('\\') > -1 || c.file.indexOf('/') > -1;
    if (hasSep && !fs.existsSync(c.file)) continue;
    if (shellWorks(c.file, c.prefix)) return c;
  }
  // 全都失败时的兜底
  return process.platform === 'win32'
    ? { name: 'cmd', file: process.env.ComSpec || 'cmd.exe', prefix: ['/d', '/c'] }
    : { name: 'sh', file: 'sh', prefix: ['-c'] };
}

const shell = pickShell();

/* ---------------- 会话状态（真实命令的工作目录） ---------------- */
const state = {
  cwd: ROOT,
  home: os.homedir(),
  shellName: shell.name,
  username: (function () {
    try { return os.userInfo().username; } catch (e) { return process.env.USERNAME || 'user'; }
  })(),
  host: os.hostname(),
  platform: process.platform
};

/* ---------------- cd 内置处理（跨进程保持目录切换） ---------------- */
function stripQuotes(s) {
  if (s.length >= 2 && ((s[0] === '"' && s[s.length - 1] === '"') || (s[0] === "'" && s[s.length - 1] === "'"))) {
    return s.slice(1, -1);
  }
  return s;
}

function tryCd(rawCmd) {
  const m = /^cd(?:\s+(.*))?$/.exec(rawCmd.trim());
  if (!m) return null;
  let arg = (m[1] || '').trim();
  arg = arg.replace(/^\/d\s+/, '');          // cmd 的 /d 开关
  if (!arg) { state.cwd = state.home; return { ok: true, msg: '' }; }
  arg = stripQuotes(arg);
  if (arg === '~') { state.cwd = state.home; return { ok: true, msg: '' }; }
  if (arg.indexOf('~/') === 0) arg = path.join(state.home, arg.slice(2));
  else if (arg.indexOf('~\\') === 0) arg = path.join(state.home, arg.slice(2));
  const target = path.resolve(state.cwd, arg);
  try {
    if (!fs.statSync(target).isDirectory()) {
      return { ok: false, msg: 'cd: ' + arg + ': Not a directory' };
    }
  } catch (e) {
    return { ok: false, msg: 'cd: ' + arg + ': No such file or directory' };
  }
  state.cwd = target;
  return { ok: true, msg: '' };
}

/* ---------------- 执行一条真实命令 ---------------- */
let activeChild = null;

function runCommand(cmd) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(shell.file, shell.prefix.concat([cmd]), {
        cwd: state.cwd,
        env: process.env,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      });
    } catch (e) {
      resolve({ stdout: '', stderr: '[启动失败] ' + e.message, code: null, timedout: false, killed: false });
      return;
    }
    activeChild = child;
    let stdout = '';
    let stderr = '';
    let timedout = false;
    let killed = false;
    const to = setTimeout(() => {
      timedout = true;
      try { child.kill('SIGKILL'); } catch (e) { /* ignore */ }
    }, CMD_TIMEOUT_MS);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (d) => { stdout += d; });
    child.stderr.on('data', (d) => { stderr += d; });
    child.on('error', (e) => {
      stderr += (stderr ? '\n' : '') + '[进程错误] ' + e.message;
    });
    child.on('close', (code, sig) => {
      clearTimeout(to);
      if (activeChild === child) activeChild = null;
      killed = sig === 'SIGKILL' || sig === 'SIGTERM';
      resolve({ stdout: stdout, stderr: stderr, code: code, timedout: timedout, killed: killed });
    });
  });
}

/* ---------------- 极简 HTTP 服务 ---------------- */
function sendJson(res, obj, status) {
  const body = JSON.stringify(obj);
  res.writeHead(status || 200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => { data += c; if (data.length > 1e6) req.destroy(); });
    req.on('end', () => { try { resolve(data ? JSON.parse(data) : {}); } catch (e) { reject(e); } });
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://' + (req.headers.host || '127.0.0.1'));
  const p = u.pathname;

  try {
    if (req.method === 'GET' && p === '/api/info') {
      sendJson(res, {
        ok: true,
        shell: state.shellName,
        platform: state.platform,
        username: state.username,
        host: state.host,
        home: state.home,
        root: ROOT,
        cwd: state.cwd
      });
      return;
    }

    if (req.method === 'POST' && p === '/api/exec') {
      const body = await readBody(req);
      const cmd = String(body.cmd || '').trim();
      if (!cmd) { sendJson(res, { ok: false, error: 'empty command' }); return; }

      const cd = tryCd(cmd);
      if (cd) {
        sendJson(res, {
          ok: true,
          cwd: state.cwd,
          stdout: cd.ok ? cd.msg : '',
          stderr: cd.ok ? '' : cd.msg,
          code: cd.ok ? 0 : 1,
          timedout: false,
          killed: false
        });
        return;
      }

      const r = await runCommand(cmd);
      sendJson(res, {
        ok: true,
        cwd: state.cwd,
        stdout: r.stdout,
        stderr: r.stderr,
        code: r.code,
        timedout: r.timedout,
        killed: r.killed
      });
      return;
    }

    if (req.method === 'POST' && p === '/api/kill') {
      if (activeChild) {
        try { activeChild.kill('SIGKILL'); } catch (e) { /* ignore */ }
      }
      sendJson(res, { ok: true });
      return;
    }

    if (req.method === 'GET' && (p === '/' || p === '/index.html')) {
      fs.readFile(path.join(ROOT, 'index.html'), (err, data) => {
        if (err) { sendJson(res, { ok: false, error: 'index.html missing: ' + err.message }, 500); return; }
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(data);
      });
      return;
    }

    sendJson(res, { ok: false, error: 'not found' }, 404);
  } catch (e) {
    sendJson(res, { ok: false, error: String(e && e.message || e) }, 500);
  }
});

server.listen(PORT, HOST, () => {
  console.log('');
  console.log('==========================================================');
  console.log('  真实终端服务已启动');
  console.log('  页面地址 : http://' + HOST + ':' + PORT + '/');
  console.log('  Shell    : ' + state.shellName);
  console.log('  默认目录 : ' + ROOT);
  console.log('  说明     : 页面里输入的命令会在这台电脑上【真实执行】');
  console.log('==========================================================');
  console.log('');
});

function shutdown() {
  if (activeChild) { try { activeChild.kill('SIGKILL'); } catch (e) { /* ignore */ } }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 500).unref();
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
