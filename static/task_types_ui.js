/*
 * Типы заданий: интерфейс для формы создания.
 *
 * Загружает активные типы (GET /api/task-types), рисует селект типа и поля
 * типа в контейнере формы, собирает значения для отправки.
 */
(function () {
  const state = { loaded: false, types: [], selected: null };

  const esc = value =>
    String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    }[ch]));

  async function ensureLoaded(force = false) {
    if (state.loaded && !force) return state.types;
    const response = await window.apiFetch("/api/task-types");
    state.types = Array.isArray(response) ? response : [];
    state.loaded = true;
    return state.types;
  }

  function fieldTypeLabel(type) {
    return {
      text: "текст",
      number: "число",
      date: "дата",
      select: "список",
    }[type] || type;
  }

  function currentType() {
    return state.types.find(item => String(item.id) === String(state.selected)) || null;
  }

  function renderFields(container, type) {
    if (!container) return;
    container.innerHTML = "";
    const fields = (type && type.fields) || [];
    if (!fields.length) {
      container.style.display = "none";
      return;
    }
    container.style.display = "";

    fields.forEach(field => {
      const label = document.createElement("label");
      label.textContent = field.label + (field.required ? " *" : "");

      let input;
      if (field.type === "select") {
        input = document.createElement("select");
        input.appendChild(new Option("— выберите —", ""));
        (field.options || []).forEach(option => input.appendChild(new Option(option, option)));
      } else if (field.type === "number") {
        input = document.createElement("input");
        input.type = "number";
        input.step = "any";
      } else if (field.type === "date") {
        input = document.createElement("input");
        input.type = "date";
      } else {
        input = document.createElement("input");
        input.type = "text";
      }

      input.dataset.fieldKey = field.key;
      input.dataset.fieldType = field.type;
      if (field.required) input.required = true;
      input.id = `taskTypeField_${field.key}`;
      label.appendChild(input);
      container.appendChild(label);
    });
  }

  async function render(selectId, containerId) {
    const select = document.getElementById(selectId);
    const container = document.getElementById(containerId);
    if (!select || !container) return;

    let types = [];
    try {
      types = await ensureLoaded();
    } catch (error) {
      select.closest(".modal")?.classList.remove("show");
      return; // форма работает и без типов
    }

    if (!types.length) {
      select.style.display = "none";
      container.style.display = "none";
      return;
    }

    select.style.display = "";
    select.innerHTML = "";
    types.forEach(type => {
      select.appendChild(new Option(type.name, String(type.id)));
    });

    // Нет полей — контейнер не показываем, выбор типа хранится в state.
    state.selected = select.value;
    renderFields(container, currentType());

    select.onchange = () => {
      state.selected = select.value;
      renderFields(container, currentType());
    };
  }

  function collect() {
    const type = currentType();
    if (!type) return null;

    const values = {};
    const fields = type.fields || [];
    fields.forEach(field => {
      const input = document.getElementById(`taskTypeField_${field.key}`);
      if (!input) return;
      const value = (input.value || "").trim();
      if (value !== "") values[field.key] = value;
    });

    if (!Object.keys(values).length) return null;
    return { task_type: type.id, extra_fields: values };
  }

  function validate() {
    const type = currentType();
    if (!type) return null;
    const missing = (type.fields || [])
      .filter(field => field.required)
      .filter(field => {
        const input = document.getElementById(`taskTypeField_${field.key}`);
        return !input || !(input.value || "").trim();
      })
      .map(field => field.label);
    if (missing.length) {
      return `Заполните поле: ${missing.join(", ")}`;
    }
    return null;
  }

  function reset(selectId, containerId) {
    state.selected = null;
    const select = document.getElementById(selectId);
    if (select && select.options.length) {
      select.selectedIndex = 0;
      state.selected = select.value;
      renderFields(document.getElementById(containerId), currentType());
    }
  }

  window.TaskTypesUI = {
    load: ensureLoaded,
    render,
    collect,
    validate,
    reset,
    types: () => state.types,
    currentType,
    fieldTypeLabel,
  };
})();
