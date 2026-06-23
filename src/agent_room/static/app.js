const state = {
  templates: [],
  teams: [],
  shareContexts: [],
  tmux: null,
  room: null,
  messages: [],
  controllerMessages: [],
  events: [],
  ws: null,
  activeChat: "messages",
  pendingAction: "",
  status: { text: "", error: false },
  bubbles: new Map(),
  bubbleTimers: new Map(),
};

const TERMINATION_TEMPLATE = {
  controller:
    "controllerがalign、diverge、cluster、deepen、evaluate、convergeを順に進め、各phase末尾でaccept/revise/blockの合意ゲートを通し、絞り込み理由と異議処理を記録し、目的、背景、決定、要件、検証観点、リスク、未決事項を含む実装引き継ぎレポートをまとめたら。",
  agent:
    "controllerが終了と判断するまでdoneしない。各agentはcontrollerから割り当てられた一時観点で短く発言し、phase合意ではaccept/revise/blockのいずれかと理由を返す。開始直後はcontrollerの初回指示を待つ。",
};

const KNOWN_AGENT_STATES = new Set(["starting", "active", "idle", "speaking", "done", "stopped", "failed"]);

const STATE_LABELS = {
  draft: "下書き",
  starting: "起動中",
  open: "進行中",
  done: "完了",
  stopped: "停止",
  failed: "失敗",
  active: "稼働中",
  idle: "待機",
  speaking: "発言中",
};

const PHASE_LABELS = {
  align: "整列",
  diverge: "発散",
  cluster: "整理",
  deepen: "深掘り",
  evaluate: "評価",
  converge: "収束",
  draft: "下書き",
  starting: "起動中",
  open: "開始",
  done: "完了",
  stopped: "停止",
  synthesis: "統合",
  research: "調査",
  final: "最終",
  summary: "要約",
};

const ACTOR_LABELS = {
  User: "ユーザー",
  System: "システム",
  Controller: "進行役",
  Default: "基本チーム",
  "Critique Lab": "批判ラボ",
  "Malcontent Table": "不満卓",
};

const ROLE_LABELS = {
  control: "進行管理",
  implementation: "実装",
  review: "レビュー",
  synthesis: "統合",
  facilitation: "司会補佐",
  operations: "運用管制",
  research: "調査",
  "technical critique": "技術批判",
  "user critique": "利用者批判",
  "business critique": "事業批判",
  "risk critique": "リスク批判",
  "idea revision": "軌道修正",
  "user complaint": "利用者不満",
  "operations complaint": "運用小言",
  "sharp critique": "辛口批判",
  "skeptical debater": "懐疑討論",
};

const MESSAGE_TAG_LABELS = {
  user: "ユーザー",
  agent: "Agent",
  controller: "進行役",
  system: "システム",
  facilitation: "進行",
  goal: "目的",
  state: "状態",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(errorMessage(body));
  }
  return response.json();
}

async function boot() {
  $("refresh").addEventListener("click", () => runAction("Refresh", refresh));
  $("newRoom").addEventListener("click", () => runAction("New", resetRoom));
  $("startRoom").addEventListener("click", () => runAction("Start", startRoom));
  $("selectAllTemplates").addEventListener("click", () => setTemplateChecks(true));
  $("clearTemplates").addEventListener("click", () => setTemplateChecks(false));
  $("sendMessage").addEventListener("click", sendMessage);
  $("sendControllerMessage").addEventListener("click", sendControllerMessage);
  $("deployAgent").addEventListener("click", () => runAction("Deploy", deployAgent));
  $("closeRoom").addEventListener("click", () => runAction("Close", closeRoom));
  $("messagesTab").addEventListener("click", () => setActiveChat("messages"));
  $("controllerTab").addEventListener("click", () => setActiveChat("controller"));
  $("goal").addEventListener("input", renderControls);
  $("controllerTermination").addEventListener("input", renderControls);
  $("agentTermination").addEventListener("input", renderControls);
  $("shareContextList").addEventListener("change", renderControls);
  $("teamList").addEventListener("change", handleTeamChange);
  $("templateList").addEventListener("change", handleTemplateChange);
  $("messageText").addEventListener("keydown", (event) => handleComposerKey(event, sendMessage));
  $("controllerText").addEventListener("keydown", (event) => handleComposerKey(event, sendControllerMessage));
  $("messageText").addEventListener("input", growComposer);
  $("controllerText").addEventListener("input", growComposer);
  await runAction("Refresh", refresh);
}

async function runAction(label, task) {
  if (state.pendingAction) return;
  state.pendingAction = label;
  setStatus(`${label}...`, false);
  renderControls();
  try {
    await task();
    setStatus(`${label} done`, false);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    state.pendingAction = "";
    renderControls();
  }
}

