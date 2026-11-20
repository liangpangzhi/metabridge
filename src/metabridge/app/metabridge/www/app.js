/* metabridge UI */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (path, opts = {}) => {
  const cfg = window.__cfg || {};
  const headers = Object.assign({}, opts.headers || {});
  if (cfg.access_token) headers['X-Token'] = cfg.access_token;
  if (opts.body) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, Object.assign({}, opts, {headers}));
  return r.json();
};

$$('.tab').forEach(b => b.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === b));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab));
}));
const goto = (t) => $$('.tab').find(b => b.dataset.tab === t)?.click();
$('#btn-goto-export').onclick = () => goto('export');
$('#btn-goto-preview').onclick = () => goto('preview');

window.__groups = [];
window.__selected = new Set();
const ACTION = {create:'新建', refresh:'更新', overwrite:'覆盖', skip:'跳过'};

function renderStatus(d) {
  window.__cfg = d.config || {};
  const ok = d.db && d.db.exists;
  $('#badge').textContent = ok ? '数据库已连接' : '数据库未找到';
  $('#badge').classList.toggle('bad', !ok);
  const items = (d.db && d.db.items) || {};
  const cards = [
    ['电影条目', items.Movie || 0], ['剧集条目', items.TV || 0],
    ['季 / 分集', (items.Season || 0) + (items.Episode || 0)],
    ['图片目录', (d.db && d.db.img_roots || []).length],
  ];
  $('#stat-cards').innerHTML = cards.map(([l, n]) =>
    `<div class="card"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join('');
  const c = d.config || {};
  $('#cfg-db').value = c.db_path || '';
  $('#cfg-img').value = c.img_root || '';
  $('#cfg-roots').value = (c.media_roots || []).join(':');
  $('#cfg-images').checked = !!c.write_images;
  $('#cfg-overwrite').checked = !!c.overwrite_nfo;
  $('#cfg-onlyours').checked = !!c.overwrite_only_ours;
  $('#cfg-overwriteimg').checked = !!c.overwrite_images;
  $('#cfg-season').checked = !!c.write_season_nfo;
  $('#cfg-thumb').checked = !!c.write_episode_thumb;
  $('#cfg-alias').checked = !!c.write_movie_nfo_alias;
  $('#cfg-remote').checked = !!c.include_remote_art;
  $('#cfg-token').value = c.access_token || '';
  $('#opt-filter').value = c.title_filter || '';
  $('#opt-limit').value = c.limit || 0;
  renderJob(d.job || {});
}

function renderJob(j) {
  const map = {idle:'空闲', running:'运行中', done:'完成', stopped:'已停止', error:'出错', stopping:'停止中'};
  $('#job-state').textContent = map[j.state] || j.state || '空闲';
  const pct = j.total ? Math.min(100, Math.round((j.progress || 0) / j.total * 100)) : 0;
  $('#bar').style.width = pct + '%';
  const s = j.stats || {};
  $('#job-stats').innerHTML = Object.entries(s).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(' &nbsp;·&nbsp; ');
  const lines = (j.lines || []).join('\n');
  $('#log').textContent = lines || '尚无任务日志。';
  $('#log-full').textContent = lines || '尚无任务日志。';
  $('#btn-stop').disabled = !(j.state === 'running' || j.state === 'stopping');
  if ((j.groups || []).length) renderGroups(j.groups);
}

function visibleGroups() {
  const q = ($('#preview-search')?.value || '').trim().toLowerCase();
  if (!q) return window.__groups;
  return window.__groups.filter(g => {
    const m = g.meta || {};
    return [g.title, m.title, m.original, (m.genres || []).join(' '),
      (m.actors || []).map(a => a.name).join(' ')].join(' ').toLowerCase().includes(q);
  });
}

function coverUrl(meta) {
  const c = (meta || {}).cover || '';
  if (!c) return '';
  if (/^https?:\/\//i.test(c)) return c;
  return '/api/image?path=' + encodeURIComponent(c);
}

function renderGroups(groups) {
  window.__groups = groups || [];
  const shown = visibleGroups();
  const totalFiles = window.__groups.reduce((n, g) => n + (g.count || 0), 0);
  const movies = window.__groups.filter(g => g.kind === 'movie').length;
  const tvs = window.__groups.filter(g => g.kind === 'tv').length;
  $('#preview-summary').innerHTML =
    `预览共 <b>${window.__groups.length}</b> 部作品（电影 ${movies} / 剧集 ${tvs}），` +
    `将写入 <b>${totalFiles}</b> 个文件。当前显示 ${shown.length} 部。`;

  $('#preview-list').innerHTML = shown.length ? shown.map(g => {
    const m = g.meta || {};
    const sel = window.__selected.has(g.key);
    const cover = coverUrl(m);
    const ids = Object.entries(m.ids || {}).map(([k, v]) => `${k.toUpperCase()}:${v}`).join(' · ');
    return `<div class="media-card ${sel ? 'selected' : ''}" data-key="${esc(g.key)}">
      <div class="card-top">
        <label class="picklab"><input type="checkbox" class="pick" data-key="${esc(g.key)}" ${sel ? 'checked' : ''}>
          <span class="tag ${g.kind}">${g.kind === 'movie' ? '电影' : '电视剧'}</span></label>
        <span class="filecount">${g.count || 0} 个文件</span>
      </div>
      <div class="cover-wrap">
        <div class="cover-ph">◉</div>
        ${cover ? `<img class="cover" src="${esc(cover)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
      </div>
      <div class="card-body">
        <div class="card-title" title="${esc(m.title || g.title)}">${esc(m.title || g.title || '(无标题)')}</div>
        <div class="card-sub">${esc(m.year || '')}${m.rating ? ' · ★ ' + esc(m.rating) : ''}${m.runtime ? ' · ' + esc(m.runtime) + ' 分钟' : ''}</div>
        <div class="genres">${(m.genres || []).map(x => `<span>${esc(x)}</span>`).join('')}</div>
        <div class="plot">${esc(m.plot || '（数据库无简介）')}</div>
        ${(m.actors || []).length ? `<div class="actors">${m.actors.slice(0,5).map(a =>
          `<span>${esc(a.name)}${a.role ? ' · ' + esc(a.role) : ''}</span>`).join('')}</div>` : ''}
        ${ids ? `<div class="ids">${esc(ids)}</div>` : ''}
        <details class="files"><summary>写入文件 (${g.count || 0})</summary>
          ${(g.files || []).map(f => `<div class="frow"><span class="mini-tag">${esc(ACTION[f.action] || f.kind)}</span><span class="fkind">${esc(f.kind)}</span><span class="fpath" title="${esc(f.nfo)}">${esc(f.nfo)}</span></div>`).join('')}
        </details>
        <details class="dirs"><summary>目标目录</summary>${(g.dirs || []).map(d => `<div class="dpath">${esc(d)}</div>`).join('')}</details>
      </div>
    </div>`;
  }).join('') : '<div class="empty">没有匹配的作品。换个关键词，或重新生成预览。</div>';
  updateSelection();
}

