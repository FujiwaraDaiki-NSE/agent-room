const state = {
  templates: [],
  room: null,
  messages: [],
  controllerMessages: [],
  ws: null,
  bubbles: new Map(),
};

const TERMINATION_TEMPLATE = {
  controller:
    "controllerがdecision phaseに入り、controllerを除く多数が同意し、最低3ラウンド以上の討論を行い、各agentが少なくとも1回は反論・留保・代替仮説のいずれかを提示し、meeting-state.md上で未解決の重要論点がないとcontrollerが判断したら。",
  agent:
    "controllerが終了と判断するまでdoneしない。各agentは最低1回、反論・留保・代替仮説・追加調査観点のいずれかを提示してから終了可能とする。",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body);
  }
  return response.json();
}

async function boot() {
  $("refresh").addEventListener("click", refresh);
  $("newRoom").addEventListener("click", resetRoom);
  $("startRoom").addEventListener("click", startRoom);
  $("sendMessage").addEventListener("click", sendMessage);
  $("sendControllerMessage").addEventListener("click", sendControllerMessage);
  $("deployAgent").addEventListener("click", deployAgent);
  $("closeRoom").addEventListener("click", closeRoom);
  $("messageText").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendMessage();
  });
  $("controllerText").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendControllerMessage();
  });
  await refresh();
}

async function refresh() {
  const [templates, tmux] = await Promise.all([api("/api/templates"), api("/api/tmux")]);
  state.templates = templates;
  renderTemplates();
  renderDeploy();
  $("tmuxHelp").textContent = tmux.inside_tmux
    ? `${tmux.attach_command} | window: ${tmux.window}`
    : "start inside tmux";
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
  state.bubbles.clear();
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
    templates: selected,
  };
  state.room = await api("/api/rooms", { method: "POST", body: JSON.stringify(payload) });
  connectRoom();
  await loadRoom();
}

async function loadRoom() {
  if (!state.room) {
    renderRoom();
    return;
  }
  state.room = await api(`/api/rooms/${state.room.id}`);
  syncRoomForm();
  state.messages = await api(`/api/rooms/${state.room.id}/messages`);
  state.controllerMessages = await api(`/api/rooms/${state.room.id}/controller/messages`);
  renderRoom();
}

function syncRoomForm() {
  if (!state.room || state.room.state !== "draft") return;
  $("goal").value = state.room.goal;
  if (state.room.controller_termination || state.room.agent_termination) {
    $("controllerTermination").value = state.room.controller_termination;
    $("agentTermination").value = state.room.agent_termination;
    return;
  }
  setTerminationTemplate();
}

function setTerminationTemplate() {
  $("controllerTermination").value = TERMINATION_TEMPLATE.controller;
  $("agentTermination").value = TERMINATION_TEMPLATE.agent;
}

function requireStartInputs() {
  const missing = [];
  if (!state.room) missing.push("Room state");
  if (!$("goal").value.trim()) missing.push("Goal");
  if (!$("controllerTermination").value.trim()) missing.push("Controller Termination");
  if (!$("agentTermination").value.trim()) missing.push("Agent Termination");
  if (missing.length > 0) {
    alert(`${missing.join(", ")} required`);
    return false;
  }
  return true;
}

function connectRoom() {
  if (state.ws) state.ws.close();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${protocol}://${location.host}/ws/rooms/${state.room.id}`);
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
      connectRoom();
      await loadRoom();
      return;
    }
    if (payload.room) state.room = payload.room;
    renderRoom();
  };
}

async function sendMessage() {
  if (!state.room) return;
  const text = $("messageText").value;
  if (!text.trim()) return;
  $("messageText").value = "";
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
}

async function sendControllerMessage() {
  if (!state.room) return;
  const text = $("controllerText").value;
  if (!text.trim()) return;
  $("controllerText").value = "";
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
}

