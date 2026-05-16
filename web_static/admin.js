const adminMessage = document.querySelector("#adminMessage");
const configForm = document.querySelector("#configForm");
const maxConcurrentInput = document.querySelector("#maxConcurrent");
const activeTasks = document.querySelector("#activeTasks");
const validPrefixes = document.querySelector("#validPrefixes");
const generate10 = document.querySelector("#generate10");
const generate50 = document.querySelector("#generate50");
const generatedIds = document.querySelector("#generatedIds");
const queryIds = document.querySelector("#queryIds");
const queryStatus = document.querySelector("#queryStatus");
const statusResult = document.querySelector("#statusResult");

function setAdminMessage(text) {
  adminMessage.textContent = text;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function renderConfig(config) {
  maxConcurrentInput.value = config.max_concurrent_tasks;
  activeTasks.textContent = config.active_tasks;
  validPrefixes.textContent = (config.valid_prefixes || []).join(", ");
}

async function loadConfig() {
  const config = await requestJson("/api/admin/config");
  renderConfig(config);
  setAdminMessage("配置已加载。");
}

async function saveConfig(event) {
  event.preventDefault();
  const value = Number(maxConcurrentInput.value);
  if (!Number.isInteger(value) || value < 1) {
    setAdminMessage("并发数必须是大于等于 1 的整数。");
    return;
  }
  const config = await requestJson("/api/admin/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ max_concurrent_tasks: value }),
  });
  renderConfig(config);
  setAdminMessage("并发设置已保存。");
}

async function generateIds(count) {
  const data = await requestJson("/api/admin/ids/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count }),
  });
  generatedIds.value = data.ids.join("\n");
  setAdminMessage(`已生成 ${data.count} 个 id。`);
}

async function queryStatuses() {
  const ids = queryIds.value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (!ids.length) {
    setAdminMessage("请输入要查询的 id。");
    return;
  }
  const data = await requestJson("/api/admin/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_ids: ids }),
  });
  statusResult.textContent = JSON.stringify(data.items, null, 2);
  setAdminMessage(`已查询 ${data.items.length} 个 id。`);
}

configForm.addEventListener("submit", (event) => {
  saveConfig(event).catch((error) => setAdminMessage(error.message));
});

generate10.addEventListener("click", () => {
  generateIds(10).catch((error) => setAdminMessage(error.message));
});

generate50.addEventListener("click", () => {
  generateIds(50).catch((error) => setAdminMessage(error.message));
});

queryStatus.addEventListener("click", () => {
  queryStatuses().catch((error) => setAdminMessage(error.message));
});

loadConfig().catch((error) => setAdminMessage(error.message));
