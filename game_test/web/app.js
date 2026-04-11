// ================================================================== //
//  全局状态                                                            //
// ================================================================== //
const API = window.GameApi?.API || '';
const appApi = (method, path, body) => window.GameControllers.performJsonRequest(method, path, body);
let selectedServer = null;  // {name, ip, port}
let selectedRoleId = null;
let selectedItemId = null;
let backpackItemsCache = [];
let eventSource = null;
let isConnected = false;
let prevConnected = false;
let unifiedLoginLockPromise = null;
let unifiedLoginLastTriggerTs = 0;
let lastConnectInfo = { account: '', password: '', loginServer: '', serverIp: '', serverPort: 0, serverName: '', roleId: '' };
let buyItemFavorites = [];
let selectedBuyItemCode = '';
let starStoneLoopRunning = false;
let starStoneAwaiting = 'idle';
let starStoneLoopCount = 0;
let starStoneTotalStone = 0;
let starStoneTotalFragment = 0;
let starStoneTimer = null;
let battleMonsters = [];
let selectedMonsterCode = '';
let battleLogMode = 'simple'; // simple | detail
let autoUseRules = [];
let controlState = { auto_reconnect_enabled: false, reconnect_state: 'idle', reconnect_attempts: 0, reconnect_max_attempts: 3, reconnect_last_error: '', reconnect_next_retry_in: null, reconnect_banned_wait_in: null };
/** 与后端 config.DEFAULT_BATTLE_LOOP_DELAY_MS 同步，由 /api/status 的 default_battle_loop_delay_ms 写入 */
let serverDefaultBattleLoopDelayMs = null;
let battleState = { state: 'idle', in_progress: false, mode: 'idle', loop_running: false, current_monster: '', loop_monster_code: '', loop_delay_ms: 0, total_count: 0, total_exp: 0, total_gold_copper: 0 };
let lastStatusData = { connected: false, connection_status: 'disconnected', role: null, server_name: '' };

// ================================================================== //
//  工具函数                                                            //
// ================================================================== //
async function api(method, path, body) {
  return appApi(method, path, body);
}

function withValidationWarning(baseText, res) {
  const warn = res?.validation_warning;
  return warn ? `${baseText}；${warn}` : baseText;
}

function showMsg(elId, text, type = 'info') {
  const el = document.getElementById(elId);
  if (!el) return;
  el.className = `msg msg-${type === 'ok' ? 'ok' : type === 'err' ? 'err' : 'info'}`;
  el.textContent = text;
}

function clearMsg(elId) {
  const el = document.getElementById(elId);
  if (el) { el.className = ''; el.textContent = ''; }
}

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach((b, i) => {
    const names = ['probe', 'backpack', 'chat', 'battle'];
    b.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.toggle('active', el.id === 'tab-' + name);
  });
  if (name === 'probe') loadPackets();
}

// ================================================================== //
//  SSE 实时连接                                                        //
// ================================================================== //
function startSSE() {
  if (eventSource) { eventSource.close(); }
  eventSource = window.GameEvents.createEventSource(API + '/api/events', (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'status') updateStatus(msg.data);
      else if (msg.type === 'control_state') setControlState(msg.data);
      else if (msg.type === 'backpack') {
        renderBackpack(msg.data);
        handleStarStoneBackpackUpdate();
      }
      else if (msg.type === 'packet') {
        appendPacketRow(msg.data);
        appendBattlePacketLine(msg.data);
        handleStarStonePacket(msg.data);
      }
      else if (msg.type === 'annotation') updatePacketAnnotation(msg.data);
      else if (msg.type === 'role_stats') renderRoleStats(msg.data);
      else if (msg.type === 'battle_response') onBattleResponse(msg.data);
      else if (msg.type === 'battle_end') onBattleEnd(msg.data);
      else if (msg.type === 'battle_not_killed') onBattleNotKilled(msg.data);
      else if (msg.type === 'battle_state') onBattleState(msg.data);
      else if (msg.type === 'control_log') onControlLog(msg.data);
      else if (msg.type === 'auto_use') onAutoUseEvent(msg.data);
      else if (msg.type === 'monsters') renderMonsterList(msg.data);
    } catch (_) {}
  }, () => {
    setTimeout(startSSE, 3000);
  });
}

// ================================================================== //
//  状态更新                                                            //
// ================================================================== //
// 心跳状态轮询定时器
let _heartbeatPollTimer = null;
const STALE_WARN_S = 60;   // 距上次收包 > 60s 开始显示警告
const RECONNECT_ROLE_STATS_TIMEOUT_MS = 5000;
const RECONNECT_ROLE_STATS_POLL_MS = 250;

function setControlState(data) {
  controlState = { ...controlState, ...(data || {}) };
  const chk = document.getElementById('chk-auto-reconnect');
  if (chk && typeof controlState.auto_reconnect_enabled === 'boolean' && chk.checked !== controlState.auto_reconnect_enabled) {
    chk.checked = controlState.auto_reconnect_enabled;
  }
  renderTopbarStatus(lastStatusData);
}

function updateBattleState(data) {
  battleState = { ...battleState, ...(data || {}) };
  if (!selectedMonsterCode && battleState.loop_monster_code) {
    selectedMonsterCode = String(battleState.loop_monster_code || '').toLowerCase();
  }
  const delayEl = document.getElementById('battle-loop-delay');
  if (delayEl) {
    const v = Number(battleState.loop_delay_ms);
    if (Number.isFinite(v) && v > 0) {
      delayEl.value = String(Math.floor(v));
    } else if (
      serverDefaultBattleLoopDelayMs != null &&
      Number.isFinite(serverDefaultBattleLoopDelayMs) &&
      delayEl.value.trim() === ''
    ) {
      delayEl.value = String(serverDefaultBattleLoopDelayMs);
    }
  }
  updateCurrentMonster();
  updateBattleStatsText();
  syncBattleLoopButton();
  renderTopbarStatus(lastStatusData);
}

function getReconnectInfoText() {
  const state = String(controlState.reconnect_state || 'idle');
  if (state === 'running') {
    return `后端重连中…(${Number(controlState.reconnect_attempts || 0)}/${Number(controlState.reconnect_max_attempts || 0)})`;
  }
  if (state === 'scheduled') {
    const wait = Number(controlState.reconnect_next_retry_in || 0);
    return wait > 0 ? `${wait.toFixed(wait >= 10 ? 0 : 1)}s 后由后端重连` : '等待后端重连';
  }
  if (state === 'banned_wait') {
    const wait = Number(controlState.reconnect_banned_wait_in || 0);
    return wait > 0 ? `角色禁封等待中，约 ${Math.ceil(wait / 60)} 分钟后重连` : '角色禁封等待中';
  }
  if (state === 'failed') {
    return controlState.reconnect_last_error
      ? `后端重连失败：${controlState.reconnect_last_error}`
      : '后端重连失败';
  }
  return '';
}

function renderTopbarStatus(data) {
  const dot = document.getElementById('status-dot');
  const txt = document.getElementById('status-text');
  const badge = document.getElementById('role-badge');
  const btnDisc = document.getElementById('btn-disconnect');
  const reconnectInfoEl = document.getElementById('reconnect-info');
  const reconnectInfoText = getReconnectInfoText();
  reconnectInfoEl.textContent = reconnectInfoText;

  if (isConnected) {
    const r = data.role;
    const age = data.last_recv_age;
    const stale = age !== null && age > STALE_WARN_S;
    dot.className = stale ? 'stale' : 'connected';
    txt.textContent = stale ? `⚠ 心跳超时 ${Math.round(age)}s · ${data.server_name || ''}` : `已连接 · ${data.server_name || ''}`;
    badge.textContent = r ? `${r.role_name} · ${r.role_job}` : '未选角';
    btnDisc.style.display = 'inline-block';
    document.getElementById('role-stats-panel').classList.add('visible');
    return;
  }

  _stopHeartbeatPoll();
  dot.className = '';
  btnDisc.style.display = data.connection_status === 'got_session' ? 'inline-block' : 'none';
  if (data.connection_status !== 'got_session') {
    badge.textContent = '—';
    renderBackpack([]);
    document.getElementById('role-stats-panel').classList.remove('visible');
    if (controlState.reconnect_state === 'running') {
      txt.textContent = '后端重连中…';
    } else if (controlState.reconnect_state === 'scheduled') {
      txt.textContent = battleState.loop_running ? '循环战斗等待后端恢复' : '等待后端重连';
    } else if (controlState.reconnect_state === 'banned_wait') {
      txt.textContent = '该角色已被禁封';
    } else if (controlState.reconnect_state === 'failed') {
      txt.textContent = battleState.loop_running ? '循环战斗恢复失败' : '重连失败';
    } else {
      txt.textContent = '未连接';
    }
  } else {
    txt.textContent = '已登录，未选角';
  }
}

