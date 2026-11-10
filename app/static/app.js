/* 演出雷达 · 前端：全量展示 + 偏好只排序 + 一键直达购票页 */
let STATE = null;
let QUERY = '';
let COLLAPSED = new Set();
let MY_ACTIONS = {};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function safeUrl(u) {
  try { const x = new URL(u); return (x.protocol === 'http:' || x.protocol === 'https:') ? x.href : '#'; }
  catch (e) { return '#'; }
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(t._timer); t._timer = setTimeout(() => t.classList.remove('on'), 2400);
}
function shortDate(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})/);
  if (m) return `${m[2]}/${m[3]} ${m[4]}`;
  return String(s).slice(0, 16);
}
function allShows() { return STATE ? STATE.groups.flatMap(g => g.items) : []; }
function findShow(id) { return allShows().find(s => s.id === id); }

async function load(silent) {
  try {
    STATE = await fetch('/api/state').then(r => r.json());
    MY_ACTIONS = {};
    (STATE.pref_summary.purchases || []).forEach(p => MY_ACTIONS[p.show_id] = 'bought');
    render();
  } catch (e) {
    $('#content').innerHTML = '<div class="empty">连不上本地服务，请确认 start.bat 还开着。</div>';
  }
  if (!silent) return;
}

function render() {
  $('#todayLabel').textContent = `${STATE.date} ${STATE.weekday} · 每日全量汇总`;
  $('#stats').innerHTML = `
    <span class="stat"><b>${STATE.total}</b>场演出</span>
    <span class="stat"><b>${STATE.category_count}</b>个品类</span>
    <span class="stat">今日新增 <b>${STATE.new_today}</b></span>
    <span class="stat">猜你喜欢 <b>${STATE.liked_count}</b></span>`;
  renderCities();
  renderGroups();
}

const HOT_CITIES = ['beijing','shanghai','guangzhou','shenzhen','hangzhou','chengdu','nanjing','wuhan','xian','chongqing'];
function renderCities() {
  const names = STATE.config.city_names || {};
  const chosen = STATE.config.cities || [];
  const list = [...new Set([...chosen, ...HOT_CITIES])];
  let html = list.map(c => `<button class="chip ${chosen.includes(c) ? 'on' : ''}" data-city="${esc(c)}">${esc(names[c] || c)}</button>`).join('');
  html += `<select class="chip more" id="cityMore" title="添加其它城市"><option value="">＋ 其它城市</option>` +
    Object.entries(names).filter(([c]) => !list.includes(c)).map(([c, n]) => `<option value="${esc(c)}">${esc(n)}</option>`).join('') +
    `</select>`;
  $('#cityChips').innerHTML = html;
}

function cardHtml(s) {
  const poster = s.poster
    ? `<img class="poster" loading="lazy" src="${esc(safeUrl(s.poster))}" alt="" onerror="this.style.visibility='hidden'">`
    : `<div class="poster-ph">🎭</div>`;
  const badges = [];
  if (s._liked) badges.push(`<span class="badge star">⭐ 猜你喜欢</span>`);
  if (s._new) badges.push(`<span class="badge new">NEW 新收录</span>`);
  if (s.on_selling) badges.push(`<span class="badge selling">售票中</span>`);
  if (s.city) badges.push(`<span class="badge">${esc(s.city)}</span>`);
  const price = s.price ? `<span class="price">${esc(s.price)}</span>` : '<span>票价见页面</span>';
  const reasons = (s._reasons || []).length ? ` · ${esc(s._reasons.join('、'))}` : '';
  const bought = MY_ACTIONS[s.id] === 'bought';
  return `<article class="card ${s._liked ? 'liked' : ''}" data-id="${esc(s.id)}">
    ${poster}
    <div class="card-main">
      <h3 class="card-title" title="${esc(s.title)}">${esc(s.title)}</h3>
      <div class="badges">${badges.join('')}</div>
      <div class="meta">
        <div class="m-row">🕒 ${esc(shortDate(s.start || s.date_text) || '时间待定')}${s._liked ? `<span style="color:var(--gold)">${reasons}</span>` : ''}</div>
        <div class="m-row">📍 ${esc(s.venue || s.city || '场馆待定')}</div>
        <div class="m-row">🎫 ${price}</div>
      </div>
      <div class="card-btns">
        <a class="cbtn buy" href="${esc(safeUrl(s.buy_url))}" target="_blank" rel="noopener" data-act="buy">去大麦买票 →</a>
        ${s.ticket_url && s.on_selling ? `<a class="cbtn" href="${esc(safeUrl(s.ticket_url))}" target="_blank" rel="noopener" data-act="buy">购票直通车</a>` : ''}
        ${s.detail_url ? `<a class="cbtn" href="${esc(safeUrl(s.detail_url))}" target="_blank" rel="noopener">详情</a>` : ''}
        <button class="cbtn ${bought ? 'done' : ''}" data-act="bought">${bought ? '✓ 已买' : '我买了'}</button>
        <button class="cbtn" data-act="want">☆ 想看</button>
      </div>
    </div>
  </article>`;
}

