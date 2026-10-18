
async function uploadCommentMedia(files, statusEl) {
  const uploaded = [];

  for(let i = 0; i < files.length; i++) {
    const file = files[i];

    if(statusEl) {
      statusEl.textContent = `Загрузка ${i + 1} из ${files.length}: ${file.name}`;
      statusEl.className = "comment-media-status loading";
    }

    const fd = new FormData();
    fd.append("file", file);

    const response = await fetch("/api/upload", {
      method: "POST",
      headers: authHeaders ? authHeaders() : {},
      body: fd
    });

    const data = await response.json().catch(() => ({}));

    if(!response.ok) {
      throw new Error(data.detail || `Ошибка загрузки файла: ${file.name}`);
    }

    uploaded.push(data.filename);
  }

  if(statusEl) {
    statusEl.textContent = `✓ Прикреплено: ${uploaded.length}`;
    statusEl.className = "comment-media-status success";
  }

  return uploaded;
}

function updateCommentMediaSelection(input, statusEl, listEl) {
  const files = Array.from(input.files || []);

  if(files.length > 10) {
    input.value = "";
    if(statusEl) {
      statusEl.textContent = "Можно выбрать не более 10 файлов";
      statusEl.className = "comment-media-status error";
    }
    if(listEl) listEl.innerHTML = "";
    return;
  }

  if(!files.length) {
    if(statusEl) statusEl.textContent = "";
    if(listEl) listEl.innerHTML = "";
    return;
  }

  if(statusEl) {
    statusEl.textContent = `Выбрано: ${files.length} ${files.length === 1 ? "файл" : "файла"}`;
    statusEl.className = "comment-media-status";
  }

  if(listEl) {
    listEl.innerHTML = files.map((file, i) => `
      <div class="comment-media-file">
        <span>📎</span>
        <span>${String(file.name).replace(/</g, "&lt;")}</span>
        <span class="comment-media-size">${Math.round(file.size / 1024)} КБ</span>
      </div>
    `).join("");
  }
}

const API = (path, opts={}) => fetch(path, opts).then(async r => {
  if(r.status === 401){ localStorage.clear(); location.href = "/"; }
  const data = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.detail || "Ошибка");
  return data;
});
const authHeaders = () => ({ "Authorization": "Bearer " + localStorage.getItem("token") });
const toast = (msg, err=false) => {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (err?" err":"");
  t.style.display = "block";
  setTimeout(()=>t.style.display="none", 3000);
};

let myLogin = "";
let myRole = "";
let allFeed = [];
let currentFilter = "all";
let closeTaskId = null;

let lastMessageSort = "new";
let taskDateSort = "";
let numberSort = "";

const expandedFeedItems = new Set();

const fmtDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T")?"":"Z"));
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
};

const fmtMonitoringDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));
  const day = String(d.getDate()).padStart(2, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const year = String(d.getFullYear()).slice(-2);
  return `${day}-${month}-${year}`;
};

const feedKey = (type, id) => `${type}-${id}`;

const getTime = value => {
  if(!value) return 0;
  const d = new Date(value + (value.includes("T") ? "" : "Z"));
  return d.getTime() || 0;
};

window.closeSettingsModal = () => document.getElementById("settingsModal").classList.remove("active");
window.closeCreateTaskModal = () => document.getElementById("createTaskModal").classList.remove("active");
window.closeCloseTaskModal = () => { document.getElementById("closeTaskModal").classList.remove("active"); closeTaskId = null; };
window.closeMonitoringModal = () => document.getElementById("monitoringModal").classList.remove("active");

["settingsModal","createTaskModal","closeTaskModal","monitoringModal"].forEach(id => {
  document.getElementById(id).addEventListener("click", e => { if(e.target.id === id) e.target.classList.remove("active"); });
});

window.selectCloseReason = (el, reason) => {
  document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
  el.classList.add("selected");
  document.getElementById("customReason").style.display = reason === "custom" ? "block" : "none";
};