function updateSelection() {
  const chosen = window.__groups.filter(g => window.__selected.has(g.key));
  const files = chosen.reduce((n, g) => n + (g.count || 0), 0);
  $('#sel-summary').textContent = `已选 ${chosen.length} 部作品 · ${files} 个文件`;
  $('#btn-export-selected').disabled = chosen.length === 0;
}

function opts() {
  return {
    only_movies: $('#opt-movies').checked,
    only_tv: $('#opt-tv').checked,
    title_filter: $('#opt-filter').value.trim(),
    limit: parseInt($('#opt-limit').value || '0', 10),
  };
}
async function refresh() { renderStatus(await api('/api/status')); }

let modalAction = null;
function showConfirm(html, action) {
  $('#modal-body').innerHTML = html;
  modalAction = action;
  $('#modal').hidden = false;
}
function closeModal() { $('#modal').hidden = true; modalAction = null; }
$('#modal-cancel').onclick = closeModal;
$('#modal-ok').onclick = async () => {
  const fn = modalAction;
  closeModal();
  if (fn) await fn();
};

function scopeHtml(chosen) {
  const files = chosen.reduce((n, g) => n + (g.count || 0), 0);
  const movies = chosen.filter(g => g.kind === 'movie').length;
  const tvs = chosen.filter(g => g.kind === 'tv').length;
  const dirs = Array.from(new Set(chosen.flatMap(g => g.dirs || []))).slice(0, 5);
  return `<p>本次将写入 <b>${files}</b> 个元文件，涉及 <b>${chosen.length}</b> 部作品（电影 ${movies} / 剧集 ${tvs}）。</p>
    <p class="confirm-names">${chosen.slice(0,12).map(g => esc(g.title)).join('、')}${chosen.length > 12 ? ' …' : ''}</p>
    <p class="confirm-dirs">目标目录示例：<br>${dirs.map(esc).join('<br>')}</p>
    <p class="confirm-note">仅写入 NFO 与图片；视频文件不改动。已有第三方 NFO 默认跳过。</p>`;
}

async function startExport(keys) {
  const body = keys
    ? {only_movies:true, only_tv:true, title_filter:'', limit:0, keys}
    : opts();
  await api('/api/export', {method:'POST', body: JSON.stringify(body)});
  if (keys) { window.__selected.clear(); updateSelection(); }
  goto('export'); poll(true);
}

function confirmSelected() {
  const chosen = window.__groups.filter(g => window.__selected.has(g.key));
  if (!chosen.length) return;
  showConfirm(scopeHtml(chosen), () => startExport(chosen.map(g => g.key)));
}