function renderGroups() {
  const q = QUERY.trim().toLowerCase();
  let shown = 0, html = '';
  for (const g of STATE.groups) {
    const items = q ? g.items.filter(s =>
      (s.title + ' ' + (s.venue||'') + ' ' + (s.city||'') + ' ' + (s.tags||[]).join(' ')).toLowerCase().includes(q)) : g.items;
    if (!items.length) continue;
    shown += items.length;
    const collapsed = COLLAPSED.has(g.category);
    html += `<section class="cat-block">
      <div class="cat-head" data-cat="${esc(g.category)}">
        <h2>${g.icon} ${esc(g.category)}</h2><span class="cat-count">${items.length} 场</span>
        <span class="cat-line"></span><span class="cat-toggle">${collapsed ? '展开 ▾' : '收起 ▴'}</span>
      </div>
      <div class="grid" ${collapsed ? 'style="display:none"' : ''}>${items.map(cardHtml).join('')}</div>
    </section>`;
  }
  if (q) html = `<div class="hint" style="margin:0 0 14px">当前视图筛选出 ${shown} 场（仅本次页面过滤，每日推送仍是全量 ${STATE.total} 场）。<button class="btn tiny ghost" id="btnResetQ">显示全部</button></div>` + html;
  $('#content').innerHTML = html || `<div class="empty">没有匹配的演出。${q ? '换个关键词试试。' : '点右上角「刷新演出」去抓一批最新演出吧。'}</div>`;
}

/* ---------- 行为：记录偏好 / 购票跳转 ---------- */
async function record(id, action) {
  try {
    await fetch('/api/action', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, action})
    });
    if (action === 'bought') {
      MY_ACTIONS[id] = 'bought';
      toast('已记下！以后这类演出会排在每组前面一点 ⭐');
      setTimeout(() => load(true), 500);
    } else if (action === 'want') {
      toast('已加入想看，偏好已更新 ☆');
      setTimeout(() => load(true), 500);
    }
  } catch (e) { /* 记录失败不影响跳转 */ }
}

document.addEventListener('click', async (e) => {
  const chip = e.target.closest('[data-city]');
  if (chip) {
    const c = chip.dataset.city;
    const chosen = [...STATE.config.cities];
    const next = chosen.includes(c) ? chosen.filter(x => x !== c) : [...chosen, c];
    if (!next.length) return toast('至少保留一个城市');
    await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cities: next})});
    STATE.config.cities = next; renderCities();
    toast('城市已保存，点「刷新演出」立即生效');
    return;
  }
  const head = e.target.closest('[data-cat]');
  if (head) {
    const c = head.dataset.cat;
    COLLAPSED.has(c) ? COLLAPSED.delete(c) : COLLAPSED.add(c);
    renderGroups(); return;
  }
  const el = e.target.closest('[data-act]');
  if (el) {
    const id = el.closest('.card').dataset.id;
    const act = el.dataset.act;
    if (act === 'buy') { record(id, 'click'); return; }  // 链接自身会打开购票页
    e.preventDefault();
    if (act === 'bought' && MY_ACTIONS[id] === 'bought') return toast('这场已经记过啦');
    record(id, act); return;
  }
  if (e.target.id === 'btnResetQ' || e.target.id === 'btnClearSearch') {
    QUERY = ''; $('#searchInput').value = ''; renderGroups();
  }
});

document.addEventListener('change', (e) => {
  if (e.target.id === 'cityMore' && e.target.value) {
    const next = [...new Set([...STATE.config.cities, e.target.value])];
    fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cities: next})})
      .then(() => { STATE.config.cities = next; renderCities(); toast('已添加，点「刷新演出」生效'); });
  }
});

$('#searchInput').addEventListener('input', (e) => { QUERY = e.target.value; renderGroups(); });

