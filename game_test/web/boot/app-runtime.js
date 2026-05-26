// ================================================================== //
//  全局状态                                                            //
// ================================================================== //
const API = window.GameApi?.API || '';
const appApi = (method, path, body) => window.GameControllers.performJsonRequest(method, path, body);
let selectedServer = null;  // {name, ip, port}
let selectedRoleId = null;
let selectedItemId = null;
/** 背包多选（Ctrl/⌘+点击切换）；仅 1 件选中时与 selectedItemId 同步 */
let selectedItemIds = new Set();
let backpackItemsCache = [];
let goldState = { reserve_copper: 0, safe_copper: 0, backpack_copper: 0 };
let eventSource = null;
let isConnected = false;
let prevConnected = false;
let unifiedLoginLockPromise = null;
let unifiedLoginLastTriggerTs = 0;
let lastConnectInfo = { account: '', password: '', loginServer: '', serverIp: '', serverPort: 0, serverName: '', roleId: '' };
let buyItemFavorites = [];
let selectedBuyItemCode = '';
let autoDecomposeS1Enabled = false;
let starStoneLoopRunning = false;
let synthesisBatchRunning = false;
let transportSupplyRunning = false;
let worldBossRunning = false;
let liaoguoRunning = false;
let liaoguoPairs = [];
let selectedLiaoguoPairKey = '';
let scheduledTasksConfig = null;
const DEFAULT_SCHEDULE_TIMES = {
  daily_checkin: ['19:00'],
  transport_supply: ['19:30'],
  world_boss: ['09:59', '21:59'],
  liaoguo: '19:45',
};
let battleMonsters = [];
let smallAccounts = [];
let smallStatusItems = [];
let smallStatusPollTimer = null;
let ladderBattleLogActive = false;
let ladderAutoState = { running: false, start_floor: 1, end_floor: 1, current_floor: 0, completed_floor: 0, last_error: '' };
let selectedMonsterCode = '';
let battleLogMode = 'simple'; // simple | detail
let autoUseRules = [];
let controlState = { auto_reconnect_enabled: false, reconnect_state: 'idle', reconnect_attempts: 0, reconnect_max_attempts: 0, reconnect_last_error: '', reconnect_next_retry_in: null, reconnect_banned_wait_in: null };
/** 与后端 config.DEFAULT_BATTLE_LOOP_DELAY_MS 同步，由 /api/status 的 default_battle_loop_delay_ms 写入 */
let serverDefaultBattleLoopDelayMs = null;
let battleState = { state: 'idle', in_progress: false, loop_running: false, current_monster: '', loop_monster_code: '', loop_delay_ms: 0, total_count: 0, total_exp: 0, total_gold_copper: 0 };
let lastStatusData = { connected: false, connection_status: 'disconnected', role: null, server_name: '' };
let teleportDestinationsCache = [];
const TELEPORT_PACKET_TEMPLATE = '18000000e80303004428{random_num}f5054728000006000000{destination}0000';
/** 当前地图 NPC：由后端 Python 解析后随 SSE packet.map_npc 下发 */
let currentMapNpcFromPacket = { idHex: '', utf8Text: '' };
let currentMapNpcListFromPacket = [];

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
    const names = ['probe', 'backpack', 'chat', 'battle', 'ladder', 'tools'];
    b.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.toggle('active', el.id === 'tab-' + name);
  });
  if (name === 'probe') loadPackets();
  if (name === 'ladder') {
    loadSmallAccounts();
    refreshSmallStatus(true);
  }
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
      }
      else if (msg.type === 'gold') {
        renderGold(msg.data);
      }
      else if (msg.type === 'synthesis_result') {
        onSynthesisResult(msg.data);
      }
      else if (msg.type === 'synthesis_batch') {
        onSynthesisBatchEvent(msg.data);
      }
      else if (msg.type === 'packet') {
        appendPacketRow(msg.data);
        appendBattlePacketLine(msg.data);
        handleMapNpcListDnPacket(msg.data);
      }
      else if (msg.type === 'flow_status') onFlowStatus(msg.data);
      else if (msg.type === 'annotation') updatePacketAnnotation(msg.data);
      else if (msg.type === 'role_stats') renderRoleStats(msg.data);
      else if (msg.type === 'battle_response') onBattleResponse(msg.data);
      else if (msg.type === 'battle_end') onBattleEnd(msg.data);
      else if (msg.type === 'battle_settlement_e207') {
        onBattleSettlementE207(msg.data);
      }
      else if (msg.type === 'battle_not_killed') onBattleNotKilled(msg.data);
      else if (msg.type === 'battle_state') onBattleState(msg.data);
      else if (msg.type === 'control_log') onControlLog(msg.data);
      else if (msg.type === 'auto_use') onAutoUseEvent(msg.data);
      else if (msg.type === 'monsters') renderMonsterList(msg.data);
      else if (msg.type === 'ladder_team') onLadderTeamEvent(msg.data);
      else if (msg.type === 'ladder_auto') onLadderAutoEvent(msg.data);
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
/** 一键登录：整链失败（含选角后久无 d607/属性）时自动重试次数 */
const QUICK_LOGIN_MAX_ATTEMPTS = 3;
const QUICK_LOGIN_RETRY_GAP_MS = 1600;
/** 一键/自动登录链：各 HTTP 步骤之间的间隔（登录服 → 游戏服拉角 → 选角） */
const LOGIN_FLOW_STEP_DELAY_MS = 750;
/** 一键登录：仅 select-role 失败时的额外重试次数（总尝试=1+该值） */
const SELECT_ROLE_RETRY_TIMES = 2;

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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
  updateBattleStateText();
  updateBattleStatsText();
  syncBattleLoopButton();
  renderTopbarStatus(lastStatusData);
}

function getReconnectInfoText() {
  const state = String(controlState.reconnect_state || 'idle');
  if (state === 'running') {
    const n = Number(controlState.reconnect_attempts || 0);
    const max = Number(controlState.reconnect_max_attempts || 0);
    if (max > 0) return `后端重连中…(${n}/${max})`;
    return `后端重连中…(第 ${n} 次)`;
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
  const wasConnected = !!prevConnected;
  lastStatusData = { ...lastStatusData, ...(data || {}) };
  const statusMapNpc = data?.current_map_npc;
  if (statusMapNpc && statusMapNpc.id_hex) {
    currentMapNpcFromPacket = {
      idHex: String(statusMapNpc.id_hex || '').trim().toLowerCase(),
      utf8Text: String(statusMapNpc.utf8_text || '').trim(),
    };
    if (!currentMapNpcListFromPacket.length) {
      currentMapNpcListFromPacket = [{
        idHex: currentMapNpcFromPacket.idHex,
        utf8Text: currentMapNpcFromPacket.utf8Text,
      }];
    }
  }
  updateMapNpcFromPacketUi();
  const d = data?.default_battle_loop_delay_ms;
  if (Number.isFinite(Number(d)) && Number(d) >= 0) {
    serverDefaultBattleLoopDelayMs = Math.floor(Number(d));
  }
  isConnected = data.connected;
  if (data.control_state) setControlState(data.control_state);
  if (data.battle_state) updateBattleState(data.battle_state);
  if (typeof data.auto_decompose_s1_enabled === 'boolean') {
    setAutoDecomposeS1Ui(data.auto_decompose_s1_enabled);
  }

  if (!data.connected) {
    backpackItemsCache = [];
    renderGold({});
    selectedItemId = null;
    selectedItemIds.clear();
    const sc = document.getElementById('stats-content');
    if (sc) sc.innerHTML = '<span class="text-muted">等待数据...</span>';
  }
  if (isConnected) {
    // 加载角色属性（先拉取，可能为空；renderRoleStats 会铺完整骨架并用报文逐步填充）
    api('GET', '/api/role-stats').then(r => { if (r.ok) renderRoleStats(r); });
    // 启动心跳轮询（已连接时每 20s 刷新一次状态以更新心跳年龄）
    _startHeartbeatPoll();
  }
  prevConnected = isConnected;
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

  await sleepMs(LOGIN_FLOW_STEP_DELAY_MS);
  // Step 2: 选区
  const rolesRes = await api('POST', '/api/roles', {
    server_ip: info.serverIp, server_port: info.serverPort
  });
  if (!rolesRes.ok) return { ok: false, error: rolesRes.error || '选区失败' };

  await sleepMs(LOGIN_FLOW_STEP_DELAY_MS);
  // Step 3: 选角
  let lastEnterRes = null;
  for (let attempt = 1; attempt <= SELECT_ROLE_RETRY_TIMES + 1; attempt++) {
    const enterRes = await api('POST', '/api/select-role', { role_id: info.roleId }).catch(() => null);
    lastEnterRes = enterRes;
    if (enterRes?.ok) {
      return { ok: true, role: enterRes.role || null };
    }
    if (attempt <= SELECT_ROLE_RETRY_TIMES) {
      await sleepMs(LOGIN_FLOW_STEP_DELAY_MS);
    }
  }
  return { ok: false, error: lastEnterRes?.error || '选角失败' };
}

function hasRenderableRoleStats(stats) {
  return !!stats && typeof stats === 'object' && Object.keys(stats).length > 0;
}

async function waitForRoleStatsReady(timeoutMs = RECONNECT_ROLE_STATS_TIMEOUT_MS) {
  renderRoleStats({ stats: {}, groups: ROLE_STAT_GROUPS, order: ROLE_STAT_ORDER });
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await api('GET', '/api/role-stats').catch(() => null);
    if (res?.ok) {
      renderRoleStats(res);
      if (hasRenderableRoleStats(res.stats)) {
        return { ok: true, stats: res.stats };
      }
    }
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_ROLE_STATS_POLL_MS));
  }
  return { ok: false, error: '重连后未获取到角色属性' };
}

