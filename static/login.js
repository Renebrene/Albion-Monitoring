
const toast = (msg, err = false) => {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.style.display = "block";
  setTimeout(() => t.style.display = "none", 3000);
};

document.getElementById("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  btn.textContent = "Вход...";

  const fd = new FormData(e.target);
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login: fd.get("login"), password: fd.get("password") }),
    });
    
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || "Ошибка входа");
    }
    
    const d = await r.json();
    localStorage.setItem("token", d.token);
    localStorage.setItem("role", d.role);
    localStorage.setItem("role_label", d.role_label);
    localStorage.setItem("login", d.login);
    
    // Редирект на главную страницу роли
    location.href = d.home;
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
    btn.textContent = "Войти";
  }
});
