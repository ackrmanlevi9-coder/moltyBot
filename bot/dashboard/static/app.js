/**
 * Molty Royale Command Center v3 — Realtime Dashboard Engine
 * DOM diffing, animated counters, game history, strategy phase display.
 */
const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; };
const fmt = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'k' : String(n);

// ─── Item display names ───
const ITEM_NAMES = {
  'rewards':'$Moltz','reward1':'$Moltz','reward':'$Moltz',
  'emergency_food':'Emergency Food','emergency_rations':'Emergency Rations',
  'bandage':'Bandage','medkit':'Medkit','energy_drink':'Energy Drink',
  'knife':'Knife','dagger':'Knife','sword':'Sword','katana':'Katana',
  'bow':'Bow','pistol':'Pistol','sniper':'Sniper',
  'binoculars':'Binoculars','map':'Map','megaphone':'Megaphone','radio':'Radio',
  'fist':'Fist','fists':'Fists',
};
const ITEM_ICONS = {
  'rewards':'💰','knife':'🔪','dagger':'🔪','sword':'⚔️','katana':'🗡️',
  'bow':'🏹','pistol':'🔫','sniper':'🎯','fist':'🥊',
  'bandage':'🩹','medkit':'💊','emergency_food':'🍖','energy_drink':'⚡',
  'binoculars':'🔭','map':'🗺️','megaphone':'📢',
};
function itemName(i) {
  if (typeof i === 'string') return ITEM_NAMES[i.toLowerCase()] || i;
  const raw = i.name || i.typeId || i.type || i.itemType || i.id || '?';
  const resolved = ITEM_NAMES[raw.toLowerCase()] || ITEM_NAMES[(i.typeId||'').toLowerCase()];
  if (resolved) return resolved;
  if (raw.length > 20 || raw.includes('-')) return raw.slice(0, 10) + '…';
  return raw;
}
function itemIcon(i) {
  const typeId = (typeof i === 'object' ? (i.typeId || i.type || '') : i).toLowerCase();
  return ITEM_ICONS[typeId] || '📦';
}
function itemTag(i) {
  const name = itemName(i);
  const icon = itemIcon(i);
  const cat = (typeof i === 'object' ? i.cat : '') || '';
  const colors = {weapon:'var(--red)',recovery:'var(--green)',utility:'var(--cyan)',currency:'var(--amber)'};
  const bdr = colors[cat] ? `border-left:2px solid ${colors[cat]};` : '';
  return `<span class="item-tag" style="${bdr}">${icon} ${esc(name)}</span>`;
}

// ─── State ───
let S = { agents:{}, stats:{}, logs:[], agent_logs:{}, accounts:[], game_history:[] };
let currentPage = 'dashboard', currentLogTab = 'all';
let prevAgentHash = '';

// ─── Navigation ───
function showPage(p) {
  document.querySelectorAll('.page').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(e => e.classList.remove('active'));
  const page = $('page-'+p);
  const nav = document.querySelector('[data-page="'+p+'"]');
  if (page) page.classList.add('active');
  if (nav) nav.classList.add('active');
  currentPage = p;
  render();
}

// ─── WebSocket with fast reconnect ───
let ws, wsRetry = 0;
function connectWS() {
  const url = (location.protocol==='https:'?'wss:':'ws:') + '//' + location.host + '/ws';
  try { ws = new WebSocket(url); } catch(e) { setTimeout(connectWS, 2000); return; }
  ws.onopen = () => { wsRetry = 0; };
  ws.onmessage = e => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === 'snapshot') { S = m.data; render(); }
    } catch(err) {}
  };
  ws.onclose = () => setTimeout(connectWS, Math.min(1000 * (++wsRetry), 8000));
  ws.onerror = () => ws.close();
}

// Polling fallback
setInterval(() => {
  fetch('/api/state').then(r=>r.json()).then(d => { S = d; render(); }).catch(()=>{});
}, 3000);

// ─── Master render ───
function render() {
  try { renderHeader(); } catch(e) {}
  try { renderUptime(); } catch(e) {}
  if (currentPage === 'dashboard') {
    try { renderAgentCards(); } catch(e) {}
    try { renderLogs('log-box'); } catch(e) {}
  }
  if (currentPage === 'agents') { try { renderAgentsTable(); } catch(e) {} }
  if (currentPage === 'history') { try { renderHistory(); } catch(e) {} }
  if (currentPage === 'strategy') { try { renderStrategy(); } catch(e) {} }
  if (currentPage === 'logs') { try { renderLogs('full-log-box'); } catch(e) {} }
}