// Разрешаем разворачивать ЛЮБУЮ карточку (и задание, и мониторинг)
window.toggleExpand = (type, id, event) => {
  if(event && (
    event.target.tagName === 'BUTTON' ||
    event.target.tagName === 'TEXTAREA' ||
    event.target.tagName === 'INPUT' ||
    event.target.closest('.chat-input-area') ||
    event.target.closest('.modal-overlay') ||
    event.target.closest('.task-actions')
  )) {
    return;
  }

  const key = feedKey(type, id);
  const el = document.getElementById(`item-${type}-${id}`);

  if(!el) return;

  el.classList.toggle('expanded');

  if(el.classList.contains('expanded')) {
    expandedFeedItems.add(key);
  } else {
    expandedFeedItems.delete(key);
  }
};

const renderFeed = (items) => {
  const feed = document.getElementById("feed");

  if(!items.length) {
    feed.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px">Событий не найдено</div>';
    return;
  }

  feed.innerHTML = items.map(item => {
    const isTask = item.type === "task";

    const typeBadge = isTask
      ? '<span class="type-badge type-task">📋 Задание</span>'
      : '<span class="type-badge type-monitoring">📷 Мониторинг</span>';

    let statusBadge = "";

    if(isTask) {
      const urgMap = {
        new: {cls:"urg-new", text:"Новое"},
        warning: {cls:"urg-warning", text:"24ч+"},
        overdue: {cls:"urg-overdue", text:"Просрочено"},
        done: {cls:"urg-done", text:"Выполнено"}
      };

      const u = urgMap[item.status] || urgMap.new;
      statusBadge = `<span class="status-badge ${u.cls}">${u.text}</span>`;
    }

    const importantIcon = (isTask && item.is_important) ? '⭐' : '';

    // НЕ меняем существующий индикатор комментария директора.
    const directorMark = item.has_director_comment
      ? `<span
          class="director-mark"
          title="Есть комментарий директора"
          onclick="event.stopPropagation(); showDirectorComment('${item.type}', ${item.id})"
        ></span>`
      : '';

    // Последнее сообщение.
    let lastMsgHtml = '<div class="last-message">Сообщений нет</div>';

    if(item.last_message_at) {
      const author = item.last_message_author || "Неизвестно";
      lastMsgHtml =
        `<div class="last-message">${fmtDate(item.last_message_at)} · ${author}</div>`;
    }

    let borderClass = "";

    if(isTask && item.status === "done") borderClass = "done";
    else if(isTask && item.is_overdue) borderClass = "overdue";
    else if(isTask && item.is_important) borderClass = "important";

    const sourceText = item.text || "";
    const shortDesc =
      sourceText.replace(/\n/g, ' ').substring(0, 80) +
      (sourceText.length > 80 ? '...' : '');

    // Для задания показываем его номер.
    // Для мониторинга — дату ДД-ММ-ГГ.
    const numberLabel = isTask
      ? `№${item.id}`
      : fmtMonitoringDate(item.monitoring_date || item.created_at);

    const chatHtml = (item.messages || [])
      .filter(m => m.is_director_comment !== true)
      .map(m => {
        const isMe = m.author === myLogin;

        const safeAuthor = String(m.author || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");

        const safeText = String(m.text || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;")
          .replace(/\n/g, "<br>");

        return `<div class="chat-msg ${isMe ? 'me' : ''}">
          <div class="chat-author">${safeAuthor}</div>
          <div class="chat-bubble">${safeText}<div class="chat-time">${fmtDate(m.created_at)}</div></div>
        </div>`;
      }).join("") ||
      '<div style="text-align:center;color:var(--muted);font-size:12px;padding:10px">Пока нет сообщений</div>';

    let directorCommentHtml = "";

    const directorComment =
      item.director_comment ||
      item.latest_director_comment ||
      item.director_comments?.[item.director_comments.length - 1] ||
      null;

    if(directorComment) {
      const dcText = String(
        directorComment.text || directorComment.comment || ""
      )
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;")
        .replace(/\n/g, "<br>");

      directorCommentHtml = `
        <div
          class="director-comment-view"
          style="margin:12px 0;padding:10px 12px;background:#fffbeb;border-left:3px solid #f59e0b;border-radius:7px"
        >
          <div style="font-size:11px;font-weight:800;color:#b45309;margin-bottom:4px">
            Комментарий директора
          </div>
          <div style="font-size:13px;line-height:1.4;color:#78350f">
            ${dcText}
          </div>
          <div style="font-size:9px;color:#a16207;margin-top:4px">
            Директор${directorComment.created_at ? " · " + fmtDate(directorComment.created_at) : ""}
          </div>
        </div>
      `;
    }

    let adminActions = "";

    if(myRole === "admin" && isTask) {
      adminActions = `<div class="task-actions" onclick="event.stopPropagation()">
        <button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:${item.is_important?'#f59e0b':'#fbbf24'};color:${item.is_important?'#fff':'#78350f'}" onclick="toggleImportant(${item.id}, ${!item.is_important})">${item.is_important ? '⭐ Важное' : '☆ Отметить важным'}</button>
        ${item.status === 'open'
          ? `<button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff" onclick="openCloseModal(${item.id})">🔒 Закрыть</button>`
          : ''}
        <button class="btn" style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#ef4444;color:#fff" onclick="deleteTask(${item.id})">🗑 Удалить</button>
      </div>`;
    }

    const inputPlaceholder =
      myRole === 'director'
        ? 'Комментарий директора...'
        : 'Написать сообщение...';

    const key = feedKey(item.type, item.id);
    const expandedClass = expandedFeedItems.has(key) ? " expanded" : "";

    return `<div class="feed-item ${borderClass}${expandedClass} ${item.type === "monitoring" ? "monitoring-card" : "task-card"}"
      id="item-${item.type}-${item.id}"
      onclick="toggleExpand('${item.type}', ${item.id}, event)">

      <div class="feed-summary">

        <div class="left">

          <div class="top-row">
            ${typeBadge}
            <span class="feed-number">${numberLabel}</span>
            ${directorMark}
            ${importantIcon ? `<span style="font-size:14px">${importantIcon}</span>` : ''}
          </div>

          <div class="addr-line">
            <span class="addr" style="font-size:15px;font-weight:700">${item.address_name}</span>
            ${statusBadge}
            ${item.is_overdue
              ? '<span style="color:#ef4444;font-weight:700;font-size:11px"> Просрочено</span>'
              : ''}
          </div>

          <div class="bottom-row">
            <span>📍 ${item.city}</span>
            ${isTask
              ? `<span>📅 ${fmtDate(item.created_at)}</span>`
              : `<span>📅 ${fmtMonitoringDate(item.monitoring_date || item.created_at)}</span>`}
            ${isTask && item.close_reason
              ? `<span style="color:#92400e"> ${item.close_reason}</span>`
              : ''}
          </div>

          ${lastMsgHtml}

        </div>

        <div class="right">
          <div class="short-desc">${shortDesc.replace(/</g,"&lt;")}</div>
          <div style="font-size:18px;color:var(--muted);align-self:flex-end">▼</div>
        </div>

      </div>

      <div class="feed-details" onclick="event.stopPropagation()">

        <div class="city">${item.city} · ${item.address_name}</div>

        <div class="full-text">
          ${sourceText.replace(/</g,"&lt;").replace(/\n/g,"<br>")}
        </div>

        ${isTask && item.close_reason
          ? `<div class="close-reason">📌 ${item.close_reason.replace(/</g,"&lt;")}</div>`
          : ''}

        ${directorCommentHtml}

        ${adminActions}

        <div style="margin-top:16px;font-size:13px;font-weight:700;color:var(--muted);margin-bottom:8px">
          💬 Обсуждение
        </div>

        <div class="chat-box" id="chat-${item.type}-${item.id}">
          ${chatHtml}
        </div>

        <div class="chat-input-area">
          ${
            isTask
              ? `
                <input
                  id="media-${item.type}-${item.id}"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v"
                  multiple
                  style="display:none"
                  onchange="updateCommentMediaSelection(
                    this,
                    document.getElementById('media-status-${item.type}-${item.id}'),
                    document.getElementById('media-list-${item.type}-${item.id}')
                  )"
                >
                <button
                  type="button"
                  class="btn comment-media-attach"
                  onclick="event.stopPropagation(); document.getElementById('media-${item.type}-${item.id}').click()"
                  title="Прикрепить фото или видео"
                >📎</button>
              `
              : ""
          }

          <textarea
            id="input-${item.type}-${item.id}"
            placeholder="${inputPlaceholder}"></textarea>

          <button class="btn" onclick="sendMessage('${item.type}', ${item.id})">➤</button>
        </div>

        ${
          isTask
            ? `
              <div
                id="media-status-${item.type}-${item.id}"
                class="comment-media-status"
              ></div>
              <div
                id="media-list-${item.type}-${item.id}"
                class="comment-media-list"
              ></div>
            `
            : ""
        }

      </div>

    </div>`;
  }).join("");

  document.querySelectorAll('.feed-item.expanded .chat-box').forEach(box => {
    box.scrollTop = box.scrollHeight;
  });
};

window.sendMessage = async (type, id) => {
  const input = document.getElementById(`input-${type}-${id}`);
  const mediaInput = document.getElementById(`media-${type}-${id}`);
  const statusEl = document.getElementById(`media-status-${type}-${id}`);
  const listEl = document.getElementById(`media-list-${type}-${id}`);

  if(!input) return;

  const text = input.value.trim();
  const files = mediaInput
    ? Array.from(mediaInput.files || [])
    : [];

  if(!text) return;

  if(files.length > 10) {
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  const btn = input.nextElementSibling;

  if(btn) {
    btn.disabled = true;
    btn.textContent = "...";
  }

  const key = feedKey(type, id);

  // Карточка после отправки должна остаться раскрытой.
  expandedFeedItems.add(key);

  try {
    let uploaded = [];

    if(files.length) {
      uploaded = await uploadCommentMedia(files, statusEl);
    }

    if(myRole === "director" || myRole === "admin") {
      await API("/api/director/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          item_type: type,
          item_id: id,
          text: text,
          media: uploaded
        })
      });

    } else if(type === "task") {
      await API("/api/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          task_id: id,
          text: text,
          media: uploaded
        })
      });

    } else {
      // Мониторинг всегда привязан к address_id.
      // Для старого feed медиа мониторинга пока не меняем:
      // backend /api/monitoring для этой ветки уже имеет свою
      // отдельную логику загрузки.
      await API("/api/monitoring", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          address_id: id,
          text: text,
          media: uploaded
        })
      });
    }

    input.value = "";

    if(mediaInput) {
      mediaInput.value = "";
    }

    if(listEl) {
      listEl.innerHTML = "";
    }

    if(statusEl) {
      statusEl.textContent = uploaded.length
        ? `✓ Прикреплено: ${uploaded.length}`
        : "";
      statusEl.className = uploaded.length
        ? "comment-media-status success"
        : "comment-media-status";
    }

    await loadFeed();

    // После перерисовки снова прокручиваем чат вниз.
    const box = document.getElementById(`chat-${type}-${id}`);

    if(box) {
      requestAnimationFrame(() => {
        box.scrollTop = box.scrollHeight;
      });
    }

    toast(
      uploaded.length
        ? `Сообщение отправлено · прикреплено: ${uploaded.length}`
        : "Сообщение отправлено"
    );

  } catch(e) {
    if(statusEl) {
      statusEl.textContent = e.message || "Ошибка загрузки";
      statusEl.className = "comment-media-status error";
    }

    toast(e.message || "Ошибка отправки", true);

  } finally {
    if(btn) {
      btn.disabled = false;
      btn.textContent = "➤";
    }
  }
};


window.showDirectorComment = async (type, id) => {
  try {
    const data = await API(`/api/feed/${type}/${id}/director-comment`, {
      headers: authHeaders()
    });

    if(!data || !data.text) {
      toast("Комментарий директора не найден");
      return;
    }

    const text = String(data.text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;")
      .replace(/\n/g, "<br>");

    let box = document.getElementById("directorCommentPopup");

    if(!box) {
      box = document.createElement("div");
      box.id = "directorCommentPopup";
      box.style.cssText = `
        position:fixed;
        inset:0;
        background:rgba(15,23,42,.45);
        z-index:9998;
        display:flex;
        align-items:center;
        justify-content:center;
        padding:16px;
      `;

      box.innerHTML = `
        <div style="
          width:min(520px,100%);
          background:#fff;
          border-radius:12px;
          padding:18px;
          box-shadow:0 15px 50px rgba(0,0,0,.25)
        ">
          <div style="
            display:flex;
            justify-content:space-between;
            align-items:center;
            font-weight:800;
            margin-bottom:12px
          ">
            <span>Комментарий директора</span>
            <button
              id="directorCommentPopupClose"
              style="
                border:0;
                background:transparent;
                font-size:22px;
                cursor:pointer;
                color:#6b7280
              "
            >×</button>
          </div>
          <div id="directorCommentPopupText"
            style="
              background:#fffbeb;
              border-left:3px solid #f59e0b;
              border-radius:7px;
              padding:12px;
              color:#78350f;
              font-size:14px;
              line-height:1.45
            "
          ></div>
        </div>
      `;

      document.body.appendChild(box);

      document.getElementById("directorCommentPopupClose").onclick = () => {
        box.remove();
      };

      box.onclick = e => {
        if(e.target === box) box.remove();
      };
    }

    document.getElementById("directorCommentPopupText").innerHTML = text;

  } catch(e) {
    toast(e.message || "Не удалось получить комментарий", true);
  }
};

const loadFeed = async () => {
  try {
    allFeed = await API("/api/feed", {headers: authHeaders()});
    applyFilters();
  } catch(e) { document.getElementById("feed").innerHTML = '<div style="color:var(--err);padding:20px">Ошибка загрузки ленты: ' + e.message + '</div>'; }
};

const applyFilters = () => {
  const city = document.getElementById("filterCity").value;
  const search = document.getElementById("filterSearch").value.toLowerCase();

  let filtered = [...allFeed];

  // Тип.
  if(currentFilter === "task") {
    filtered = filtered.filter(i => i.type === "task");
  } else if(currentFilter === "monitoring") {
    filtered = filtered.filter(i => i.type === "monitoring");
  }

  // Город.
  if(city) {
    filtered = filtered.filter(i => i.city === city);
  }

  // Поиск.
  if(search) {
    filtered = filtered.filter(i =>
      (i.address_name || "").toLowerCase().includes(search)
    );
  }

  // Последнее сообщение.
  if(lastMessageSort === "new") {
    filtered.sort((a, b) =>
      getTime(b.last_message_at || b.created_at) -
      getTime(a.last_message_at || a.created_at)
    );
  } else if(lastMessageSort === "old") {
    filtered.sort((a, b) =>
      getTime(a.last_message_at || a.created_at) -
      getTime(b.last_message_at || b.created_at)
    );
  }

  // Дата задания.
  if(taskDateSort === "new") {
    filtered.sort((a, b) =>
      getTime(b.created_at) - getTime(a.created_at)
    );
  } else if(taskDateSort === "old") {
    filtered.sort((a, b) =>
      getTime(a.created_at) - getTime(b.created_at)
    );
  }

  // Номер.
  if(numberSort === "asc" || numberSort === "desc") {
    filtered.sort((a, b) => {
      const av = a.type === "task"
        ? Number(a.id)
        : getTime(a.monitoring_date || a.created_at);

      const bv = b.type === "task"
        ? Number(b.id)
        : getTime(b.monitoring_date || b.created_at);

      return numberSort === "asc" ? av - bv : bv - av;
    });
  }

  renderFeed(filtered);
};

document.getElementById("lastMessageNew").onclick = () => {
  lastMessageSort = "new";
  document.getElementById("lastMessageNew").classList.add("active");
  document.getElementById("lastMessageOld").classList.remove("active");
  applyFilters();
};

document.getElementById("lastMessageOld").onclick = () => {
  lastMessageSort = "old";
  document.getElementById("lastMessageOld").classList.add("active");
  document.getElementById("lastMessageNew").classList.remove("active");
  applyFilters();
};

document.getElementById("taskDateNew").onclick = () => {
  taskDateSort = "new";
  document.getElementById("taskDateNew").classList.add("active");
  document.getElementById("taskDateOld").classList.remove("active");
  applyFilters();
};

document.getElementById("taskDateOld").onclick = () => {
  taskDateSort = "old";
  document.getElementById("taskDateOld").classList.add("active");
  document.getElementById("taskDateNew").classList.remove("active");
  applyFilters();
};

document.getElementById("numberAsc").onclick = () => {
  numberSort = "asc";
  document.getElementById("numberAsc").classList.add("active");
  document.getElementById("numberDesc").classList.remove("active");
  applyFilters();
};

document.getElementById("numberDesc").onclick = () => {
  numberSort = "desc";
  document.getElementById("numberDesc").classList.add("active");
  document.getElementById("numberAsc").classList.remove("active");
  applyFilters();
};

// ===== ФУНКЦИИ АДМИНА =====
document.getElementById("adminSettingsBtn").onclick = () => { document.getElementById("settingsModal").classList.add("active"); loadUsers(); loadMediaSettings(); };

window.saveMediaSettings = async () => {
  try {
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"photos_enabled", value: document.getElementById("togglePhotos").checked?"1":"0"}) });
    await API("/api/settings", { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({key:"videos_enabled", value: document.getElementById("toggleVideos").checked?"1":"0"}) });
    toast("✓ Настройки сохранены");
  } catch(e) { toast(e.message, true); }
};

const loadMediaSettings = async () => {
  try {
    const s = await API("/api/settings", {headers: authHeaders()});
    document.getElementById("togglePhotos").checked = s.photos_enabled;
    document.getElementById("toggleVideos").checked = s.videos_enabled;
  } catch(e){}
};

const loadUsers = async () => {
  try {
    const users = await API("/api/users", {headers: authHeaders()});
    const roleNames = {"admin":"Админ","service":"Сервис","director":"Руководитель"};
    document.getElementById("usersList").innerHTML = users.map(u => `<div class="user-row"><span style="flex:1;min-width:150px"><b>${u.login}</b> <span style="color:var(--muted);font-size:12px">(${roleNames[u.role]})</span></span><input id="pwd_${u.id}" type="password" placeholder="Новый пароль" style="flex:1;min-width:150px;margin:0;padding:8px;font-size:13px"><button class="btn secondary" style="width:auto;margin:0;padding:8px 16px;font-size:13px" onclick="changePassword(${u.id})">Сменить</button></div>`).join("");
  } catch(e){}
};

window.addUser = async () => {
  const login = document.getElementById("newLogin").value.trim();
  const password = document.getElementById("newPassword").value.trim();
  const role = document.getElementById("newRole").value;
  if(!login || !password){ toast("Заполните логин и пароль", true); return; }
  try {
    await API("/api/users", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({login, password, role}) });
    toast("✓ Пользователь добавлен");
    document.getElementById("newLogin").value = ""; document.getElementById("newPassword").value = "";
    loadUsers();
  } catch(e) { toast(e.message, true); }
};

window.changePassword = async (userId) => {
  const pwd = document.getElementById(`pwd_${userId}`).value.trim();
  if(!pwd){ toast("Введите новый пароль", true); return; }
  try {
    await API(`/api/users/${userId}/password`, { method:"PUT", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({new_password: pwd}) });
    toast("✓ Пароль изменён");
    document.getElementById(`pwd_${userId}`).value = "";
  } catch(e) { toast(e.message, true); }
};

// Создание задания (Админ и Директор)
document.getElementById("addTaskBtn").onclick = async () => {
  document.getElementById("createTaskModal").classList.add("active");
  const cities = await API("/api/cities");
  const sel = document.getElementById("taskCity");
  sel.innerHTML = '<option value="">— город —</option>';
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; sel.appendChild(o); });
  document.getElementById("taskAddrId").value = ""; document.getElementById("taskAddrSearch").value = ""; document.getElementById("taskAddrList").innerHTML = ""; document.getElementById("taskDesc").value = "";
};

window.createTask = async () => {
  const addrId = document.getElementById("taskAddrId").value;
  const desc = document.getElementById("taskDesc").value.trim();
  if(!addrId || !desc){ toast("Заполните адрес и описание", true); return; }
  try {
    await API("/api/tasks", { method:"POST", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({address_id: Number(addrId), description: desc}) });
    toast("✓ Задание создано");
    closeCreateTaskModal();
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

// Создание мониторинга (Сервис, но можно расширить)
document.getElementById("monitoringBtn").onclick = async () => {
  document.getElementById("monitoringModal").classList.add("active");
  const cities = await API("/api/cities");
  const sel = document.getElementById("monCity");
  sel.innerHTML = '<option value="">— город —</option>';
  cities.forEach(c => { const o=document.createElement("option"); o.value=c; o.textContent=c; sel.appendChild(o); });
  document.getElementById("monAddrId").value = "";
  document.getElementById("monAddrSearch").value = "";
  document.getElementById("monAddrList").innerHTML = "";
  document.getElementById("monText").value = "";
  const mediaInput = document.getElementById("monMedia");
  if(mediaInput) mediaInput.value = "";
};

window.createMonitoring = async () => {
  const addrId = document.getElementById("monAddrId").value;
  const text = document.getElementById("monText").value.trim();
  const mediaInput = document.getElementById("monMedia");
  const files = mediaInput ? Array.from(mediaInput.files || []) : [];

  if(!addrId || !text){
    toast("Заполните адрес и текст", true);
    return;
  }

  if(files.length > 10){
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  const button = document.querySelector("#monitoringModal .btn:not(.secondary)");
  if(button){
    button.disabled = true;
    button.textContent = "Отправка...";
  }

  try {
    const uploaded = [];

    for(const file of files){
      const fd = new FormData();
      fd.append("file", file);

      const response = await fetch("/api/upload", {
        method: "POST",
        headers: authHeaders(),
        body: fd
      });

      if(!response.ok){
        let message = "Ошибка загрузки файла";
        try {
          const error = await response.json();
          message = error.detail || message;
        } catch(e) {}
        throw new Error(message);
      }

      const media = await response.json();
      uploaded.push(media.filename);
    }

    await API("/api/monitoring", {
      method:"POST",
      headers:{"Content-Type":"application/json", ...authHeaders()},
      body: JSON.stringify({
        address_id: Number(addrId),
        text: text,
        media: uploaded
      })
    });

    toast("✓ Запись мониторинга создана");
    if(mediaInput) mediaInput.value = "";
    closeMonitoringModal();
    loadFeed();

  } catch(e) {
    toast(e.message, true);
  } finally {
    if(button){
      button.disabled = false;
      button.textContent = "Отправить";
    }
  }
};

document.getElementById("taskAddrSearch").addEventListener("input", async e => {
  const city = document.getElementById("taskCity").value;
  if(!city) return;
  const rows = await API(`/api/addresses?city=${encodeURIComponent(city)}&q=${encodeURIComponent(e.target.value)}`);
  document.getElementById("taskAddrList").innerHTML = rows.map(r => `<button type="button" class="btn secondary" style="margin:4px 0;padding:10px;text-align:left;font-size:14px;width:100%" onclick="pickTaskAddr(${r.id}, '${r.address_name.replace(/'/g, "\\'")}')">${r.address_name}</button>`).join("");
});
window.pickTaskAddr = (id, name) => { document.getElementById("taskAddrId").value = id; document.getElementById("taskAddrSearch").value = name; document.getElementById("taskAddrList").innerHTML = ""; };

document.getElementById("monAddrSearch").addEventListener("input", async e => {
  const city = document.getElementById("monCity").value;
  if(!city) return;
  const rows = await API(`/api/addresses?city=${encodeURIComponent(city)}&q=${encodeURIComponent(e.target.value)}`);
  document.getElementById("monAddrList").innerHTML = rows.map(r => `<button type="button" class="btn secondary" style="margin:4px 0;padding:10px;text-align:left;font-size:14px;width:100%" onclick="pickMonAddr(${r.id}, '${r.address_name.replace(/'/g, "\\'")}')">${r.address_name}</button>`).join("");
});
window.pickMonAddr = (id, name) => { document.getElementById("monAddrId").value = id; document.getElementById("monAddrSearch").value = name; document.getElementById("monAddrList").innerHTML = ""; };

window.toggleImportant = async (taskId, value) => {
  try {
    await API(`/api/tasks/${taskId}`, { method:"PATCH", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ is_important: value }) });
    toast(value ? "⭐ Важное" : "☆ Обычное");
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

window.openCloseModal = (taskId) => {
  closeTaskId = taskId;
  document.getElementById("closeTaskId").textContent = taskId;
  document.getElementById("closeTaskModal").classList.add("active");
  document.querySelectorAll(".status-option").forEach(o => o.classList.remove("selected"));
  document.querySelector('.status-option[data-reason="Сделано"]').classList.add("selected");
  document.getElementById("customReason").value = "";
  document.getElementById("customReason").style.display = "none";
};

window.confirmCloseTask = async () => {
  if(!closeTaskId) return;
  const selected = document.querySelector('.status-option.selected');
  let reason = selected.dataset.reason;
  if(reason === "custom"){
    reason = document.getElementById("customReason").value.trim();
    if(!reason){ toast("Введите свой статус", true); return; }
  }
  try {
    await API(`/api/tasks/${closeTaskId}`, { method:"PATCH", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ status: "done", close_reason: reason }) });
    toast(`✓ Задание закрыто: ${reason}`);
    closeCloseTaskModal();
    loadFeed();
  } catch(e) { toast(e.message, true); }
};

