/*
 * Переключатель юрлица для ролевых страниц.
 *
 * Активное юрлицо хранится на сервере в сессии, поэтому выбор переживает
 * перезагрузку и не зависит от localStorage. В режиме «Все юрлица» (сводный
 * просмотр для администратора группы) создание записей недоступно — кнопки
 * блокируются, а сервер дополнительно отвечает 400.
 */
(function () {
  const CREATE_BUTTON_IDS = [
    "addTaskBtn",
    "monitoringBtn",
    "createTaskBtn",
    "crewReportBtn",
    "addCrewReportBtn",
    "sendReportBtn",
  ];

  const state = { allEntities: false, scope: null };

  const token = () => localStorage.getItem("token") || "";

  /* Ролевые страницы используют разные обёртки шапки: topbar или director-header. */
  function topbar() {
    return (
      document.querySelector("header.topbar") ||
      document.querySelector(".topbar") ||
      document.querySelector(".director-header") ||
      document.querySelector("header")
    );
  }

  function actionsContainer(bar) {
    return (
      bar.querySelector(".right") ||
      bar.querySelector(".director-actions") ||
      bar
    );
  }

  function injectStyles() {
    if (document.getElementById("entityScopeStyles")) return;
    const style = document.createElement("style");
    style.id = "entityScopeStyles";
    style.textContent = `
      .entity-scope {
        margin-right: 8px;
        padding: 6px 10px;
        border-radius: 8px;
        border: 1px solid rgba(255,255,255,.35);
        background: rgba(255,255,255,.12);
        color: inherit;
        font-size: 13px;
        max-width: 220px;
      }
      .entity-scope option { color: #111; }
      .entity-scope-banner {
        background: #fff7ed;
        border-bottom: 1px solid #fdba74;
        color: #9a3412;
        padding: 8px 16px;
        font-size: 13px;
      }
      .entity-scope-blocked { opacity: .5; }
    `;
    document.head.appendChild(style);
  }

  function renderSelect(scope) {
    const bar = topbar();
    if (!bar) return;
    const right = actionsContainer(bar);

    let select = document.getElementById("entityScope");
    if (!select) {
      select = document.createElement("select");
      select.id = "entityScope";
      select.className = "entity-scope";
      select.title = "Юрлицо";
      const logout = right.querySelector("#logout");
      if (logout) right.insertBefore(select, logout);
      else right.appendChild(select);
    }

    select.innerHTML = "";

    if (scope.can_view_all) {
      const option = document.createElement("option");
      option.value = "__all__";
      option.textContent = "Все юрлица";
      if (scope.current.all_entities) option.selected = true;
      select.appendChild(option);
    }

    (scope.options || []).forEach(item => {
      const option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = item.short_name || item.name || `Юрлицо ${item.id}`;
      if (!scope.current.all_entities && item.id === scope.current.legal_entity_id) {
        option.selected = true;
      }
      select.appendChild(option);
    });

    select.onchange = async () => {
      const value = select.value === "__all__" ? null : Number(select.value);
      select.disabled = true;
      try {
        const response = await fetch("/api/session/legal-entity", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token(),
          },
          body: JSON.stringify({ legal_entity_id: value }),
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || "Не удалось переключить юрлицо");
        }
        window.location.reload();
      } catch (error) {
        alert(error.message || "Не удалось переключить юрлицо");
        select.disabled = false;
      }
    };
  }

  function renderBanner(scope) {
    const bar = topbar();
    if (!bar || document.getElementById("entityScopeBanner")) return;
    const banner = document.createElement("div");
    banner.id = "entityScopeBanner";
    banner.className = "entity-scope-banner";
    banner.textContent =
      "Режим «Все юрлица»: создание записей недоступно — выберите конкретное юрлицо в переключателе.";
    bar.insertAdjacentElement("afterend", banner);
  }

  function blockCreation() {
    CREATE_BUTTON_IDS.forEach(id => {
      const element = document.getElementById(id);
      if (element) element.classList.add("entity-scope-blocked");
    });
  }

  document.addEventListener(
    "click",
    event => {
      if (!state.allEntities) return;
      const target = event.target.closest("button, a, [role='button']");
      if (!target || !CREATE_BUTTON_IDS.includes(target.id)) return;
      event.preventDefault();
      event.stopPropagation();
      alert("Выберите конкретное юрлицо: в режиме «Все юрлица» создание недоступно.");
    },
    true
  );

  async function init() {
    let scope;
    try {
      const response = await fetch("/api/legal-entities", {
        headers: { "Authorization": "Bearer " + token() },
      });
      if (!response.ok) return;
      scope = await response.json();
    } catch (error) {
      return; // переключатель не критичен для работы страницы
    }

    state.scope = scope;
    state.allEntities = Boolean(scope.current && scope.current.all_entities);
    injectStyles();

    const optionCount = (scope.options || []).length + (scope.can_view_all ? 1 : 0);
    if (optionCount > 1) renderSelect(scope);
    if (state.allEntities) {
      renderBanner(scope);
      blockCreation();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