// ─── Smooth text update ───
function setText(el, val) {
  if (typeof el === 'string') el = $(el);
  if (!el) return;
  const v = String(val);
  if (el.textContent !== v) el.textContent = v;
}

// ─── Animated counter ───
const counters = {};
function animateNum(id, target) {
  const el = $(id);
  if (!el) return;
  const key = id;
  const current = counters[key] || 0;
  if (current === target) return;
  counters[key] = target;
  const start = parseFloat(el.textContent.replace(/[^0-9.-]/g,'')) || 0;
  const diff = target - start;
  if (diff === 0) { el.textContent = fmt(target); return; }
  const steps = 15;
  let step = 0;
  const timer = setInterval(() => {
    step++;
    const progress = step / steps;
    const eased = 1 - Math.pow(1 - progress, 3);
    const val = Math.round(start + diff * eased);
    el.textContent = fmt(val);
    if (step >= steps) { clearInterval(timer); el.textContent = fmt(target); }
  }, 25);
}

// ─── Uptime ───
function renderUptime() {
  const uptime = S.stats?.uptime || 0;
  const h = Math.floor(uptime / 3600);
  const m = Math.floor((uptime % 3600) / 60);
  setText('uptime-text', `${h}h ${m}m uptime`);
}

// ─── Header ───
function renderHeader() {
  const s = S.stats || {};
  const agentList = Object.values(S.agents || {});
  const playing = agentList.filter(a => a.status === 'playing').length;
  const dead = agentList.filter(a => a.status === 'dead').length;
  animateNum('h-agents', agentList.length);
  animateNum('h-playing', playing);
  animateNum('h-dead', dead);
  animateNum('h-wins', s.total_wins || 0);
  animateNum('h-smoltz', s.total_smoltz || 0);
  animateNum('h-kills', s.total_kills || 0);
  setText('h-rate', (s.action_rate || 0) + '%');
}

// ─── Agent Cards ───
function renderAgentCards() {
  const container = $('agent-cards');
  if (!container) return;
  const entries = Object.entries(S.agents || {});
  
  // Sort by status (playing first, then idle, then dead) and then numerically by Agent Name/ID
  const statusWeight = { 'playing': 0, 'idle': 1, 'error': 2, 'dead': 3 };
  entries.sort((a, b) => {
    const w1 = statusWeight[a[1].status || 'idle'] || 0;
    const w2 = statusWeight[b[1].status || 'idle'] || 0;
    if (w1 !== w2) return w1 - w2;
    // Extract numbers from id (e.g. "agent-bot2" vs "agent-bot10")
    const n1 = parseInt(a[0].match(/\d+/)) || 0;
    const n2 = parseInt(b[0].match(/\d+/)) || 0;
    return n1 - n2;
  });

  const hash = JSON.stringify(entries.map(([id,a]) => id + (a.hp||0) + (a.ep||0) + (a.status||'') + (a.last_action||'') + (a.kills||0) + (a.alive_count||0) + (a.inventory||[]).length + (a.enemies||[]).length + (a.region_items||[]).length + (a.region||'')));
  if (hash === prevAgentHash) return;
  prevAgentHash = hash;

  const agents = entries;

  if (!agents.length) {
    container.innerHTML = '<div class="card glass" style="text-align:center;padding:40px;color:var(--text2)"><div class="loader"></div><p style="margin-top:16px">Waiting for agent connection...</p></div>';
    return;
  }

  const existingCards = container.querySelectorAll('.agent-card');
  if (existingCards.length !== agents.length) {
    container.innerHTML = agents.map(([id]) => `<div class="card glass agent-card" data-aid="${id}"></div>`).join('');
  }

  agents.forEach(([id, a]) => {
    let card = container.querySelector(`[data-aid="${id}"]`);
    if (!card) return;
    patchAgentCard(card, id, a);
  });
}