$('#btn-refresh').onclick = refresh;
async function scan() {
  await api('/api/scan', {method:'POST', body: JSON.stringify(opts())});
  goto('preview'); poll(true);
}
$('#btn-scan').onclick = scan;
$('#btn-scan2').onclick = scan;
$('#btn-export-selected').onclick = confirmSelected;
$('#btn-export').onclick = () => {
  const chosen = window.__groups.filter(g => window.__selected.has(g.key));
  if (chosen.length) { confirmSelected(); return; }
  const files = window.__groups.reduce((n, g) => n + (g.count || 0), 0);
  showConfirm(`<p>未勾选具体作品，将按「导出」页当前筛选条件导出${files ? `全部 <b>${files}</b> 个文件` : '全部匹配内容'}。</p>
    <p class="confirm-note">建议先到「预览与确认」页逐部核对并勾选范围。视频文件不会被改动。</p>`,
    () => startExport(null));
};
$('#btn-stop').onclick = async () => { await api('/api/stop', {method:'POST', body:'{}'}); poll(true); };
$('#btn-clearlog').onclick = async () => { await api('/api/clearlog', {method:'POST', body:'{}'}); refresh(); };

$('#preview-list').addEventListener('change', e => {
  if (!e.target.classList.contains('pick')) return;
  const key = e.target.dataset.key;
  if (e.target.checked) window.__selected.add(key); else window.__selected.delete(key);
  e.target.closest('.media-card')?.classList.toggle('selected', e.target.checked);
  updateSelection();
});
$('#preview-search').addEventListener('input', () => renderGroups(window.__groups));
$('#btn-sel-all').onclick = () => { visibleGroups().forEach(g => window.__selected.add(g.key)); renderGroups(window.__groups); };
$('#btn-sel-none').onclick = () => { visibleGroups().forEach(g => window.__selected.delete(g.key)); renderGroups(window.__groups); };
$('#btn-sel-rev').onclick = () => { visibleGroups().forEach(g =>
  window.__selected.has(g.key) ? window.__selected.delete(g.key) : window.__selected.add(g.key)); renderGroups(window.__groups); };

$('#btn-save').onclick = async () => {
  const split = s => s.split(':').map(x => x.trim()).filter(Boolean);
  const cfg = {
    db_path: $('#cfg-db').value.trim(),
    img_root: $('#cfg-img').value.trim(),
    media_roots: split($('#cfg-roots').value),
    write_images: $('#cfg-images').checked,
    overwrite_nfo: $('#cfg-overwrite').checked,
    overwrite_only_ours: $('#cfg-onlyours').checked,
    overwrite_images: $('#cfg-overwriteimg').checked,
    write_season_nfo: $('#cfg-season').checked,
    write_episode_thumb: $('#cfg-thumb').checked,
    write_movie_nfo_alias: $('#cfg-alias').checked,
    include_remote_art: $('#cfg-remote').checked,
    access_token: $('#cfg-token').value.trim(),
  };
  const r = await api('/api/config', {method:'POST', body: JSON.stringify(cfg)});
  $('#save-msg').textContent = r.ok ? '已保存' : '保存失败';
  window.__cfg = r.config || window.__cfg;
  setTimeout(() => $('#save-msg').textContent = '', 2500);
};

let browsing = false, browsePath = '/';
$('#btn-browse').onclick = async () => {
  browsing = !browsing; $('#browser').hidden = !browsing;
  if (browsing) loadBrowse('/');
};
async function loadBrowse(p) {
  const r = await api('/api/browse?path=' + encodeURIComponent(p));
  browsePath = r.path || '/'; $('#browser-path').textContent = browsePath;
  $('#browser-list').innerHTML = (r.dirs || []).map(d =>
    `<button class="d" data-p="${esc(d)}">📁 ${esc(d)}</button>`).join('');
  $$('#browser-list .d').forEach(b => b.onclick = () => loadBrowse(b.dataset.p));
}
$('#nav-up').onclick = () => loadBrowse(browsePath.split('/').slice(0,-1).join('/') || '/');
$('#nav-pick').onclick = () => {
  const i = $('#cfg-roots'); const cur = split(i.value);
  if (!cur.includes(browsePath)) cur.push(browsePath);
  i.value = cur.join(':');
};
const split = s => s.split(':').map(x => x.trim()).filter(Boolean);

let timer = null;
function poll(fast = false) {
  clearInterval(timer);
  const tick = async () => {
    const d = await api('/api/job');
    renderJob(d);
    if (!['running','stopping'].includes(d.state)) clearInterval(timer);
  };
  timer = setInterval(tick, fast ? 1000 : 4000);
  tick();
}
refresh().then(() => api('/api/job').then(j => { if ((j.groups || []).length) renderGroups(j.groups); }));
