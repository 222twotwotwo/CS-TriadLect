// ============================================================
//  api.js —— 和服务器说话的唯一通道
//
//  浏览器里的所有数据都从这里来：fetch() 就是浏览器发 HTTP
//  请求的方式。你在游戏里做的每一件事，最终都是一行请求。
// ============================================================

/** 发一条请求，把 {status, ok, data} 带回来——不管成败，都是"信息" */
export async function request(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch { /* 不是 JSON 就原样给 null */ }
  return { status: res.status, ok: res.ok, data };
}

export const get = (path) => request('GET', path);
export const post = (path, body) => request('POST', path, body);