function patchAgentCard(card, id, a) {
  const st = a.status || 'idle';
  const bc = st==='playing'?'ok':st==='dead'?'dead':st==='error'?'err':'warn';
  const name = a.name || 'Agent';
  const hp = a.hp ?? 0, maxHp = a.maxHp || 100;
  const ep = a.ep ?? 0, maxEp = a.maxEp || 10;
  const hpPct = Math.min(100, Math.round((hp/maxHp)*100));
  const epPct = Math.min(100, Math.round((ep/maxEp)*100));
  const atk = a.atk || 0, def = a.def || 0, wpnBonus = a.weapon_bonus || 0;
  const weapon = a.weapon || 'fist';
  const kills = a.kills || 0;
  const region = a.region || '—';
  const roomId = a.room_id || '—';

  const inv = (a.inventory||[]).map(i => itemTag(i)).join('') || '<span style="color:var(--text2)">Empty</span>';
  const enemies = (a.enemies||[]).map(e => `<span class="item-tag" style="border-left:2px solid var(--red)">👤 ${esc(e.name||'?')} HP:${e.hp}</span>`).join('') || '<span style="color:var(--text2)">Clear</span>';
  const items = (a.region_items||[]).map(i => itemTag(i)).join('') || '<span style="color:var(--text2)">None</span>';

  let statusIcon;
  if (st === 'dead') statusIcon = '<span style="font-size:14px;margin-right:6px">☠️</span>';
  else if (st === 'playing') statusIcon = '<span class="status-dot active"></span>';
  else if (st === 'error') statusIcon = '<span class="status-dot error"></span>';
  else statusIcon = '<span class="status-dot idle"></span>';

  // Determine game phase from alive_count
  const alive = a.alive_count || 0;
  let phaseLabel = '';
  if (st === 'playing' && alive > 0) {
    if (alive > 40) phaseLabel = '<span class="badge" style="background:rgba(0,210,255,.1);color:var(--cyan);border:1px solid rgba(0,210,255,.2)">🌅 EARLY</span>';
    else if (alive > 15) phaseLabel = '<span class="badge" style="background:rgba(255,184,0,.1);color:var(--amber);border:1px solid rgba(255,184,0,.2)">⚡ MID</span>';
    else phaseLabel = '<span class="badge" style="background:rgba(255,68,102,.1);color:var(--red);border:1px solid rgba(255,68,102,.2)">🔥 LATE</span>';
  }

  card.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <div>${statusIcon}<span class="agent-name">${esc(name)}</span></div>
      <div style="display:flex;gap:6px">${phaseLabel}<span class="badge ${bc}">${st.toUpperCase()}</span></div>
    </div>
    <div class="agent-meta">📍 ${esc(region)} &nbsp;|&nbsp; ${esc(a.room_name||'—')} &nbsp;|&nbsp; ID: <span style="color:var(--text)">${esc(roomId)}</span></div>
    ${(a.agent_wallet || a.owner_wallet) ? `<div class="wallet-row">
      ${a.agent_wallet ? `<div class="wallet-item"><span class="wallet-label">🤖 Agent</span><span class="wallet-addr" title="Click to copy" onclick="navigator.clipboard.writeText('${esc(a.agent_wallet)}').then(()=>this.textContent='✅ Copied!').catch(()=>{}); setTimeout(()=>this.textContent='${esc(a.agent_wallet)}',1500)">${esc(a.agent_wallet)}</span></div>` : ''}
      ${a.agent_pk ? `<div class="wallet-item"><span class="wallet-label" style="color:var(--amber)">🔑 PK</span><span class="wallet-addr pk-hidden" data-pk="${esc(a.agent_pk)}" onclick="if(this.classList.contains('pk-hidden')){this.textContent=this.dataset.pk;this.classList.remove('pk-hidden')}else{navigator.clipboard.writeText(this.dataset.pk).then(()=>{this.textContent='✅ Copied!';setTimeout(()=>{this.textContent='•'.repeat(20)+' (click to show)';this.classList.add('pk-hidden')},1500)})}" style="color:var(--amber)">•••••••••••••••••••• (click to show)</span></div>` : ''}
      ${a.owner_wallet ? `<div class="wallet-item"><span class="wallet-label">👤 Owner</span><span class="wallet-addr" title="Click to copy" onclick="navigator.clipboard.writeText('${esc(a.owner_wallet)}').then(()=>this.textContent='✅ Copied!').catch(()=>{}); setTimeout(()=>this.textContent='${esc(a.owner_wallet)}',1500)">${esc(a.owner_wallet)}</span></div>` : ''}
      ${a.owner_pk ? `<div class="wallet-item"><span class="wallet-label" style="color:var(--amber)">🔑 PK</span><span class="wallet-addr pk-hidden" data-pk="${esc(a.owner_pk)}" onclick="if(this.classList.contains('pk-hidden')){this.textContent=this.dataset.pk;this.classList.remove('pk-hidden')}else{navigator.clipboard.writeText(this.dataset.pk).then(()=>{this.textContent='✅ Copied!';setTimeout(()=>{this.textContent='•'.repeat(20)+' (click to show)';this.classList.add('pk-hidden')},1500)})}" style="color:var(--amber)">•••••••••••••••••••• (click to show)</span></div>` : ''}
    </div>` : ''}
    <div class="bar-row">
      <div class="bar-wrap">
        <div class="bar-label"><span class="bl">❤️ HP</span><span class="bv" style="color:${hpPct>50?'var(--green)':hpPct>25?'var(--amber)':'var(--red)'}">${hp}/${maxHp}</span></div>
        <div class="bar-track"><div class="bar-fill hp" style="width:${hpPct}%"></div></div>
      </div>
      <div class="bar-wrap">
        <div class="bar-label"><span class="bl">⚡ EP</span><span class="bv" style="color:var(--cyan)">${ep}/${maxEp}</span></div>
        <div class="bar-track"><div class="bar-fill ep" style="width:${epPct}%"></div></div>
      </div>
    </div>
    <div class="combat-row">
      <div class="combat-stat"><div class="cv">${atk+wpnBonus}</div><div class="cl">⚔️ ATK</div></div>
      <div class="combat-stat"><div class="cv">${def}</div><div class="cl">🛡️ DEF</div></div>
      <div class="combat-stat"><div class="cv" style="font-size:11px">${esc(ITEM_NAMES[weapon.toLowerCase()]||weapon)}</div><div class="cl">🗡️ WEAPON</div></div>
      <div class="combat-stat"><div class="cv">${kills}</div><div class="cl">💀 KILLS</div></div>
      <div class="combat-stat"><div class="cv">${alive||'?'}</div><div class="cl">👥 ALIVE</div></div>
    </div>
    <div class="action-log">${a.last_action ? '▸ '+esc(a.last_action) : '<span style="color:var(--text2)">Waiting...</span>'}</div>
    <div class="info-grid">
      <div class="info-block"><h4>📦 Inventory</h4><div class="items">${inv}</div></div>
      <div class="info-block"><h4>👁️ Enemies</h4><div class="items">${enemies}</div></div>
      <div class="info-block"><h4>🎯 Region Items</h4><div class="items">${items}</div></div>
    </div>`;
}

// ─── Agents Table ───
function renderAgentsTable() {
  const s = S.stats || {};
  setText('ov-active', s.agents_active || 0);
  setText('ov-idle', s.agents_idle || 0);
  setText('ov-dead', s.agents_dead || 0);
  const tb = $('agents-tbody');
  if (!tb) return;
  const agents = Object.entries(S.agents || {});
  if (!agents.length) { tb.innerHTML = '<tr><td colspan="7" style="color:var(--text2);text-align:center">No agents</td></tr>'; return; }
  tb.innerHTML = agents.map(([id,a]) => {
    const st = a.status||'idle';
    const bc = st==='playing'?'ok':st==='dead'?'dead':st==='error'?'err':'warn';
    const weapon = a.weapon || 'fist';
    const weaponDisplay = (ITEM_ICONS[weapon.toLowerCase()]||'') + ' ' + (ITEM_NAMES[weapon.toLowerCase()]||weapon);
    return `<tr><td><strong>${esc(a.name||id)}</strong></td><td><span class="badge ${bc}">${st}</span></td>
      <td style="color:${(a.hp||0)>50?'var(--green)':'var(--red)'}"><strong>${a.hp||0}</strong>/${a.maxHp||100}</td>
      <td>📍 ${esc(a.region||'—')}</td><td>${weaponDisplay}</td>
      <td><strong>${a.kills||0}</strong></td><td>${fmt(a.smoltz||0)}</td></tr>`;
  }).join('');
}

// ─── Game History ───
function renderHistory() {
  const s = S.stats || {};
  const wins = s.total_wins || 0;
  const losses = s.total_losses || 0;
  const total = wins + losses;
  const winrate = total > 0 ? Math.round((wins/total)*100) : 0;
  setText('hist-wins', wins);
  setText('hist-losses', losses);
  setText('hist-winrate', winrate + '%');

  const tb = $('history-tbody');
  if (!tb) return;
  const history = (S.game_history || []).slice().reverse();
  if (!history.length) {
    tb.innerHTML = '<tr><td colspan="6" style="color:var(--text2);text-align:center">No games completed yet</td></tr>';
    return;
  }
  tb.innerHTML = history.map(g => {
    const time = g.timestamp ? new Date(g.timestamp*1000).toLocaleString() : '—';
    const won = g.is_winner;
    const badge = won ? '<span class="badge ok">🏆 WIN</span>' : `<span class="badge dead">💀 #${g.final_rank||'?'}</span>`;
    return `<tr>
      <td style="font-size:11px;color:var(--text2)">${time}</td>
      <td>${badge}</td>
      <td><strong>#${g.final_rank||'?'}</strong></td>
      <td><strong>${g.kills||0}</strong></td>
      <td style="color:var(--amber)">${fmt(g.smoltz_earned||0)}</td>
      <td>${g.entry_type||'free'}</td>
    </tr>`;
  }).join('');
}

