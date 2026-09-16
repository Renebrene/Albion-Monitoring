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

let sortMode = "message";
let sortDirection = "desc";
let stateFilter = "";

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

const fmtMonitoringTime = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return `${hours}-${minutes}`;
};

const fmtDirectorDate = iso => {
  if(!iso) return "";
  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));
  const day = String(d.getDate()).padStart(2, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const year = d.getFullYear();
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return `${day}-${month}-${year} ${hours}:${minutes}`;
};

const feedKey = (type, id) => `${type}-${id}`;

const getTime = value => {
  if(!value) return 0;
  const d = new Date(value + (value.includes("T") ? "" : "Z"));
  return d.getTime() || 0;
};

window.closeSettingsModal = () => document.getElementById("settingsModal").classList.remove("active");
window.closeCreateTaskModal = () => document.getElementById("createTaskModal").classList.remove("active");
window.closeMonitoringModal = () => document.getElementById("monitoringModal").classList.remove("active");

["settingsModal","createTaskModal","monitoringModal"].forEach(id => {
  document.getElementById(id).addEventListener("click", e => { if(e.target.id === id) e.target.classList.remove("active"); });
});



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


const workflowStatusLabel = (item) => {
  if(item.type !== "task") return "";

  const labels = {
    open: "Открыто",
    pending_review: "На проверке",
    completed: "Выполнено",
    rework: "На доработку",
    closed: "Отменено"
  };

  return item.workflow_status_label ||
    labels[item.workflow_status] ||
    "";
};