function updateStatus(data) {
  lastStatusData = { ...lastStatusData, ...(data || {}) };
  const d = data?.default_battle_loop_delay_ms;
  if (Number.isFinite(Number(d)) && Number(d) >= 0) {
    serverDefaultBattleLoopDelayMs = Math.floor(Number(d));
  }
  isConnected = data.connected;
  prevConnected = isConnected;
  if (data.control_state) setControlState(data.control_state);
  if (data.battle_state) updateBattleState(data.battle_state);

  if (isConnected) {
    // 加载角色属性
    api('GET', '/api/role-stats').then(r => { if (r.ok && r.stats) renderRoleStats(r); });
    // 启动心跳轮询（已连接时每 20s 刷新一次状态以更新心跳年龄）
    _startHeartbeatPoll();
  }
  renderTopbarStatus(data);
}

function _startHeartbeatPoll() {
  if (_heartbeatPollTimer) return;
  // 每 10s 轮询一次，与后端心跳线程节奏对齐
  _heartbeatPollTimer = setInterval(async () => {
    if (!isConnected) { _stopHeartbeatPoll(); return; }
    try {
      const status = await api('GET', '/api/status');
      if (status) updateStatus(status);
    } catch (_) {}
  }, 10000);
}

function _stopHeartbeatPoll() {
  if (_heartbeatPollTimer) { clearInterval(_heartbeatPollTimer); _heartbeatPollTimer = null; }
}

// ================================================================== //
//  登录流程                                                            //
// ================================================================== //
async function performLoginFlow(info) {
  // Step 1: 登录
  const loginRes = await api('POST', '/api/login', {
    account: info.account, password: info.password, server: info.loginServer
  });
  if (!loginRes.ok) return { ok: false, error: loginRes.error || '登录失败' };

  // Step 2: 选区
  const rolesRes = await api('POST', '/api/roles', {
    server_ip: info.serverIp, server_port: info.serverPort
  });
  if (!rolesRes.ok) return { ok: false, error: rolesRes.error || '选区失败' };

  // Step 3: 选角
  const enterRes = await api('POST', '/api/select-role', { role_id: info.roleId });
  if (!enterRes.ok) return { ok: false, error: enterRes.error || '选角失败' };
  return { ok: true, role: enterRes.role || null };
}

function hasRenderableRoleStats(stats) {
  return !!stats && typeof stats === 'object' && Object.keys(stats).length > 0;
}

async function waitForRoleStatsReady(timeoutMs = RECONNECT_ROLE_STATS_TIMEOUT_MS) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await api('GET', '/api/role-stats').catch(() => null);
    if (res?.ok && hasRenderableRoleStats(res.stats)) {
      renderRoleStats(res);
      return { ok: true, stats: res.stats };
    }
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_ROLE_STATS_POLL_MS));
  }
  return { ok: false, error: '重连后未获取到角色属性' };
}

async function runUnifiedQuickLogin(info, opts = {}) {
  const reason = String(opts.reason || '一键登录');
  const updateUi = opts.updateUi !== false;
  const requireRoleStats = opts.requireRoleStats !== false;
  const now = Date.now();
  if (unifiedLoginLockPromise) {
    return { ok: false, error: `${reason}触发过于频繁，请稍后再试`, throttled: true };
  }
  if (now - unifiedLoginLastTriggerTs < 1000) {
    return { ok: false, error: `${reason}1秒内只允许触发一次`, throttled: true };
  }
  unifiedLoginLastTriggerTs = now;

  const runner = (async () => {
    if (!info.account || !info.password || !info.loginServer || !info.serverIp || !info.serverPort || !info.roleId) {
      return { ok: false, error: '缺少一键登录所需的完整上下文' };
    }
    if (updateUi) {
      document.getElementById('status-text').textContent = `${reason}中…`;
    }
    const flow = await performLoginFlow(info);
    if (flow.ok && requireRoleStats) {
      const statsReady = await waitForRoleStatsReady();
      if (!statsReady.ok) {
        await api('POST', '/api/disconnect', {}).catch(() => null);
        return statsReady;
      }
    }
    if (flow.ok) {
      lastConnectInfo = {
        account: info.account || '',
        password: info.password || '',
        loginServer: info.loginServer || '',
        serverIp: info.serverIp || '',
        serverPort: Number(info.serverPort || 0),
        serverName: info.serverName || '',
        roleId: info.roleId || '',
      };
      localStorage.setItem('lastConnectInfo', JSON.stringify(lastConnectInfo));
    }
    return flow;
  })();

  unifiedLoginLockPromise = runner;
  try {
    return await runner;
  } finally {
    if (unifiedLoginLockPromise === runner) {
      unifiedLoginLockPromise = null;
    }
  }
}

async function saveQuickLoginEntry(roleObj) {
  if (!lastConnectInfo.account || !lastConnectInfo.password || !lastConnectInfo.roleId) return;
  await api('POST', '/api/quick-logins', {
    account: lastConnectInfo.account,
    password: lastConnectInfo.password,
    login_server: lastConnectInfo.loginServer,
    server_ip: lastConnectInfo.serverIp,
    server_port: lastConnectInfo.serverPort,
    server_name: lastConnectInfo.serverName,
    role_id: lastConnectInfo.roleId,
    role_name: roleObj?.role_name || '',
    role_job: roleObj?.role_job || '',
  }).catch(() => null);
  loadQuickLogins();
}

async function loadQuickLogins() {
  const res = await api('GET', '/api/quick-logins').catch(() => null);
  if (!res || !res.ok) return;
  const list = Array.isArray(res.items) ? res.items : [];
  const box = document.getElementById('quick-login-list');
  if (!box) return;
  if (!list.length) {
    box.innerHTML = '<div class="text-muted text-sm">暂无已保存登录</div>';
    return;
  }
  box.innerHTML = list.map((x) => {
    const id = escAttr(x.id || '');
    const line = `${escHtml(x.account || '')} · ${escHtml(x.server_name || x.server_ip || '')} · ${escHtml(x.role_name || x.role_id || '')}`;
    return `<div style="border:1px solid var(--border); border-radius:6px; padding:6px; margin-bottom:6px;">
      <div style="font-size:12px; margin-bottom:6px;">${line}</div>
      <div class="battle-row">
        <button class="btn btn-primary btn-sm" onclick="quickLoginRun('${id}')">登录</button>
        <button class="btn btn-danger btn-sm" onclick="quickLoginDelete('${id}')">删除</button>
      </div>
    </div>`;
  }).join('');
}

async function quickLoginRun(id) {
  const res = await api('GET', '/api/quick-logins').catch(() => null);
  if (!res || !res.ok) return;
  const item = (res.items || []).find(x => x.id === id);
  if (!item) return;
  startSSE();
  lastConnectInfo = {
    account: item.account || '',
    password: item.password || '',
    loginServer: item.login_server || '',
    serverIp: item.server_ip || '',
    serverPort: Number(item.server_port || 0),
    serverName: item.server_name || '',
    roleId: item.role_id || '',
  };
  const flow = await runUnifiedQuickLogin(lastConnectInfo, { reason: '一键登录' });
  if (flow.ok) {
    localStorage.setItem('lastConnectInfo', JSON.stringify(lastConnectInfo));
    document.getElementById('login-panel').style.display = 'none';
    document.getElementById('server-panel').style.display = 'none';
    document.getElementById('role-panel').style.display = 'none';
  } else {
    showMsg('login-msg', `一键登录失败: ${flow.error || '未知错误'}`, 'err');
  }
}

async function quickLoginDelete(id) {
  await api('DELETE', `/api/quick-logins/${encodeURIComponent(id)}`).catch(() => null);
  loadQuickLogins();
}

async function doLogin() {
  const account = document.getElementById('inp-account').value.trim();
  const password = document.getElementById('inp-password').value.trim();
  const server = document.getElementById('sel-login-server').value;
  if (!account || !password) { showMsg('login-msg', '账号和密码不能为空', 'err'); return; }

  document.getElementById('btn-login').disabled = true;
  showMsg('login-msg', '登录中…', 'info');
  const res = await api('POST', '/api/login', { account, password, server });
  document.getElementById('btn-login').disabled = false;

  if (res.ok) {
    lastConnectInfo.account = account;
    lastConnectInfo.password = password;
    lastConnectInfo.loginServer = server;
    showMsg('login-msg', '登录成功', 'ok');
    startSSE();
    // 短暂显示成功提示后隐藏登录面板，展示服务器选择
    setTimeout(() => {
      document.getElementById('login-panel').style.display = 'none';
      showServerPanel(res.announcement, res.server_list);
    }, 600);
  } else {
    showMsg('login-msg', res.error || '登录失败', 'err');
  }
}

