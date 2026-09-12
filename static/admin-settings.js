(() => {
  const API_SETTINGS = async (url, options = {}) => {
    const token = localStorage.getItem("token");

    const headers = {
      ...(options.headers || {}),
      ...(token ? { "Authorization": `Bearer ${token}` } : {})
    };

    const res = await fetch(url, {
      ...options,
      headers
    });

    if (!res.ok) {
      let msg = `Ошибка ${res.status}`;
      try {
        const data = await res.json();
        msg = data.detail || msg;
      } catch {}
      throw new Error(msg);
    }

    return res.json();
  };

  const roleNames = {
    admin: "Администратор",
    director: "Директор",
    service: "Сервис"
  };

  const setStatus = (text, error = false) => {
    const el = document.getElementById("settingsStatus");
    if (!el) return;

    el.textContent = text || "";
    el.classList.toggle("error", error);
  };

  async function loadSettings() {
    const data = await API_SETTINGS("/api/settings");

    const photos = document.getElementById("settingPhotos");
    const videos = document.getElementById("settingVideos");

    if (photos) photos.checked = !!data.photos_enabled;
    if (videos) videos.checked = !!data.videos_enabled;
  }

  async function saveSetting(key, enabled) {
    try {
      await API_SETTINGS("/api/settings", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          key,
          value: enabled ? "1" : "0"
        })
      });

      setStatus("Настройка сохранена");
      setTimeout(() => setStatus(""), 1800);
    } catch (err) {
      setStatus(err.message || "Не удалось сохранить настройку", true);

      // Возвращаем предыдущее значение после ошибки.
      await loadSettings().catch(() => {});
    }
  }

  function renderUsers(users) {
    const container = document.getElementById("usersList");
    if (!container) return;

    if (!users.length) {
      container.innerHTML = `
        <div class="users-empty">
          Пользователи не найдены
        </div>
      `;
      return;
    }

    container.innerHTML = users.map(user => `
      <div class="user-row">
        <div class="user-main">
          <div class="user-login">${escapeHtml(user.login)}</div>
          <div class="user-role">${roleNames[user.role] || user.role}</div>
        </div>

        <button
          type="button"
          class="user-password-btn"
          data-user-id="${user.id}"
          data-user-login="${escapeHtml(user.login)}"
        >
          Сменить пароль
        </button>
      </div>
    `).join("");

    container.querySelectorAll(".user-password-btn").forEach(button => {
      button.addEventListener("click", () => {
        changePassword(
          Number(button.dataset.userId),
          button.dataset.userLogin
        );
      });
    });
  }

  async function loadUsers() {
    const users = await API_SETTINGS("/api/users");
    renderUsers(users);
  }

  async function changePassword(userId, login) {
    const password = prompt(`Новый пароль для пользователя «${login}»:`);

    if (password === null) return;

    if (!password.trim()) {
      setStatus("Пароль не может быть пустым", true);
      return;
    }

    try {
      await API_SETTINGS(`/api/users/${userId}/password`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          new_password: password
        })
      });

      setStatus(`Пароль пользователя «${login}» изменён`);
      setTimeout(() => setStatus(""), 2200);
    } catch (err) {
      setStatus(err.message || "Не удалось изменить пароль", true);
    }
  }

  async function createUser() {
    const login = document.getElementById("newUserLogin");
    const password = document.getElementById("newUserPassword");
    const role = document.getElementById("newUserRole");

    if (!login || !password || !role) return;

    const loginValue = login.value.trim();
    const passwordValue = password.value;

    if (!loginValue) {
      setStatus("Введите логин", true);
      login.focus();
      return;
    }

    if (!passwordValue.trim()) {
      setStatus("Введите пароль", true);
      password.focus();
      return;
    }

    try {
      await API_SETTINGS("/api/users", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          login: loginValue,
          password: passwordValue,
          role: role.value
        })
      });

      login.value = "";
      password.value = "";

      await loadUsers();

      setStatus(`Пользователь «${loginValue}» добавлен`);
      setTimeout(() => setStatus(""), 2200);
    } catch (err) {
      setStatus(err.message || "Не удалось создать пользователя", true);
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function initSettings() {
    const toggle = document.getElementById("settingsToggle");
    const panel = document.getElementById("settingsPanel");

    if (!toggle || !panel) return;

    let me;

    try {
      me = await API_SETTINGS("/api/me");
    } catch {
      return;
    }

    // Настройки доступны только администратору.
    if (!me || me.role !== "admin") {
      toggle.style.display = "none";
      panel.style.display = "none";
      return;
    }

    toggle.style.display = "";

    toggle.addEventListener("click", async () => {
      const isOpen = panel.classList.toggle("open");

      if (!isOpen) return;

      try {
        setStatus("Загрузка...");
        await Promise.all([
          loadSettings(),
          loadUsers()
        ]);
        setStatus("");
      } catch (err) {
        setStatus(err.message || "Не удалось загрузить настройки", true);
      }
    });

    const photos = document.getElementById("settingPhotos");
    const videos = document.getElementById("settingVideos");

    if (photos) {
      photos.addEventListener("change", () => {
        saveSetting("photos_enabled", photos.checked);
      });
    }

    if (videos) {
      videos.addEventListener("change", () => {
        saveSetting("videos_enabled", videos.checked);
      });
    }

    const createButton = document.getElementById("createUserBtn");

    if (createButton) {
      createButton.addEventListener("click", createUser);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettings);
  } else {
    initSettings();
  }
})();
