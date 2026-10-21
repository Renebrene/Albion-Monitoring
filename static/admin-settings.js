(() => {
  const API_SETTINGS = (url, options = {}) => window.apiFetch(url, options);

  const roleNames = {
    admin: "Администратор",
    director: "Директор",
    service: "Сервис",
    crew: "Экипаж",
    head: "Руководитель",
    staff: "Бригада",
    administrator: "Администратор",
    curator: "Куратор"
  };

  const MIB = 1024 * 1024;
  const GIB = 1024 * 1024 * 1024;

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

    const photoQuota = document.getElementById("quotaPhotoMiB");
    const videoQuota = document.getElementById("quotaVideoMiB");
    const storageQuota = document.getElementById("quotaStorageGiB");
    if (photoQuota) photoQuota.value = Number(data.photo_max_upload_bytes) / MIB;
    if (videoQuota) videoQuota.value = Number(data.video_max_upload_bytes) / MIB;
    if (storageQuota) storageQuota.value = Number(data.uploads_quota_bytes) / GIB;

    renderQuotaUsage(data);
  }

  function renderQuotaUsage(data) {
    const el = document.getElementById("quotaUsage");
    if (!el) return;
    const used = Number(data.uploads_usage_bytes) || 0;
    const quota = Number(data.uploads_quota_bytes) || 0;
    const formatGiB = bytes => (bytes / GIB).toFixed(2);
    el.textContent = quota
      ? `Использовано ${formatGiB(used)} / ${formatGiB(quota)} GiB`
      : `Использовано ${formatGiB(used)} GiB · общая квота не ограничена`;
    el.classList.toggle("over-quota", !!quota && used > quota);
    if (data.uploads_quota_exceeded) {
      el.textContent += " · квота превышена: новые файлы не принимаются";
    }
  }

  async function saveQuotas() {
    const fields = [
      ["quotaPhotoMiB", "photo_max_upload_bytes", MIB, 100],
      ["quotaVideoMiB", "video_max_upload_bytes", MIB, 100],
      ["quotaStorageGiB", "uploads_quota_bytes", GIB, 10240],
    ];
    try {
      const values = {};
      for (const [id, key, multiplier, max] of fields) {
        const input = document.getElementById(id);
        if (!input || input.value.trim() === "") {
          throw new Error("Заполните все значения квот");
        }
        const number = Number(input.value);
        if (!Number.isFinite(number) || number < 0 || number > max) {
          throw new Error(`Проверьте значение «${input?.previousElementSibling?.textContent || id}»`);
        }
        values[key] = String(Math.round(number * multiplier));
      }
      await API_SETTINGS("/api/settings/quotas", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      await loadSettings();
      setStatus("Квоты сохранены");
    } catch (err) {
      setStatus(err.message || "Не удалось сохранить квоты", true);
      await loadSettings().catch(() => {});
    }
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

  let passwordUserId = null;
  let passwordUserLogin = "";

  function passwordModal() {
    return document.getElementById("passwordModal");
  }

  function showPasswordError(message) {
    const error = document.getElementById("passwordError");
    if (!error) return;

    error.textContent = message || "";
    error.style.display = message ? "block" : "none";
  }

  function openPasswordModal(userId, login) {
    const modal = passwordModal();
    if (!modal) return;

    passwordUserId = userId;
    passwordUserLogin = login;

    const user = document.getElementById("passwordUser");
    if (user) user.textContent = `Пользователь: ${login}`;

    ["passwordNew", "passwordConfirm"].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = "";
    });

    showPasswordError("");
    modal.classList.add("show");

    document.getElementById("passwordNew")?.focus();
  }

  window.closePasswordModal = () => {
    const modal = passwordModal();
    if (modal) modal.classList.remove("show");

    passwordUserId = null;
    passwordUserLogin = "";
    showPasswordError("");
  };

  window.submitPasswordChange = async () => {
    if (passwordUserId === null) return;

    const input = document.getElementById("passwordNew");
    const confirm = document.getElementById("passwordConfirm");
    const button = document.getElementById("passwordSaveBtn");

    const password = input ? input.value : "";
    const confirmation = confirm ? confirm.value : "";

    if (!password.trim()) {
      showPasswordError("Введите новый пароль");
      input?.focus();
      return;
    }

    if (password !== confirmation) {
      showPasswordError("Пароли не совпадают");
      confirm?.focus();
      return;
    }

    showPasswordError("");

    if (button) {
      button.disabled = true;
      button.textContent = "Сохранение...";
    }

    try {
      await API_SETTINGS(`/api/users/${passwordUserId}/password`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          new_password: password
        })
      });

      const login = passwordUserLogin;
      window.closePasswordModal();
      setStatus(`Пароль пользователя «${login}» изменён`);
      setTimeout(() => setStatus(""), 2200);
    } catch (err) {
      showPasswordError(err.message || "Не удалось изменить пароль");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Сохранить";
      }
    }
  };

  function changePassword(userId, login) {
    openPasswordModal(userId, login);
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

    // Настройки доступны администратору и профилю administrator.
    if (!me || window.getBaseRole(me.role) !== "admin") {
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
    if (createButton) createButton.addEventListener("click", createUser);

    const quotaSaveButton = document.getElementById("saveQuotasBtn");
    if (quotaSaveButton) quotaSaveButton.addEventListener("click", saveQuotas);

    const modal = passwordModal();
    if (modal) {
      modal.addEventListener("click", event => {
        if (event.target === modal) window.closePasswordModal();
      });
    }

    ["passwordNew", "passwordConfirm"].forEach(id => {
      document.getElementById(id)?.addEventListener("keydown", event => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        window.submitPasswordChange();
      });
    });

    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && modal?.classList.contains("show")) {
        window.closePasswordModal();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettings);
  } else {
    initSettings();
  }
})();