// ─── Strategy Page ───
function renderStrategy() {
  const s = S.stats || {};
  const agents = Object.values(S.agents || {});
  const playing = agents.find(a => a.status === 'playing');

  // Update phase display
  const alive = playing ? (playing.alive_count || 0) : 0;
  let activePhase = 'early';
  if (alive > 0 && alive <= 15) activePhase = 'late';
  else if (alive > 0 && alive <= 40) activePhase = 'mid';

  document.querySelectorAll('.phase-item').forEach(el => {
    el.classList.toggle('active', el.dataset.phase === activePhase);
  });

  // Update performance ring
  const rate = s.action_rate || 0;
  const offset = 264 - (264 * rate / 100);
  const ring = $('ring-success');
  if (ring) ring.style.strokeDashoffset = offset;
  setText('perf-success-val', rate + '%');
  setText('perf-sent', fmt(s.actions_sent || 0));
  setText('perf-ok', fmt(s.actions_success || 0));
  setText('perf-fail', fmt(s.actions_failed || 0));
}

// ─── Logs ───
function renderLogs(boxId) {
  const logs = currentLogTab === 'all' ? (S.logs||[]) : ((S.agent_logs||{})[currentLogTab]||[]);
  const box = $(boxId || 'log-box');
  if (!box) return;

  const wasBottom = box.scrollTop >= box.scrollHeight - box.clientHeight - 40;
  const visible = logs.slice(-200);
  box.innerHTML = visible.map(l => `<div class="log-line">${_logLine(l)}</div>`).join('');
  if (wasBottom || box.scrollTop === 0) box.scrollTop = box.scrollHeight;
}

