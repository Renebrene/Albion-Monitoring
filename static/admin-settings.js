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

  // ---------- Юрлица группы и доступ пользователей ----------
  // Раздел доступен только групповому администратору в режиме «Все юрлица»:
  // там неизвестно «текущее» юрлицо, и администрирование группы идёт целиком.
  const groupScope = {
    allEntities: false,
    entities: []
  };

  function isGroupScope() {
    return groupScope.allEntities;
  }

  async function loadGroupScope() {
    try {
      const scope = await API_SETTINGS("/api/legal-entities");
      groupScope.allEntities = Boolean(scope.current && scope.current.all_entities);
    } catch {
      groupScope.allEntities = false;
    }
    return groupScope;
  }

  async function loadEntities() {
    const section = document.getElementById("entitiesSection");
    if (!section) return;

    if (!isGroupScope()) {
      section.style.display = "none";
      groupScope.entities = [];
      return;
    }

    section.style.display = "";
    groupScope.entities = await API_SETTINGS("/api/admin/legal-entities");
    renderEntities(groupScope.entities);
    renderEntityOptions();
  }

  function renderEntityOptions() {
    const select = document.getElementById("newUserEntity");
    if (!select) return;

    const active = groupScope.entities.filter(entity => entity.active);
    if (!isGroupScope() || active.length < 2) {
      select.style.display = "none";
      return;
    }

    select.style.display = "";
    select.innerHTML = active
      .map(entity => `<option value="${entity.id}">${escapeHtml(entityLabel(entity))}</option>`)
      .join("");
  }

  function entityLabel(entity) {
    const short = (entity.short_name || "").trim();
    return short || entity.name;
  }

  function entityName(entityId) {
    const entity = groupScope.entities.find(item => item.id === entityId);
    return entity ? entityLabel(entity) : `Юрлицо ${entityId}`;
  }

  function renderEntities(entities) {
    const container = document.getElementById("entitiesList");
    if (!container) return;

    if (!entities.length) {
      container.innerHTML = '<div class="users-empty">Юрлица не найдены</div>';
      return;
    }

    container.innerHTML = entities.map(entity => `
      <div class="user-row" data-entity-row="${entity.id}">
        <div class="user-main">
          <div class="user-login">
            ${escapeHtml(entity.name)}
            ${entity.active ? "" : '<span class="entity-badge off">выключено</span>'}
          </div>
          <div class="user-role">
            ${escapeHtml(entityLabel(entity))}${entity.inn ? " · ИНН " + escapeHtml(entity.inn) : ""}
            · пользователей: ${entity.usage.users}, адресов: ${entity.usage.addresses},
            заданий: ${entity.usage.tasks}, отчётов: ${entity.usage.reports}
          </div>
        </div>
        <div class="user-actions">
          <button type="button" class="user-password-btn" data-entity-edit="${entity.id}">Изменить</button>
          <button type="button" class="user-password-btn" data-entity-toggle="${entity.id}">
            ${entity.active ? "Выключить" : "Включить"}
          </button>
        </div>
      </div>
    `).join("");

    container.querySelectorAll("[data-entity-edit]").forEach(button => {
      button.addEventListener("click", () => openEntityEditor(Number(button.dataset.entityEdit)));
    });
    container.querySelectorAll("[data-entity-toggle]").forEach(button => {
      button.addEventListener("click", async () => {
        const entityId = Number(button.dataset.entityToggle);
        const entity = groupScope.entities.find(item => item.id === entityId);
        if (!entity) return;
        if (entity.active && !confirm(`Выключить юрлицо «${entityLabel(entity)}»? Данные сохранятся.`)) {
          return;
        }
        try {
          await API_SETTINGS(`/api/admin/legal-entities/${entityId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active: !entity.active })
          });
          await loadEntities();
          await loadUsers();
          setStatus(entity.active ? "Юрлицо выключено" : "Юрлицо включено");
          setTimeout(() => setStatus(""), 1800);
        } catch (err) {
          setStatus(err.message || "Не удалось изменить юрлицо", true);
        }
      });
    });
  }

  function openEntityEditor(entityId) {
    const entity = groupScope.entities.find(item => item.id === entityId);
    const row = document.querySelector(`[data-entity-row="${entityId}"]`);
    if (!entity || !row) return;

    const editor = document.createElement("div");
    editor.className = "entity-editor";
    editor.innerHTML = `
      <div class="entity-inline-form">
        <input type="text" id="entityEditName" value="${escapeHtml(entity.name)}" placeholder="Название">
        <input type="text" id="entityEditShort" value="${escapeHtml(entity.short_name || "")}" placeholder="Короткое имя">
        <input type="text" id="entityEditInn" value="${escapeHtml(entity.inn || "")}" placeholder="ИНН">
        <button type="button" class="settings-create-btn" id="entityEditSave">Сохранить</button>
        <button type="button" class="user-password-btn" id="entityEditCancel">Отмена</button>
      </div>
    `;
    row.appendChild(editor);

    editor.querySelector("#entityEditCancel").addEventListener("click", () => editor.remove());
    editor.querySelector("#entityEditSave").addEventListener("click", async () => {
      try {
        await API_SETTINGS(`/api/admin/legal-entities/${entityId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: editor.querySelector("#entityEditName").value,
            short_name: editor.querySelector("#entityEditShort").value,
            inn: editor.querySelector("#entityEditInn").value
          })
        });
        await loadEntities();
        setStatus("Юрлицо сохранено");
        setTimeout(() => setStatus(""), 1800);
      } catch (err) {
        setStatus(err.message || "Не удалось сохранить юрлицо", true);
      }
    });
  }

  async function createEntity() {
    const name = document.getElementById("newEntityName");
    const short = document.getElementById("newEntityShort");
    const inn = document.getElementById("newEntityInn");
    if (!name) return;

    if (!name.value.trim()) {
      setStatus("Введите название юрлица", true);
      name.focus();
      return;
    }

    try {
      await API_SETTINGS("/api/admin/legal-entities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.value,
          short_name: short ? short.value : "",
          inn: inn ? inn.value : ""
        })
      });
      name.value = "";
      if (short) short.value = "";
      if (inn) inn.value = "";
      await loadEntities();
      setStatus("Юрлицо добавлено");
      setTimeout(() => setStatus(""), 1800);
    } catch (err) {
      setStatus(err.message || "Не удалось добавить юрлицо", true);
    }
  }

  let managedUsers = [];

  function renderManagedUsers(users) {
    const container = document.getElementById("usersList");
    if (!container) return;

    managedUsers = users;

    if (!users.length) {
      container.innerHTML = '<div class="users-empty">Пользователи не найдены</div>';
      return;
    }

    container.innerHTML = users.map(user => {
      const badges = (user.entities || []).length
        ? (user.entities || [])
            .map(item => {
              const entity = groupScope.entities.find(entry => entry.id === item.legal_entity_id);
              const inactive = entity && !entity.active;
              const mark = item.is_default ? "★ " : "";
              return `<span class="entity-badge${inactive ? " off" : ""}">${mark}${escapeHtml(
                entity ? entityLabel(entity) : `Юрлицо ${item.legal_entity_id}`
              )}</span>`;
            })
            .join("")
        : '<span class="entity-badge off">нет доступа</span>';

      return `
        <div class="user-row" data-user-row="${user.id}">
          <div class="user-main">
            <div class="user-login">${escapeHtml(user.login)}</div>
            <div class="user-role">${roleNames[user.role] || user.role}</div>
            <div class="entity-badges">${badges}</div>
          </div>
          <div class="user-actions">
            <button type="button" class="user-password-btn" data-user-access="${user.id}">Доступ</button>
            <button
              type="button"
              class="user-password-btn"
              data-user-id="${user.id}"
              data-user-login="${escapeHtml(user.login)}"
            >Сменить пароль</button>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".user-password-btn[data-user-id]").forEach(button => {
      button.addEventListener("click", () => {
        changePassword(Number(button.dataset.userId), button.dataset.userLogin);
      });
    });

    container.querySelectorAll("[data-user-access]").forEach(button => {
      button.addEventListener("click", () => openAccessEditor(Number(button.dataset.userAccess)));
    });
  }

  function openAccessEditor(userId) {
    const row = document.querySelector(`[data-user-row="${userId}"]`);
    if (!row) return;
    if (row.querySelector(".entity-editor")) {
      row.querySelector(".entity-editor").remove();
      return;
    }

    const activeEntities = groupScope.entities.filter(entity => entity.active);
    const editor = document.createElement("div");
    editor.className = "entity-editor";
    editor.innerHTML = `
      <div class="entity-editor-grid">
        ${activeEntities.map(entity => `
          <label>
            <input type="checkbox" data-access-entity="${entity.id}">
            ${escapeHtml(entityLabel(entity))}
            <input type="radio" name="accessDefault${userId}" value="${entity.id}" title="Основное юрлицо">
            <span class="user-role">основное</span>
          </label>
        `).join("")}
      </div>
      <div class="entity-editor-row">
        <select id="accessRole${userId}">
          ${Object.entries(roleNames)
            .map(([value, label]) => `<option value="${value}">${label}</option>`)
            .join("")}
        </select>
        <button type="button" class="settings-create-btn" id="accessSave${userId}">Сохранить доступ</button>
        <button type="button" class="user-password-btn" id="accessCancel${userId}">Отмена</button>
      </div>
    `;
    row.appendChild(editor);

    // Показываем текущее состояние: отмеченные юрлица, роль и основное юрлицо.
    const user = managedUsers.find(item => item.id === userId);
    const current = (user && user.entities) || [];
    const currentIds = new Set(current.map(item => item.legal_entity_id));
    editor.querySelectorAll("[data-access-entity]").forEach(checkbox => {
      const entityId = Number(checkbox.dataset.accessEntity);
      checkbox.checked = currentIds.has(entityId);
      const radio = editor.querySelector(
        `input[name="accessDefault${userId}"][value="${entityId}"]`
      );
      if (radio) {
        radio.checked = current.some(
          item => item.legal_entity_id === entityId && item.is_default
        );
        radio.disabled = !currentIds.has(entityId);
        checkbox.addEventListener("change", () => {
          radio.disabled = !checkbox.checked;
          if (!checkbox.checked && radio.checked) radio.checked = false;
        });
      }
    });

    const roleSelect = editor.querySelector(`#accessRole${userId}`);
    if (roleSelect && user && user.role) roleSelect.value = user.role;

    editor.querySelector(`#accessCancel${userId}`).addEventListener("click", () => editor.remove());
    editor.querySelector(`#accessSave${userId}`).addEventListener("click", () => saveAccess(userId, editor));
  }

  async function saveAccess(userId, editor) {
    const role = editor.querySelector(`#accessRole${userId}`).value;
    const entities = [];
    let defaultEntityId = null;

    editor.querySelectorAll("[data-access-entity]").forEach(checkbox => {
      if (!checkbox.checked) return;
      const entityId = Number(checkbox.dataset.accessEntity);
      entities.push({ legal_entity_id: entityId, is_default: false });
    });

    const checkedRadios = editor.querySelectorAll(`input[name="accessDefault${userId}"]:checked`);
    if (checkedRadios.length) {
      defaultEntityId = Number(checkedRadios[0].value);
    }

    if (!entities.length) {
      setStatus("Выберите хотя бы одно юрлицо", true);
      return;
    }
    if (!defaultEntityId || !entities.some(item => item.legal_entity_id === defaultEntityId)) {
      defaultEntityId = entities[0].legal_entity_id;
    }
    entities.forEach(item => {
      item.is_default = item.legal_entity_id === defaultEntityId;
    });

    try {
      await API_SETTINGS(`/api/admin/users/${userId}/entities`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, entities })
      });
      await loadUsers();
      setStatus("Доступ обновлён");
      setTimeout(() => setStatus(""), 1800);
    } catch (err) {
      setStatus(err.message || "Не удалось сохранить доступ", true);
    }
  }

  async function loadUsers() {
    if (isGroupScope()) {
      await loadEntities();
      const users = await API_SETTINGS("/api/admin/users");
      renderManagedUsers(users);
      return;
    }
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

    const entitySelect = document.getElementById("newUserEntity");
    const payload = {
      login: loginValue,
      password: passwordValue,
      role: role.value
    };

    // В сводном режиме юрлицо выбирается явно: активного юрлица нет.
    if (isGroupScope() && entitySelect && entitySelect.style.display !== "none") {
      payload.legal_entity_id = Number(entitySelect.value);
    }

    try {
      await API_SETTINGS("/api/users", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
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

    // Режим «Все юрлица» включает раздел администрирования группы.
    await loadGroupScope();

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

    const createEntityButton = document.getElementById("createEntityBtn");
    if (createEntityButton) createEntityButton.addEventListener("click", createEntity);

    // Переключатель юрлица перезагружает страницу, поэтому режим узнаём заново
    // после каждого открытия панели.
    if (isGroupScope()) {
      loadEntities().catch(() => {});
    }

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