async function refresh() {
  const [templates, teams, shareContexts, tmux] = await Promise.all([
    api("/api/templates"),
    api("/api/teams"),
    api("/api/share/contexts"),
    api("/api/tmux"),
  ]);
  state.templates = templates;
  state.teams = teams;
  state.shareContexts = shareContexts;
  state.tmux = tmux;
  renderTemplates();
  renderTeams();
  renderShareContexts();
  renderDeploy();
  renderTmux();
  const rooms = await api("/api/rooms");
  const room = rooms[0];
  if (room) {
    const shouldConnect = !state.room || state.room.id !== room.id;
    state.room = room;
    if (shouldConnect) connectRoom();
  }
  await loadRoom();
}

async function resetRoom() {
  if (state.room && (state.room.agents.length || state.messages.length) && !confirm("New room?")) return;
  state.room = await api("/api/rooms/reset", {
    method: "POST",
    body: JSON.stringify({
      actor_id: "user",
      reason: "user requested new room",
      force: false,
    }),
  });
  state.messages = [];
  state.controllerMessages = [];
  state.events = [];
  clearBubbles();
  $("goal").value = "";
  setTerminationTemplate();
  connectRoom();
  await loadRoom();
}

async function startRoom() {
  const selected = [...document.querySelectorAll("[data-template-check]:checked")].map((input) => input.value);
  if (!selected.includes("controller")) selected.unshift("controller");
  if (!requireStartInputs()) return;
  const payload = {
    name: state.room.name,
    goal: $("goal").value.trim(),
    controller_termination: $("controllerTermination").value.trim(),
    agent_termination: $("agentTermination").value.trim(),
    share_contexts: selectedShareContexts(),
    templates: selected,
    teams: [],
  };
  state.room = await api("/api/rooms", { method: "POST", body: JSON.stringify(payload) });
  connectRoom();
  await loadRoom();
}

async function loadRoom() {
  if (!state.room) {
    state.events = [];
    renderRoom();
    return;
  }
  state.room = await api(`/api/rooms/${state.room.id}`);
  syncRoomForm();
  [state.messages, state.controllerMessages, state.events] = await Promise.all([
    api(`/api/rooms/${state.room.id}/messages`),
    api(`/api/rooms/${state.room.id}/controller/messages`),
    api(`/api/rooms/${state.room.id}/events`),
  ]);
  renderRoom();
}

function syncRoomForm() {
  if (!state.room) return;
  $("goal").value = state.room.goal;
  if (state.room.controller_termination || state.room.agent_termination) {
    $("controllerTermination").value = state.room.controller_termination;
    $("agentTermination").value = state.room.agent_termination;
    return;
  }
  if (state.room.state === "draft") setTerminationTemplate();
}

function setTerminationTemplate() {
  $("controllerTermination").value = TERMINATION_TEMPLATE.controller;
  $("agentTermination").value = TERMINATION_TEMPLATE.agent;
}

function requireStartInputs() {
  if (!state.room) {
    setStatus("Room state required", true);
    return false;
  }
  const fields = [
    ["Goal", $("goal")],
    ["Controller Termination", $("controllerTermination")],
    ["Agent Termination", $("agentTermination")],
  ];
  const missing = fields.find(([, field]) => !field.value.trim());
  if (missing) {
    setStatus(`${missing[0]} required`, true);
    missing[1].focus();
    return false;
  }
  return true;
}

function connectRoom() {
  if (state.ws) state.ws.close();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${protocol}://${location.host}/ws/rooms/${state.room.id}`);
  state.ws.onopen = renderControls;
  state.ws.onclose = renderControls;
  state.ws.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    if (payload.message) {
      state.messages.push(payload.message);
      showBubble(payload.message);
    }
    if (payload.controller_message) {
      state.controllerMessages.push(payload.controller_message);
    }
    if (payload.type === "room.reset" && payload.room) {
      state.room = payload.room;
      state.messages = [];
      state.controllerMessages = [];
      state.events = [];
      clearBubbles();
      connectRoom();
      await loadRoom();
      return;
    }
    if (payload.room) {
      state.room = payload.room;
      if (payload.type === "room.status_updated") trackStatusEvent(payload.room);
    }
    renderRoom();
  };
}