function _logLine(l) {
  const t = new Date((l.ts||0)*1000);
  const ts = t.toLocaleTimeString();
  const lvl = l.level || 'info';
  const agentName = l.agent ? (S.agents?.[l.agent]?.name || l.agent.slice(0,8)) : '';
  const agentLabel = agentName ? `<span style="color:var(--cyan);opacity:.6">[${esc(agentName)}]</span> ` : '';
  return `<span class="ts">${ts}</span> <span class="lvl-${lvl}">${lvl.toUpperCase()}</span> ${agentLabel}${esc(l.msg||'')}`;
}

function switchLogTab(tab, elem) {
  currentLogTab = tab;
  document.querySelectorAll('.log-tab').forEach(e => e.classList.remove('active'));
  if (elem) elem.classList.add('active');
  render();
}

// ─── Export / Import ───
function exportData() {
  fetch('/api/export').then(r=>r.blob()).then(b => {
    const u = URL.createObjectURL(b);
    const a = document.createElement('a');
    a.href = u; a.download = 'molty-'+new Date().toISOString().slice(0,10)+'.json'; a.click();
    URL.revokeObjectURL(u);
  });
}
function importData(e) {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = ev => {
    fetch('/api/import', { method:'POST', headers:{'Content-Type':'application/json'}, body:ev.target.result })
      .then(() => alert('✅ Data imported successfully!')).catch(err => alert('❌ Error: ' + err));
  };
  r.readAsText(f);
}

// ─── Boot ───
document.addEventListener('DOMContentLoaded', () => {
  fetch('/api/state').then(r => r.json()).then(d => { S = d; render(); }).catch(()=>{});
  connectWS();
  setTimeout(() => { render(); }, 2000);
});
