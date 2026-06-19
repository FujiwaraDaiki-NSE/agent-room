const state = {
  templates: [],
  room: null,
  messages: [],
  ws: null,
  bubbles: new Map(),
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
  $("startRoom").addEventListener("click", startRoom);
  $("sendMessage").addEventListener("click", sendMessage);
  $("deployAgent").addEventListener("click", deployAgent);
  $("stopRoom").addEventListener("click", stopRoom);
  $("messageText").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendMessage();
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
  if (!state.room && rooms.length > 0) {
    state.room = rooms[rooms.length - 1];
    connectRoom();
  }
  await loadRoom();
}

async function startRoom() {
  const selected = [...document.querySelectorAll("[data-template-check]:checked")].map((input) => input.value);
  const payload = {
    name: $("roomName").value,
    goal: $("goal").value,
    termination: $("termination").value,
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
  state.messages = await api(`/api/rooms/${state.room.id}/messages`);
  renderRoom();
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

async function stopRoom() {
  if (!state.room) return;
  await api(`/api/rooms/${state.room.id}/stop`, {
    method: "POST",
    body: JSON.stringify({
      actor_id: "user",
      reason: "user requested stop",
      force: false,
    }),
  });
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
          <input data-template-check type="checkbox" value="${template.id}" ${template.scope === "controller" ? "checked" : ""} />
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
  renderAgents(room ? room.agents : []);
  renderMessages();
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
        <article class="message">
          <div class="messageMeta">
            <strong>${escapeHtml(message.actor_name)}</strong>
            <span>#${message.id}</span>
          </div>
          <div class="messageText">${escapeHtml(message.text)}</div>
        </article>
      `,
    )
    .join("");
  messages.scrollTop = messages.scrollHeight;
}

function showBubble(message) {
  if (message.actor_type === "system") return;
  state.bubbles.set(message.actor_id, message.text.slice(0, 120));
  setTimeout(() => {
    state.bubbles.delete(message.actor_id);
    renderRoom();
  }, 7000);
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