async function runUnifiedQuickLogin(info, opts = {}) {
  const reason = String(opts.reason || '一键登录');
  const updateUi = opts.updateUi !== false;
  const requireRoleStats = opts.requireRoleStats !== false;
  const maxAttempts = Math.max(1, Math.min(10, Number(opts.maxAttempts) || QUICK_LOGIN_MAX_ATTEMPTS));
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
    const statusEl = () => document.getElementById('status-text');
    let lastError = '未知错误';
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      if (attempt > 1) {
        await api('POST', '/api/disconnect', {}).catch(() => null);
        await new Promise((r) => setTimeout(r, QUICK_LOGIN_RETRY_GAP_MS));
        if (updateUi) {
          const el = statusEl();
          if (el) el.textContent = `${reason}中（第 ${attempt}/${maxAttempts} 次）…`;
        }
      } else if (updateUi) {
        const el = statusEl();
        if (el) el.textContent = `${reason}中…`;
      }
      const flow = await performLoginFlow(info);
      if (!flow.ok) {
        lastError = flow.error || lastError;
        continue;
      }
      if (requireRoleStats) {
        const statsReady = await waitForRoleStatsReady();
        if (!statsReady.ok) {
          lastError = statsReady.error || lastError;
          await api('POST', '/api/disconnect', {}).catch(() => null);
          continue;
        }
      }
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
      return { ok: true, role: flow.role || null };
    }
    await api('POST', '/api/disconnect', {}).catch(() => null);
    const ri = document.getElementById('reconnect-info');
    if (ri) ri.textContent = '';
    resetToLoginState();
    const st = await api('GET', '/api/status').catch(() => null);
    if (st) updateStatus(st);
    return {
      ok: false,
      error: maxAttempts > 1 ? `${lastError}（已重试 ${maxAttempts} 次）` : lastError,
    };
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
    // 选角成功后尽快展示属性面板骨架（顶栏「已连接」仍由后续 status/SSE 驱动）
    renderRoleStats({ stats: {}, groups: ROLE_STAT_GROUPS, order: ROLE_STAT_ORDER });
    api('GET', '/api/role-stats').then((r) => { if (r?.ok) renderRoleStats(r); }).catch(() => {});
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
  selectedItemIds.clear();
  document.getElementById('login-panel').style.display = '';
  document.getElementById('server-panel').style.display = 'none';
  document.getElementById('role-panel').style.display = 'none';
  document.getElementById('btn-login').disabled = false;
  document.getElementById('status-dot').className = '';
  document.getElementById('status-text').textContent = '未连接';
  document.getElementById('role-badge').textContent = '—';
  document.getElementById('btn-disconnect').style.display = 'none';
  document.getElementById('role-stats-panel').classList.remove('visible');
  document.getElementById('stats-content').innerHTML = '<span class="text-muted">等待数据...</span>';
  renderBackpack([]);
  renderGold({});
}

// ================================================================== //
//  背包                                                                //
// ================================================================== //
function renderGold(data) {
  goldState = {
    reserve_copper: Number(data?.reserve_copper || 0),
    safe_copper: Number(data?.safe_copper || 0),
    backpack_copper: Number(data?.backpack_copper || 0),
  };
  const reserveEl = document.getElementById('gold-reserve');
  const safeEl = document.getElementById('gold-safe');
  const backpackEl = document.getElementById('gold-backpack');
  if (reserveEl) reserveEl.textContent = data?.reserve_text || formatGoldFromCopper(goldState.reserve_copper);
  if (safeEl) safeEl.textContent = data?.safe_text || formatGoldFromCopper(goldState.safe_copper);
  if (backpackEl) backpackEl.textContent = data?.backpack_text || formatGoldFromCopper(goldState.backpack_copper);
}

async function loadGold() {
  const res = await api('GET', '/api/gold').catch(() => null);
  if (res?.ok) renderGold(res.gold || {});
}

async function refreshGold(options = {}) {
  const silent = options?.silent === true;
  const res = await api('POST', '/api/gold/refresh').catch((e) => ({ ok: false, error: String(e) }));
  if (res.ok) {
    if (res.gold) renderGold(res.gold);
    if (!silent) showMsg('backpack-msg', '金币刷新请求已入队', 'ok');
  } else {
    if (!silent) showMsg('backpack-msg', res.error || '金币刷新失败', 'err');
  }
}

async function depositGold() {
  const res = await api('POST', '/api/gold/deposit').catch((e) => ({ ok: false, error: String(e) }));
  showMsg('backpack-msg', res.ok ? '金币存储请求已入队，后端将自动刷新' : (res.error || '金币存储失败'), res.ok ? 'ok' : 'err');
}

async function withdrawGold() {
  const res = await api('POST', '/api/gold/withdraw').catch((e) => ({ ok: false, error: String(e) }));
  showMsg('backpack-msg', res.ok ? '金币提取请求已入队，后端将自动刷新' : (res.error || '金币提取失败'), res.ok ? 'ok' : 'err');
}

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
    if (res.gold) renderGold(res.gold);
    showMsg('backpack-msg', `手动刷新完成，背包当前物品数量：${res.count} 件`, 'ok');
  } else {
    showMsg('backpack-msg', res.error || '刷新失败', 'err');
  }
}

function renderBackpack(items) {
  backpackItemsCache = Array.isArray(items) ? items : [];
  const grid = document.getElementById('backpack-grid');
  const validIds = new Set(backpackItemsCache.map((x) => x.item_id));
  for (const id of [...selectedItemIds]) {
    if (!validIds.has(id)) selectedItemIds.delete(id);
  }
  syncSelectedItemIdFromSet();
  syncBackpackActionQtyInput();
  syncBackpackSelectionUi();
  const countEl = document.getElementById('backpack-count');
  const selN = selectedItemIds.size;
  const base = `共 ${backpackItemsCache.length} 件`;
  if (countEl) countEl.textContent = selN > 0 ? `${base} · 已选 ${selN} 件` : base;
  if (!backpackItemsCache.length) {
    grid.innerHTML = '<div class="text-muted text-sm">背包为空</div>';
    return;
  }
  grid.innerHTML = '';
  backpackItemsCache.forEach(item => {
    const d = document.createElement('div');
    d.className = 'item-card' + (item.can_disassemble ? ' can-decompose' : '');
    d.dataset.itemId = item.item_id;
    if (selectedItemIds.has(item.item_id)) {
      d.classList.add('selected');
      if (selectedItemIds.size > 1) d.classList.add('multi-selected');
    }
    d.innerHTML = `<div class="item-name">${escHtml(item.name)}</div>
      <div class="item-qty">数量：${item.quantity}</div>
      <div class="item-id mono">${item.item_id}</div>`;
    d.onclick = (e) => onBackpackItemClick(item.item_id, e);
    grid.appendChild(d);
  });
}

function syncSelectedItemIdFromSet() {
  if (selectedItemIds.size === 1) {
    selectedItemId = [...selectedItemIds][0];
  } else {
    selectedItemId = null;
  }
}

function onBackpackItemClick(itemId, e) {
  if (e.ctrlKey || e.metaKey) {
    if (selectedItemIds.has(itemId)) selectedItemIds.delete(itemId);
    else selectedItemIds.add(itemId);
  } else {
    selectedItemIds.clear();
    selectedItemIds.add(itemId);
  }
  syncSelectedItemIdFromSet();
  updateBackpackCardSelection();
  syncBackpackActionQtyInput();
  syncBackpackSelectionUi();
  const countEl = document.getElementById('backpack-count');
  const selN = selectedItemIds.size;
  const base = `共 ${backpackItemsCache.length} 件`;
  if (countEl) countEl.textContent = selN > 0 ? `${base} · 已选 ${selN} 件` : base;
}

function updateBackpackCardSelection() {
  const multi = selectedItemIds.size > 1;
  document.querySelectorAll('#backpack-grid .item-card').forEach((card) => {
    const id = card.dataset.itemId || '';
    const on = selectedItemIds.has(id);
    card.classList.toggle('selected', on);
    card.classList.toggle('multi-selected', on && multi);
  });
}

function isBackpackMultiSelect() {
  return selectedItemIds.size > 1;
}

function syncBackpackSelectionUi() {
  const multi = isBackpackMultiSelect();
  const none = selectedItemIds.size === 0;
  const batchLocked = synthesisBatchRunning;
  document.querySelectorAll('[data-backpack-single-only="1"]').forEach((el) => {
    el.disabled = batchLocked || none || multi;
  });
  document.querySelectorAll('[data-backpack-basic-action="1"]').forEach((el) => {
    el.disabled = batchLocked || none;
  });
  const qtyWrap = document.getElementById('backpack-action-qty-wrap');
  if (qtyWrap) qtyWrap.style.display = multi ? 'none' : '';
}

function getSelectedBackpackItem() {
  if (!selectedItemId) return null;
  return backpackItemsCache.find((x) => x.item_id === selectedItemId) || null;
}

function getSelectedBackpackItemsForAction() {
  if (!selectedItemIds.size) {
    showMsg('backpack-msg', '请先选择物品', 'err');
    return null;
  }
  const items = backpackItemsCache.filter((x) => selectedItemIds.has(x.item_id));
  if (!items.length) {
    showMsg('backpack-msg', '所选物品已不在背包', 'err');
    return null;
  }
  return items;
}

function getBackpackActionQuantity(actionText) {
  const selected = getSelectedBackpackItem();
  if (!selected) {
    showMsg('backpack-msg', '请先选择物品', 'err');
    return null;
  }
  const raw = String(document.getElementById('backpack-action-qty')?.value || '').trim();
  const req = Number(raw);
  if (!Number.isInteger(req) || req <= 0) {
    showMsg('backpack-msg', '次数必须是大于 0 的整数', 'err');
    return null;
  }
  const maxQty = Math.max(1, Number(selected.quantity || 0));
  if (req > maxQty) {
    showMsg('backpack-msg', `${actionText}次数不能大于当前物品数量（最多 ${maxQty}）`, 'err');
    return null;
  }
  return req;
}

function syncBackpackActionQtyInput() {
  const input = document.getElementById('backpack-action-qty');
  if (!input) return;
  const selected = getSelectedBackpackItem();
  const maxQty = selected ? Math.max(1, Number(selected.quantity || 0)) : null;
  const current = Number(String(input.value || '').trim());
  if (!Number.isInteger(current) || current <= 0) {
    input.value = '1';
    return;
  }
  if (maxQty !== null && current > maxQty) {
    input.value = String(maxQty);
  }
}