async function sendMessage() {
  if (!state.room) {
    setStatus("Room required", true);
    return;
  }
  const text = $("messageText").value;
  if (!text.trim()) return;
  $("messageText").value = "";
  growComposer({ target: $("messageText") });
  try {
    await api(`/api/rooms/${state.room.id}/messages`, {
      method: "POST",
      body: JSON.stringify({
        actor_type: "user",
        actor_id: "user",
        actor_name: "User",
        text,
        kind: "message",
      }),
    });
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function sendControllerMessage() {
  if (!state.room) {
    setStatus("Room required", true);
    return;
  }
  const text = $("controllerText").value;
  if (!text.trim()) return;
  $("controllerText").value = "";
  growComposer({ target: $("controllerText") });
  try {
    await api(`/api/rooms/${state.room.id}/controller/messages`, {
      method: "POST",
      body: JSON.stringify({
        actor_type: "user",
        actor_id: "user",
        actor_name: "User",
        text,
        kind: "message",
      }),
    });
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function deployAgent() {
  if (!state.room || state.room.state !== "open") {
    setStatus("Open room required", true);
    return;
  }
  await api(`/api/rooms/${state.room.id}/agents`, {
    method: "POST",
    body: JSON.stringify({
      template_id: $("deployTemplate").value,
      count: 1,
      actor_id: "user",
    }),
  });
}

async function closeRoom() {
  if (!state.room || state.room.state !== "open") {
    setStatus("Open room required", true);
    return;
  }
  if (hasActiveAgents() && !confirm("Close room?")) return;
  await api(`/api/rooms/${state.room.id}/stop`, {
    method: "POST",
    body: JSON.stringify({
      actor_id: "user",
      reason: "user requested stop",
      force: false,
    }),
  });
  await loadRoom();
}

function renderTemplates() {
  $("templateList").innerHTML = state.templates
    .filter((template) => template.launch)
    .map(
      (template) => `
        <label class="templateCard" style="--accent:${safeColor(template.accent)}">
          <img src="${escapeHtml(template.avatarUrl)}" alt="" />
          <span>
            <strong>${escapeHtml(displayName(template.name))}</strong>
            <span>${escapeHtml(template.personality)}</span>
          </span>
          <input data-template-check type="checkbox" value="${escapeHtml(template.id)}" ${template.scope === "controller" ? "checked disabled" : ""} />
        </label>
      `,
    )
    .join("");
  syncTeamChecksFromTemplates();
}

function renderTeams() {
  if (!state.teams.length) {
    $("teamList").innerHTML = `<div class="emptyState">チームなし</div>`;
    return;
  }
  const templateNames = new Map(state.templates.map((template) => [template.id, displayName(template.name)]));
  $("teamList").innerHTML = state.teams
    .map((team) => {
      const members = team.templates.map((templateId) => templateNames.get(templateId)).join(" / ");
      return `
        <label class="teamCard">
          <span>
            <strong>${escapeHtml(displayName(team.name))}</strong>
            <span>${escapeHtml(team.summary)}</span>
            <small>${escapeHtml(members)}</small>
          </span>
          <input data-team-check type="checkbox" value="${escapeHtml(team.id)}" />
        </label>
      `;
    })
    .join("");
  syncTeamChecksFromTemplates();
}

function renderShareContexts() {
  const selected = new Set(state.room && state.room.share_contexts ? state.room.share_contexts : []);
  if (!state.shareContexts.length) {
    $("shareContextList").innerHTML = `<div class="emptyState">共有文脈なし</div>`;
    return;
  }
  $("shareContextList").innerHTML = state.shareContexts
    .map(
      (context) => `
        <label class="contextOption">
          <input data-share-context-check type="checkbox" value="${escapeHtml(context.name)}" ${selected.has(context.name) ? "checked" : ""} />
          <span>${escapeHtml(context.name)}</span>
        </label>
      `,
    )
    .join("");
}

function renderDeploy() {
  const options = state.templates.filter((template) => template.launch && template.scope !== "controller");
  $("deployTemplate").innerHTML = options
    .map((template) => `<option value="${escapeHtml(template.id)}">${escapeHtml(displayName(template.name))}</option>`)
    .join("");
}

function renderRoom() {
  const room = state.room;
  $("activeRoom").textContent = room
    ? `${stateLabel(room.state)}${room.agent_posting_closed ? " · 静か" : ""} · ${state.messages.length}`
    : "Roomなし";
  $("roomState").textContent = room ? stateLabel(room.state) : "待機";
  $("tableState").textContent = room ? stateLabel(room.state) : "待機";
  $("setupState").textContent = room ? stateLabel(room.state) : "下書き";
  renderBrief(room);
  renderShareContexts();
  renderAgents(room ? room.agents : []);
  renderMeetingStatus(room);
  renderProgressStrip(room);
  renderRoster(room ? room.agents : []);
  renderMessageList("messages", state.messages, "発言なし", false);
  renderMessageList("controllerMessages", state.controllerMessages, "非公開発言なし", true);
  renderControls();
}

function renderBrief(room) {
  $("briefGoal").textContent = room && room.goal ? localizeText(room.goal) : "下書き";
  $("briefControllerTermination").textContent =
    room && room.controller_termination ? localizeText(room.controller_termination) : "下書き";
  $("briefAgentTermination").textContent = room && room.agent_termination ? localizeText(room.agent_termination) : "下書き";
  $("briefShareContexts").textContent =
    room && room.share_contexts && room.share_contexts.length ? room.share_contexts.join(", ") : "なし";
}

function renderMeetingStatus(room) {
  const status = room ? room.meeting_status : null;
  $("meetingPhase").textContent = status ? phaseLabel(status.phase) : "下書き";
  $("meetingTopic").textContent = status && status.topic ? localizeText(status.topic) : "なし";
  $("meetingSummary").textContent = status && status.summary ? localizeText(status.summary) : "保留";
  $("meetingNext").textContent = status && status.next ? localizeText(status.next) : "進行役";
  $("meetingStatusUpdated").textContent = status && status.updated_at ? formatTime(status.updated_at) : "保留";
  renderStatusList("meetingDecisions", status ? status.decisions : [], "なし");
  renderStatusList("meetingOpenQuestions", status ? status.open_questions : [], "なし");
  renderMeetingTransitions(room);
}

function renderMeetingTransitions(room) {
  const target = $("meetingTransitions");
  const history = meetingStatusHistory(room);
  if (!history.length) {
    target.innerHTML = `<div class="transitionEmpty">保留</div>`;
    return;
  }
  const currentIndex = history.length - 1;
  const path = history
    .map((entry, index) => {
      const label = phaseLabel(entry.phase);
      return index === currentIndex ? `&lt;&lt; ${escapeHtml(label)} &gt;&gt;` : escapeHtml(label);
    })
    .join(`<span class="transitionArrow"> &gt;&gt; </span>`);
  const items = history
    .map(
      (entry, index) => `
        <li class="${index === currentIndex ? "currentTransition" : ""}">
          <div>
            <strong>${escapeHtml(phaseLabel(entry.phase))}</strong>
            <time datetime="${escapeHtml(entry.updated_at || "")}">${escapeHtml(entry.updated_at ? formatTime(entry.updated_at) : "")}</time>
          </div>
          <p>${escapeHtml(localizeText(entry.summary || "要約なし"))}</p>
          ${entry.topic ? `<small>${escapeHtml(localizeText(entry.topic))}</small>` : ""}
        </li>
      `,
    )
    .join("");
  target.innerHTML = `
    <div class="transitionPath">${path}</div>
    <ol class="transitionHistory">${items}</ol>
  `;
}

function meetingStatusHistory(room) {
  if (!room) return [];
  const history = [];
  const seen = new Set();
  state.events
    .filter((event) => event.type === "room.status_updated" && event.payload)
    .forEach((event) => addStatusHistoryEntry(history, seen, event.payload, event.created_at));
  if (room.meeting_status) {
    addStatusHistoryEntry(
      history,
      seen,
      room.meeting_status,
      room.meeting_status.updated_at || room.created_at,
    );
  }
  return history.slice(-8);
}

function addStatusHistoryEntry(history, seen, status, createdAt) {
  const entry = {
    phase: status.phase,
    topic: status.topic,
    summary: status.summary,
    next: status.next,
    updated_at: status.updated_at || createdAt,
  };
  const key = statusSignature(entry);
  if (seen.has(key)) return;
  seen.add(key);
  history.push(entry);
}

function trackStatusEvent(room) {
  if (!room || !room.meeting_status) return;
  const status = room.meeting_status;
  const event = {
    id: `local-${status.updated_at || Date.now()}`,
    room_id: room.id,
    type: "room.status_updated",
    actor_id: "controller",
    target_id: room.id,
    reason: null,
    payload: { ...status },
    created_at: status.updated_at || new Date().toISOString(),
  };
  const key = statusSignature(event.payload);
  const exists = state.events.some(
    (item) => item.type === "room.status_updated" && item.payload && statusSignature(item.payload) === key,
  );
  if (!exists) state.events.push(event);
}

function statusSignature(status) {
  return [
    status.phase || "",
    status.topic || "",
    status.summary || "",
    status.next || "",
    status.updated_at || "",
  ].join("\n");
}

function renderStatusList(targetId, items, emptyText) {
  const list = $(targetId);
  if (!items || !items.length) {
    list.classList.add("emptyList");
    list.innerHTML = `<li>${escapeHtml(emptyText)}</li>`;
    return;
  }
  list.classList.remove("emptyList");
  list.innerHTML = items.map((item) => `<li>${escapeHtml(localizeText(item))}</li>`).join("");
}

function renderProgressStrip(room) {
  if (!room) {
    $("progressStrip").textContent = "進捗";
    return;
  }
  const status = room.meeting_status;
  const agents = room.agents || [];
  const activeCount = agents.filter((agent) => ["starting", "active", "speaking", "idle"].includes(agent.state)).length;
  const doneCount = agents.filter((agent) => agent.state === "done").length;
  const mutedCount = room.muted_agent_ids ? room.muted_agent_ids.length : 0;
  const discussion = room.agent_posting_closed ? "静か" : "公開";
  const parts = [
    `${stateLabel(room.state)} / ${status ? phaseLabel(status.phase) : "状態"}`,
    `Agent ${agents.length}`,
    `稼働 ${activeCount}`,
    `完了 ${doneCount}`,
    discussion,
  ];
  if (mutedCount) parts.push(`ミュート ${mutedCount}`);
  if (status && status.next) parts.push(`Next: ${localizeText(status.next)}`);
  $("progressStrip").textContent = parts.join(" · ");
}

function renderControls() {
  const room = state.room;
  document.body.dataset.roomState = room ? room.state : "none";
  const isDraft = room && room.state === "draft";
  const isOpen = room && room.state === "open";
  const hasRequiredInput = Boolean(
    room && $("goal").value.trim() && $("controllerTermination").value.trim() && $("agentTermination").value.trim(),
  );
  const pending = Boolean(state.pendingAction);
  $("startRoom").disabled = !isDraft || !hasRequiredInput || pending;
  $("deployAgent").disabled = !isOpen || pending || !state.tmux || !state.tmux.inside_tmux || !$("deployTemplate").value;
  $("closeRoom").disabled = !isOpen || pending;
  $("newRoom").disabled = pending;
  $("refresh").disabled = pending;
  $("selectAllTemplates").disabled = !isDraft || pending;
  $("clearTemplates").disabled = !isDraft || pending;
  $("sendMessage").disabled = !room || pending;
  $("sendControllerMessage").disabled = !room || pending;
  $("goal").disabled = !isDraft || pending;
  $("controllerTermination").disabled = !isDraft || pending;
  $("agentTermination").disabled = !isDraft || pending;
  $("deployTemplate").disabled = !isOpen || pending;
  document.querySelectorAll("[data-share-context-check]").forEach((input) => {
    input.disabled = !isDraft || pending;
  });
  document.querySelectorAll("[data-team-check]").forEach((input) => {
    input.disabled = !isDraft || pending;
  });
  document.querySelectorAll("[data-template-check]").forEach((input) => {
    const isController = input.value === "controller";
    input.disabled = isController || !isDraft || pending;
  });
  $("startRoom").textContent = state.pendingAction === "Start" ? "Starting" : "Start";
  $("deployAgent").textContent = state.pendingAction === "Deploy" ? "Deploying" : "Deploy";
  $("closeRoom").textContent = state.pendingAction === "Close" ? "Closing" : "Close";
  $("newRoom").textContent = state.pendingAction === "New" ? "Creating" : "New";
  updateTemplateCount();
  renderStatus();
  renderPills();
}

function setTemplateChecks(checked) {
  document.querySelectorAll("[data-template-check]").forEach((input) => {
    if (input.value !== "controller") input.checked = checked;
  });
  syncTeamChecksFromTemplates();
  renderControls();
}

function handleTeamChange(event) {
  const input = event.target;
  if (!input.matches("[data-team-check]")) return;
  const team = state.teams.find((item) => item.id === input.value);
  if (!team) return;
  const templateInputs = new Map(
    [...document.querySelectorAll("[data-template-check]")].map((item) => [item.value, item]),
  );
  const selectedByTeams = selectedTeamTemplateIds();
  team.templates.forEach((templateId) => {
    const templateInput = templateInputs.get(templateId);
    if (templateInput && !templateInput.disabled) {
      templateInput.checked = input.checked || selectedByTeams.has(templateId);
    }
  });
  syncTeamChecksFromTemplates();
  renderControls();
}

function handleTemplateChange(event) {
  if (!event.target.matches("[data-template-check]")) return;
  syncTeamChecksFromTemplates();
  renderControls();
}

function syncTeamChecksFromTemplates() {
  const templateInputs = new Map(
    [...document.querySelectorAll("[data-template-check]")].map((input) => [input.value, input]),
  );
  document.querySelectorAll("[data-team-check]").forEach((input) => {
    const team = state.teams.find((item) => item.id === input.value);
    const memberInputs = team.templates.map((templateId) => templateInputs.get(templateId));
    const checkedCount = memberInputs.filter((memberInput) => memberInput.checked).length;
    input.checked = checkedCount === memberInputs.length;
    input.indeterminate = checkedCount > 0 && checkedCount < memberInputs.length;
  });
  updateTemplateCount();
}

function updateTemplateCount() {
  const checks = [...document.querySelectorAll("[data-template-check]")];
  const selected = checks.filter((input) => input.checked).length;
  $("templateCount").textContent = checks.length ? `${selected} / ${checks.length}` : "0";
  const teamChecks = [...document.querySelectorAll("[data-team-check]")];
  const selectedTeams = teamChecks.filter((input) => input.checked).length;
  $("teamCount").textContent = teamChecks.length ? `${selectedTeams} / ${teamChecks.length}` : "0";
}

function renderPills() {
  const room = state.room;
  const agents = room ? room.agents : [];
  const activeCount = agents.filter((agent) => ["starting", "active", "speaking", "idle"].includes(agent.state)).length;
  const doneCount = agents.filter((agent) => agent.state === "done").length;
  $("roomStatusPill").textContent = room
    ? `${stateLabel(room.state)}${room.agent_posting_closed ? " · 静か" : ""}`
    : "Roomなし";
  $("agentCountPill").textContent = `Agent ${agents.length} / 稼働 ${activeCount} / 完了 ${doneCount}`;
  $("activeAgentCount").textContent = `稼働 ${activeCount}`;
  $("socketStatusPill").textContent = socketLabel();
}

function renderTmux() {
  $("tmuxHelp").textContent = state.tmux && state.tmux.inside_tmux
    ? `${state.tmux.attach_command} | window: ${state.tmux.window}`
    : "tmux内で起動";
}

function renderStatus() {
  const status = $("statusLine");
  status.textContent = state.status.text;
  status.classList.toggle("error", state.status.error);
  status.classList.toggle("empty", !state.status.text);
}

function renderAgents(agents) {
  const layer = $("agentLayer");
  if (!agents.length) {
    layer.innerHTML = `<div class="emptySeat">Agentなし</div>`;
    return;
  }
  const radius = agents.length > 8 ? 39 : 42;
  layer.innerHTML = agents
    .map((agent, index) => {
      const angle = -90 + (360 / agents.length) * index;
      const rad = (angle * Math.PI) / 180;
      const x = 50 + Math.cos(rad) * radius;
      const y = 50 + Math.sin(rad) * radius;
      const bubble = state.bubbles.get(agent.id);
      const agentState = agentStateClass(agent.state);
      const signal = agentSignal(agent);
      const bubbleClass = y > 58 ? "bubbleUp" : x < 35 ? "bubbleRight" : x > 65 ? "bubbleLeft" : "bubbleDown";
      const label = seatLabel(agent, agents);
      return `
        <div class="seat state-${agentState} ${agents.length > 8 ? "compactSeat" : ""} ${bubbleClass}"
          style="--seat-x:${x}%;--seat-y:${y}%;--accent:${safeColor(agent.accent)}"
          aria-label="${escapeHtml(`${displayName(agent.name)} ${stateLabel(agent.state)}`)}">
          <div class="avatar">
            <img src="${escapeHtml(agent.avatar_url)}" alt="${escapeHtml(displayName(agent.name))}" />
            <span class="stateDot" aria-hidden="true"></span>
            <span class="moodGlyph mood-${signal.key}" title="${escapeHtml(signal.label)}" aria-label="${escapeHtml(signal.label)}">${escapeHtml(signal.glyph)}</span>
          </div>
          <div class="seatName">${escapeHtml(label)}</div>
          <div class="seatRole">${escapeHtml(roleLabel(agent.role))}</div>
          ${bubble ? `<div class="bubble">${escapeHtml(previewText(bubble.text, 180))}</div>` : ""}
        </div>
      `;
    })
    .join("");
}

function renderRoster(agents) {
  const roster = $("agentRoster");
  if (!agents.length) {
    roster.innerHTML = `<div class="emptyState">Agentなし</div>`;
    return;
  }
  roster.innerHTML = agents
    .map(
      (agent) => {
        const signal = agentSignal(agent);
        return `
        <article class="rosterItem state-${agentStateClass(agent.state)}" style="--accent:${safeColor(agent.accent)}">
          <img src="${escapeHtml(agent.avatar_url)}" alt="" />
          <div>
            <strong>${escapeHtml(displayName(agent.name))}</strong>
            <span>${escapeHtml(roleLabel(agent.role))}</span>
            <small>${escapeHtml(agent.pane_id || agent.id)}</small>
          </div>
          <div class="rosterState">
            <span class="moodBadge mood-${signal.key}" title="${escapeHtml(signal.label)}" aria-label="${escapeHtml(signal.label)}">${escapeHtml(signal.glyph)}</span>
            ${isMuted(agent) ? `<span class="stateBadge mutedBadge">ミュート</span>` : ""}
            <span class="stateBadge">${escapeHtml(stateLabel(agent.state))}</span>
          </div>
        </article>
      `;
      },
    )
    .join("");
}

function renderMessageList(targetId, items, emptyText, isPrivate) {
  const messages = $(targetId);
  const shouldScroll = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  if (!items.length) {
    messages.innerHTML = `<div class="emptyState">${emptyText}</div>`;
    return;
  }
  const agents = agentMap();
  messages.innerHTML = items.map((message) => messageArticle(message, agents, isPrivate)).join("");
  if (shouldScroll) scrollToLatest(messages);
}

function messageArticle(message, agents, isPrivate) {
  const agent = agents.get(message.actor_id);
  const accent = safeColor(agent ? agent.accent : actorAccent(message.actor_type));
  const tag = message.kind && message.kind !== "message" ? message.kind : message.actor_type;
  const actorName = agent ? displayName(agent.name) : displayName(message.actor_name);
  return `
    <article class="message ${messageClass(message)} ${isPrivate ? "privateMessage" : ""}" style="--accent:${accent}">
      ${messageAvatar(message, agent, accent)}
      <div class="messageBody">
        <div class="messageMeta">
          <strong>${escapeHtml(actorName)}</strong>
          <span><time datetime="${escapeHtml(message.created_at)}">${escapeHtml(formatTime(message.created_at))}</time> · #${message.id}</span>
        </div>
        <div class="messageTag">${escapeHtml(messageTagLabel(tag))}</div>
        <div class="messageText">${renderMessageText(message.text)}</div>
      </div>
    </article>
  `;
}

function messageAvatar(message, agent, accent) {
  if (agent && agent.avatar_url) {
    return `
      <div class="messageAvatar" style="--accent:${accent}">
        <img src="${escapeHtml(agent.avatar_url)}" alt="" />
      </div>
    `;
  }
  return `<div class="messageAvatar initialsAvatar" style="--accent:${accent}">${escapeHtml(initials(displayName(message.actor_name)))}</div>`;
}

function showBubble(message) {
  if (message.actor_type === "system") return;
  const previous = state.bubbleTimers.get(message.actor_id);
  if (previous) clearTimeout(previous);
  state.bubbles.set(message.actor_id, { id: message.id, text: message.text });
  const timer = setTimeout(() => {
    const current = state.bubbles.get(message.actor_id);
    if (current && current.id === message.id) state.bubbles.delete(message.actor_id);
    renderAgents(state.room ? state.room.agents : []);
  }, 7000);
  state.bubbleTimers.set(message.actor_id, timer);
}

function clearBubbles() {
  state.bubbleTimers.forEach((timer) => clearTimeout(timer));
  state.bubbleTimers.clear();
  state.bubbles.clear();
}

function setActiveChat(chat) {
  state.activeChat = chat;
  const messagesActive = chat === "messages";
  $("messagesPanel").hidden = !messagesActive;
  $("controllerPanel").hidden = messagesActive;
  $("messagesPanel").classList.toggle("active", messagesActive);
  $("controllerPanel").classList.toggle("active", !messagesActive);
  $("messagesTab").classList.toggle("active", messagesActive);
  $("controllerTab").classList.toggle("active", !messagesActive);
  $("messagesTab").setAttribute("aria-selected", messagesActive ? "true" : "false");
  $("controllerTab").setAttribute("aria-selected", messagesActive ? "false" : "true");
}

function handleComposerKey(event, submit) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
}

function growComposer(event) {
  const field = event.target;
  field.style.height = "auto";
  field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
}

function scrollToLatest(messages) {
  const latest = messages.lastElementChild;
  if (!latest) return;
  if (latest.clientHeight > messages.clientHeight * 0.72) {
    messages.scrollTop = latest.offsetTop - messages.offsetTop - 10;
    return;
  }
  messages.scrollTop = messages.scrollHeight;
}

function setStatus(text, error) {
  state.status = { text, error };
  renderStatus();
}

function hasActiveAgents() {
  if (!state.room) return false;
  return state.room.agents.some((agent) => ["starting", "active", "speaking", "idle"].includes(agent.state));
}

function isMuted(agent) {
  return Boolean(state.room && state.room.muted_agent_ids && state.room.muted_agent_ids.includes(agent.id));
}

function selectedShareContexts() {
  return [...document.querySelectorAll("[data-share-context-check]:checked")].map((input) => input.value);
}

function selectedTeamTemplateIds() {
  const selectedTeamIds = new Set([...document.querySelectorAll("[data-team-check]:checked")].map((input) => input.value));
  return new Set(
    state.teams.filter((team) => selectedTeamIds.has(team.id)).flatMap((team) => team.templates),
  );
}

function agentMap() {
  return new Map((state.room ? state.room.agents : []).map((agent) => [agent.id, agent]));
}

function seatLabel(agent, agents) {
  const duplicates = agents.filter((item) => item.template_id === agent.template_id);
  const label = displayName(agent.short_name);
  if (duplicates.length < 2) return label;
  return `${label} ${agent.id.slice(-3)}`;
}

function agentSignal(agent) {
  const message = latestAgentMessage(agent.id);
  if (!message) return { key: "quiet", glyph: "-", label: "発言なし" };
  const text = message.text.toLowerCase();
  if (
    includesAny(text, [
      "反論",
      "異論",
      "懸念",
      "リスク",
      "留保",
      "ただし",
      "弱い",
      "詭弁",
      "risk",
      "concern",
      "weak",
      "however",
    ])
  ) {
    return { key: "challenge", glyph: "!", label: "反論" };
  }
  if (
    includesAny(text, [
      "調査",
      "確認",
      "不明",
      "未知",
      "仮説",
      "観点",
      "なぜ",
      "question",
      "unknown",
      "research",
      "verify",
      "explore",
    ])
  ) {
    return { key: "curious", glyph: "?", label: "探索" };
  }
  if (
    includesAny(text, [
      "同意",
      "確信",
      "明確",
      "結論",
      "十分",
      "妥当",
      "賛成",
      "agree",
      "confident",
      "clear",
      "settled",
    ])
  ) {
    return { key: "confident", glyph: "+", label: "確信" };
  }
  return { key: "steady", glyph: "=", label: "安定" };
}

function latestAgentMessage(agentId) {
  for (let index = state.messages.length - 1; index >= 0; index -= 1) {
    const message = state.messages[index];
    if (message.actor_id === agentId && message.actor_type !== "user") return message;
  }
  return null;
}

function includesAny(text, terms) {
  return terms.some((term) => text.includes(term));
}

function messageClass(message) {
  if (message.actor_type === "user") return "userMessage";
  if (message.actor_type === "controller") return "controllerMessage";
  if (message.actor_type === "system") return "systemMessage";
  return "agentMessage";
}

function actorAccent(actorType) {
  if (actorType === "user") return "#136F63";
  if (actorType === "controller") return "#254D70";
  if (actorType === "system") return "#607080";
  return "#48515A";
}

function stateLabel(value) {
  const key = String(value || "").trim();
  return STATE_LABELS[key] || key.replace(/^./, (letter) => letter.toUpperCase());
}

function phaseLabel(value) {
  const key = String(value || "").trim();
  const normalized = key.toLowerCase();
  return PHASE_LABELS[key] || PHASE_LABELS[normalized] || displayName(key);
}

function roleLabel(value) {
  const key = String(value || "").trim();
  return ROLE_LABELS[key] || displayName(key);
}

function messageTagLabel(value) {
  const key = String(value || "").trim();
  return MESSAGE_TAG_LABELS[key] || displayName(key);
}

function displayName(value) {
  const text = String(value || "").trim();
  return ACTOR_LABELS[text] || text;
}

function localizeText(value) {
  let text = String(value || "");
  Object.entries(ACTOR_LABELS)
    .sort((left, right) => right[0].length - left[0].length)
    .forEach(([source, label]) => {
      text = text.replaceAll(source, label);
    });
  return text;
}

function renderMessageText(value) {
  return renderMarkdown(localizeText(value));
}

function renderMarkdown(value) {
  const lines = String(value).replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  const blocks = [];
  const paragraph = [];
  let index = 0;
  let fence = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join("\n")).replaceAll("\n", "<br />")}</p>`);
    paragraph.length = 0;
  };

  while (index < lines.length) {
    const line = lines[index];

    if (fence) {
      if (/^```\s*$/.test(line)) {
        blocks.push(codeBlock(fence.lines.join("\n"), fence.language));
        fence = null;
      } else {
        fence.lines.push(line);
      }
      index += 1;
      continue;
    }

    const fenceMatch = line.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fenceMatch) {
      flushParagraph();
      fence = { language: fenceMatch[1], lines: [] };
      index += 1;
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      flushParagraph();
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    const unorderedMatch = line.match(/^\s*[-*]\s+(.+)$/);
    const orderedMatch = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unorderedMatch || orderedMatch) {
      flushParagraph();
      const ordered = Boolean(orderedMatch);
      const items = [];
      while (index < lines.length) {
        const match = ordered
          ? lines[index].match(/^\s*\d+\.\s+(.+)$/)
          : lines[index].match(/^\s*[-*]\s+(.+)$/);
        if (!match) break;
        items.push(`<li>${renderInlineMarkdown(match[1].trim())}</li>`);
        index += 1;
      }
      blocks.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }

    paragraph.push(line);
    index += 1;
  }

  flushParagraph();
  if (fence) blocks.push(codeBlock(fence.lines.join("\n"), fence.language));
  return blocks.join("");
}