window.deleteTask = async (id) => {
  if(!confirm("Удалить задание и всю переписку?")) return;
  try { await API(`/api/tasks/${id}`, {method:"DELETE", headers: authHeaders()}); toast("Удалено"); loadFeed(); } 
  catch(e) { toast(e.message, true); }
};

// Старт
(async () => {
  const me = await API("/api/me", {headers: authHeaders()}).catch(()=>null);
  if(!me){ location.href = "/"; return; }

  if(!["admin", "director", "service"].includes(me.role)) {
    location.href = "/";
    return;
  }
  myLogin = me.login;
  myRole = me.role;
  
  document.getElementById("roleName").textContent = me.role_label;
  document.getElementById("whoami").textContent = me.login;

  const pageTitle = document.getElementById("pageTitle");

  if(pageTitle) {
    pageTitle.textContent =
      myRole === "service"
        ? "Лента"
        : myRole === "director"
          ? "Лента"
          : "Лента";
  }
  
  // Показываем кнопки в зависимости от роли
  if(myRole === "admin") {
    document.getElementById("adminSettingsBtn").style.display = "inline-flex";
    document.getElementById("addTaskBtn").style.display = "inline-flex";
  }
  else if(myRole === "director") {
    document.getElementById("addTaskBtn").style.display = "inline-flex";
  }
  else if(myRole === "service") {
    document.getElementById("monitoringBtn").style.display = "inline-flex";
  }
  
  const cities = await API("/api/cities");
  const citySel = document.getElementById("filterCity");
  cities.forEach(c => { const o = document.createElement("option"); o.value = c; o.textContent = c; citySel.appendChild(o); });

  document.getElementById("filterCity").addEventListener("change", applyFilters);
  document.getElementById("filterSearch").addEventListener("input", applyFilters);
  document.getElementById("logout").onclick = () => logoutCurrentSession("/");
  
  loadFeed();
})();