$('#btnRefresh').addEventListener('click', async () => {
  const b = $('#btnRefresh'); b.disabled = true; b.textContent = '正在抓取…';
  toast('正在从各售票页抓最新演出，约 10～20 秒');
  try {
    const r = await fetch('/api/refresh', {method:'POST'}).then(x => x.json());
    toast(`刷新完成：库内 ${r.total} 场，新增 ${r.new} 场` + (r.errors?.length ? `（${r.errors.length} 个页面被限流，已保留历史数据）` : ''));
    await load(true);
  } catch (err) { toast('刷新失败，请查看控制台日志'); }
  b.disabled = false; b.textContent = '↻ 刷新演出';
});

$('#btnReport').addEventListener('click', () => window.open('/api/report', '_blank', 'noopener'));
$('#btnPrefs').addEventListener('click', () => { renderDrawer(); $('#drawer').classList.add('on'); $('#drawerMask').classList.add('on'); });
$('#btnCloseDrawer').addEventListener('click', closeDrawer);
$('#drawerMask').addEventListener('click', closeDrawer);
function closeDrawer() { $('#drawer').classList.remove('on'); $('#drawerMask').classList.remove('on'); }

function barList(pairs, max) {
  if (!pairs.length) return '<div class="hint">还没有学到偏好。点几次「我买了」或「想看」，这里就会长出你的画像。</div>';
  const top = Math.max(max || 1, ...pairs.map(p => Math.abs(p[1])));
  return pairs.map(([k, v]) => `<div class="bar-row"><span class="bar-name" title="${esc(k)}">${esc(k)}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${Math.max(4, Math.round(Math.abs(v)/top*100))}%"></span></span>
    <span class="bar-val">${v.toFixed(1)}</span></div>`).join('');
}

function renderDrawer() {
  const p = STATE.pref_summary;
  const purchases = (p.purchases || []).map(x => `<div class="purchase">
      <a href="${esc(safeUrl(x.buy_url || '#'))}" target="_blank" rel="noopener">${esc(x.title)}</a>
      <small>${esc(x.category || '')} · ${esc((x.ts||'').replace('T',' '))}</small></div>`).join('');
  $('#drawerBody').innerHTML = `
    <div class="panel"><h3>学到的品类偏好</h3>${barList(p.top_categories)}</div>
    <div class="panel"><h3>关键词 / 剧社 / 场馆</h3>${barList(p.top_tags)}</div>
    <div class="panel"><h3>常去场馆</h3>${barList(p.top_venues)}</div>
    <div class="panel"><h3>我买过的票（${(p.purchases||[]).length}）</h3>${purchases || '<div class="hint">还没有购买记录</div>'}</div>
    <div class="panel"><h3>规则说明</h3>
      <div class="hint">偏好只影响「同一品类内部的先后顺序」。每日汇总和手机推送永远是全量：话剧、音乐剧、演唱会、音乐会……一个都不会因为你不常看就被删掉。<br>已累计 ${p.actions_count} 次行为${p.updated_at ? '，最近更新 ' + esc(p.updated_at.replace('T',' ')) : ''}。</div>
    </div>
    <div class="panel"><h3>推送设置</h3>
      <div class="setting-row"><span>每天推送时间</span><input id="pushTime" type="time" value="${esc(STATE.config.push_time)}"></div>
      <div class="setting-row"><span>手机推送通道</span><button class="btn tiny" id="btnTestPush">发一条测试</button></div>
      <div class="hint" style="margin-top:8px">推送通道在项目根目录运行 <b>python setup.py</b> 配置；改时间后需重新运行 <b>python schedule.py install</b>。</div>
    </div>
    <div class="panel"><button class="btn tiny danger" id="btnResetPrefs">清空全部偏好</button></div>`;

  $('#btnTestPush').onclick = async () => {
    toast('正在发送测试推送…');
    const r = await fetch('/api/push/test', {method:'POST'}).then(x => x.json());
    if (!r.results?.length) toast('还没配置推送通道，请先运行 python setup.py');
    else toast(r.results[0].failed?.length ? '发送失败：' + r.results[0].failed[0] : '测试推送已发出，去看看手机');
  };
  $('#pushTime').onchange = async (e) => {
    await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({push_time: e.target.value})});
    STATE.config.push_time = e.target.value; toast('时间已保存，记得重新运行 python schedule.py install');
  };
  $('#btnResetPrefs').onclick = async () => {
    if (!confirm('确定清空所有已学到的喜好和购买记录吗？')) return;
    await fetch('/api/prefs/reset', {method:'POST'});
    toast('已清空，排行榜回到客观顺序'); await load(true); renderDrawer();
  };
}

load();