async function deployAgent() {
  if (!state.room) return;
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
  if (!state.room) return;
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
        <label class="templateCard" style="--accent:${template.accent}">
          <img src="${template.avatarUrl}" alt="" />
          <span>
            <strong>${escapeHtml(template.name)}</strong>
            <span>${escapeHtml(template.personality)}</span>
          </span>
          <input data-template-check type="checkbox" value="${template.id}" ${template.scope === "controller" ? "checked disabled" : ""} />
        </label>
      `,
    )
    .join("");
}

function renderDeploy() {
  $("deployTemplate").innerHTML = state.templates
    .filter((template) => template.launch)
    .map((template) => `<option value="${template.id}">${escapeHtml(template.name)}</option>`)
    .join("");
}

function renderRoom() {
  const room = state.room;
  $("activeRoom").textContent = room ? room.name : "No room";
  $("roomState").textContent = room ? room.state : "Idle";
  renderBrief(room);
  renderAgents(room ? room.agents : []);
  renderMessages();
  renderControllerMessages();
}

function renderBrief(room) {
  $("briefGoal").textContent = room && room.goal ? room.goal : "Draft";
  $("briefControllerTermination").textContent =
    room && room.controller_termination ? room.controller_termination : "Draft";
  $("briefAgentTermination").textContent = room && room.agent_termination ? room.agent_termination : "Draft";
}

function renderAgents(agents) {
  const layer = $("agentLayer");
  if (!agents.length) {
    layer.innerHTML = "";
    return;
  }
  const radius = 43;
  layer.innerHTML = agents
    .map((agent, index) => {
      const angle = -90 + (360 / agents.length) * index;
      const rad = (angle * Math.PI) / 180;
      const x = 50 + Math.cos(rad) * radius;
      const y = 50 + Math.sin(rad) * radius;
      const bubble = state.bubbles.get(agent.id);
      return `
        <div class="seat ${agent.state}" style="left:${x}%;top:${y}%;--accent:${agent.accent}">
          <div class="avatar"><img src="${agent.avatar_url}" alt="" /></div>
          <div class="seatName">${escapeHtml(agent.short_name)}</div>
          ${bubble ? `<div class="bubble">${escapeHtml(bubble)}</div>` : ""}
        </div>
      `;
    })
    .join("");
}

function renderMessages() {
  const messages = $("messages");
  messages.innerHTML = state.messages
    .map(
      (message) => `
        <article class="message ${messageClass(message)}">
          ${messageAvatar(message)}
          <div class="messageBody">
            <div class="messageMeta">
              <strong>${escapeHtml(message.actor_name)}</strong>
              <span>#${message.id}</span>
            </div>
            <div class="messageText">${escapeHtml(message.text)}</div>
          </div>
        </article>
      `,
    )
    .join("");
  messages.scrollTop = messages.scrollHeight;
}

function renderControllerMessages() {
  const messages = $("controllerMessages");
  messages.innerHTML = state.controllerMessages
    .map(
      (message) => `
        <article class="message privateMessage ${messageClass(message)}">
          ${messageAvatar(message)}
          <div class="messageBody">
            <div class="messageMeta">
              <strong>${escapeHtml(message.actor_name)}</strong>
              <span>#${message.id}</span>
            </div>
            <div class="messageText">${escapeHtml(message.text)}</div>
          </div>
        </article>
      `,
    )
    .join("");
  messages.scrollTop = messages.scrollHeight;
}

function showBubble(message) {
  if (message.actor_type === "system") return;
  state.bubbles.set(message.actor_id, message.text);
  setTimeout(() => {
    state.bubbles.delete(message.actor_id);
    renderRoom();
  }, 7000);
}

function messageClass(message) {
  if (message.actor_type === "user") return "userMessage";
  if (message.actor_type === "controller") return "controllerMessage";
  if (message.actor_type === "system") return "systemMessage";
  return "agentMessage";
}

function messageAvatar(message) {
  const agent = state.room ? state.room.agents.find((item) => item.id === message.actor_id) : null;
  const accent = safeColor(agent ? agent.accent : actorAccent(message.actor_type));
  if (agent && agent.avatar_url) {
    return `
      <div class="messageAvatar" style="--accent:${accent}">
        <img src="${escapeHtml(agent.avatar_url)}" alt="" />
      </div>
    `;
  }
  return `<div class="messageAvatar" style="--accent:${accent}">${escapeHtml(initials(message.actor_name))}</div>`;
}

function actorAccent(actorType) {
  if (actorType === "user") return "#136F63";
  if (actorType === "controller") return "#254D70";
  if (actorType === "system") return "#607080";
  return "#48515A";
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

function safeColor(value) {
  return /^#[0-9a-fA-F]{6}$/.test(String(value)) ? value : "#607080";
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
  alert(error.message);
});
