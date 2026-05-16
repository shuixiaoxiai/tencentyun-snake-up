const loginForm = document.querySelector("#loginForm");
const usernameInput = document.querySelector("#username");
const passwordInput = document.querySelector("#password");
const loginMessage = document.querySelector("#loginMessage");

function setLoginMessage(text) {
  loginMessage.textContent = text;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) {
    setLoginMessage("请输入用户名和密码。");
    return;
  }
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "登录失败");
    }
    window.location.href = "/admin";
  } catch (error) {
    setLoginMessage(error.message);
  }
});