function codeBlock(code, language) {
  const className = language ? ` class="language-${escapeHtml(language)}"` : "";
  return `<pre><code${className}>${escapeHtml(code)}</code></pre>`;
}

function renderInlineMarkdown(value) {
  const tokens = [];
  const marker = "\u0000";
  const stash = (html) => `${marker}${tokens.push(html) - 1}${marker}`;
  let text = String(value);

  text = text.replace(/`([^`\n]+)`/g, (_match, code) => stash(`<code>${escapeHtml(code)}</code>`));
  text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_match, label, url) =>
    stash(
      `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`,
    ),
  );

  text = escapeHtml(text)
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(/_([^_\n]+)_/g, "<em>$1</em>");

  return text.replace(/\u0000(\d+)\u0000/g, (_match, tokenIndex) => tokens[Number(tokenIndex)]);
}

function agentStateClass(value) {
  const normalized = String(value);
  return KNOWN_AGENT_STATES.has(normalized) ? normalized : "idle";
}

function socketLabel() {
  if (!state.ws) return "Socketなし";
  if (state.ws.readyState === WebSocket.OPEN) return "Socket接続";
  if (state.ws.readyState === WebSocket.CONNECTING) return "Socket接続中";
  return "Socketなし";
}

function initials(name) {
  return String(name || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function previewText(value, limit) {
  const text = String(value);
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function safeColor(value) {
  return /^#[0-9a-fA-F]{6}$/.test(String(value)) ? value : "#607080";
}

function errorMessage(body) {
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    return body;
  }
  return body;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot().catch((error) => {
  setStatus(error.message, true);
});