const workflowLastChange = (item) => {
  if(item.type !== "task") return "";

  const history = Array.isArray(item.status_history)
    ? item.status_history
    : [];

  if(!history.length) return "";

  const last = history[history.length - 1];

  if(!last || !last.changed_at) return "";

  return fmtDate(last.changed_at);
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
      ? (
          item.author_role === "director"
            ? '<span class="type-badge type-task-director">ЗАДАНИЕ</span>'
            : '<span class="type-badge type-task">ЗАДАНИЕ</span>'
        )
      : '<span class="type-badge type-monitoring">МОНИТОРИНГ</span>';

    let statusBadge = "";

    if(isTask) {
      const ws = item.workflow_status || "open";
      const label = workflowStatusLabel(item);
      const changed = workflowLastChange(item);

      let cls = "status-open-admin";

      if(ws === "pending_review") {
        cls = "status-review";
      } else if(ws === "rework") {
        cls = item.created_by_role === "director"
          ? "status-rework-director"
          : "status-rework-admin";
      } else if(ws === "completed" || ws === "closed") {
        cls = "status-done";
      } else if(item.is_overdue) {
        cls = "status-overdue";
      } else if(item.created_by_role === "director") {
        cls = "status-open-director";
      }

      const overdueMark =
        item.is_overdue && ["open", "rework"].includes(ws)
          ? " !!!"
          : "";

      statusBadge = `
        <span class="status-badge ${cls}">
          ${label}${overdueMark}
          ${changed ? ` · ${changed}` : ""}
        </span>
      `;
    }

    // Важность показываем только жёлтой звездочкой справа.
    const importantIcon =
      (isTask && item.is_important)
        ? '<span class="important-star" title="Важное">⭐</span>'
        : '';

    // Сохраняем существующий индикатор комментария директора.
    const directorMark = item.has_director_comment
      ? '<span class="director-mark" title="Есть комментарий директора"></span>'
      : '';

    // Последнее сообщение.
    let lastMsgHtml = '<span style="color:var(--muted)">Сообщений нет</span>';

    if(item.last_message_at) {
      const author = item.last_message_author || "Неизвестно";
      lastMsgHtml =
        `<span>${fmtDate(item.last_message_at)} · ${author}</span>`;
    }

    let borderClass = "";

    if(item.is_new) {
      if(item.type === "monitoring") {
        borderClass = "new-monitoring";
      } else if(item.created_by_role === "director") {
        borderClass = "new-director";
      } else {
        borderClass = "new-admin";
      }
    }

    const sourceText = item.text || "";

    // Краткое содержание — начало текста.
    const shortDesc =
      sourceText.replace(/\n/g, ' ').trim().substring(0, 110) +
      (sourceText.replace(/\n/g, ' ').trim().length > 110 ? '...' : '');

    // Для задания показываем его номер.
    // Для мониторинга — дату ДД-ММ-ГГ.
    const numberLabel = isTask
      ? `№${item.id}`
      : fmtMonitoringDate(item.monitoring_date || item.created_at);

    const chatMessages = (item.messages || []);

    const chatHtml = chatMessages.map(m => {
      const authorName = m.author;

      const chatRole =
        m.role === "service"
          ? "service"
          : "admin";

      const safeText = (m.text || "")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br>");

      const commentMedia = Array.isArray(m.media) ? m.media : [];

      const mediaHtml = commentMedia.length
        ? `<div class="client-comment-attachments">
            ${commentMedia.map(media => {
              let name = "";
              let url = "";
              let mediaType = "";

              if (media && typeof media === "object") {
                url = String(media.url || "");
                mediaType = String(media.type || media.media_type || "");

                if (url) {
                  name = url.split("/").pop() || "Файл";
                }
              } else {
                name = String(media || "");

                url = name.startsWith("/uploads/")
                  ? name
                  : `/uploads/${encodeURIComponent(name)}`;
              }

              if (!url) {
                return "";
              }

              const isImage =
                mediaType === "photo" ||
                /\\.(jpg|jpeg|png|webp|gif)$/i.test(name);

              const isVideo =
                mediaType === "video" ||
                /\\.(mp4|mov|webm|m4v)$/i.test(name);

              if (isImage) {
                return `
                  <div class="client-comment-attachment client-comment-image-attachment">
                    <a
                      href="${url}"
                      target="_blank"
                      rel="noopener"
                      title="Открыть фотографию"
                    >
                      <img
                        src="${url}"
                        class="client-comment-image"
                        alt="Фото"
                        loading="lazy"
                      >
                    </a>
                    <a href="${url}" download>Скачать</a>
                  </div>
                `;
              }

              return `
                <div class="client-comment-attachment">
                  <span>${isVideo ? "🎬" : "📎"}</span>
                  <a
                    class="client-comment-attachment-name"
                    href="${url}"
                    target="_blank"
                    rel="noopener"
                    title="Открыть файл"
                  >
                    ${name.replace(/</g, "&lt;")}
                  </a>
                  <a href="${url}" download>Скачать</a>
                </div>
              `;
            }).join("")}
          </div>`
        : "";

            return `<div class="chat-msg ${chatRole}">
        <div class="chat-author">${authorName}</div>
        <div class="chat-bubble">
          ${safeText}
          ${mediaHtml}
          <div class="chat-time">${fmtDate(m.created_at)}</div>
        </div>
      </div>`;
    }).join("") ||
      '<div style="text-align:center;color:var(--muted);font-size:12px;padding:10px">Пока нет сообщений</div>';

    const taskMedia = isTask && Array.isArray(item.media)
      ? item.media
      : [];

    const taskMediaCount = taskMedia.length;

    const taskMediaIndicator = isTask && taskMediaCount
      ? `<span class="client-media-indicator" title="Прикреплено файлов: ${taskMediaCount}">📎 ${taskMediaCount}</span>`
      : "";

    // Исходные фото/видео обычного мониторинга.
    // В /api/feed они находятся внутри первого сообщения мониторинга.
    const monitoringMedia =
      !isTask &&
      Array.isArray(item.messages) &&
      item.messages.length &&
      Array.isArray(item.messages[0].media)
        ? item.messages[0].media
        : [];

    const monitoringMediaCount = monitoringMedia.length;

    const monitoringMediaIndicator = !isTask && monitoringMediaCount
      ? `<span class="client-media-indicator" title="Прикреплено файлов: ${monitoringMediaCount}">📎 ${monitoringMediaCount}</span>`
      : "";

    let adminActions = "";

    if(isTask) {
      const ws = item.workflow_status || "open";
      const creatorRole = item.created_by_role || item.author_role || "";

      let actions = "";

      // Админ подтверждает/отклоняет задания,
      // а также может закрыть любое открытое задание.
      if(myRole === "admin") {
        if(ws === "pending_review") {
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff"
              onclick="approveTask(${item.id})">✓</button>
          `;
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#ef4444;color:#fff"
              onclick="reworkTask(${item.id})">✕</button>
          `;
        }

        if(["open", "rework"].includes(ws)) {
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff"
              onclick="closeWorkflowTask(${item.id})">
              ✕
            </button>
          `;
        }

        actions += `
          <button class="btn"
            style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:${item.is_important?'#f59e0b':'#fbbf24'};color:${item.is_important?'#fff':'#78350c'}"
            onclick="toggleImportant(${item.id}, ${!item.is_important})">
            ${item.is_important ? '⭐ Важное' : '☆ Отметить важным'}
          </button>
        `;

        actions += `
          <button class="btn"
            style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#ef4444;color:#fff"
            onclick="deleteTask(${item.id})">
            🗑 Удалить
          </button>
        `;
      }

      // Директор может подтверждать/отклонять и закрывать
      // только задания, созданные директором.
      if(myRole === "director" && creatorRole === "director") {
        if(ws === "pending_review") {
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff"
              onclick="approveTask(${item.id})">✓</button>
          `;
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#ef4444;color:#fff"
              onclick="reworkTask(${item.id})">✕</button>
          `;
        }

        if(["open", "rework"].includes(ws)) {
          actions += `
            <button class="btn"
              style="width:auto;margin:0;padding:6px 12px;font-size:12px;background:#10b981;color:#fff"
              onclick="closeWorkflowTask(${item.id})">
              ✕
            </button>
          `;
        }
      }

      if(actions) {
        adminActions = `
          <div class="task-actions" onclick="event.stopPropagation()">
            ${actions}
          </div>
        `;
      }
    }

    const inputPlaceholder =
      myRole === 'director'
        ? 'Оставить комментарий...'
        : 'Написать сообщение...';

    const directorComments = Array.isArray(item.director_comments)
      ? item.director_comments
      : [];

    const lastDirectorComment = directorComments.length
      ? directorComments[directorComments.length - 1]
      : null;

    const directorCommentText = lastDirectorComment
      ? String(lastDirectorComment.text || "")
      : "";

    const directorCommentPreview = directorCommentText
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\s+/g, " ")
      .trim();

    const key = feedKey(item.type, item.id);
    const expandedClass = expandedFeedItems.has(key) ? " expanded" : "";

    return `<div class="feed-item ${borderClass}${expandedClass}"
      id="item-${item.type}-${item.id}"
      onclick="toggleExpand('${item.type}', ${item.id}, event)">

      <div class="feed-summary">

        <div class="left">

          <!-- СТРОКА 1: тип / номер / дата / статусы справа -->
          <div class="top-row">
            <div class="top-row-main">
              ${typeBadge}
              <span class="feed-number">${numberLabel}</span>

              ${!isTask
                ? `
                  <span class="feed-created">
                    ${fmtMonitoringDate(item.monitoring_date || item.created_at)}
                    ·
                    ${fmtMonitoringTime(item.monitoring_date || item.created_at)}
                  </span>
                `
                : ""}
            </div>

            <div class="top-row-status">
              ${statusBadge}

              ${myRole === "service" && isTask && ["open", "rework"].includes(item.workflow_status || "open")
                ? `<button
                    class="client-ready-btn"
                    title="Задание готово"
                    onclick="event.stopPropagation(); completeTask(${item.id})"
                  >✓</button>`
                : ""}

              ${taskMediaIndicator}
              ${monitoringMediaIndicator}
              ${importantIcon}
            </div>
          </div>

          <!-- СТРОКА 2: город и адрес -->
          <div class="addr-line">
            <span class="addr" style="font-size:15px;font-weight:700">
              ${item.city} · ${item.address_name}
            </span>
          </div>

          <!-- СТРОКА 3: краткое содержание -->
          <div
            class="closed-short-desc"
            style="font-weight:700;font-style:italic;color:#111"
          >
            ${shortDesc.replace(/</g,"&lt;")}
          </div>

          <!-- СТРОКА 4: последнее сообщение -->
          <div class="bottom-row">
            <span>
              Последнее сообщение: ${lastMsgHtml}
            </span>
          </div>

          <!-- СТРОКА 5: комментарий директора -->
          ${lastDirectorComment
            ? `<div class="closed-director-comment">
                ${directorMark}
                <span><b>Комментарий директора:</b> ${directorCommentPreview}</span>
              </div>`
            : ''}

        </div>

      </div>

      <div class="feed-details" onclick="event.stopPropagation()">

        <div class="full-text">
          ${sourceText.replace(/</g,"&lt;").replace(/\n/g,"<br>")}
        </div>

        ${isTask && taskMediaCount
          ? `
            <div class="client-task-attachments">
              <div class="client-task-attachments-title">Прикреплённые фото и видео</div>
              <div class="client-task-attachments-list">
                ${taskMedia.map((media, index) => {
                  const url = String(media.url || "");
                  const type = String(media.type || media.media_type || "");

                  const isVideo =
                    type === "video" ||
                    /\\.(mp4|mov|webm|m4v)$/i.test(url);

                  return isVideo
                    ? `
                      <video
                        src="${url}"
                        controls
                        preload="metadata"
                        title="Вложение ${index + 1}"
                      ></video>
                    `
                    : `
                      <img
                        src="${url}"
                        alt="Вложение ${index + 1}"
                        onclick="window.open('${url}', '_blank')"
                      >
                    `;
                }).join("")}
              </div>
            </div>
          `
          : ""}

        ${isTask && item.close_reason
          ? `<div class="close-reason">📌 ${item.close_reason.replace(/</g,"&lt;")}</div>`
          : ''}

        ${!isTask && monitoringMediaCount
          ? `<div class="client-comment-attachments" style="margin-top:14px">
              ${monitoringMedia.map(media => {
                let name = "";
                let url = "";
                let mediaType = "";

                if (media && typeof media === "object") {
                  url = String(media.url || "");
                  mediaType = String(media.type || media.media_type || "");

                  if (url) {
                    name = url.split("/").pop() || "Файл";
                  }
                } else {
                  name = String(media || "");

                  url = name.startsWith("/uploads/")
                    ? name
                    : `/uploads/${encodeURIComponent(name)}`;
                }

                if (!url) {
                  return "";
                }

                const isImage =
                  mediaType === "photo" ||
                  /\.(jpg|jpeg|png|webp|gif)$/i.test(name);

                const isVideo =
                  mediaType === "video" ||
                  /\.(mp4|mov|webm|m4v)$/i.test(name);

                if (isImage) {
                  return `
                    <div class="client-comment-attachment client-comment-image-attachment">
                      <a
                        href="${url}"
                        target="_blank"
                        rel="noopener"
                        title="Открыть фотографию"
                      >
                        <img
                          src="${url}"
                          class="client-comment-image"
                          alt="Фото"
                          loading="lazy"
                        >
                      </a>
                      <a href="${url}" download>Скачать</a>
                    </div>
                  `;
                }

                return `
                  <div class="client-comment-attachment">
                    <span>${isVideo ? "🎬" : "📎"}</span>
                    <a
                      class="client-comment-attachment-name"
                      href="${url}"
                      target="_blank"
                      rel="noopener"
                      title="Открыть файл"
                    >
                      ${name.replace(/</g, "&lt;")}
                    </a>
                    <a href="${url}" download>Скачать</a>
                  </div>
                `;
              }).join("")}
            </div>`
          : ""}

        ${adminActions}

        ${isTask ? `
          <div style="margin-top:16px;font-size:13px;font-weight:700;color:var(--muted);margin-bottom:8px">
            💬 Обсуждение
          </div>

          <div class="chat-box" id="chat-${item.type}-${item.id}">
            ${chatHtml}
          </div>
        ` : ""}

        ${directorComments.length
          ? `<div class="director-comments-block">
              <div class="director-comment-title">Комментарии директора</div>
              ${directorComments.map(comment => {
                const commentText = String(comment.text || "")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/\n/g, "<br>");

                const commentMedia = Array.isArray(comment.media)
                  ? comment.media
                  : [];

                const mediaHtml = commentMedia.length
                  ? `<div class="client-comment-attachments">
                      ${commentMedia.map(media => {
                        const name = String(media || "");
                        const url = name.startsWith("/uploads/")
                          ? name
                          : `/uploads/${encodeURIComponent(name)}`;
                        const isVideo = /\\.(mp4|mov|webm|m4v)$/i.test(name);

                        return `
                          <div class="client-comment-attachment">
                            <span>${isVideo ? "🎬" : "📎"}</span>
                            <a
                              class="client-comment-attachment-name"
                              href="${url}"
                              target="_blank"
                              rel="noopener"
                              title="Открыть файл"
                            >
                              ${name.replace(/</g, "&lt;")}
                            </a>
                            <a href="${url}" download>Скачать</a>
                          </div>
                        `;
                      }).join("")}
                    </div>`
                  : "";

                return `<div class="director-comment-block">
                  <div class="director-comment-author">
                    Директор${comment.created_at
                      ? ` (${fmtDirectorDate(comment.created_at)})`
                      : ""}
                  </div>
                  <div class="director-comment-text">
                    ${commentText}
                    ${mediaHtml}
                  </div>
                </div>`;
              }).join("")}
            </div>`
          : ''}

        ${
          !isTask
            ? ""
            : (
                ["completed", "closed"].includes(item.workflow_status) ||
                (myRole === "service" && item.workflow_status === "pending_review")
              )
                ? ""
                : `<div class="chat-input-area">
                ${myRole !== "director"
                  ? `
                    <input
                      id="media-${item.type}-${item.id}"
                      type="file"
                      accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v"
                      multiple
                      style="display:none"
                      onchange="updateClientChatMediaSelection(
                        this,
                        document.getElementById('media-status-${item.type}-${item.id}'),
                        document.getElementById('media-list-${item.type}-${item.id}')
                      )"
                    >

                    <button
                      type="button"
                      class="btn chat-attach-btn"
                      onclick="event.stopPropagation(); document.getElementById('media-${item.type}-${item.id}').click()"
                      title="Прикрепить фото или видео"
                    >📎</button>
                  `
                  : ""}

                <textarea
                  id="input-${item.type}-${item.id}"
                  placeholder="${inputPlaceholder}"></textarea>

                <button
                  id="send-${item.type}-${item.id}"
                  class="btn"
                  onclick="sendMessage('${item.type}', ${item.id})"
                >➤</button>
              </div>

              ${myRole !== "director"
                ? `
                  <div
                    id="media-status-${item.type}-${item.id}"
                    class="client-chat-media-status"
                  ></div>

                  <div
                    id="media-list-${item.type}-${item.id}"
                    class="client-chat-media-list"
                  ></div>
                `
                : ""}`
        }

      </div>

    </div>`;
  }).join("");

  document.querySelectorAll('.feed-item.expanded .chat-box').forEach(box => {
    box.scrollTop = box.scrollHeight;
  });
};

window.updateClientChatMediaSelection = (input, statusEl, listEl) => {
  const files = Array.from(input.files || []);

  if(files.length > 10) {
    input.value = "";

    if(statusEl) {
      statusEl.textContent = "Можно выбрать не более 10 файлов";
      statusEl.className = "client-chat-media-status error";
    }

    if(listEl) {
      listEl.innerHTML = "";
    }

    return;
  }

  if(!files.length) {
    if(statusEl) {
      statusEl.textContent = "";
      statusEl.className = "client-chat-media-status";
    }

    if(listEl) {
      listEl.innerHTML = "";
    }

    return;
  }

  if(statusEl) {
    statusEl.textContent =
      `Выбрано: ${files.length} ${files.length === 1 ? "файл" : "файла"}`;
    statusEl.className = "client-chat-media-status";
  }

  if(listEl) {
    listEl.innerHTML = files.map(file => `
      <div class="client-chat-media-file">
        <span>📎</span>
        <span class="client-chat-media-file-name">
          ${String(file.name)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;")}
        </span>
        <span class="client-chat-media-size">
          ${Math.round(file.size / 1024)} КБ
        </span>
      </div>
    `).join("");
  }
};

const uploadClientChatMedia = async (files, statusEl) => {
  const uploaded = [];

  for(let i = 0; i < files.length; i++) {
    const file = files[i];

    if(statusEl) {
      statusEl.textContent =
        `Загрузка ${i + 1} из ${files.length}: ${file.name}`;
      statusEl.className = "client-chat-media-status loading";
    }

    const fd = new FormData();
    fd.append("file", file);

    const response = await fetch("/api/upload", {
      method: "POST",
      headers: authHeaders(),
      body: fd
    });

    const data = await response.json().catch(() => ({}));

    if(!response.ok) {
      throw new Error(
        data.detail || `Ошибка загрузки файла: ${file.name}`
      );
    }

    uploaded.push(data.filename);
  }

  if(statusEl) {
    statusEl.textContent = `✓ Прикреплено: ${uploaded.length}`;
    statusEl.className = "client-chat-media-status success";
  }

  return uploaded;
};

window.sendMessage = async (type, id) => {
  const input = document.getElementById(`input-${type}-${id}`);

  if(!input) return;

  const text = input.value.trim();

  const mediaInput =
    document.getElementById(`media-${type}-${id}`);

  const files = mediaInput
    ? Array.from(mediaInput.files || [])
    : [];

  if(!text && !files.length) return;

  if(files.length > 10) {
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  const btn =
    document.getElementById(`send-${type}-${id}`);

  const mediaStatus =
    document.getElementById(`media-status-${type}-${id}`);

  btn.disabled = true;
  btn.textContent = "...";

  const key = feedKey(type, id);
  let newMonitoringId = null;

  // Карточка после отправки должна остаться раскрытой.
  expandedFeedItems.add(key);

  try {
    if(type === "task") {
      if(myRole === "director") {
        await API("/api/director/comments", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...authHeaders()
          },
          body: JSON.stringify({
            item_type: "task",
            item_id: id,
            text
          })
        });
      } else {
        const uploaded = files.length
          ? await uploadClientChatMedia(files, mediaStatus)
          : [];

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
      }
    } else {
      // Для мониторинга id карточки = report_id.
      // Новый отчет создается для address_id текущей карточки.
      const item = allFeed.find(
        x => x.type === "monitoring" && x.id === id
      );

      if(!item || !item.address_id) {
        throw new Error("Не удалось определить адрес мониторинга");
      }

      const uploaded = files.length
        ? await uploadClientChatMedia(files, mediaStatus)
        : [];

      const monitoringResult = await API("/api/monitoring", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          address_id: item.address_id,
          text: text,
          media: uploaded
        })
      });

      // После отправки должна открыться именно новая карточка.
      if(monitoringResult && monitoringResult.report_id) {
        newMonitoringId = monitoringResult.report_id;

        expandedFeedItems.delete(key);
        expandedFeedItems.add(
          feedKey("monitoring", newMonitoringId)
        );
      }
    }

    input.value = "";

    if(mediaInput) {
      mediaInput.value = "";
    }

    await loadFeed();

    // После перерисовки снова прокручиваем чат вниз.
    // Для мониторинга используем ID только что созданного report.
    const boxId =
      type === "monitoring" && newMonitoringId
        ? newMonitoringId
        : id;

    const box =
      document.getElementById(`chat-${type}-${boxId}`);

    if(box) {
      requestAnimationFrame(() => {
        box.scrollTop = box.scrollHeight;
      });
    }

  } catch(e) {
    toast(e.message || "Ошибка отправки", true);
  }

  btn.disabled = false;
  btn.textContent = "➤";
};

const loadFeed = async () => {
  try {
    allFeed = await API("/api/feed", {headers: authHeaders()});
    applyFilters();
  } catch(e) { document.getElementById("feed").innerHTML = '<div style="color:var(--err);padding:20px">Ошибка загрузки ленты: ' + e.message + '</div>'; }
};

const updateStats = (filteredFeed = allFeed) => {
  const tasks = filteredFeed.filter(i => i.type === "task");
  const monitoring = filteredFeed.filter(i => i.type === "monitoring");

  const set = (id, value) => {
    const el = document.getElementById(id);
    if(el) el.textContent = value;
  };

  set("statTasksTotal", tasks.length);
  set("statTasksNew", tasks.filter(i => i.workflow_status === "open").length);
  set("statTasksOverdue", tasks.filter(i => ["open", "rework"].includes(i.workflow_status || "open") && i.is_overdue).length);
  set("statTasksImportant", tasks.filter(i => i.is_important).length);
  set("statTasksDone", tasks.filter(i => ["completed", "closed"].includes(i.workflow_status)).length);

  set("statMonitoringTotal", monitoring.length);
  set("statMonitoringMessages", monitoring.filter(i => i.last_message_at).length);

  const today = new Date().toISOString().slice(0, 10);
  set("statMonitoringToday",
    monitoring.filter(i => (i.monitoring_date || i.created_at || "").slice(0, 10) === today).length
  );

  set("statDirectorComments",
    filteredFeed.filter(i => i.has_director_comment).length
  );
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

  // Состояние.
  if(stateFilter === "new") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.workflow_status === "open"
    );

  } else if(stateFilter === "pending_review") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.workflow_status === "pending_review"
    );

  } else if(stateFilter === "rework") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.workflow_status === "rework"
    );

  } else if(stateFilter === "overdue") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      ["open", "pending_review", "rework"].includes(
        i.workflow_status || "open"
      ) &&
      i.is_overdue
    );

  } else if(stateFilter === "important") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.is_important
    );

  } else if(stateFilter === "done") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.workflow_status === "completed"
    );

  } else if(stateFilter === "closed") {
    filtered = filtered.filter(i =>
      i.type === "task" &&
      i.workflow_status === "closed"
    );

  } else if(stateFilter === "director") {
    filtered = filtered.filter(i =>
      i.has_director_comment
    );
  }

  // Автор.
  const authorFilter =
    document.getElementById("filterAuthor")?.value || "";

  if(authorFilter) {
    filtered = filtered.filter(i =>
      String(i.created_by_role || "").toLowerCase() ===
      authorFilter.toLowerCase()
    );
  }

  // Поставщик.
  const supplierFilter =
    document.getElementById("filterSupplier")?.value || "";

  if(supplierFilter) {
    filtered = filtered.filter(i =>
      String(i.manufacturer || "").trim() === supplierFilter
    );
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

  // Только одна активная сортировка.
  if(sortMode === "message") {
    filtered.sort((a, b) => {
      const av = getTime(a.last_message_at || a.created_at);
      const bv = getTime(b.last_message_at || b.created_at);
      return sortDirection === "asc" ? av - bv : bv - av;
    });
  } else if(sortMode === "date") {
    filtered.sort((a, b) => {
      const av = getTime(a.created_at);
      const bv = getTime(b.created_at);
      return sortDirection === "asc" ? av - bv : bv - av;
    });
  } else if(sortMode === "number") {
    filtered.sort((a, b) => {
      const av = a.type === "task"
        ? Number(a.id)
        : getTime(a.monitoring_date || a.created_at);

      const bv = b.type === "task"
        ? Number(b.id)
        : getTime(b.monitoring_date || b.created_at);

      return sortDirection === "asc" ? av - bv : bv - av;
    });
  }

  renderFeed(filtered);
  updateStats(filtered);
};

// ===== ТИП =====
document.querySelectorAll(".type-filter[data-filter]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".type-filter[data-filter]")
      .forEach(b => b.classList.remove("active"));

    btn.classList.add("active");
    currentFilter = btn.dataset.filter;

    applyFilters();
  });
});

// ===== СОСТОЯНИЕ =====
document.querySelectorAll(".state-filter[data-state]").forEach(btn => {
  btn.addEventListener("click", () => {
    const value = btn.dataset.state;

    if(stateFilter === value) {
      stateFilter = "";
      btn.classList.remove("active");
    } else {
      document.querySelectorAll(".state-filter[data-state]")
        .forEach(b => b.classList.remove("active"));

      stateFilter = value;
      btn.classList.add("active");
    }

    applyFilters();
  });
});

// ===== АВТОР =====
document.getElementById("filterAuthor")?.addEventListener("change", () => {
  applyFilters();
});

// ===== ПОСТАВЩИК =====
document.getElementById("filterSupplier")?.addEventListener("change", () => {
  applyFilters();
});

// ===== СОРТИРОВКА =====
document.querySelectorAll(".sort-filter[data-sort]").forEach(btn => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.sort;

    if(sortMode === mode) {
      sortDirection = sortDirection === "desc" ? "asc" : "desc";
    } else {
      sortMode = mode;
      sortDirection = mode === "number" ? "asc" : "desc";
    }

    document.querySelectorAll(".sort-filter[data-sort]")
      .forEach(b => b.classList.remove("active"));

    btn.classList.add("active");
    btn.dataset.direction = sortDirection;

    document.querySelectorAll(".sort-filter[data-sort]").forEach(b => {
      const arrow = b.querySelector(".sort-direction");
      if(arrow && b.classList.contains("active")) {
        arrow.textContent = sortDirection === "asc" ? "↑" : "↓";
      }
    });

    applyFilters();
  });
});

// ===== ВЫДВИЖНЫЕ ПАНЕЛИ =====
const filtersToggle = document.getElementById("filtersToggle");
const statsToggle = document.getElementById("statsToggle");
const filtersPanel = document.getElementById("filtersPanel");
const statsPanel = document.getElementById("statsPanel");

filtersToggle.onclick = () => {
  const open = filtersPanel.classList.toggle("open");
  filtersToggle.classList.toggle("open", open);
};

statsToggle.onclick = () => {
  const open = statsPanel.classList.toggle("open");
  statsToggle.classList.toggle("open", open);
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
const monMediaInput = document.getElementById("monMedia");

if(monMediaInput) {
  monMediaInput.addEventListener("change", () => {
    const status = document.getElementById("monMediaStatus");
    const files = Array.from(monMediaInput.files || []);

    if(!status) return;

    if(!files.length) {
      status.textContent = "Файлы не выбраны";
      status.className = "client-chat-media-status";
      return;
    }

    if(files.length > 10) {
      status.textContent = `Выбрано ${files.length} файлов — максимум 10`;
      status.className = "client-chat-media-status error";
      return;
    }

    status.textContent = `✓ Выбрано файлов: ${files.length}`;
    status.className = "client-chat-media-status success";
  });
}

document.getElementById("monitoringBtn").onclick = async () => {
  document.getElementById("monitoringModal").classList.add("active");

  await loadServiceMonitoringAddresses();

  document.getElementById("monCity").value = "";
  document.getElementById("monAddrId").value = "";
  document.getElementById("monAddrSearch").value = "";
  document.getElementById("monText").value = "";

  const mediaInput = document.getElementById("monMedia");
  if(mediaInput) mediaInput.value = "";
};

// ============================================================
// Адреса мониторинга Сервиса
// Поведение как у Admin / Director при создании задания
// ============================================================

const escServiceAddress = (value) => {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

let serviceMonitoringAddresses = [];

const loadServiceMonitoringAddresses = async () => {
  try {
    serviceMonitoringAddresses = await API("/api/addresses?limit=10000");

    const cityList = document.getElementById("monCityList");

    if(cityList) {
      const cities = [...new Set(
        serviceMonitoringAddresses
          .map(a => String(a.city || "").trim())
          .filter(Boolean)
      )].sort((a, b) => a.localeCompare(b, "ru"));

      cityList.innerHTML = cities
        .map(city => `<option value="${escServiceAddress(city)}"></option>`)
        .join("");
    }

    updateServiceMonitoringAddressList();

  } catch(e) {
    serviceMonitoringAddresses = [];
    toast("Не удалось загрузить адреса", true);
  }
};

const updateServiceMonitoringAddressList = () => {
  const cityInput = document.getElementById("monCity");
  const addressInput = document.getElementById("monAddrSearch");
  const addressId = document.getElementById("monAddrId");
  const addressList = document.getElementById("monAddrList");

  if(!cityInput || !addressInput || !addressId || !addressList) return;

  const city = cityInput.value.trim().toLowerCase();

  addressList.innerHTML = "";
  addressId.value = "";

  if(!city) {
    addressInput.value = "";
    addressInput.placeholder = "Сначала выберите город...";
    return;
  }

  const matches = serviceMonitoringAddresses
    .filter(a =>
      String(a.city || "").trim().toLowerCase() === city
    )
    .sort((a, b) =>
      String(a.address_name || "").localeCompare(
        String(b.address_name || ""),
        "ru"
      )
    );

  addressList.innerHTML = matches
    .map(a => `<option value="${escServiceAddress(a.address_name || "")}"></option>`)
    .join("");

  addressInput.placeholder =
    matches.length
      ? "Начните вводить адрес..."
      : "Адресов для города нет";

  const selected = matches.find(a =>
    String(a.address_name || "").trim().toLowerCase() ===
    addressInput.value.trim().toLowerCase()
  );

  if(selected) {
    addressId.value = selected.id;
  }
};

const resolveServiceMonitoringAddress = () => {
  const cityInput = document.getElementById("monCity");
  const addressInput = document.getElementById("monAddrSearch");
  const addressId = document.getElementById("monAddrId");

  if(!cityInput || !addressInput || !addressId) return null;

  const city = cityInput.value.trim().toLowerCase();
  const name = addressInput.value.trim().toLowerCase();

  const address = serviceMonitoringAddresses.find(a =>
    String(a.city || "").trim().toLowerCase() === city &&
    String(a.address_name || "").trim().toLowerCase() === name
  );

  addressId.value = address ? address.id : "";

  return address || null;
};

document.getElementById("monCity").addEventListener("input", () => {
  updateServiceMonitoringAddressList();
});

document.getElementById("monAddrSearch").addEventListener("input", () => {
  resolveServiceMonitoringAddress();
});

window.createMonitoring = async () => {
  const selectedAddress = resolveServiceMonitoringAddress();
  const addrId = document.getElementById("monAddrId").value;
  const text = document.getElementById("monText").value.trim();
  const mediaInput = document.getElementById("monMedia");
  const files = mediaInput ? Array.from(mediaInput.files || []) : [];

  if(!selectedAddress || !addrId || !text){
    toast(
      !selectedAddress
        ? "Выберите адрес из списка"
        : "Заполните адрес и текст",
      true
    );
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

window.toggleImportant = async (taskId, value) => {
  try {
    await API(`/api/tasks/${taskId}`, { method:"PATCH", headers:{"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({ is_important: value }) });
    toast(value ? "⭐ Важное" : "☆ Обычное");
    loadFeed();
  } catch(e) { toast(e.message, true); }
};


window.completeTask = async (taskId) => {
  try {
    await API(`/api/tasks/${taskId}/complete`, {
      method: "POST",
      headers: authHeaders()
    });

    toast("✓ Задание отправлено на проверку");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка", true);
  }
};

window.approveTask = async (taskId) => {
  if(!confirm("Подтвердить выполнение задания?")) return;

  try {
    await API(`/api/tasks/${taskId}/approve`, {
      method: "POST",
      headers: authHeaders()
    });

    toast("✓ Задание выполнено");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка", true);
  }
};

window.reworkTask = async (taskId) => {
  if(!confirm("Отправить задание на доработку?")) return;

  try {
    await API(`/api/tasks/${taskId}/rework`, {
      method: "POST",
      headers: authHeaders()
    });

    toast("Задание отправлено на доработку");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка", true);
  }
};

window.closeWorkflowTask = async (taskId) => {
  if(!confirm("Действительно хотите закрыть задание?")) return;

  try {
    await API(`/api/tasks/${taskId}/close`, {
      method: "POST",
      headers: authHeaders()
    });

    toast("✓ Задание закрыто");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка", true);
  }
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
  myLogin = me.login;
  myRole = me.role;

  document.getElementById("whoami").textContent = me.login;

  // Статистика недоступна для роли Сервис
  if (myRole === "service") {
    const statsColumn = document.getElementById("statsColumn");
    console.log("SERVICE: statsColumn =", statsColumn);
    if (statsColumn) {
      statsColumn.style.display = "none";
      console.log("SERVICE: statsColumn display =", statsColumn.style.display);
    }

    const miscFilterBlock = document.getElementById("miscFilterBlock");
    if (miscFilterBlock) {
      miscFilterBlock.style.display = "none";
    }
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
  document.getElementById("logout").onclick = () => { localStorage.clear(); location.href = "/"; };
  
  loadFeed();
})();