async function runBackpackActionOnSelection(action, apiPath, actionLabel) {
  const items = getSelectedBackpackItemsForAction();
  if (!items) return;
  if (isBackpackMultiSelect()) {
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    let lastErr = '';
    for (const item of items) {
      if (Number(item.quantity || 0) < 1) {
        skipped += 1;
        continue;
      }
      if (action === 'decompose' && !item.can_disassemble) {
        skipped += 1;
        continue;
      }
      const res = await api('POST', apiPath, { item_id: item.item_id, quantity: 1 });
      if (res.ok) ok += 1;
      else {
        fail += 1;
        lastErr = res.error || lastErr;
      }
    }
    const parts = [`${actionLabel}：成功 ${ok} 件`];
    if (fail) parts.push(`失败 ${fail} 件`);
    if (skipped) parts.push(`跳过 ${skipped} 件`);
    const level = ok > 0 ? 'ok' : 'err';
    showMsg('backpack-msg', lastErr && fail && !ok ? `${parts.join('，')}（${lastErr}）` : parts.join('，'), level);
    return;
  }
  const quantity = action === 'decompose' ? 1 : getBackpackActionQuantity(actionLabel);
  if (quantity == null) return;
  const itemId = items[0].item_id;
  const res = await api('POST', apiPath, { item_id: itemId, ...(action === 'decompose' ? {} : { quantity }) });
  if (action === 'use') {
    showMsg('backpack-msg', res.ok ? withValidationWarning(`已加入发送队列 x${res.queued}`, res) : res.error, res.ok ? 'ok' : 'err');
  } else if (action === 'drop') {
    showMsg('backpack-msg', res.ok ? withValidationWarning(`丢弃请求已入队 x${res.actual_quantity || quantity}`, res) : res.error, res.ok ? 'ok' : 'err');
  } else {
    showMsg('backpack-msg', res.ok ? withValidationWarning('分解请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
  }
}

async function useSelected() {
  return runBackpackActionOnSelection('use', '/api/item/use', '使用');
}

async function dropSelected() {
  return runBackpackActionOnSelection('drop', '/api/item/drop', '丢弃');
}

async function decomposeSelected() {
  return runBackpackActionOnSelection('decompose', '/api/item/decompose', '分解');
}

async function synthesizeSelected() {
  if (isBackpackMultiSelect()) {
    showMsg('backpack-msg', '多选时不可合成，请只选择一件物品', 'err');
    return;
  }
  const quantity = getBackpackActionQuantity('合成');
  if (quantity == null) return;
  const res = await api('POST', '/api/item/synthesize', { item_id: selectedItemId, quantity });
  showMsg('backpack-msg', res.ok ? withValidationWarning(`合成请求已入队 x${res.queued || quantity}`, res) : res.error, res.ok ? 'ok' : 'err');
}

/** 下行 e80301004f51 合成结果，与「合成请求已入队」区分展示 */
function onSynthesisResult(data) {
  const msg = data && (data.message != null ? String(data.message) : '');
  const o = data && data.outcome;
  if (!msg) return;
  const level = o === 'ok' ? 'ok' : 'err';
  showMsg('backpack-msg', msg, level);
}

async function exchangeWuling() {
  const res = await api('POST', '/api/item/exchange-wuling');
  showMsg('backpack-msg', res.ok ? withValidationWarning('兑换五灵请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

function setAutoDecomposeS1Ui(enabled) {
  autoDecomposeS1Enabled = !!enabled;
  const chk = document.getElementById('chk-auto-decompose-s1');
  if (chk && chk.checked !== autoDecomposeS1Enabled) chk.checked = autoDecomposeS1Enabled;
}

async function refreshAutoDecomposeS1Status(silent = true) {
  const res = await api('GET', '/api/backpack/auto-decompose').catch(() => null);
  if (!res || !res.ok) {
    if (!silent) showMsg('backpack-msg', res?.error || '读取自动分解状态失败', 'err');
    return;
  }
  setAutoDecomposeS1Ui(!!res.enabled);
}

async function toggleAutoDecomposeS1(enabled) {
  const previous = autoDecomposeS1Enabled;
  const next = !!enabled;
  setAutoDecomposeS1Ui(next);
  const res = await api('PUT', '/api/backpack/auto-decompose', { enabled: next }).catch(() => null);
  if (!res || !res.ok) {
    setAutoDecomposeS1Ui(previous);
    showMsg('backpack-msg', res?.error || '自动分解设置失败', 'err');
    return;
  }
  setAutoDecomposeS1Ui(!!res.enabled);
  showMsg('backpack-msg', res.enabled ? '自动分解已启用' : '自动分解已关闭', 'ok');
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
  if (!itemCode) { showMsg('backpack-msg', '请输入 14 位物品编码', 'err'); return; }
  let npcId = '';
  try {
    npcId = getCurrentBuyNpcId();
  } catch (err) {
    showMsg('backpack-msg', err?.message || '无法获取当前地图 NPC id', 'err');
    return;
  }
  const res = await buyByNpcAndItemCode(npcId, itemCode);
  showMsg('backpack-msg', res.ok ? withValidationWarning('购买请求已入队', res) : res.error, res.ok ? 'ok' : 'err');
}

async function saveBuyItem() {
  const name = document.getElementById('backpack-buy-name').value.trim();
  const code = document.getElementById('backpack-buy-code').value.trim().toLowerCase();
  if (!name || !code) { showMsg('backpack-msg', '请填写物品名称和 14 位编码', 'err'); return; }
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

function appendBackpackFlowLog(text, kind = 'info') {
  const box = document.getElementById('backpack-flow-log');
  if (!box) return;
  const line = document.createElement('div');
  line.className = kind === 'err' ? 'text-red' : kind === 'ok' ? 'text-green' : 'text-muted';
  line.textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function clearBackpackFlowLog() {
  const box = document.getElementById('backpack-flow-log');
  if (!box) return;
  box.innerHTML = '';
}

function appendStarStoneLog(text, kind = 'info') {
  appendBackpackFlowLog(text, kind);
}

function clearStarStoneLog() {
  clearBackpackFlowLog();
}

function updateStarStoneButton() {
  const btn = document.getElementById('btn-star-stone-loop');
  if (!btn) return;
  btn.textContent = starStoneLoopRunning ? '停止获取升星石' : '获取升星石';
  btn.className = starStoneLoopRunning ? 'btn btn-danger btn-sm' : 'btn btn-primary btn-sm';
}

function appendSynthesisBatchLog(text, kind = 'info') {
  appendBackpackFlowLog(text, kind);
}

function clearSynthesisBatchLog() {
  clearBackpackFlowLog();
}

function setBackpackSynthesisBatchUiLocked(_locked) {
  syncBackpackSelectionUi();
}

function updateSynthesisBatchButton() {
  const btn = document.getElementById('btn-synthesis-batch');
  if (!btn) return;
  btn.textContent = synthesisBatchRunning ? '停止一键合成' : '一键合成';
  btn.className = synthesisBatchRunning
    ? 'btn btn-danger btn-sm'
    : 'btn btn-primary btn-sm';
}

function formatSynthesisBatchFinished(data) {
  const ok = Number(data?.ok ?? 0) || 0;
  const fail = Number(data?.fail ?? 0) || 0;
  const r = data?.reason;
  const map = {
    insufficient: '已因材料不足（数量不足）结束',
    user: '已手动停止',
    timeout: '等待服务器响应超时',
    not_in_backpack: '物品已不在背包',
    send_error: '发包失败',
    error: '发生异常',
  };
  const extra = (r && map[r]) || (data?.message ? String(data.message) : '') || (r || '');
  return `一键合成已结束。成功 ${ok} 次，失败 ${fail} 次。${extra ? `（${extra}）` : ''}`;
}

function onSynthesisBatchEvent(data) {
  if (!data) return;
  if (data.state === 'started') {
    synthesisBatchRunning = true;
    updateSynthesisBatchButton();
    setBackpackSynthesisBatchUiLocked(true);
    return;
  }
  if (data.state === 'finished') {
    synthesisBatchRunning = false;
    updateSynthesisBatchButton();
    setBackpackSynthesisBatchUiLocked(false);
    const r = data.reason;
    const level = r === 'insufficient' ? 'ok' : (r === 'user' ? 'ok' : 'err');
    showMsg('backpack-msg', formatSynthesisBatchFinished(data), level);
  }
}

async function refreshSynthesisBatchStatus(silent = true) {
  const res = await api('GET', '/api/flow/synthesis-batch/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) showMsg('backpack-msg', res?.error || '读取一键合成状态失败', 'err');
    return false;
  }
  synthesisBatchRunning = !!res.running;
  updateSynthesisBatchButton();
  setBackpackSynthesisBatchUiLocked(!!synthesisBatchRunning);
  return true;
}

async function toggleSynthesisBatch() {
  const isStarting = !synthesisBatchRunning;
  if (isStarting) {
    if (!selectedItemId) {
      showMsg('backpack-msg', '请先选择要一键合成的物品', 'err');
      return;
    }
    clearBackpackFlowLog();
    const res = await api('POST', '/api/flow/synthesis-batch/start', { item_id: selectedItemId });
    if (!res?.ok) {
      showMsg('backpack-msg', res?.error || '启动失败', 'err');
      await refreshSynthesisBatchStatus(true);
      return;
    }
    appendSynthesisBatchLog(`已启动：${selectedItemId}`, 'ok');
    showMsg('backpack-msg', '一键合成已启动', 'ok');
    await refreshSynthesisBatchStatus(true);
    return;
  }
  const res = await api('POST', '/api/flow/synthesis-batch/stop').catch(() => null);
  if (!res?.ok) {
    showMsg('backpack-msg', res?.error || '停止失败', 'err');
    await refreshSynthesisBatchStatus(true);
    return;
  }
  showMsg('backpack-msg', '已请求停止一键合成', 'ok');
  await refreshSynthesisBatchStatus(true);
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

function updateMapNpcFromPacketUi() {
  const el = document.getElementById('battle-map-npc-from-packet');
  if (!el) return;
  if (Array.isArray(currentMapNpcListFromPacket) && currentMapNpcListFromPacket.length) {
    el.textContent = currentMapNpcListFromPacket
      .map((x) => {
        const idHex = String(x?.idHex || '').trim().toLowerCase();
        if (!idHex) return '';
        const name = String(x?.utf8Text || '').trim();
        return name ? `${name} · ${idHex}` : idHex;
      })
      .filter(Boolean)
      .join(' / ') || '—';
    return;
  }
  const { idHex, utf8Text } = currentMapNpcFromPacket;
  if (!idHex) {
    el.textContent = '—';
    return;
  }
  const name = utf8Text ? `${utf8Text} · ` : '';
  el.textContent = `${name}${idHex}`;
}

function applyMapNpcFromEntry(entry) {
  const idHex = String(entry?.id_hex || '').trim().toLowerCase();
  if (!/^[0-9a-f]{8}$/.test(idHex)) return false;
  currentMapNpcFromPacket = {
    idHex,
    utf8Text: String(entry?.utf8_text || '').trim(),
  };
  currentMapNpcListFromPacket = [{
    idHex: currentMapNpcFromPacket.idHex,
    utf8Text: currentMapNpcFromPacket.utf8Text,
  }];
  updateMapNpcFromPacketUi();
  return true;
}

function applyMapNpcFromList(list) {
  if (!Array.isArray(list) || !list.length) return false;
  const out = [];
  const seen = new Set();
  list.forEach((entry) => {
    const idHex = String(entry?.id_hex || '').trim().toLowerCase();
    if (!/^[0-9a-f]{8}$/.test(idHex) || seen.has(idHex)) return;
    seen.add(idHex);
    out.push({ idHex, utf8Text: String(entry?.utf8_text || '').trim() });
  });
  if (!out.length) return false;
  currentMapNpcListFromPacket = out;
  currentMapNpcFromPacket = { ...out[0] };
  updateMapNpcFromPacketUi();
  return true;
}

function handleMapNpcListDnPacket(record) {
  const list = record?.map_npc_list;
  if (Array.isArray(list) && list.length) {
    applyMapNpcFromList(list);
    return;
  }
  const m = record?.map_npc;
  if (!m || !m.id_hex) {
    return;
  }
  applyMapNpcFromEntry(m);
}

function getTransportSupplyNpcIdHexForPacket() {
  const h = String(currentMapNpcFromPacket.idHex || '').trim().toLowerCase();
  if (h.length !== 8 || !/^[0-9a-f]{8}$/.test(h)) return '';
  return h;
}

function getCurrentBuyNpcId() {
  return getTransportSupplyNpcIdHexForPacket();
}

async function buyByNpcAndItemCode(npcIdHex, itemCodeHex14) {
  return api('POST', '/api/item/buy', {
    npc_id: String(npcIdHex || '').trim().toLowerCase(),
    item_code: String(itemCodeHex14 || '').trim().toLowerCase(),
  });
}

async function refreshStarStoneStatus(silent = true) {
  const res = await api('GET', '/api/flow/star-stone/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) showMsg('backpack-msg', res?.error || '读取获取升星石状态失败', 'err');
    return false;
  }
  starStoneLoopRunning = !!res.running;
  updateStarStoneButton();
  return true;
}

async function toggleStarStoneLoop() {
  const isStarting = !starStoneLoopRunning;
  if (isStarting) clearBackpackFlowLog();
  const endpoint = starStoneLoopRunning
    ? '/api/flow/star-stone/stop'
    : '/api/flow/star-stone/start';
  const actionText = starStoneLoopRunning ? '停止' : '启动';
  const res = await api('POST', endpoint).catch(() => null);
  if (!res?.ok) {
    showMsg('backpack-msg', `获取升星石${actionText}失败：${res?.error || '未知错误'}`, 'err');
    await refreshStarStoneStatus(true);
    return;
  }
  showMsg('backpack-msg', `获取升星石：已请求${actionText}`, 'ok');
  await refreshStarStoneStatus(true);
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
  if (data.scope === 'star_stone') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendStarStoneLog(data.message, kind);
    return;
  }
  if (data.scope === 'transport_supply') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendTransportSupplyLog(data.message, kind);
    return;
  }
  if (data.scope === 'liaoguo') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendLiaoguoLog(data.message, kind);
    return;
  }
  if (data.scope === 'world_boss') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendWorldBossLog(data.message, kind);
    return;
  }
  if (data.scope === 'synthesis_batch') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendSynthesisBatchLog(data.message, kind);
    return;
  }
  if (data.scope === 'tools' || data.scope === 'scheduled_tasks') {
    const kind = data.level === 'err' ? 'err' : data.level === 'ok' ? 'ok' : 'info';
    appendFeatureLog(data.message, kind);
    return;
  }
  const kind = data.level === 'warn' ? 'end' : 'response';
  appendBattleLog({ raw_text: data.message }, kind);
}

function onFlowStatus(data) {
  if (!data || typeof data !== 'object') return;
  if (typeof data.star_stone_running === 'boolean') {
    starStoneLoopRunning = data.star_stone_running;
    updateStarStoneButton();
  }
  if (typeof data.transport_supply_running === 'boolean') {
    transportSupplyRunning = data.transport_supply_running;
    updateTransportSupplyButton();
  }
  if (typeof data.world_boss_running === 'boolean') {
    worldBossRunning = data.world_boss_running;
    updateWorldBossButton();
  }
  if (typeof data.liaoguo_running === 'boolean') {
    liaoguoRunning = data.liaoguo_running;
    updateLiaoguoButton();
  }
  if (typeof data.synthesis_batch_running === 'boolean') {
    synthesisBatchRunning = data.synthesis_batch_running;
    updateSynthesisBatchButton();
    setBackpackSynthesisBatchUiLocked(!!data.synthesis_batch_running);
  }
}

function appendBattlePacketLine(record) {
  const fp = (record.fingerprint || '').toLowerCase();
  const watch = ['e8030500f603', 'e8030500f703', 'e8030100de07', 'e8030100df07', 'e8030100e207'];
  if (!watch.includes(fp)) return;
  if (battleLogMode !== 'detail') return;
  appendBattleLog({
    raw_text: `[${record.direction}] ${fp} ${record.raw_hex || ''}`.trim(),
  }, 'packet');
}

async function onBattleResponse(data) {
  updateBattleState(data?.battle_state || {});
  if (battleLogMode === 'detail') appendBattleLog(data, 'response');
  const monsterName = String(data?.monster_name || '').trim();
  const monsterCount = Number(data?.monster_count || 0);
  if (data?.de07_status === 'normal' && monsterName && monsterCount > 0) {
    const n = Number(data?.battle_state?.total_count || battleState.total_count || 0) + 1;
    const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    appendBattleLog({ raw_text: `【${t}】 第${n}次 遭遇：${monsterName} x${monsterCount}` }, 'response');
  }
  appendLadderBattleLogFromEvent('response', data);
}

function onBattleEnd(data) {
  updateBattleState(data?.battle_state || {});
  if (data?.no_energy) {
    appendBattleLog({ raw_text: '内力不足' }, 'end');
    appendLadderBattleLog('内力不足，天梯战斗结束', 'end');
    ladderBattleLogActive = false;
    return;
  }
  const n = Number(data?.battle_state?.total_count || battleState.total_count || 0);
  appendBattleLog({ raw_text: `第${n}次 战斗结束` }, 'end');
  appendLadderBattleLog(`第${n || 1}次 战斗结束`, 'end');
  if (!shouldKeepLadderBattleLog()) ladderBattleLogActive = false;
}

function buildE207SettlementDisplayText(data) {
  const sum = String(data?.settlement_summary || '').trim();
  if (sum) return sum;
  const lines = data?.settlement_lines;
  if (Array.isArray(lines) && lines.length) return lines.join(' / ');
  const raw = String(data?.raw_text || '');
  if (!raw) return '';
  return raw
    .split('/')
    .map((s) => s.trim())
    .filter((s) => s)
    .join(' / ');
}

function onBattleSettlementE207(data) {
  updateBattleState(data?.battle_state || {});
  const raw = String(data?.raw_text || '');
  const settlementBody = buildE207SettlementDisplayText(data) || raw;
  if (raw.includes('失去') || data?.outcome === 'defeat') {
    appendBattleLog({ raw_text: `结算：失败 — ${settlementBody}` }, 'end');
    appendLadderBattleLog(`结算：失败 - ${settlementBody}`, 'end');
    if (!shouldKeepLadderBattleLog()) ladderBattleLogActive = false;
    return;
  }
  const gCopper = (typeof data?.gold === 'number')
    ? data.gold
    : (parseGoldToCopperFromText(raw) ?? 0);
  const resultGold = formatGoldFromCopper(gCopper);
  const resultExp = (typeof data?.exp === 'number')
    ? data.exp
    : Number((raw.match(/(?:获得)?经验[：:+\s]*([0-9]+)/)?.[1] || 0));
  if (data?.outcome === 'victory' || raw.includes('获得')) {
    const n = Number(data?.battle_state?.total_count || battleState.total_count || 0);
    const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    appendBattleLog({ raw_text: `【${t}】 第${n}次 战斗结束` }, 'end');
  }
  const detailLine = settlementBody
    ? `结算：${settlementBody}`
    : `结算：本次获得经验 ${resultExp} / 金币 ${resultGold}`;
  appendBattleLog({ raw_text: detailLine }, 'end');
  appendLadderBattleLog(detailLine, 'end');
  if (!shouldKeepLadderBattleLog()) ladderBattleLogActive = false;
}

async function onBattleNotKilled(data) {
  updateBattleState(data?.battle_state || {});
  if (battleLogMode === 'detail') appendBattleLog(data, 'response');
  appendLadderBattleLogFromEvent('not_killed', data);
}

function onBattleState(data) {
  updateBattleState(data || {});
  if (ladderBattleLogActive && data?.state === 'error' && !shouldKeepLadderBattleLog()) {
    const err = data?.last_result?.error || '战斗流程异常';
    appendLadderBattleLog(`天梯战斗异常：${err}`, 'end');
    ladderBattleLogActive = false;
  }
}

function updateBattleStatsText() {
  const g = formatGoldFromCopper(battleState.total_gold_copper || 0);
  document.getElementById('battle-stats').textContent =
    `总战斗次数: ${Number(battleState.total_count || 0)} / 总获得经验: ${Number(battleState.total_exp || 0)} / 总获得金币: ${g}`;
}

function updateBattleStateText() {
  const el = document.getElementById('battle-state-text');
  if (!el) return;
  const labels = {
    idle: '空闲',
    waiting_de07: '等待战斗启动响应',
    waiting_df07: '战斗进行中',
    cooldown: '等待下一轮',
    error: '错误',
  };
  el.textContent = labels[battleState.state] || String(battleState.state || '空闲');
}

function parseGoldToCopperFromText(text) {
  // 与后端一致：「金币」一词不误判为「金」单位；支持仅铜、仅数字（铜）
  const s = String(text);
  const m = s.match(/(?:获得)?金币\s*[：:]\s*([^\r\n]+)/);
  if (!m) return null;
  const tail = m[1].trim();
  if (!tail) return null;
  const mj = tail.match(/(\d+)\s*金(?!币)/);
  const my = tail.match(/(\d+)\s*银/);
  const mt = tail.match(/(\d+)\s*铜/);
  let jin = 0; let yin = 0; let tong = 0;
  if (mj) jin = Number(mj[1]);
  if (my) yin = Number(my[1]);
  if (mt) tong = Number(mt[1]);
  if (mj || my || mt) return jin * 10000 + yin * 100 + tong;
  const mp = tail.match(/^(\d+)$/);
  if (mp) return Number(mp[1]);
  return null;
}

function formatGoldFromCopper(copper) {
  const total = Math.max(0, Number(copper) || 0);
  const jin = Math.floor(total / 10000);
  const rem1 = total % 10000;
  const yin = Math.floor(rem1 / 100);
  const tong = rem1 % 100;
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
  syncMapNpcFromPacketHistory(all);
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

function syncMapNpcFromPacketHistory(records) {
  if (!Array.isArray(records) || !records.length) {
    return;
  }
  const latestNpcRecord = records.find((record) => {
    if (!record || record.direction !== 'DN') return false;
    return (Array.isArray(record.map_npc_list) && record.map_npc_list.length > 0) || !!record.map_npc;
  });
  if (!latestNpcRecord) {
    return;
  }
  handleMapNpcListDnPacket(latestNpcRecord);
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

function setToolResult(text, type = 'info') {
  showMsg('tool-send-result', text, type);
}

function toLittleEndianHex16(value) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 0 || n > 0xffff) {
    throw new Error('加点数量必须是 0~65535 的整数');
  }
  const hex = n.toString(16).padStart(4, '0');
  return hex.slice(2, 4) + hex.slice(0, 2);
}

function appendFeatureLog(text, kind = 'info') {
  const box = document.getElementById('tool-feature-log');
  if (!box) return;
  if (box.querySelector('.text-muted.text-sm') && box.children.length === 1) {
    box.innerHTML = '';
  }
  const line = document.createElement('div');
  line.className = kind === 'err' ? 'text-red' : kind === 'ok' ? 'text-green' : 'text-muted';
  line.textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function clearFeatureLog() {
  const box = document.getElementById('tool-feature-log');
  if (!box) return;
  box.innerHTML = '';
}

function appendTransportSupplyLog(text, kind = 'info') {
  appendFeatureLog(text, kind);
}

function clearTransportSupplyLog() {
  clearFeatureLog();
}

function appendWorldBossLog(text, kind = 'info') {
  appendFeatureLog(text, kind);
}

function clearWorldBossLog() {
  clearFeatureLog();
}

function appendLiaoguoLog(text, kind = 'info') {
  appendFeatureLog(text, kind);
}

function clearLiaoguoLog() {
  clearFeatureLog();
}

function updateTransportSupplyButton() {
  const btn = document.getElementById('btn-transport-supply');
  if (!btn) return;
  btn.textContent = transportSupplyRunning ? '停止运输物资' : '开始运输物资';
  btn.className = transportSupplyRunning ? 'btn btn-danger btn-sm' : 'btn btn-primary btn-sm';
}

function updateWorldBossButton() {
  const btn = document.getElementById('btn-world-boss');
  if (!btn) return;
  btn.textContent = worldBossRunning ? '停止世界 BOSS' : '启动世界 BOSS';
  btn.className = worldBossRunning ? 'btn btn-danger btn-sm' : 'btn btn-primary btn-sm';
}

function updateLiaoguoButton() {
  const btn = document.getElementById('btn-liaoguo-run');
  if (!btn) return;
  btn.textContent = liaoguoRunning ? '停止辽国战斗' : '启动辽国战斗';
  btn.className = liaoguoRunning ? 'btn btn-danger btn-sm' : 'btn btn-primary btn-sm';
}

function normalizeLiaoguoPair(pair) {
  const itemCode = String(pair?.itemCode || '').trim().toLowerCase();
  const monsterCode = String(pair?.monsterCode || '').trim().toLowerCase();
  const label = String(pair?.label || '').trim();
  const taskName = String(pair?.taskName || '').trim();
  const ticketItemCode = String(pair?.ticketItemCode || '').trim().toLowerCase();
  const abandonTaskCode = String(pair?.abandonTaskCode || '21a1').trim().toLowerCase();
  if (!/^[0-9a-f]{14}$/.test(itemCode)) return null;
  if (!/^[0-9a-f]{4}$/.test(monsterCode)) return null;
  if (!label) return null;
  if (!taskName) return null;
  if (!/^[0-9a-f]+$/.test(ticketItemCode) || ticketItemCode.length % 2 !== 0 || ticketItemCode.length < 4 || ticketItemCode.length > 20) return null;
  if (!/^[0-9a-f]{4}$/.test(abandonTaskCode)) return null;
  return {
    id: taskName,
    itemCode,
    monsterCode,
    label,
    taskName,
    ticketItemCode,
    abandonTaskCode,
  };
}

function getLiaoguoPairKey(pair) {
  return String(pair?.id || pair?.taskName || '').trim();
}

async function loadLiaoguoPairs() {
  const res = await api('GET', '/api/liaoguo-pairs').catch(() => null);
  const list = Array.isArray(res?.items) ? res.items.map(normalizeLiaoguoPair).filter(Boolean) : [];
  liaoguoPairs = list;
  if (liaoguoPairs.length && !liaoguoPairs.some((p) => getLiaoguoPairKey(p) === selectedLiaoguoPairKey)) {
    selectedLiaoguoPairKey = getLiaoguoPairKey(liaoguoPairs[0]);
  } else if (!liaoguoPairs.length) {
    selectedLiaoguoPairKey = '';
  }
  renderLiaoguoPairs();
}

function renderLiaoguoPairs() {
  const select = document.getElementById('liaoguo-pair-select');
  if (!select) return;
  if (!liaoguoPairs.length) {
    select.innerHTML = '<option value="">暂无辽国映射，请先新增并保存</option>';
    select.value = '';
    const itemEl = document.getElementById('liaoguo-item-code');
    const monsterEl = document.getElementById('liaoguo-monster-code');
    const labelEl = document.getElementById('liaoguo-label');
    const taskEl = document.getElementById('liaoguo-task-name');
    const ticketEl = document.getElementById('liaoguo-ticket-item-code');
    const abandonEl = document.getElementById('liaoguo-abandon-task-code');
    if (itemEl) itemEl.value = '';
    if (monsterEl) monsterEl.value = '';
    if (labelEl) labelEl.value = '';
    if (taskEl) taskEl.value = '';
    if (ticketEl) ticketEl.value = '';
    if (abandonEl) abandonEl.value = '';
    renderScheduledLiaoguoPairs();
    return;
  }
  select.innerHTML = liaoguoPairs
    .map((p) => {
      const key = getLiaoguoPairKey(p);
      return `<option value="${escAttr(key)}">${escHtml(p.itemCode)} - ${escHtml(p.label)} - ${escHtml(p.monsterCode)} - ${escHtml(p.taskName || '')}</option>`;
    })
    .join('');
  select.value = selectedLiaoguoPairKey;
  applySelectedLiaoguoPairToInputs();
  renderScheduledLiaoguoPairs();
}

function setInputValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

function setCheckboxValue(id, checked) {
  const el = document.getElementById(id);
  if (el) el.checked = !!checked;
}

function getCheckboxValue(id) {
  return !!document.getElementById(id)?.checked;
}

function renderScheduledLiaoguoPairs() {
  const box = document.getElementById('sched-liaoguo-pairs');
  if (!box) return;
  if (!liaoguoPairs.length) {
    box.innerHTML = '<span class="text-muted text-sm">暂无辽国映射</span>';
    return;
  }
  box.innerHTML = liaoguoPairs.map((pair) => {
    const key = getLiaoguoPairKey(pair);
    return `<span style="display:inline-flex; align-items:center; gap:4px; margin-right:10px; margin-bottom:6px;">
      <span>${escHtml(pair.label || key)} (${escHtml(pair.monsterCode || '')})</span>
    </span>`;
  }).join('');
}

function renderScheduledTasksConfig(config) {
  scheduledTasksConfig = config || {};
  const daily = scheduledTasksConfig.daily_checkin || {};
  const transport = scheduledTasksConfig.transport_supply || {};
  const worldBoss = scheduledTasksConfig.world_boss || {};
  const liaoguo = scheduledTasksConfig.liaoguo || {};
  setCheckboxValue('sched-daily-enabled', daily.enabled);
  setInputValue('sched-daily-times', (Array.isArray(daily.times) ? daily.times : DEFAULT_SCHEDULE_TIMES.daily_checkin).join(','));
  setCheckboxValue('sched-daily-login', daily.run_on_login);
  setCheckboxValue('sched-transport-enabled', transport.enabled);
  setInputValue('sched-transport-times', (Array.isArray(transport.times) ? transport.times : DEFAULT_SCHEDULE_TIMES.transport_supply).join(','));
  setCheckboxValue('tool-transport-auto-use-gold-ticket', transport.auto_use_gold_ticket);
  setCheckboxValue('sched-world-boss-enabled', worldBoss.enabled);
  setInputValue('sched-world-boss-times', (Array.isArray(worldBoss.times) ? worldBoss.times : DEFAULT_SCHEDULE_TIMES.world_boss).join(','));
  setCheckboxValue('sched-liaoguo-enabled', liaoguo.enabled);
  setInputValue('sched-liaoguo-time', liaoguo.time || DEFAULT_SCHEDULE_TIMES.liaoguo);
  renderScheduledLiaoguoPairs();
}

async function loadScheduledTasksConfig() {
  const res = await api('GET', '/api/scheduled-tasks/config').catch(() => null);
  if (!res?.ok) {
    setToolResult(res?.error || '读取定时配置失败', 'err');
    return;
  }
  renderScheduledTasksConfig(res.config || {});
}

async function saveScheduledTasksConfig() {
  const body = {
    daily_checkin: {
      enabled: getCheckboxValue('sched-daily-enabled'),
      run_on_login: getCheckboxValue('sched-daily-login'),
    },
    transport_supply: {
      enabled: getCheckboxValue('sched-transport-enabled'),
      auto_use_gold_ticket: getCheckboxValue('tool-transport-auto-use-gold-ticket'),
    },
    world_boss: {
      enabled: getCheckboxValue('sched-world-boss-enabled'),
    },
    liaoguo: {
      enabled: getCheckboxValue('sched-liaoguo-enabled'),
    },
  };
  const res = await api('PUT', '/api/scheduled-tasks/config', body).catch(() => null);
  if (!res?.ok) {
    setToolResult(res?.error || '保存定时配置失败', 'err');
    return;
  }
  renderScheduledTasksConfig(res.config || body);
  setToolResult('定时配置已保存', 'ok');
}

function applySelectedLiaoguoPairToInputs() {
  const pair = liaoguoPairs.find((p) => getLiaoguoPairKey(p) === selectedLiaoguoPairKey);
  if (!pair) return;
  const itemEl = document.getElementById('liaoguo-item-code');
  const monsterEl = document.getElementById('liaoguo-monster-code');
  const labelEl = document.getElementById('liaoguo-label');
  const taskEl = document.getElementById('liaoguo-task-name');
  const ticketEl = document.getElementById('liaoguo-ticket-item-code');
  const abandonEl = document.getElementById('liaoguo-abandon-task-code');
  if (itemEl) itemEl.value = pair.itemCode;
  if (monsterEl) monsterEl.value = pair.monsterCode;
  if (labelEl) labelEl.value = pair.label;
  if (taskEl) taskEl.value = pair.taskName;
  if (ticketEl) ticketEl.value = pair.ticketItemCode;
  if (abandonEl) abandonEl.value = pair.abandonTaskCode;
}

function onLiaoguoPairSelectChange() {
  const select = document.getElementById('liaoguo-pair-select');
  selectedLiaoguoPairKey = String(select?.value || '').trim();
  applySelectedLiaoguoPairToInputs();
}

async function saveLiaoguoPair() {
  const pair = normalizeLiaoguoPair({
    itemCode: document.getElementById('liaoguo-item-code')?.value || '',
    monsterCode: document.getElementById('liaoguo-monster-code')?.value || '',
    taskName: document.getElementById('liaoguo-task-name')?.value || '',
    label: document.getElementById('liaoguo-label')?.value || '',
    ticketItemCode: document.getElementById('liaoguo-ticket-item-code')?.value || '',
    abandonTaskCode: document.getElementById('liaoguo-abandon-task-code')?.value || '',
  });
  if (!pair) {
    setToolResult('辽国映射不合法：itemcode 需14位hex、monster 需4位hex、label/taskName 不能为空、任务券 itemcode 需为偶数位hex、放弃任务code 需4位hex', 'err');
    return;
  }
  const saveRes = await api('POST', '/api/liaoguo-pairs', pair).catch(() => null);
  if (!saveRes?.ok) {
    setToolResult(saveRes?.error || '保存辽国映射失败', 'err');
    return;
  }
  liaoguoPairs = Array.isArray(saveRes.items) ? saveRes.items.map(normalizeLiaoguoPair).filter(Boolean) : [{ ...pair }];
  selectedLiaoguoPairKey = getLiaoguoPairKey(pair);
  renderLiaoguoPairs();
  setToolResult(`辽国映射已保存：${pair.itemCode} - ${pair.label} - ${pair.monsterCode} - ${pair.taskName} - 任务券 ${pair.ticketItemCode} - 放弃任务 ${pair.abandonTaskCode}`, 'ok');
}

async function deleteLiaoguoPair() {
  if (!selectedLiaoguoPairKey) return;
  const res = await api('DELETE', `/api/liaoguo-pairs/${encodeURIComponent(selectedLiaoguoPairKey)}`).catch(() => null);
  if (!res?.ok) {
    setToolResult(res?.error || '删除辽国映射失败', 'err');
    return;
  }
  liaoguoPairs = Array.isArray(res.items) ? res.items.map(normalizeLiaoguoPair).filter(Boolean) : [];
  selectedLiaoguoPairKey = liaoguoPairs.length ? getLiaoguoPairKey(liaoguoPairs[0]) : '';
  renderLiaoguoPairs();
  setToolResult('辽国映射已删除', 'ok');
}

function getSelectedLiaoguoPair() {
  return liaoguoPairs.find((p) => getLiaoguoPairKey(p) === selectedLiaoguoPairKey) || null;
}

function toggleLiaoguoFlow() {
  const pair = getSelectedLiaoguoPair();
  if (!liaoguoRunning && !pair) {
    setToolResult('请先配置辽国映射', 'err');
    return;
  }
  runLiaoguoFlow(pair);
}

async function refreshLiaoguoStatus(silent = true) {
  const res = await api('GET', '/api/flow/liaoguo/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) setToolResult(res?.error || '读取辽国战斗状态失败', 'err');
    return false;
  }
  liaoguoRunning = !!res.running;
  updateLiaoguoButton();
  return true;
}

async function runLiaoguoFlow(pair) {
  const endpoint = liaoguoRunning ? '/api/flow/liaoguo/stop' : '/api/flow/liaoguo/start';
  const actionText = liaoguoRunning ? '停止' : '启动';
  const body = liaoguoRunning ? {} : { ...(pair || {}) };
  const res = await api('POST', endpoint, body).catch(() => null);
  if (!res?.ok) {
    setToolResult(`辽国战斗${actionText}失败：${res?.error || '未知错误'}`, 'err');
    await refreshLiaoguoStatus(true);
    return;
  }
  setToolResult(`辽国战斗：已请求${actionText}`, 'ok');
  await refreshLiaoguoStatus(true);
}

async function refreshTransportSupplyStatus(silent = true) {
  const res = await api('GET', '/api/flow/transport-supply/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) setToolResult(res?.error || '读取运输物资状态失败', 'err');
    return false;
  }
  transportSupplyRunning = !!res.running;
  updateTransportSupplyButton();
  return true;
}

async function refreshWorldBossStatus(silent = true) {
  const res = await api('GET', '/api/flow/world-boss/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) setToolResult(res?.error || '读取世界 BOSS 状态失败', 'err');
    return false;
  }
  worldBossRunning = !!res.running;
  updateWorldBossButton();
  return true;
}

async function toggleTransportSupplyFlow() {
  const endpoint = transportSupplyRunning
    ? '/api/flow/transport-supply/stop'
    : '/api/flow/transport-supply/start';
  const actionText = transportSupplyRunning ? '停止' : '启动';
  const body = transportSupplyRunning
    ? {}
    : { auto_use_gold_ticket: getCheckboxValue('tool-transport-auto-use-gold-ticket') };
  const res = await api('POST', endpoint, body).catch(() => null);
  if (!res?.ok) {
    setToolResult(`运输物资：${actionText}失败 — ${res?.error || '未知错误'}`, 'err');
    await refreshTransportSupplyStatus(true);
    return;
  }
  setToolResult(`运输物资：已请求${actionText}`, 'ok');
  await refreshTransportSupplyStatus(true);
}

async function toggleWorldBossFlow() {
  const endpoint = worldBossRunning
    ? '/api/flow/world-boss/stop'
    : '/api/flow/world-boss/start';
  const actionText = worldBossRunning ? '停止' : '启动';
  const res = await api('POST', endpoint).catch(() => null);
  if (!res?.ok) {
    setToolResult(`世界 BOSS：${actionText}失败 — ${res?.error || '未知错误'}`, 'err');
    await refreshWorldBossStatus(true);
    return;
  }
  setToolResult(`世界 BOSS：已请求${actionText}`, 'ok');
  await refreshWorldBossStatus(true);
}

async function loadToolTeleportOptions() {
  const select = document.getElementById('tool-teleport-destination');
  if (!select) return;
  const res = await api('GET', '/api/teleport/destinations').catch(() => null);
  const items = Array.isArray(res?.items) ? res.items : [];
  teleportDestinationsCache = items
    .map((item) => ({
      name: String(item?.name || '').trim(),
      code: String(item?.code || '').trim().toLowerCase(),
    }))
    .filter((item) => item.name && /^[0-9a-f]{8}$/.test(item.code));
  if (!teleportDestinationsCache.length) {
    select.innerHTML = '<option value="">暂无可用地点</option>';
    select.value = '';
    return;
  }
  select.innerHTML = teleportDestinationsCache
    .map((item) => `<option value="${escAttr(item.code)}">${escHtml(item.name)} (${escHtml(item.code)})</option>`)
    .join('');
  select.value = teleportDestinationsCache[0].code;
}

async function sendToolPacket(hex) {
  const cleanHex = String(hex || '').replace(/\s+/g, '').toLowerCase();
  if (!cleanHex) {
    setToolResult('报文不能为空', 'err');
    return;
  }
  if (cleanHex.length % 2 !== 0 || !/^[0-9a-f]+$/.test(cleanHex)) {
    setToolResult('报文必须是偶数字节长度的 hex 字符串', 'err');
    return;
  }
  const res = await api('POST', '/api/probe/send', { hex: cleanHex, use_queue: true });
  if (!res.ok) {
    setToolResult(res.error || '发送失败', 'err');
    return;
  }
  const sentText = withValidationWarning(
    `发送成功 · 方式: ${res.method}${res.sent_bytes !== undefined ? ` · ${res.sent_bytes} bytes` : ''}`,
    res
  );
  setToolResult(`${sentText} · hex: ${cleanHex}`, 'ok');
}

async function sendTeleportPacket() {
  const select = document.getElementById('tool-teleport-destination');
  const destination = String(select?.value || '').trim().toLowerCase();
  if (!destination || destination.length !== 8 || !/^[0-9a-f]{8}$/.test(destination)) {
    setToolResult('请选择有效的传送地点', 'err');
    return;
  }
  const packetHex = TELEPORT_PACKET_TEMPLATE
    .replace('{random_num}', randomNumHex4())
    .replace('{destination}', destination);
  await sendToolPacket(packetHex);
}

async function sendDailyCheckinPacket() {
  const res = await api('POST', '/api/flow/daily-checkin/run', {}).catch(() => null);
  if (!res?.ok) {
    setToolResult(`每日签到发送失败：${res?.error || '未知错误'}`, 'err');
    return;
  }
  setToolResult('每日签到：已发送', 'ok');
}

async function sendRoleStatPacket() {
  const attrCode = String(document.getElementById('tool-role-attr')?.value || '').trim().toLowerCase();
  if (!/^(00|01|02|03)$/.test(attrCode)) {
    setToolResult('请选择有效属性：00/01/02/03', 'err');
    return;
  }
  const pointsRaw = String(document.getElementById('tool-role-points')?.value || '').trim();
  if (!/^\d+$/.test(pointsRaw)) {
    setToolResult('加点数量必须是十进制整数', 'err');
    return;
  }
  let amountLE;
  try {
    amountLE = toLittleEndianHex16(pointsRaw);
  } catch (err) {
    setToolResult(err?.message || '加点数量不合法', 'err');
    return;
  }
  const packetHex = `1a000000e8030200fd03${randomNumHex4()}f505020400000800000001${attrCode}${amountLE}00000000`;
  await sendToolPacket(packetHex);
}

function setLadderResult(text, type = 'info') {
  showMsg('ladder-result', text, type);
}

function clearLadderBattleLog() {
  const box = document.getElementById('ladder-battle-log');
  if (box) box.innerHTML = '';
}

function appendLadderBattleLog(text, kind = 'response') {
  if (!ladderBattleLogActive) return;
  const box = document.getElementById('ladder-battle-log');
  if (!box) return;
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const div = document.createElement('div');
  div.className = `ladder-log-item ${kind === 'end' ? 'end' : ''}`;
  div.textContent = `【${t}】 ${text}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function shouldKeepLadderBattleLog() {
  return ladderBattleLogActive && !!ladderAutoState.running;
}

function appendLadderBattleLogFromEvent(kind, data) {
  if (!ladderBattleLogActive) return;
  const state = String(data?.battle_state?.state || battleState.state || '');
  const raw = String(data?.raw_text || '').trim();
  if (kind === 'response') {
    appendLadderBattleLog('收到战斗启动响应，主号已发送攻击');
    return;
  }
  if (kind === 'not_killed') {
    appendLadderBattleLog(raw ? `战斗继续：${raw}` : `战斗继续，当前状态 ${state || '进行中'}`);
  }
}

async function sendLadderChallenge() {
  const raw = String(document.getElementById('ladder-floor')?.value || '').trim();
  if (!/^\d+$/.test(raw)) {
    setLadderResult('楼层必须是 1~20 的整数', 'err');
    return;
  }
  const res = await api('POST', '/api/ladder/challenge', { floor: Number(raw) }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '单梯挑战发送失败', 'err');
    return;
  }
  ladderBattleLogActive = true;
  clearLadderBattleLog();
  updateBattleState(res.battle_state || {});
  const meta = document.getElementById('ladder-challenge-meta');
  if (meta) meta.textContent = `targetIdLE: ${res.target_id_le || ''}`;
  appendBattleLog({ raw_text: `天梯挑战开始：${res.floor} 层` }, 'response');
  appendLadderBattleLog(`天梯挑战开始：${res.floor} 层，等待服务器响应`);
  setLadderResult(`单梯挑战已入队：${res.floor} 层，已进入单次战斗流程`, 'ok');
}

function renderLadderAutoState(data) {
  ladderAutoState = { ...ladderAutoState, ...(data || {}) };
  const btn = document.getElementById('btn-ladder-auto');
  if (btn) {
    btn.textContent = ladderAutoState.running ? '停止一键挑战' : '一键挑战';
    btn.classList.toggle('btn-danger', !!ladderAutoState.running);
    btn.classList.toggle('btn-success', !ladderAutoState.running);
  }
  const el = document.getElementById('ladder-auto-status');
  if (!el) return;
  if (ladderAutoState.running) {
    el.textContent = `运行中：${ladderAutoState.current_floor || '-'} / ${ladderAutoState.end_floor || '-'}`;
  } else if (ladderAutoState.last_error) {
    el.textContent = `已停止：${ladderAutoState.last_error}`;
  } else if (ladderAutoState.completed_floor) {
    el.textContent = `已完成到第 ${ladderAutoState.completed_floor} 层`;
  } else {
    el.textContent = '未启动';
  }
}

async function refreshLadderAutoStatus() {
  const res = await api('GET', '/api/ladder/auto/status').catch(() => null);
  if (res?.ok) renderLadderAutoState(res);
}

async function toggleLadderAuto() {
  if (ladderAutoState.running) {
    const res = await api('POST', '/api/ladder/auto/stop', {}).catch(() => null);
    if (!res?.ok) {
      setLadderResult(res?.error || '停止一键挑战失败', 'err');
      return;
    }
    renderLadderAutoState(res);
    setLadderResult('已请求停止一键挑战', 'info');
    return;
  }
  const startRaw = String(document.getElementById('ladder-auto-start-floor')?.value || '1').trim() || '1';
  const endRaw = String(document.getElementById('ladder-auto-end-floor')?.value || '').trim();
  if (!/^\d+$/.test(startRaw) || !/^\d+$/.test(endRaw)) {
    setLadderResult('一键挑战需要填写起始层和结束层', 'err');
    return;
  }
  ladderBattleLogActive = true;
  clearLadderBattleLog();
  const res = await api('POST', '/api/ladder/auto/start', {
    start_floor: Number(startRaw),
    end_floor: Number(endRaw),
  }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '启动一键挑战失败', 'err');
    ladderBattleLogActive = false;
    return;
  }
  renderLadderAutoState(res);
  appendLadderBattleLog(`一键挑战启动：${startRaw}~${endRaw} 层`);
  setLadderResult(`一键挑战已启动：${startRaw}~${endRaw} 层`, 'ok');
}

function onLadderAutoEvent(data) {
  const event = String(data?.event || '');
  if (event === 'started') {
    ladderBattleLogActive = true;
    clearLadderBattleLog();
  }
  if (data?.message) {
    const endKinds = ['finished', 'stopped', 'error'];
    appendLadderBattleLog(String(data.message), endKinds.includes(event) ? 'end' : 'response');
  }
  refreshLadderAutoStatus();
  if (['finished', 'stopped', 'error'].includes(event)) {
    if (event !== 'error') ladderBattleLogActive = false;
  }
}

function parseInviteUserIdsInput() {
  const raw = String(document.getElementById('ladder-invite-user-ids')?.value || '').trim();
  return raw.split(/[\s,，;；]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
}

async function sendLadderInvite(includeManagedOnline) {
  const body = {
    target_user_ids: includeManagedOnline ? [] : parseInviteUserIdsInput(),
    include_managed_online: !!includeManagedOnline,
  };
  const res = await api('POST', '/api/ladder/team/invite', body).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '组队邀请发送失败', 'err');
    return;
  }
  setLadderResult(`组队邀请已入队：${(res.invited || []).join(', ')}`, 'ok');
}

function clearSmallAccountForm() {
  const accountEl = document.getElementById('small-account-account');
  const passwordEl = document.getElementById('small-account-password');
  if (accountEl) accountEl.value = '';
  if (passwordEl) passwordEl.value = '';
}

function renderSmallAccounts(items) {
  smallAccounts = Array.isArray(items) ? items : [];
  const summary = document.getElementById('small-account-list-summary');
  const box = document.getElementById('small-account-list');
  if (!box) return;
  const count = smallAccounts.length;
  if (summary) summary.textContent = `小号列表（${count}）`;
  if (!count) {
    box.className = 'small-account-list-body ladder-empty';
    box.innerHTML = '暂无小号';
    return;
  }
  box.className = 'small-account-list-body';
  box.innerHTML = smallAccounts.map((item, idx) => {
    const account = escAttr(item.account || '');
    const statusItem = smallStatusItems.find((x) => x.account === item.account);
    const running = !!statusItem && !['stopped', 'offline'].includes(String(statusItem.status || ''));
    const toggleLabel = running ? '停止' : '启动';
    const toggleClass = running ? 'btn-danger' : 'btn-success';
    const label = `${idx + 1}. ${escHtml(item.account || '')}`;
    const last = item.last_user_id ? ` · 上次角色ID ${escHtml(item.last_user_id)}` : '';
    return `<div class="small-account-item">
      <div style="font-size:12px; margin-bottom:6px;">${label}${last}</div>
      <div class="battle-row">
        <button class="btn ${toggleClass} btn-sm" onclick="toggleSmallAccount('${account}')">${toggleLabel}</button>
        <button class="btn btn-ghost btn-sm" onclick="editSmallAccount('${account}')">编辑</button>
        <button class="btn btn-ghost btn-sm" onclick="moveSmallAccount('${account}', 'up')">上移</button>
        <button class="btn btn-ghost btn-sm" onclick="moveSmallAccount('${account}', 'down')">下移</button>
        <button class="btn btn-danger btn-sm" onclick="deleteSmallAccount('${account}')">删除</button>
      </div>
    </div>`;
  }).join('');
}

async function loadSmallAccounts() {
  const res = await api('GET', '/api/ladder/small-accounts').catch(() => null);
  if (!res?.ok) {
    renderSmallAccounts([]);
    return;
  }
  renderSmallAccounts(res.items || []);
}

function isSmallAccountRunning(account) {
  const item = smallStatusItems.find((x) => x.account === account);
  return !!item && !['stopped', 'offline'].includes(String(item.status || ''));
}

async function toggleSmallAccount(account) {
  if (isSmallAccountRunning(account)) {
    await stopSmallAccount(account);
    return;
  }
  await startSmallAccount(account);
}

function editSmallAccount(account) {
  const item = smallAccounts.find((x) => x.account === account);
  if (!item) return;
  const accountEl = document.getElementById('small-account-account');
  const passwordEl = document.getElementById('small-account-password');
  if (accountEl) accountEl.value = item.account || '';
  if (passwordEl) passwordEl.value = item.password || '';
}

async function saveSmallAccount() {
  const account = String(document.getElementById('small-account-account')?.value || '').trim();
  const password = String(document.getElementById('small-account-password')?.value || '').trim();
  const res = await api('POST', '/api/ladder/small-accounts', { account, password }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '保存小号失败', 'err');
    return;
  }
  renderSmallAccounts(res.items || []);
  clearSmallAccountForm();
  setLadderResult('小号已保存', 'ok');
}

async function deleteSmallAccount(account) {
  const res = await api('DELETE', `/api/ladder/small-accounts/${encodeURIComponent(account)}`).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '删除小号失败', 'err');
    return;
  }
  renderSmallAccounts(res.items || []);
  setLadderResult('小号已删除', 'ok');
}

async function moveSmallAccount(account, direction) {
  const res = await api('PUT', `/api/ladder/small-accounts/${encodeURIComponent(account)}`, { direction }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '调整小号顺序失败', 'err');
    return;
  }
  renderSmallAccounts(res.items || []);
}

async function startSmallAccount(account) {
  const res = await api('POST', '/api/ladder/small/start-one', { account }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '启动小号失败', 'err');
    return;
  }
  renderSmallStatus(res.items || []);
  ensureSmallStatusPolling();
  setLadderResult(`已请求启动小号：${(res.started || []).join(', ')}`, 'ok');
}

async function stopSmallAccount(account) {
  const res = await api('POST', '/api/ladder/small/stop-one', { account }).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '停止小号失败', 'err');
    return;
  }
  renderSmallStatus(res.items || []);
  renderSmallAccounts(smallAccounts);
  setLadderResult(`已请求停止小号：${account}`, 'ok');
}

function renderSmallStatus(items) {
  smallStatusItems = Array.isArray(items) ? items : [];
  const list = smallStatusItems.filter((item) => !['stopped', 'offline'].includes(String(item.status || '')));
  const box = document.getElementById('small-status-list');
  if (!box) return;
  if (!list.length) {
    box.innerHTML = '<div class="ladder-empty">暂无托管小号</div>';
    renderSmallAccounts(smallAccounts);
    return;
  }
  box.innerHTML = list.map((item) => {
    const status = item.status || 'offline';
    const role = item.role_name || item.role_id || '未选角';
    const age = item.last_recv_age !== null && item.last_recv_age !== undefined ? ` · ${item.last_recv_age}s` : '';
    const err = item.error ? `<div class="text-red text-sm">${escHtml(item.error)}</div>` : '';
    const joined = item.team_joined ? '<span class="small-status-badge online">已入队</span>' : '<span class="small-status-badge stopped">未入队</span>';
    const checkin = item.checkin_sent ? '<span class="small-status-badge online">已签到</span>' : '';
    return `<div style="border:1px solid var(--border); border-radius:6px; padding:6px; margin-bottom:6px;">
      <div class="battle-row">
        <span class="small-status-badge ${escAttr(status)}">${escHtml(status)}</span>
        ${joined}
        ${checkin}
        <span>${escHtml(item.account || '')} · ${escHtml(role)}${age}</span>
      </div>
      <div class="ladder-meta">userId: ${escHtml(item.role_id || item.last_user_id || '—')}</div>
      ${err}
    </div>`;
  }).join('');
  renderSmallAccounts(smallAccounts);
}

async function refreshSmallStatus(silent = true) {
  const res = await api('GET', '/api/ladder/small/status').catch(() => null);
  if (!res?.ok) {
    if (!silent) setLadderResult(res?.error || '读取小号状态失败', 'err');
    return;
  }
  renderSmallStatus(res.items || []);
}

function ensureSmallStatusPolling() {
  if (smallStatusPollTimer) return;
  smallStatusPollTimer = setInterval(() => {
    const active = document.getElementById('tab-ladder')?.classList.contains('active');
    if (active) refreshSmallStatus(true);
  }, 3000);
}

async function startSmallAccounts() {
  const res = await api('POST', '/api/ladder/small/start', {}).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '启动小号失败', 'err');
    return;
  }
  renderSmallStatus(res.items || []);
  ensureSmallStatusPolling();
  setLadderResult(`已请求启动小号：${(res.started || []).join(', ')}`, 'ok');
}

async function stopSmallAccounts() {
  const res = await api('POST', '/api/ladder/small/stop', {}).catch(() => null);
  if (!res?.ok) {
    setLadderResult(res?.error || '停止小号失败', 'err');
    return;
  }
  renderSmallStatus(res.items || []);
  setLadderResult('已请求停止小号', 'ok');
}

function onLadderTeamEvent(data) {
  const message = String(data?.message || '').trim();
  if (!message) return;
  setLadderResult(`组队状态：${message}`, data?.event === 'joined' ? 'ok' : 'info');
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
// 与 game_test/features/role_stats.py 中 STAT_GROUPS / STAT_NAMES 保持一致
const ROLE_STAT_GROUPS = {
  角色信息: ['等级', '职业', '声望', '积分'],
  基础属性: ['力量', '智力', '敏捷', '体质'],
  战斗属性: ['物攻', '物防', '法攻', '法防', '命中', '躲闪', '暴击', '速度'],
  其他信息: ['月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数', '经验UP', '攻击UP', '金钱UP', '回血', '回蓝'],
};
const ROLE_STAT_GROUP_ORDER = ['角色信息', '基础属性', '战斗属性', '其他信息'];
const ROLE_STAT_ORDER = [
  '力量', '智力', '敏捷', '体质',
  '物攻', '物防', '法攻', '法防',
  '命中', '躲闪', '暴击', '速度',
  '等级', '职业', '声望', '积分',
  '月VIP', '周VIP', '任务积分', '金库次数', '珍珑宝库次数',
  '经验UP', '攻击UP', '金钱UP', '回血', '回蓝',
];

let statsCollapsed = false;

function toggleStatsPanel() {
  statsCollapsed = !statsCollapsed;
  document.getElementById('stats-body').style.display = statsCollapsed ? 'none' : '';
  document.getElementById('stats-toggle-icon').textContent = statsCollapsed ? '▶' : '▼';
}

function renderRoleStats(data) {
  const stats = data.stats || {};
  const groups = (data.groups && Object.keys(data.groups).length) ? data.groups : ROLE_STAT_GROUPS;

  document.getElementById('role-stats-panel').classList.add('visible');

  let html = '';
  for (const groupName of ROLE_STAT_GROUP_ORDER) {
    const keys = groups[groupName];
    if (!keys || !keys.length) continue;
    html += `<div class="stats-group"><div class="stats-group-title">${escHtml(groupName)}</div><div class="stats-grid">`;
    for (const k of keys) {
      const raw = stats[k];
      const has = raw !== undefined && raw !== null && String(raw).length > 0;
      const v = has ? String(raw) : '';
      const isHighlight = has && ['等级', '职业'].includes(k);
      const valueClass = has ? `stat-value${isHighlight ? ' highlight' : ''}` : 'stat-value stat-pending';
      const valueText = has ? escHtml(v) : '—';
      html += `<div class="stat-row"><span class="stat-name">${escHtml(k)}</span><span class="${valueClass}">${valueText}</span></div>`;
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
  const backpackQtyInput = document.getElementById('backpack-action-qty');
  if (backpackQtyInput) {
    backpackQtyInput.addEventListener('input', () => {
      // 仅允许数字输入，避免误输入字母导致提交失败提示过多。
      backpackQtyInput.value = String(backpackQtyInput.value || '').replace(/\D+/g, '');
    });
    backpackQtyInput.addEventListener('blur', () => {
      syncBackpackActionQtyInput();
    });
  }

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
  // 必须先同步勾选到后端再 GET：否则服务端默认 false 会通过 setControlState 改掉勾选框，
  // 紧接着的 PUT 会误提交 false，导致「明明勾了自动重连却不生效」（尤其刷新页面后）。
  await api('PUT', '/api/control-config', { auto_reconnect: chkAR.checked }).catch(() => null);
  const controlRes = await api('GET', '/api/control-state').catch(() => null);
  if (controlRes?.ok) {
    setControlState(controlRes.control_state || {});
    updateBattleState(controlRes.battle_state || {});
  }
  loadMonsters();
  loadAutoUseRules();
  loadQuickLogins();
  loadSmallAccounts();
  refreshSmallStatus(true);
  refreshLadderAutoStatus();
  ensureSmallStatusPolling();
  loadBuyItems();
  loadGold();
  await refreshAutoDecomposeS1Status(true);
  await loadLiaoguoPairs();
  await loadScheduledTasksConfig();
  const liaoguoSel = document.getElementById('liaoguo-pair-select');
  if (liaoguoSel) liaoguoSel.addEventListener('change', onLiaoguoPairSelectChange);
  await refreshStarStoneStatus(true);
  await refreshSynthesisBatchStatus(true);
  syncBackpackSelectionUi();
  await refreshLiaoguoStatus(true);
  await refreshTransportSupplyStatus(true);
  await refreshWorldBossStatus(true);
  await loadToolTeleportOptions();
  updateBattleStatsText();
})();
