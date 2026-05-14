const form = document.querySelector("#taskForm");
const userIdInput = document.querySelector("#userId");
const startBtn = document.querySelector("#startBtn");
const queryBtn = document.querySelector("#queryBtn");
const cancelBtn = document.querySelector("#cancelBtn");
const messageEl = document.querySelector("#message");
const statusBadge = document.querySelector("#statusBadge");
const grid = document.querySelector(".grid");
const qrPanel = document.querySelector("#qrPanel");
const qrImage = document.querySelector("#qrImage");
const qrLoading = document.querySelector("#qrLoading");
const qrPlaceholder = document.querySelector("#qrPlaceholder");

const fields = {
  userId: document.querySelector("#stateUserId"),
  status: document.querySelector("#stateStatus"),
  next: document.querySelector("#stateNext"),
  remaining: document.querySelector("#stateRemaining"),
  message: document.querySelector("#stateMessage"),
  result: document.querySelector("#resultBox"),
};

let events = null;
let activeUserId = "";
const terminalStatuses = new Set(["success", "failed", "canceled"]);
const loginStatuses = new Set(["created", "waiting_login"]);

function setMessage(text) {
  messageEl.textContent = text;
}

function getUserId() {
  return userIdInput.value.trim();
}

function formatRemaining(seconds) {
  if (seconds === undefined || seconds === null) return "-";
  const total = Math.max(0, Number(seconds));
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function resetQr() {
  qrImage.removeAttribute("src");
  qrImage.classList.remove("visible");
  qrLoading.classList.add("hidden");
  qrPlaceholder.style.display = "";
}

function showQrLoading() {
  qrImage.removeAttribute("src");
  qrImage.classList.remove("visible");
  qrLoading.classList.remove("hidden");
  qrPlaceholder.style.display = "none";
}

function resetResult() {
  fields.result.textContent = "暂无结果";
}

function renderState(state) {
  const status = state.status || "";
  if (state.message) {
    setMessage(state.message);
  }
  fields.userId.textContent = state.user_id || "-";
  fields.status.textContent = status || "-";
  fields.next.textContent = state.next_seckill_time || "-";
  fields.remaining.textContent = formatRemaining(state.remaining_seconds);
  fields.message.textContent = state.message || "-";
  statusBadge.textContent = status || "未知";
  statusBadge.classList.toggle("active", Boolean(status));
  cancelBtn.disabled = !activeUserId || terminalStatuses.has(status);
  if (terminalStatuses.has(status) && events) {
    events.close();
    events = null;
  }

  if (state.result) {
    fields.result.textContent = JSON.stringify(state.result, null, 2);
  }

  if (!loginStatuses.has(status)) {
    qrPanel.classList.add("hidden");
    grid.classList.add("status-only");
  } else {
    qrPanel.classList.remove("hidden");
    grid.classList.remove("status-only");
  }

  if (state.qr_ready && activeUserId && loginStatuses.has(status)) {
    qrImage.src = `/api/tasks/${encodeURIComponent(activeUserId)}/qr?t=${Date.now()}`;
    qrImage.classList.add("visible");
    qrLoading.classList.add("hidden");
    qrPlaceholder.style.display = "none";
  } else if (activeUserId && loginStatuses.has(status)) {
    showQrLoading();
  } else {
    resetQr();
  }
}

function subscribe(userId) {
  if (events) events.close();
  activeUserId = userId;
  events = new EventSource(`/api/tasks/${encodeURIComponent(userId)}/events`);
  events.onmessage = (event) => {
    renderState(JSON.parse(event.data));
  };
  events.onerror = () => {
    setMessage("实时连接已断开，可点击查询刷新。");
    events.close();
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

async function createTask(userId) {
  startBtn.disabled = true;
  queryBtn.disabled = true;
  try {
    if (activeUserId !== userId) {
      resetQr();
      resetResult();
    }
    const state = await requestJson("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId }),
    });
    activeUserId = userId;
    renderState(state);
    if (!terminalStatuses.has(state.status)) {
      subscribe(userId);
    }
  } catch (error) {
    setMessage(error.message);
  } finally {
    startBtn.disabled = false;
    queryBtn.disabled = false;
  }
}

async function cancelTask(userId) {
  cancelBtn.disabled = true;
  try {
    const state = await requestJson(`/api/tasks/${encodeURIComponent(userId)}/cancel`, {
      method: "POST",
    });
    setMessage("抢购已取消。");
    renderState(state);
  } catch (error) {
    setMessage(error.message);
    cancelBtn.disabled = false;
  }
}

async function queryTask(userId) {
  queryBtn.disabled = true;
  try {
    if (activeUserId !== userId) {
      resetQr();
      resetResult();
    }
    const state = await requestJson(`/api/tasks/${encodeURIComponent(userId)}`);
    activeUserId = userId;
    renderState(state);
    if (!terminalStatuses.has(state.status)) {
      subscribe(userId);
    }
  } catch (error) {
    setMessage(error.message);
  } finally {
    queryBtn.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const userId = getUserId();
  if (!userId) {
    setMessage("请输入唯一 id。");
    return;
  }
  createTask(userId);
});

queryBtn.addEventListener("click", () => {
  const userId = getUserId();
  if (!userId) {
    setMessage("请输入要查询的 id。");
    return;
  }
  queryTask(userId);
});

cancelBtn.addEventListener("click", () => {
  const userId = activeUserId || getUserId();
  if (!userId) {
    setMessage("请输入要取消的 id。");
    return;
  }
  cancelTask(userId);
});