function showServerPanel(announcement, serverList) {
  document.getElementById('server-panel').style.display = '';
  // 公告
  const ann = announcement || '暂无公告';
  document.getElementById('announcement-box').textContent = ann;
  // 服务器列表：优先使用从登录响应解析出的，否则用预设
  const list = document.getElementById('server-list');
  list.innerHTML = '';
  const servers = serverList && serverList.length > 0 ? serverList : [];
  // 追加预设服务器
  const preset = [
    { name: '龙一服', ip: 'tlz.shuihl.cn', port: 12065, id: 'preset' },
    { name: '龙二服', ip: 'tl10.shuihl.cn', port: 12001, id: 'preset' },
    { name: '生死符(推荐)', ip: 'tl11.shuihl.cn', port: 12001, id: 'preset' },
  ];
  const all = servers.length > 0 ? servers : preset;
  all.forEach(srv => {
    const d = document.createElement('div');
    d.className = 'server-item';
    d.innerHTML = `<div class="server-dot"></div>
      <span class="server-name">${escHtml(srv.name)}</span>
      <span class="server-addr">${escHtml(srv.ip)}:${srv.port}</span>`;
    d.onclick = () => {
      document.querySelectorAll('.server-item').forEach(x => x.classList.remove('selected'));
      d.classList.add('selected');
      selectedServer = srv;
      document.getElementById('btn-fetch-roles').disabled = false;
    };
    list.appendChild(d);
  });
}

async function fetchRoles() {
  if (!selectedServer) { showMsg('server-msg', '请先选择服务器', 'err'); return; }
  document.getElementById('btn-fetch-roles').disabled = true;
  showMsg('server-msg', '连接游戏服，获取角色列表…', 'info');
  const res = await api('POST', '/api/roles', { server_ip: selectedServer.ip, server_port: selectedServer.port });
  document.getElementById('btn-fetch-roles').disabled = false;
  if (res.ok) {
    lastConnectInfo.serverIp = selectedServer.ip;
    lastConnectInfo.serverPort = selectedServer.port;
    lastConnectInfo.serverName = selectedServer.name;
    clearMsg('server-msg');
    renderRoleList(res.roles || []);
    // 隐藏服务器面板，展示角色选择
    document.getElementById('server-panel').style.display = 'none';
    document.getElementById('role-panel').style.display = '';
  } else {
    showMsg('server-msg', res.error || '获取角色列表失败', 'err');
  }
}

function renderRoleList(roles) {
  const container = document.getElementById('role-list');
  container.innerHTML = '';
  if (!roles || roles.length === 0) {
    container.innerHTML = '<div class="text-muted text-sm">未找到角色</div>';
    return;
  }
  roles.forEach(r => {
    const d = document.createElement('div');
    d.className = 'role-card';
    d.innerHTML = `<div class="role-avatar">🧙</div>
      <div>
        <div class="role-name">${escHtml(r.role_name)}</div>
        <div class="role-job">${escHtml(r.role_job)} · ID: ${escHtml(r.role_id)}</div>
      </div>`;
    d.onclick = () => {
      document.querySelectorAll('.role-card').forEach(x => x.classList.remove('selected'));
      d.classList.add('selected');
      selectedRoleId = r.role_id;
      document.getElementById('btn-enter').disabled = false;
    };
    container.appendChild(d);
  });
}

async function enterGame() {
  if (!selectedRoleId) return;
  document.getElementById('btn-enter').disabled = true;
  showMsg('role-msg', '进入游戏中…', 'info');
  const res = await api('POST', '/api/select-role', { role_id: selectedRoleId });
  document.getElementById('btn-enter').disabled = false;
  if (res.ok) {
    lastConnectInfo.roleId = selectedRoleId;
    // 持久化到 localStorage，供页面刷新后自动重连使用
    localStorage.setItem('lastConnectInfo', JSON.stringify(lastConnectInfo));
    await saveQuickLoginEntry(res.role || null);
    // 进游戏成功：隐藏角色面板，顶栏已显示角色信息
    document.getElementById('role-panel').style.display = 'none';
  } else {
    document.getElementById('btn-enter').disabled = false;
    showMsg('role-msg', res.error || '进入游戏失败', 'err');
  }
}

async function disconnect() {
  document.getElementById('reconnect-info').textContent = '';
  if (!battleState.loop_running) {
    // 非循环战斗场景下，主动断线仍视为彻底退出
    lastConnectInfo.roleId = '';
    localStorage.removeItem('lastConnectInfo');
  }
  await api('POST', '/api/disconnect');
  prevConnected = false;
  if (!battleState.loop_running) {
    resetToLoginState();
  }
}

function resetToLoginState() {
  isConnected = false;
  _stopHeartbeatPoll();
  selectedServer = null;
  selectedRoleId = null;
  selectedItemId = null;
  document.getElementById('login-panel').style.display = '';
  document.getElementById('server-panel').style.display = 'none';
  document.getElementById('role-panel').style.display = 'none';
  document.getElementById('btn-login').disabled = false;
  document.getElementById('status-dot').className = '';
  document.getElementById('status-text').textContent = '未连接';
  document.getElementById('role-badge').textContent = '—';
  document.getElementById('btn-disconnect').style.display = 'none';
  document.getElementById('role-stats-panel').classList.remove('visible');
  renderBackpack([]);
}

// ================================================================== //
//  背包                                                                //
// ================================================================== //
async function refreshBackpack() {
  // 对齐 main-000.py _refresh_backpack_manual：清空显示 → 拉取最新缓存 → 展示数量
  const grid = document.getElementById('backpack-grid');
  const countEl = document.getElementById('backpack-count');
  const msgEl = document.getElementById('backpack-msg');
  grid.innerHTML = '<div class="text-muted text-sm">刷新中…</div>';
  countEl.textContent = '';

  const res = await api('POST', '/api/backpack/refresh');
  if (res.ok) {
    renderBackpack(res.items || []);
    showMsg('backpack-msg', `手动刷新完成，背包当前物品数量：${res.count} 件`, 'ok');
  } else {
    showMsg('backpack-msg', res.error || '刷新失败', 'err');
  }
}

function renderBackpack(items) {
  backpackItemsCache = Array.isArray(items) ? items : [];
  const grid = document.getElementById('backpack-grid');
  document.getElementById('backpack-count').textContent = `共 ${backpackItemsCache.length} 件`;
  if (!backpackItemsCache.length) {
    grid.innerHTML = '<div class="text-muted text-sm">背包为空</div>';
    return;
  }
  grid.innerHTML = '';
  backpackItemsCache.forEach(item => {
    const d = document.createElement('div');
    d.className = 'item-card' + (item.can_disassemble ? ' can-decompose' : '');
    if (item.item_id === selectedItemId) d.classList.add('selected');
    d.innerHTML = `<div class="item-name">${escHtml(item.name)}</div>
      <div class="item-qty">数量：${item.quantity}</div>
      <div class="item-id mono">${item.item_id}</div>`;
    d.onclick = () => {
      document.querySelectorAll('.item-card').forEach(x => x.classList.remove('selected'));
      d.classList.add('selected');
      selectedItemId = item.item_id;
    };
    grid.appendChild(d);
  });
}

async function useSelected() {
  if (!selectedItemId) { showMsg('backpack-msg', '请先选择物品', 'err'); return; }
  const res = await api('POST', '/api/item/use', { item_id: selectedItemId, quantity: 1 });
  showMsg('backpack-msg', res.ok ? withValidationWarning(`已加入发送队列 x${res.queued}`, res) : res.error, res.ok ? 'ok' : 'err');
}

async function dropSelected() {
  if (!selectedItemId) { showMsg('backpack-msg', '请先选择物品', 'err'); return; }
  const res = await api('POST', '/api/item/drop', { item_id: selectedItemId, quantity: 1 });
  showMsg('backpack-msg', res.ok ? withValidationWarning('丢弃请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

async function decomposeSelected() {
  if (!selectedItemId) { showMsg('backpack-msg', '请先选择物品', 'err'); return; }
  const res = await api('POST', '/api/item/decompose', { item_id: selectedItemId });
  showMsg('backpack-msg', res.ok ? withValidationWarning('分解请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

async function decomposeAll() {
  const role = document.getElementById('role-badge').textContent;
  const jobMap = { '侠客': ['侠士战甲','侠士头盔'], '刺客': ['刺客战甲','刺客头盔'], '术士': ['术士战甲','术士头盔'] };
  let protected_items = [];
  for (const [job, items] of Object.entries(jobMap)) {
    if (role.includes(job)) { protected_items = items; break; }
  }
  const res = await api('POST', '/api/item/decompose-all', { protected_items });
  showMsg('backpack-msg',
    res.ok ? withValidationWarning(`一键分解完成：已分解 ${res.queued?.length || 0} 件，跳过 ${res.skipped?.length || 0} 件`, res) : res.error,
    res.ok ? 'ok' : 'err');
}

async function exchangeWuling() {
  const res = await api('POST', '/api/item/exchange-wuling');
  showMsg('backpack-msg', res.ok ? withValidationWarning('兑换五灵请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

async function loadBuyItems() {
  const res = await api('GET', '/api/buy-items').catch(() => null);
  if (!res || !res.ok) return;
  buyItemFavorites = Array.isArray(res.items) ? res.items : [];
  renderBuyItems();
}

function renderBuyItems() {
  const select = document.getElementById('backpack-buy-select');
  if (!select) return;
  if (!buyItemFavorites.length) {
    select.innerHTML = '<option value="">请选择常用物品</option>';
    selectedBuyItemCode = '';
    return;
  }
  const options = buyItemFavorites.map((x) =>
    `<option value="${escAttr(x.code || '')}">${escHtml(x.name || '')} (${escHtml(x.code || '')})</option>`
  ).join('');
  if (!buyItemFavorites.some((x) => (x.code || '').toLowerCase() === selectedBuyItemCode)) {
    selectedBuyItemCode = (buyItemFavorites[0]?.code || '').toLowerCase();
  }
  select.innerHTML = `<option value="">请选择常用物品</option>${options}`;
  select.value = selectedBuyItemCode || '';
}

function selectBuyItem(code) {
  selectedBuyItemCode = String(code || '').trim().toLowerCase();
  const item = buyItemFavorites.find((x) => (x.code || '').toLowerCase() === selectedBuyItemCode);
  document.getElementById('backpack-buy-select').value = selectedBuyItemCode;
  document.getElementById('backpack-buy-code').value = selectedBuyItemCode;
  document.getElementById('backpack-buy-name').value = item ? (item.name || '') : '';
}

async function buyItem() {
  const input = document.getElementById('backpack-buy-code');
  const itemCode = input.value.trim().toLowerCase();
  if (!itemCode) { showMsg('backpack-msg', '请输入 22 位物品编码', 'err'); return; }
  const res = await api('POST', '/api/item/buy', { item_code: itemCode });
  showMsg('backpack-msg', res.ok ? withValidationWarning('购买请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

async function saveBuyItem() {
  const name = document.getElementById('backpack-buy-name').value.trim();
  const code = document.getElementById('backpack-buy-code').value.trim().toLowerCase();
  if (!name || !code) { showMsg('backpack-msg', '请填写物品名称和 22 位编码', 'err'); return; }
  const res = await api('POST', '/api/buy-items', { name, code });
  if (res.ok) {
    buyItemFavorites = Array.isArray(res.items) ? res.items : [];
    selectedBuyItemCode = code;
    renderBuyItems();
    document.getElementById('backpack-buy-select').value = code;
    showMsg('backpack-msg', '常用购买物品已保存', 'ok');
  } else {
    showMsg('backpack-msg', res.error || '保存失败', 'err');
  }
}

async function deleteBuyItem() {
  const code = document.getElementById('backpack-buy-code').value.trim().toLowerCase()
    || document.getElementById('backpack-buy-select').value.trim().toLowerCase();
  if (!code) { showMsg('backpack-msg', '请先选择或输入要删除的常用物品', 'err'); return; }
  const res = await api('DELETE', `/api/buy-items/${encodeURIComponent(code)}`);
  if (res.ok) {
    buyItemFavorites = Array.isArray(res.items) ? res.items : [];
    if (selectedBuyItemCode === code) selectedBuyItemCode = '';
    renderBuyItems();
    document.getElementById('backpack-buy-select').value = '';
    document.getElementById('backpack-buy-name').value = '';
    document.getElementById('backpack-buy-code').value = '';
    showMsg('backpack-msg', '常用购买物品已删除', 'ok');
  } else {
    showMsg('backpack-msg', res.error || '删除失败', 'err');
  }
}

function appendStarStoneLog(text, kind = 'info') {
  const box = document.getElementById('star-stone-log');
  if (!box) return;
  const line = document.createElement('div');
  line.className = kind === 'err' ? 'text-red' : kind === 'ok' ? 'text-green' : 'text-muted';
  line.textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function updateStarStoneButton() {
  const btn = document.getElementById('btn-star-stone-loop');
  if (!btn) return;
  btn.textContent = starStoneLoopRunning ? '停止获取升星石' : '获取升星石';
  btn.className = starStoneLoopRunning ? 'btn btn-danger btn-sm' : 'btn btn-primary btn-sm';
}

function clearStarStoneTimer() {
  if (starStoneTimer) {
    clearTimeout(starStoneTimer);
    starStoneTimer = null;
  }
}

function decodePacketText(rawHex) {
  try {
    return bytesFromHex(rawHex).decodeText;
  } catch (_) {
    return '';
  }
}

function bytesFromHex(rawHex) {
  const cleanHex = String(rawHex || '').replace(/\s+/g, '').toLowerCase();
  const bytes = [];
  for (let i = 0; i < cleanHex.length; i += 2) {
    bytes.push(parseInt(cleanHex.slice(i, i + 2), 16));
  }
  const arr = new Uint8Array(bytes);
  return {
    decodeText: new TextDecoder('utf-8', { fatal: false }).decode(arr).replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '').trim(),
  };
}

function parseStarStoneRewards(record) {
  const text = decodePacketText(record?.raw_hex || '');
  if (!text || !text.includes('分解装备')) return null;
  const stone = Number((text.match(/升星(?:基础)?石\*(\d+)/) || [])[1] || 0);
  const fragment = Number((text.match(/宝石碎片\*(\d+)/) || [])[1] || 0);
  if (!stone && !fragment) return null;
  return { stone, fragment, text };
}

function findStarStoneBuyFavorite() {
  return buyItemFavorites.find((x) => /特步鞋/.test(x.name || ''));
}

function findStarStoneDecomposeItem() {
  return backpackItemsCache.find((x) => /特步鞋/.test(x.name || ''));
}

function stopStarStoneLoop(reason = '', msgType = 'info') {
  starStoneLoopRunning = false;
  starStoneAwaiting = 'idle';
  clearStarStoneTimer();
  updateStarStoneButton();
  if (reason) {
    appendStarStoneLog(reason, msgType === 'err' ? 'err' : 'ok');
    showMsg('backpack-msg', reason, msgType);
  }
}

async function runStarStoneCycle() {
  if (!starStoneLoopRunning) return;
  const favorite = findStarStoneBuyFavorite();
  if (!favorite) {
    stopStarStoneLoop('未找到常用购买物品“特步鞋”，请先在购买物品管理中保存', 'err');
    return;
  }
  starStoneAwaiting = 'buy';
  const res = await api('POST', '/api/item/buy', { item_code: favorite.code });
  if (!res.ok) {
    stopStarStoneLoop(res.error || '购买失败，已停止', 'err');
    return;
  }
  appendStarStoneLog(`第${starStoneLoopCount + 1}轮：已发送购买 ${favorite.name}`, 'info');
}

async function startStarStoneLoop() {
  if (starStoneLoopRunning) return;
  starStoneLoopRunning = true;
  starStoneAwaiting = 'idle';
  starStoneLoopCount = 0;
  starStoneTotalStone = 0;
  starStoneTotalFragment = 0;
  clearStarStoneTimer();
  document.getElementById('star-stone-log').innerHTML = '';
  updateStarStoneButton();
  appendStarStoneLog('开始执行获取升星石：先传送上京', 'info');
  const tpRes = await api('POST', '/api/teleport', { destination: '上京' });
  if (!tpRes.ok) {
    stopStarStoneLoop(tpRes.error || '传送上京失败，已停止', 'err');
    return;
  }
  appendStarStoneLog('已发送传送上京', 'ok');
  starStoneTimer = setTimeout(runStarStoneCycle, 1000);
}

function toggleStarStoneLoop() {
  if (starStoneLoopRunning) {
    stopStarStoneLoop('已手动停止获取升星石', 'info');
    return;
  }
  startStarStoneLoop();
}

async function handleStarStonePacket(record) {
  if (!starStoneLoopRunning || record?.direction !== 'DN') return;
  const fingerprint = record.fingerprint || '';
  const text = decodePacketText(record.raw_hex || '');

  if (text.includes('包裹已满')) {
    stopStarStoneLoop('包裹已满，已自动停止', 'err');
    return;
  }

  if (starStoneAwaiting === 'buy' && fingerprint.includes('e607')) {
    await handleStarStoneBackpackUpdate();
    return;
  }

  if (starStoneAwaiting === 'decompose') {
    const rewards = parseStarStoneRewards(record);
    if (!rewards) return;
    starStoneLoopCount += 1;
    starStoneTotalStone += rewards.stone;
    starStoneTotalFragment += rewards.fragment;
    appendStarStoneLog(
      `第${starStoneLoopCount}轮：获得升星石 ${rewards.stone}，获得宝石碎片 ${rewards.fragment}；累计 升星石 ${starStoneTotalStone} / 宝石碎片 ${starStoneTotalFragment}`,
      'ok'
    );
    starStoneAwaiting = 'idle';
    clearStarStoneTimer();
    starStoneTimer = setTimeout(runStarStoneCycle, 1000);
  }
}

// ================================================================== //
//  聊天                                                                //
// ================================================================== //
function appendChatLine(text, cls) {
  const log = document.getElementById('chat-log');
  const div = document.createElement('div');
  div.className = cls || '';
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;
  const res = await api('POST', '/api/chat', { message });
  if (res.ok) {
    appendChatLine(`我: ${message}`, 'chat-msg-self');
    input.value = '';
  } else {
    appendChatLine(`发送失败: ${res.error}`, 'chat-msg-system');
  }
}

// ================================================================== //
//  战斗                                                                //
// ================================================================== //
async function loadMonsters() {
  const res = await api('GET', '/api/battle/monsters').catch(() => null);
  if (res && res.ok) renderMonsterList(res.monsters || []);
}

async function loadAutoUseRules() {
  const res = await api('GET', '/api/auto-use/config').catch(() => null);
  if (!res || !res.ok) return;
  autoUseRules = Array.isArray(res.rules) ? res.rules : [];
  renderAutoUseRules();
}

function renderAutoUseRules() {
  const box = document.getElementById('auto-use-list');
  if (!box) return;
  if (!autoUseRules.length) {
    box.innerHTML = '<div class="text-muted text-sm">暂无配置</div>';
    return;
  }
  const isDurationRule = (rule) => ['经验UP', '攻击UP', '金钱UP'].includes(String(rule?.stat_key || ''));
  const buildRuleHint = (rule) => {
    if (rule?.id === 'battle_teleport_ticket') return '条件：战斗开始前执行一次';
    if (isDurationRule(rule)) return `条件：${rule.stat_key || ''} < ${String(rule.threshold ?? '')} 分钟`;
    return `条件：${rule.stat_key || ''} < ${String(rule.threshold ?? '')}`;
  };
  box.innerHTML = autoUseRules.map((r, i) => `
    <div style="border:1px solid var(--border); border-radius:6px; padding:8px; margin-bottom:6px;">
      <label style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
        <input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="autoUseRules[${i}].enabled=this.checked">
        <span>${escHtml(r.label || r.id)}</span>
      </label>
      <div class="battle-row">
        <span class="text-muted">阈值:</span>
        <input type="text" value="${escAttr(String(r.threshold ?? ''))}" style="width:70px; padding:3px 6px;"
          onchange="autoUseRules[${i}].threshold=Number(this.value||0)">
        <span class="text-muted">物品名:</span>
        <input type="text" value="${escAttr(r.item_name || '')}" style="width:180px; padding:3px 6px;"
          onchange="autoUseRules[${i}].item_name=this.value">
        <span class="text-muted">代码:</span>
        <input type="text" value="${escAttr(r.item_id || '')}" style="width:150px; padding:3px 6px;"
          onchange="autoUseRules[${i}].item_id=this.value.toLowerCase()">
      </div>
      <div class="text-muted text-sm mt-8">${escHtml(buildRuleHint(r))}</div>
    </div>
  `).join('');
}

async function saveAutoUseRules() {
  const res = await api('PUT', '/api/auto-use/config', { rules: autoUseRules });
  if (res && res.ok) {
    autoUseRules = res.rules || [];
    renderAutoUseRules();
    appendBattleLog({ raw_text: '自动使用配置已保存' }, 'response');
  } else {
    appendBattleLog({ raw_text: `自动使用配置保存失败: ${res?.error || '未知错误'}` }, 'end');
  }
}

function renderMonsterList(monsters) {
  battleMonsters = Array.isArray(monsters) ? monsters : [];
  const selectEl = document.getElementById('battle-monster-select');
  const manageEl = document.getElementById('battle-monster-manage-select');
  if (!battleMonsters.length) {
    if (selectEl) selectEl.innerHTML = '<option value="">暂无怪物</option>';
    if (manageEl) manageEl.innerHTML = '<option value="">请选择怪物</option>';
    selectedMonsterCode = '';
    updateCurrentMonster();
    return;
  }
  if (!battleMonsters.some(m => (m.code || '').toLowerCase() === selectedMonsterCode)) {
    selectedMonsterCode = (battleMonsters[0].code || '').toLowerCase();
  }
  const options = battleMonsters.map((m) => {
    const code = (m.code || '').toLowerCase();
    return `<option value="${escAttr(code)}">${escHtml(m.name || '')} (${escHtml(code)})</option>`;
  }).join('');
  if (selectEl) {
    selectEl.innerHTML = options;
    selectEl.value = selectedMonsterCode;
  }
  if (manageEl) {
    manageEl.innerHTML = `<option value="">请选择怪物</option>${options}`;
    manageEl.value = selectedMonsterCode || '';
  }
  updateCurrentMonster();
}

function selectMonster(code) {
  selectedMonsterCode = (code || '').toLowerCase();
  const selectEl = document.getElementById('battle-monster-select');
  const manageEl = document.getElementById('battle-monster-manage-select');
  if (selectEl) selectEl.value = selectedMonsterCode;
  if (manageEl) manageEl.value = selectedMonsterCode;
  updateCurrentMonster();
}

async function addMonster() {
  const name = document.getElementById('battle-monster-name').value.trim();
  const code = document.getElementById('battle-monster-code').value.trim().toLowerCase();
  if (!name || code.length !== 4) return;
  const res = await api('POST', '/api/battle/monsters', { name, code });
  if (res.ok) {
    selectedMonsterCode = code;
    renderMonsterList(res.monsters || []);
    document.getElementById('battle-monster-name').value = '';
    document.getElementById('battle-monster-code').value = '';
  }
}

async function deleteMonster(code) {
  const res = await api('DELETE', `/api/battle/monsters/${encodeURIComponent(code)}`);
  if (res.ok) {
    if (selectedMonsterCode === code) selectedMonsterCode = '';
    renderMonsterList(res.monsters || []);
  }
}

async function deleteSelectedMonster() {
  if (!selectedMonsterCode) return;
  await deleteMonster(selectedMonsterCode);
}

function updateCurrentMonster() {
  const activeCode = String(battleState.loop_monster_code || battleState.current_monster || selectedMonsterCode || '').toLowerCase();
  const cur = battleMonsters.find(x => (x.code || '').toLowerCase() === activeCode);
  document.getElementById('battle-current-monster').textContent = cur ? `${cur.name} (${cur.code})` : (activeCode || '未选择');
}

function appendBattleLog(data, kind) {
  const box = document.getElementById('battle-log');
  const line = document.createElement('div');
  line.className = 'battle-log-item' + (kind === 'end' ? ' end' : '');
  if (kind === 'packet') line.dataset.kind = 'packet';
  else line.dataset.kind = 'result';
  const text = (data && data.raw_text) ? data.raw_text : JSON.stringify(data || {});
  line.textContent = text || '(空)';
  if (battleLogMode === 'simple' && line.dataset.kind === 'packet') {
    line.style.display = 'none';
  }
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function onControlLog(data) {
  if (!data?.message) return;
  const kind = data.level === 'warn' ? 'end' : 'response';
  appendBattleLog({ raw_text: data.message }, kind);
}

function appendBattlePacketLine(record) {
  const fp = (record.fingerprint || '').toLowerCase();
  const watch = ['e8030500f603', 'e8030500f703', 'e8030100de07', 'e8030100df07'];
  if (!watch.includes(fp)) return;
  if (battleLogMode !== 'detail') return;
  appendBattleLog({
    raw_text: `[${record.direction}] ${fp} ${record.raw_hex || ''}`.trim(),
  }, 'packet');
}

async function onBattleResponse(data) {
  updateBattleState(data?.battle_state || {});
  if (battleLogMode === 'detail') appendBattleLog(data, 'response');
}

function onBattleEnd(data) {
  updateBattleState(data?.battle_state || {});
  if (data?.no_energy) {
    appendBattleLog({ raw_text: '内力不足' }, 'end');
    return;
  }
  const resultGold = formatGoldFromCopper(
    (typeof data?.gold === 'number') ? data.gold : (parseGoldToCopperFromText(String(data?.raw_text || '')) || 0)
  );
  const resultExp = (typeof data?.exp === 'number')
    ? data.exp
    : Number((String(data?.raw_text || '').match(/(?:获得)?经验[：:+\s]*([0-9]+)/)?.[1] || 0));
  appendBattleLog({
    raw_text: `第${Number(data?.battle_state?.total_count || battleState.total_count || 0)}次 / 本次获得经验${resultExp} / 获得金币${resultGold}`
  }, 'end');
}

async function onBattleNotKilled(data) {
  updateBattleState(data?.battle_state || {});
  if (battleLogMode === 'detail') appendBattleLog(data, 'response');
}

function onBattleState(data) {
  updateBattleState(data || {});
}

function updateBattleStatsText() {
  const g = formatGoldFromCopper(battleState.total_gold_copper || 0);
  document.getElementById('battle-stats').textContent =
    `总战斗次数: ${Number(battleState.total_count || 0)} / 总获得经验: ${Number(battleState.total_exp || 0)} / 总获得金币: ${g}`;
}

function parseGoldToCopperFromText(text) {
  // 支持：17铜 / 2银 / 1金2银3铜
  const match = String(text).match(/(?:获得)?金币[：:]\s*([0-9]+金)?\s*([0-9]+银)?\s*([0-9]+铜)?/);
  if (!match) return null;
  const getNum = (s) => {
    const m = (s || '').match(/([0-9]+)/);
    return m ? Number(m[1]) : 0;
  };
  const jin = getNum(match[1]);
  const yin = getNum(match[2]);
  const tong = getNum(match[3]);
  if (!match[1] && !match[2] && !match[3]) return null;
  return jin * 1000 * 1000 + yin * 1000 + tong;
}

function formatGoldFromCopper(copper) {
  const total = Math.max(0, Number(copper) || 0);
  const jin = Math.floor(total / 1000000);
  const rem1 = total % 1000000;
  const yin = Math.floor(rem1 / 1000);
  const tong = rem1 % 1000;
  return `${jin}金${yin}银${tong}铜`;
}

async function triggerBattleOnce(isManual = false) {
  if (!selectedMonsterCode) return;
  appendBattleLog({ raw_text: '战斗开始' }, 'response');
  const res = await api('POST', '/api/battle/start', {
    monster_code: selectedMonsterCode,
    run_pre_battle_actions: true,
  });
  if (!res.ok) {
    appendBattleLog({ raw_text: `发送失败: ${res.error || '未知错误'}` }, 'end');
    return;
  }
  updateBattleState(res.battle_state || {});
}

async function toggleBattleLoop() {
  if (!selectedMonsterCode && !battleState.loop_running) {
    appendBattleLog({ raw_text: '请先选择怪物' }, 'end');
    return;
  }
  if (!battleState.loop_running) {
    const res = await api('POST', '/api/battle/loop/start', {
      monster_code: selectedMonsterCode,
      loop_delay_ms: getBattleLoopDelayMs(),
    });
    if (!res.ok) {
      appendBattleLog({ raw_text: `启动循环战斗失败：${res.error || '未知错误'}` }, 'end');
      return;
    }
    updateBattleState(res.battle_state || {});
    return;
  }
  const res = await api('POST', '/api/battle/loop/stop', { reason: '前端请求停止循环战斗' });
  if (!res.ok) {
    appendBattleLog({ raw_text: `停止循环战斗失败：${res.error || '未知错误'}` }, 'end');
    return;
  }
  updateBattleState(res.battle_state || {});
}

function getBattleLoopDelayMs() {
  const el = document.getElementById('battle-loop-delay');
  const raw = (el?.value || '').trim();
  if (raw !== '') {
    const n = Number(raw);
    if (Number.isFinite(n) && n >= 0) return Math.floor(n);
  }
  if (serverDefaultBattleLoopDelayMs != null && Number.isFinite(serverDefaultBattleLoopDelayMs) && serverDefaultBattleLoopDelayMs >= 0) {
    return Math.floor(serverDefaultBattleLoopDelayMs);
  }
  const bs = Number(battleState.loop_delay_ms);
  if (Number.isFinite(bs) && bs > 0) return Math.floor(bs);
  return 0;
}

function syncBattleLoopButton() {
  const btn = document.getElementById('btn-battle-loop');
  if (btn) {
    btn.textContent = battleState.loop_running ? '停止循环战斗' : '开始循环战斗';
    btn.classList.toggle('btn-danger', !!battleState.loop_running);
    btn.classList.toggle('btn-success', !battleState.loop_running);
  }
}

function clearBattleLog() {
  document.getElementById('battle-log').innerHTML = '';
}

function onBattleLogModeChange() {
  const modeEl = document.getElementById('battle-log-mode');
  battleLogMode = (modeEl?.value === 'detail') ? 'detail' : 'simple';
  const box = document.getElementById('battle-log');
  if (!box) return;
  box.querySelectorAll('.battle-log-item[data-kind="packet"]').forEach((el) => {
    el.style.display = (battleLogMode === 'detail') ? '' : 'none';
  });
}

function onAutoUseEvent(data) {
  if (!data || !Array.isArray(data.actions) || !data.actions.length) return;
  const lines = data.actions.map(a => {
    if (a.ok) return `[自动使用] ${a.item_name || a.item_id} 已使用`;
    return `[自动使用] ${a.item_name || a.item_id} 失败: ${a.reason || a.error || '未知错误'}`;
  });
  appendBattleLog({ raw_text: lines.join(' / ') }, 'response');
}

// SSE 收到世界频道消息
function handleWorldChat(parsed) {
  if (parsed && parsed.utf8_text) {
    appendChatLine(parsed.utf8_text, 'chat-msg-world');
  }
}

// ================================================================== //
//  报文探测                                                            //
// ================================================================== //
const MAX_PACKET_ROWS = 1000;  // 前端最多保留 1000 条
/** 判定「贴在顶部」：此时顶部插入新报文允许自然跟随最新，不补偿 scrollTop */
const PACKET_LIST_TOP_EPS = 2;
let currentAutoExpandedDetailId = null;

function sortPacketsNewestFirst(records) {
  return [...(records || [])].sort((a, b) => {
    const tsA = Number(a?.ts || 0);
    const tsB = Number(b?.ts || 0);
    if (tsA !== tsB) return tsB - tsA;
    return Number(b?.id || 0) - Number(a?.id || 0);
  });
}

async function handleStarStoneBackpackUpdate() {
  if (!starStoneLoopRunning || starStoneAwaiting !== 'buy') return;
  const item = findStarStoneDecomposeItem();
  if (!item) return;
  starStoneAwaiting = 'decompose';
  const res = await api('POST', '/api/item/decompose', { item_id: item.item_id });
  if (!res.ok) {
    stopStarStoneLoop(res.error || '分解失败，已停止', 'err');
    return;
  }
  appendStarStoneLog(`第${starStoneLoopCount + 1}轮：已发送分解 ${item.name}`, 'info');
}

function buildPacketRow(record, collapseCount = 0, openByDefault = false) {
  const fp = record.fingerprint || record.raw_hex.substring(0, 16);
  const row = document.createElement('div');
  row.className = 'packet-row';
  row.id = `pkt-${record.id}`;
  row.dataset.fingerprint = fp;
  row.dataset.packetId = String(record.id || '');
  row.dataset.packetTs = String(record.ts || '');
  if (collapseCount > 0) row.id = `pkt-fp-${fp}`;

  const p = record.parsed;
  const hasParsed = !!p;
  const level = p ? (p.level || 'generic') : 'unknown';
  const noChineseKnown = hasParsed && level === 'known' && !hasChineseContent(p?.utf8_text);
  const isUnresolved = !hasParsed || noChineseKnown;
  const parseLabel = isUnresolved ? '未解析' : (level === 'known' ? '已解析' : '通用解析');
  const parseCls = isUnresolved ? 'unknown' : (level === 'known' ? 'known' : 'generic');

  const desc = record.annotation || (p && p.type) || '';
  const shortFp = (record.fingerprint || '').substring(8, 16);
  let typeText = '';
  if (desc) {
    typeText = shortFp ? `${shortFp} · ${desc}` : desc;
  } else if (hasParsed && p && p.command_hex) {
    typeText = p.command_hex;
  }

  const countBadge = collapseCount > 1
    ? `<span class="pkt-collapse-count">×${collapseCount}</span>`
    : '';

  row.innerHTML = `
    <div class="packet-header" onclick="togglePktDetail(${record.id})">
      <span class="dir-badge dir-${record.direction}">${record.direction === 'UP' ? '↑ UP' : '↓ DN'}</span>
      <span class="pkt-time">${record.ts_str || ''}</span>
      <span class="pkt-fp mono">${shortFp}</span>
      ${typeText ? `<span class="pkt-type">${escHtml(typeText)}</span>` : ''}
      ${countBadge}
      <span class="parse-badge ${parseCls}">${parseLabel}</span>
    </div>
    <div class="pkt-detail" id="pkt-detail-${record.id}">
      ${renderPktDetail(record)}
    </div>`;
  if (openByDefault) {
    const detail = row.querySelector('.pkt-detail');
    if (detail) detail.classList.add('open');
  }
  return row;
}

function insertPacketRowAtTop(row) {
  const list = document.getElementById('packet-list');
  if (!list) return;
  if (currentAutoExpandedDetailId) {
    const prev = document.getElementById(currentAutoExpandedDetailId);
    if (prev) prev.classList.remove('open');
  }

  const scrollTopBefore = list.scrollTop;
  const scrollHeightBefore = list.scrollHeight;
  const stickToTop = scrollTopBefore <= PACKET_LIST_TOP_EPS;

  list.insertBefore(row, list.firstChild);

  if (!stickToTop) {
    const delta = list.scrollHeight - scrollHeightBefore;
    list.scrollTop = scrollTopBefore + delta;
  }

  const opened = row.querySelector('.pkt-detail.open');
  currentAutoExpandedDetailId = opened ? opened.id : null;

  while (list.children.length > MAX_PACKET_ROWS) {
    list.removeChild(list.lastChild);
  }

  const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
  if (list.scrollTop > maxScroll) {
    list.scrollTop = maxScroll;
  }
}

function appendPacketRow(record) {
  if (document.getElementById('tab-probe').classList.contains('active')) {
    if (!matchFilter(record)) return;
    if (collapseMode) {
      const fp = record.fingerprint || record.raw_hex.substring(0, 16);
      fpCountMap[fp] = (fpCountMap[fp] || 0) + 1;
      const existing = document.getElementById(`pkt-fp-${CSS.escape(fp)}`);
      if (existing) existing.remove();
      insertPacketRowAtTop(buildPacketRow(record, fpCountMap[fp], true));
    } else {
      insertPacketRowAtTop(buildPacketRow(record, 0, true));
    }
  }
  if (record.direction === 'DN' && record.parsed) {
    const fp = record.fingerprint || '';
    if (fp.includes('f207') || (record.parsed.type && record.parsed.type.includes('世界'))) {
      handleWorldChat(record.parsed);
    }
  }
}

function hasChineseContent(text) {
  if (!text) return false;
  return /[\u4e00-\u9fa5]/.test(text);
}


function matchFilter(record) {
  const dir = document.getElementById('flt-dir').value;
  const parsed = document.getElementById('flt-parsed').value;
  const annotated = document.getElementById('flt-annotated').value;
  const search = (document.getElementById('flt-search')?.value || '').trim().toLowerCase();
  const excludeFpRaw = (document.getElementById('flt-exclude-fp')?.value || '').trim().toLowerCase();

  if (dir && record.direction !== dir) return false;

  const isUnresolved = !record.parsed || (record.parsed.level === 'known' && !hasChineseContent(record.parsed?.utf8_text));
  if (parsed === 'true' && isUnresolved) return false;
  if (parsed === 'false' && !isUnresolved) return false;

  if (annotated === 'true' && !record.annotation) return false;

  if (excludeFpRaw) {
    const excludeList = excludeFpRaw.split(',').map(s => s.trim()).filter(Boolean);
    const fp = (record.fingerprint || '').toLowerCase();
    for (const ex of excludeList) {
      if (fp.includes(ex)) return false;
    }
  }

  if (search) {
    const p = record.parsed || {};
    const haystack = [
      record.raw_hex || '',
      record.annotation || '',
      p.type || '',
      p.utf8_text || '',
      p.command_hex || '',
      record.fingerprint || '',
    ].join(' ').toLowerCase();
    if (!haystack.includes(search)) return false;
  }

  return true;
}

async function loadPackets() {
  const dir = document.getElementById('flt-dir').value;
  const annotated = document.getElementById('flt-annotated').value;
  const params = new URLSearchParams({ limit: 200 });
  if (dir) params.set('direction', dir);
  if (annotated) params.set('annotated', annotated);
  const res = await fetch(`${API}/api/packets?${params}`);
  const data = await res.json();
  const list = document.getElementById('packet-list');
  list.innerHTML = '';
  currentAutoExpandedDetailId = null;
  // 清除折叠计数
  Object.keys(fpCountMap).forEach(k => delete fpCountMap[k]);

  const all = sortPacketsNewestFirst(data.packets || []);
  const filtered = all.filter(r => matchFilter(r));

  if (collapseMode) {
    // 折叠：按指纹去重（已经是最新在前，保留第一次出现）
    const seen = new Set();
    const counts = {};
    filtered.forEach(r => {
      const fp = r.fingerprint || r.raw_hex.substring(0, 16);
      counts[fp] = (counts[fp] || 0) + 1;
    });
    filtered.forEach(r => {
      const fp = r.fingerprint || r.raw_hex.substring(0, 16);
      if (!seen.has(fp)) {
        seen.add(fp);
        fpCountMap[fp] = counts[fp];
        list.appendChild(buildPacketRow(r, counts[fp], seen.size === 1));
      }
    });
    document.getElementById('pkt-count').textContent = `折叠后 ${seen.size} 种（总 ${data.total || 0} 条）`;
  } else {
    document.getElementById('pkt-count').textContent = `共 ${filtered.length} 条（总 ${data.total || 0}）`;
    filtered.forEach((r, i) => list.appendChild(buildPacketRow(r, 0, i === 0)));
  }
}

function filterPackets() { loadPackets(); }
function clearPacketList() {
  document.getElementById('packet-list').innerHTML = '';
  currentAutoExpandedDetailId = null;
}

function copyHex(id) {
  const el = document.getElementById(`hex-content-${id}`);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(() => {
    const btn = document.getElementById(`copy-btn-${id}`);
    if (btn) { btn.textContent = '已复制'; setTimeout(() => { btn.textContent = '复制'; }, 1500); }
  }).catch(() => {
    // fallback for older browsers
    const range = document.createRange();
    range.selectNode(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    document.execCommand('copy');
    window.getSelection().removeAllRanges();
  });
}

function renderPktDetail(record) {
  const p = record.parsed;
  let html = `<div class="pkt-raw-hex-wrapper">
    <div class="pkt-raw-hex" id="hex-content-${record.id}">${escHtml(record.raw_hex)}</div>
    <button class="pkt-copy-btn" id="copy-btn-${record.id}" onclick="copyHex(${record.id})">复制</button>
  </div>`;

  html += `<div class="pkt-meta-row">
    <div class="pkt-fields">指纹：<span class="mono" style="user-select:all">${escHtml(record.fingerprint || '')}</span></div>
    <div class="annotation-row">
      <input type="text" id="ann-input-${record.id}"
        placeholder="为此指纹添加描述（将应用于所有相同指纹的报文）"
        value="${escAttr(record.annotation || '')}">
      <button class="btn btn-warn btn-sm" onclick="submitAnnotation(${record.id})">保存指纹描述</button>
    </div>
  </div>`;

  if (p) {
    if (p.utf8_text) {
      html += `<div class="pkt-utf8">${highlightChinese(escHtml(p.utf8_text))}</div>`;
    }
    if (p.command_hex) {
      html += `<div class="pkt-fields">命令字：<span>${p.command_hex}</span>`;
      if (p.content_length !== undefined) html += `  内容长度：<span>${p.content_length}</span>`;
      html += `</div>`;
    }
  }

  return html;
}

function togglePktDetail(id) {
  const d = document.getElementById(`pkt-detail-${id}`);
  if (d) d.classList.toggle('open');
}

async function submitAnnotation(id) {
  const input = document.getElementById(`ann-input-${id}`);
  if (!input) return;
  const text = input.value.trim();
  const res = await api('POST', `/api/packets/${id}/annotate`, { text });
  if (res.ok && res.fingerprint !== undefined) {
    updateAllFingerprintAnnotations(res.fingerprint, res.annotation || text);
  }
}

// SSE annotation 事件：data 现在携带 {fingerprint, annotation}
function updatePacketAnnotation(data) {
  if (data.fingerprint !== undefined) {
    updateAllFingerprintAnnotations(data.fingerprint, data.annotation || '');
  }
}

// 批量更新所有相同指纹行的 pkt-type 显示和标注输入框
function updateAllFingerprintAnnotations(fp, text) {
  const shortFp = (fp || '').substring(8, 16);
  const newTypeText = text ? `${shortFp} · ${text}` : '';

  document.querySelectorAll('.packet-row').forEach(row => {
    if (row.dataset.fingerprint !== fp) return;

    const header = row.querySelector('.packet-header');
    if (header) {
      let typeSpan = header.querySelector('.pkt-type');
      if (newTypeText) {
        if (!typeSpan) {
          typeSpan = document.createElement('span');
          typeSpan.className = 'pkt-type';
          const fpSpan = header.querySelector('.pkt-fp');
          if (fpSpan) fpSpan.after(typeSpan);
          else header.prepend(typeSpan);
        }
        typeSpan.textContent = newTypeText;
      } else if (typeSpan) {
        typeSpan.remove();
      }
    }
    // 更新已展开的指纹描述输入框
    row.querySelectorAll('input[id^="ann-input-"]').forEach(inp => {
      inp.value = text;
    });
  });
}

// 高亮中文字符
function highlightChinese(htmlStr) {
  return window.GameRender.highlightChinese(htmlStr).replace(/hl-cn/g, 'cn-text');
}

function randomNumHex4() {
  return Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0');
}

function randomNumHex6() {
  return (0x100000 + Math.floor(Math.random() * (0x1000000 - 0x100000))).toString(16).padStart(6, '0');
}

function applyCustomPacketRandomNum(hexStr) {
  const cleanHex = String(hexStr || '').replace(/\s+/g, '').toLowerCase();
  const mode = document.getElementById('custom-random-mode')?.value || 'hex4';
  if (mode === 'hex6') {
    if (cleanHex.length < 26) return cleanHex;
    return cleanHex.slice(0, 20) + randomNumHex6() + cleanHex.slice(26);
  }
  if (cleanHex.length < 24) return cleanHex;
  return cleanHex.slice(0, 20) + randomNumHex4() + cleanHex.slice(24);
}

async function sendProbePacket() {
  const inputEl = document.getElementById('custom-hex');
  const hexStr = inputEl.value.trim();
  if (!hexStr) return;
  const replacedHex = applyCustomPacketRandomNum(hexStr);
  inputEl.value = replacedHex;
  const useQueue = document.getElementById('chk-use-queue').checked;
  const res = await api('POST', '/api/probe/send', { hex: replacedHex, use_queue: useQueue });
  const el = document.getElementById('custom-send-result');
  el.className = `msg msg-${res.ok ? 'ok' : 'err'}`;
  el.textContent = res.ok
    ? withValidationWarning(`发送成功 · 方式: ${res.method}${res.sent_bytes !== undefined ? ' · ' + res.sent_bytes + ' bytes' : ''}`, res)
    : (res.error || '发送失败');
}

async function parseProbeHex() {
  const hexStr = document.getElementById('custom-hex').value.trim();
  if (!hexStr) return;
  const res = await api('POST', '/api/probe/parse', { hex: hexStr });
  const el = document.getElementById('custom-send-result');
  if (res.ok && res.parsed) {
    const p = res.parsed;
    el.className = 'msg msg-ok';
    el.innerHTML = `<b>解析结果</b><br>
      类型：${p.type || p.level || '通用'}<br>
      ${p.command_hex ? '命令字：' + p.command_hex + '<br>' : ''}
      ${p.utf8_text ? 'UTF-8：' + highlightChinese(escHtml(p.utf8_text)) : '无可读内容'}`;
  } else if (res.ok && !res.parsed) {
    el.className = 'msg msg-err';
    el.textContent = '无法解析（帧头不满足最小长度或格式错误）';
  } else {
    el.className = 'msg msg-err';
    el.textContent = res.error || '解析失败';
  }
}

// ================================================================== //
//  HTML 转义工具                                                       //
// ================================================================== //
function escHtml(s) {
  return window.GameRender.escHtml(s);
}
function escAttr(s) {
  return window.GameRender.escAttr(s);
}

// ================================================================== //
//  角色属性                                                            //
// ================================================================== //

let statsCollapsed = false;

function toggleStatsPanel() {
  statsCollapsed = !statsCollapsed;
  document.getElementById('stats-body').style.display = statsCollapsed ? 'none' : '';
  document.getElementById('stats-toggle-icon').textContent = statsCollapsed ? '▶' : '▼';
}

function renderRoleStats(data) {
  const stats = data.stats || {};
  const groups = data.groups || {};
  const order = data.order || [];

  if (!Object.keys(stats).length) {
    document.getElementById('stats-content').innerHTML = '<span class="text-muted">暂无数据</span>';
    return;
  }

  document.getElementById('role-stats-panel').classList.add('visible');

  // 展示分组
  const groupOrder = ['基础属性', '战斗属性', '角色信息', '其他信息'];
  let html = '';
  for (const groupName of groupOrder) {
    const keys = groups[groupName] || [];
    const rows = keys.filter(k => stats[k] !== undefined);
    if (!rows.length) continue;
    html += `<div class="stats-group"><div class="stats-group-title">${escHtml(groupName)}</div><div class="stats-grid">`;
    for (const k of rows) {
      const v = stats[k] || '';
      const isHighlight = ['等级', '职业'].includes(k);
      html += `<div class="stat-row"><span class="stat-name">${escHtml(k)}</span><span class="stat-value${isHighlight ? ' highlight' : ''}">${escHtml(v)}</span></div>`;
    }
    html += '</div></div>';
  }
  document.getElementById('stats-content').innerHTML = html;
}

// ================================================================== //
//  折叠同类型报文                                                      //
// ================================================================== //

let collapseMode = false;
// 折叠时每个指纹的计数 {fingerprint: count}
const fpCountMap = {};

function toggleCollapseMode() {
  collapseMode = !collapseMode;
  const btn = document.getElementById('btn-collapse-mode');
  btn.classList.toggle('active', collapseMode);
  btn.textContent = collapseMode ? '取消折叠' : '折叠同类';
  loadPackets();
}

// ================================================================== //
//  初始化                                                              //
// ================================================================== //
(async function init() {
  startSSE();

  // ---- 恢复"自动重连"勾选状态（localStorage 持久化） ----
  const chkAR = document.getElementById('chk-auto-reconnect');
  const savedAR = localStorage.getItem('autoReconnect');
  if (savedAR !== null) chkAR.checked = (savedAR === 'true');
  chkAR.addEventListener('change', async () => {
    localStorage.setItem('autoReconnect', chkAR.checked);
    const res = await api('PUT', '/api/control-config', { auto_reconnect: chkAR.checked }).catch(() => null);
    if (res?.ok && res.control_state) setControlState(res.control_state);
  });

  // 检查是否已有连接状态
  loadPackets();   // 默认 Tab 是报文探测，页面加载时即刻拉取历史报文
  const status = await api('GET', '/api/status').catch(() => null);
  if (status) {
    prevConnected = !!status.connected;
    updateStatus(status);
    if (status.connected) {
      // 已进入游戏：隐藏所有左侧流程面板
      document.getElementById('login-panel').style.display = 'none';
      document.getElementById('server-panel').style.display = 'none';
      document.getElementById('role-panel').style.display = 'none';
      // 恢复 lastConnectInfo，保证页面刷新后仍可自动重连
      const _savedCI = localStorage.getItem('lastConnectInfo');
      if (_savedCI) {
        try { Object.assign(lastConnectInfo, JSON.parse(_savedCI)); } catch (_) {}
      }
      refreshBackpack();
    } else if (status.connection_status === 'got_session') {
      // 已登录未选角：隐藏登录，显示服务器选择
      document.getElementById('login-panel').style.display = 'none';
      showServerPanel('', []);
    }
  }
  const controlRes = await api('GET', '/api/control-state').catch(() => null);
  if (controlRes?.ok) {
    setControlState(controlRes.control_state || {});
    updateBattleState(controlRes.battle_state || {});
  }
  const appliedControl = await api('PUT', '/api/control-config', { auto_reconnect: chkAR.checked }).catch(() => null);
  if (appliedControl?.ok && appliedControl.control_state) setControlState(appliedControl.control_state);
  loadMonsters();
  loadAutoUseRules();
  loadQuickLogins();
  loadBuyItems();
  updateBattleStatsText();
})();
