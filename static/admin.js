
const authHeaders = () => ({
  "Authorization": "Bearer " + localStorage.getItem("token")
});


function renderMonitoringMedia(media) {
  if(!Array.isArray(media) || !media.length) return "";

  return `
    <div class="feed-media-block">
      <div class="feed-media-title">Прикреплённые фото и видео</div>

      <div class="feed-media-list">
        ${media.map((m, index) => {
          let url = "";
          let type = "";

          if(m && typeof m === "object") {
            url = String(m.url || "");
            type = String(m.type || m.media_type || "");
          } else {
            const name = String(m || "");
            url = name.startsWith("/uploads/")
              ? name
              : `/uploads/${encodeURIComponent(name)}`;
          }

          if(!url) return "";

          const isVideo =
            type === "video" ||
            /\.(mp4|mov|webm|m4v)$/i.test(url);

          return `
            <div class="feed-media-item">
              ${
                isVideo
                  ? `
                    <video
                      src="${esc(url)}"
                      controls
                      preload="metadata"
                    ></video>
                  `
                  : `
                    <img
                      src="${esc(url)}"
                      alt="Вложение ${index + 1}"
                      loading="lazy"
                      onclick="event.stopPropagation(); window.open('${esc(url)}', '_blank')"
                    >
                  `
              }
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}function renderDirectorCommentMedia(media) {
  if(!Array.isArray(media) || !media.length) return "";

  return `
    <div class="comment-attachments">
      ${media.map((m, index) => {
        const name = String(m || "");
        const url = name.startsWith("/uploads/")
          ? name
          : `/uploads/${encodeURIComponent(name)}`;

        const isVideo = /\\.(mp4|mov|webm|m4v)$/i.test(name);

        return `
          <div class="comment-attachment">
            <span class="comment-attachment-icon">${isVideo ? "🎬" : "📎"}</span>
            <a
              class="comment-attachment-name"
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
    </div>
  `;
}


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

const API = async (url, options = {}) => {
  const token = localStorage.getItem("token");

  const headers = {
    ...(options.headers || {}),
    ...(token ? {"Authorization": `Bearer ${token}`} : {})
  };

  const res = await fetch(url, {...options, headers});

  if(!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const data = await res.json();
      msg = data.detail || msg;
    } catch {}
    throw new Error(msg);
  }

  return res.json();
};

let myLogin = "";
let myRole = "";

let currentFilter = "all";
let stateFilter = "";
let supplierFilter = "";

let sortMode = "message";
let sortDirection = "desc";

const expandedFeedItems = new Set();

const esc = value => String(value ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");

const fmtDate = iso => {
  if(!iso) return "";

  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));

  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
};

const fmtMonitoringDate = iso => {
  if(!iso) return "";

  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));

  const day = String(d.getDate()).padStart(2, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const year = String(d.getFullYear()).slice(-2);

  return `${day}-${month}-${year}`;
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

const fmtMonitoringTime = iso => {
  if(!iso) return "";

  const d = new Date(iso + (iso.includes("T") ? "" : "Z"));

  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");

  return `${hours}-${minutes}`;
};

const feedKey = (type, id) => `${type}-${id}`;

const getTime = value => {
  if(!value) return 0;

  const d = new Date(value + (value.includes("T") ? "" : "Z"));

  return d.getTime() || 0;
};

const toast = message => {
  let el = document.getElementById("adminToast");

  if(!el) {
    el = document.createElement("div");
    el.id = "adminToast";
    el.style.cssText = `
      position:fixed;
      left:50%;
      bottom:24px;
      transform:translateX(-50%);
      background:#111827;
      color:#fff;
      padding:10px 16px;
      border-radius:10px;
      z-index:9999;
      font-size:14px;
      box-shadow:0 4px 18px rgba(0,0,0,.25);
    `;
    document.body.appendChild(el);
  }

  el.textContent = message;
  el.style.display = "block";

  clearTimeout(el._timer);

  el._timer = setTimeout(() => {
    el.style.display = "none";
  }, 2500);
};

window.toggleExpand = (type, id, event) => {
  if(event && event.target.closest("button, textarea, input, select, a, label")) {
    return;
  }

  const key = feedKey(type, id);
  const el = document.getElementById(`item-${type}-${id}`);

  if(!el) return;

  el.classList.toggle("expanded");

  if(el.classList.contains("expanded")) {
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

  return last && last.changed_at
    ? fmtDate(last.changed_at)
    : "";
};

const renderFeed = items => {
  const feed = document.getElementById("feed");

  if(!items.length) {
    feed.innerHTML = `
      <div style="text-align:center;color:var(--muted);padding:40px">
        Событий не найдено
      </div>
    `;
    return;
  }

  feed.innerHTML = items.map(item => {
    const isTask = item.type === "task";
    const isCrewReport = item.type === "crew_report";

    const typeBadge = isCrewReport
      ? '<span class="type-badge" style="background:#2563eb;color:#fff">ОТЧЕТ ЭКИПАЖ1</span>'
      : isTask
        ? (
            item.author_role === "director"
              ? '<span class="type-badge director-type-task-director">ЗАДАНИЕ</span>'
              : '<span class="type-badge director-type-task">ЗАДАНИЕ</span>'
          )
        : '<span class="type-badge director-type-monitoring">МОНИТОРИНГ</span>';

    let statusBadge = "";

    if(isTask) {
      const ws = item.workflow_status || "open";
      const label = workflowStatusLabel(item);
      const changed = workflowLastChange(item);

      let cls = "";

      if(ws === "pending_review") {
        cls = "director-status-review";
      } else if(ws === "rework") {
        cls = item.created_by_role === "director"
          ? "director-status-rework-director"
          : "director-status-rework-admin";
      } else if(ws === "completed" || ws === "closed") {
        cls = "director-status-done";
      } else if(item.is_overdue) {
        cls = "director-status-overdue";
      } else if(item.created_by_role === "director") {
        cls = "director-status-open-director";
      } else {
        cls = "director-status-open-admin";
      }

      const overdueMark =
        item.is_overdue && ["open", "rework"].includes(ws)
          ? " !!!"
          : "";

      statusBadge = `
        <span class="director-status ${cls}">
          ${esc(label)}${overdueMark}
          ${changed ? ` · ${esc(changed)}` : ""}
        </span>
      `;
    }

    const importantIcon =
      isTask && item.is_important
        ? '<span class="director-important" title="Важное">⭐</span>'
        : '';

    const directorMark = item.has_director_comment
      ? '<span class="director-mark" title="Есть комментарий директора"></span>'
      : '';

    const messages = item.messages || [];

    // Медиа самого задания хранится в item.media.
    // Для мониторинга медиа находится внутри messages[].media.
    const itemMedia = isTask
      ? (Array.isArray(item.media) ? item.media : [])
      : messages.flatMap(m => Array.isArray(m.media) ? m.media : []);

    const mediaCount = itemMedia.length;

    const mediaIcon = mediaCount
      ? `
        <span
          class="director-media-indicator"
          title="Прикреплено файлов: ${mediaCount}"
          onclick="event.stopPropagation()"
        >📎 ${mediaCount}</span>
      `
      : "";

    let lastMsgHtml = '<span class="director-last-message-empty">Сообщений нет</span>';

    if(item.last_message_at) {
      lastMsgHtml = `
        <span class="director-last-message">
          Последнее сообщение:
          ${esc(fmtDate(item.last_message_at))}
          ·
          ${esc(item.last_message_author || "Неизвестно")}
        </span>
      `;
    }

    let lastFeedComment = null;
    let lastFeedCommentLabel = "Директор";

    if(isCrewReport) {
      const headComments = Array.isArray(item.head_comments)
        ? item.head_comments
        : [];

      if(headComments.length) {
        lastFeedComment = headComments[headComments.length - 1];
        lastFeedCommentLabel = "Директор";
      }
    } else if(Array.isArray(item.director_comments) && item.director_comments.length) {
      lastFeedComment =
        item.director_comments[item.director_comments.length - 1];
    } else {
      lastFeedComment =
        [...messages]
          .reverse()
          .find(m => m.is_director_comment === true) || null;
    }

    const directorCommentText = lastFeedComment
      ? String(lastFeedComment.text || "")
      : "";

    const directorCommentPreview = esc(directorCommentText)
      .replace(/\s+/g, " ")
      .trim();

    const shortDesc = isTask
      ? String(item.text || "")
      : String(
          item.description ||
          item.text ||
          ""
        );

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

    const numberLabel = isTask
      ? `№${item.id}`
      : fmtMonitoringDate(item.monitoring_date || item.created_at);

    const directorComments = Array.isArray(item.director_comments)
      ? item.director_comments
      : [];

    const headComments = isCrewReport && Array.isArray(item.head_comments)
      ? item.head_comments
      : [];

    const headCommentsHtml = headComments.map(c => `
      <div class="director-comment">
        <div class="director-comment-label">
          Директор (${esc(fmtDirectorDate(c.created_at))})
        </div>
        <div class="director-comment-text">
          ${esc(String(c.text || "")).replace(/\n/g, "<br>")}
          ${renderDirectorCommentMedia(c.media)}
        </div>
      </div>
    `).join("");

    const chatMessages = messages
      .filter(m => m.is_director_comment !== true)
      .sort((a, b) => {
        const ta = new Date(a.created_at || 0).getTime();
        const tb = new Date(b.created_at || 0).getTime();
        return ta - tb;
      });

    const directorCommentsHtml = directorComments.map(c => `
      <div class="director-comment">
        <div class="director-comment-label">
          Директор (${esc(fmtDirectorDate(c.created_at))})
        </div>
        <div class="director-comment-text">
          ${esc(String(c.text || "")).replace(/\\n/g, "<br>")}
          ${renderDirectorCommentMedia(c.media)}
        </div>
      </div>
    `).join("");

    const chatHtml = chatMessages.map(m => {
      const authorName = m.author;

      return `
        <div class="chat-msg ${m.role === "service" ? "service" : "admin"}">
          <div class="chat-author">${esc(authorName)}</div>

          <div class="chat-bubble">
            ${esc(String(m.text || "")).replace(/\n/g, "<br>")}
            ${renderMonitoringMedia(m.media)}
            <div class="chat-time">${esc(fmtDate(m.created_at))}</div>
          </div>
        </div>
      `;
    }).join("") ||
      '<div style="text-align:center;color:var(--muted);font-size:12px;padding:10px">Пока нет сообщений</div>';

    window.hideFeedItem = async (itemType, itemId, event) => {
  if (event) event.stopPropagation();

  const labels = {
    task: "задание",
    monitoring: "мониторинг",
    crew_report: "отчёт экипажа"
  };

  const label = labels[itemType] || "запись";

  if (!confirm(`Скрыть ${label}?`)) return;

  try {
    await API(`/api/feed/${itemType}/${itemId}/hide`, {
      method: "POST"
    });

    toast(`✓ ${label.charAt(0).toUpperCase() + label.slice(1)} скрыто`);
    await loadFeed();
  } catch (e) {
    toast(e.message || "Не удалось скрыть запись");
  }
};

const taskActionsHtml =
      (isTask || item.type === "monitoring" || item.type === "crew_report") &&
      ["admin", "director"].includes(myRole)
        ? `
          <div
            class="task-actions"
            onclick="event.stopPropagation()"
          >
            ${item.workflow_status === "pending_review"
              ? `
                <button
                  class="btn"
                  onclick="approveDirectorTask(${item.id})"
                >✓</button>
                <button
                  class="btn"
                  onclick="reworkDirectorTask(${item.id})"
                >✕</button>
              `
              : ''
            }

            ${item.type === "task" &&
              ["open", "rework"].includes(item.workflow_status || "open") &&
              (myRole === "admin" || item.created_by_role === "director")
              ? `
                <button
                  class="btn"
                  onclick="closeDirectorTask(${item.id})"
                >✕</button>
              `
              : ''
            }

            ${myRole === "admin" && ["task", "monitoring", "crew_report"].includes(item.type)
              ? `
                <button
                  class="btn"
                  onclick="hideFeedItem('${item.type}', ${item.id}, event)"
                  title="Скрыть запись"
                >Скрыть</button>
              `
              : ''
            }
          </div>
        `
        : "";

    const key = feedKey(item.type, item.id);
    const expandedClass = expandedFeedItems.has(key) ? " expanded" : "";

    return `
      <div
        class="feed-item ${borderClass}${expandedClass}"
        id="item-${item.type}-${item.id}"
        onclick="toggleExpand('${item.type}', ${item.id}, event)"
      >

        <div class="feed-summary">

          <div class="left">

            <div class="top-row">

              <div class="director-top-main">
                ${typeBadge}
                <span class="feed-number">${esc(numberLabel)}</span>

                ${!isTask
                  ? `
                    <span class="feed-created">
                      ${esc(fmtMonitoringDate(item.monitoring_date || item.created_at))}
                      ·
                      ${esc(fmtMonitoringTime(item.monitoring_date || item.created_at))}
                    </span>
                  `
                  : ""}
              </div>

              <div class="director-top-status">
                ${statusBadge}
                ${importantIcon}
                ${taskActionsHtml}
              </div>

            </div>

            <div class="addr-line">
              <span class="addr">
                ${esc(item.city)} · ${esc(item.address_name)}
              </span>
            </div>

            <div
              class="director-short-desc"
              style="font-weight:700;font-style:italic;color:#111"
            >
              ${esc(shortDesc)}
            </div>

            <div class="bottom-row">
              ${lastMsgHtml}
            </div>

            ${lastFeedComment
              ? `
                <div class="closed-director-comment">
                  ${isCrewReport ? "" : directorMark}
                  <span>
                    <b>${esc(lastFeedCommentLabel)} (${esc(fmtDirectorDate(lastFeedComment.created_at))})</b>
                    ${directorCommentPreview}
                  </span>
                </div>
              `
              : ''
            }

          </div>

          <div class="right">
            ${mediaIcon}
            <span class="expand-arrow">⌄</span>
          </div>

        </div>

        <div class="feed-details">

          ${isTask
            ? `<div class="task-description">${esc(item.text || "").replace(/\n/g, "<br>")}</div>`
            : ''}

          ${mediaCount
            ? `
              <div class="feed-media-block">
                <div class="feed-media-title">Прикреплённые фото и видео</div>

                <div class="feed-media-list">
                  ${itemMedia.map((m, index) => {
                    const url = String(m.url || "");
                    const type = String(m.type || "");
                    const isVideo =
                      type === "video" ||
                      /\.(mp4|mov|webm|m4v)$/i.test(url);

                    return `
                      <div class="feed-media-item">
                        ${
                          isVideo
                            ? `
                              <video
                                src="${esc(url)}"
                                controls
                                preload="metadata"
                              ></video>
                            `
                            : `
                              <img
                                src="${esc(url)}"
                                alt="Вложение ${index + 1}"
                                onclick="event.stopPropagation(); window.open('${esc(url)}', '_blank')"
                              >
                            `
                        }
                      </div>
                    `;
                  }).join("")}
                </div>
              </div>
            `
            : ''
          }

          <div class="chat">
            ${chatHtml}
          </div>

          ${isCrewReport
            ? (
                headCommentsHtml
                  ? `
                    <div class="director-comments">
                      ${headCommentsHtml}
                    </div>
                  `
                  : ''
              )
            : (
                directorCommentsHtml
                  ? `
                    <div class="director-comments">
                      ${directorCommentsHtml}
                    </div>
                  `
                  : ''
              )
          }

          <div
            class="chat-input-wrap"
            onclick="event.stopPropagation()"
          >
            <input
              id="dc-media-${item.type}-${item.id}"
              type="file"
              accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v"
              multiple
              style="display:none"
              onchange="updateCommentMediaSelection(
                this,
                document.getElementById('dc-media-status-${item.type}-${item.id}'),
                document.getElementById('dc-media-list-${item.type}-${item.id}')
              )"
            >

            <button
              type="button"
              class="comment-media-attach"
              title="Прикрепить фото или видео"
              onclick="event.stopPropagation(); document.getElementById('dc-media-${item.type}-${item.id}').click()"
            >📎</button>

            <textarea
              id="dc-input-${item.type}-${item.id}"
              placeholder="Оставить комментарий..."
              rows="2"
            ></textarea>

            <button
              class="send-chat-btn"
              onclick="sendDirectorComment('${item.type}', ${item.id})"
            >
              Отправить
            </button>

            <div
              id="dc-media-status-${item.type}-${item.id}"
              class="comment-media-status"
            ></div>

            <div
              id="dc-media-list-${item.type}-${item.id}"
              class="comment-media-list"
            ></div>
          </div>

        </div>

      </div>
    `;
  }).join("");
};


const updateStats = (filteredFeed = allFeedItems) => {
  const set = (id, value) => {
    const el = document.getElementById(id);
    if(el) el.textContent = value;
  };

  const tasks = filteredFeed.filter(
    item => item.type === "task"
  );

  const monitoring = filteredFeed.filter(
    item => item.type === "monitoring"
  );

  // Тип.
  set("statTypeAll", filteredFeed.length);
  set("statTypeTasks", tasks.length);
  set("statTypeMonitoring", monitoring.length);

  // Состояние.
  set(
    "statStateNew",
    tasks.filter(item =>
      item.workflow_status === "open"
    ).length
  );

  set(
    "statStatePending",
    tasks.filter(item =>
      item.workflow_status === "pending_review"
    ).length
  );

  set(
    "statStateRework",
    tasks.filter(item =>
      item.workflow_status === "rework"
    ).length
  );

  set(
    "statStateOverdue",
    tasks.filter(item =>
      ["open", "pending_review", "rework"].includes(
        item.workflow_status || "open"
      ) &&
      item.is_overdue
    ).length
  );

  set(
    "statStateImportant",
    tasks.filter(item =>
      item.is_important
    ).length
  );

  set(
    "statStateDone",
    tasks.filter(item =>
      item.workflow_status === "completed"
    ).length
  );

  set(
    "statStateClosed",
    tasks.filter(item =>
      item.workflow_status === "closed"
    ).length
  );

  set(
    "statStateDirector",
    filteredFeed.filter(item =>
      item.has_director_comment
    ).length
  );

  // Автор.
  set(
    "statAuthorAdmin",
    filteredFeed.filter(item =>
      String(item.created_by_role || "").toLowerCase() === "admin"
    ).length
  );

  set(
    "statAuthorDirector",
    filteredFeed.filter(item =>
      String(item.created_by_role || "").toLowerCase() === "director"
    ).length
  );

  // Поставщик.
  set(
    "statSupplierLedGarant",
    filteredFeed.filter(item =>
      String(item.manufacturer || "").trim() === "Лед Гарант"
    ).length
  );

  set(
    "statSupplierVisionLed",
    filteredFeed.filter(item =>
      String(item.manufacturer || "").trim() === "Вижн Лед"
    ).length
  );
};

const applyFilters = items => {
  let result = [...items];

  // Тип.
  if(currentFilter !== "all") {
    result = result.filter(item => {
      if(currentFilter === "monitoring") {
        return item.type === "monitoring" || item.type === "crew_report";
      }

      return item.type === currentFilter;
    });
  }

  // Состояние.
  if(stateFilter === "new") {
    result = result.filter(item =>
      item.type === "task" &&
      item.workflow_status === "open"
    );

  } else if(stateFilter === "pending_review") {
    result = result.filter(item =>
      item.type === "task" &&
      item.workflow_status === "pending_review"
    );

  } else if(stateFilter === "rework") {
    result = result.filter(item =>
      item.type === "task" &&
      item.workflow_status === "rework"
    );

  } else if(stateFilter === "overdue") {
    result = result.filter(item =>
      item.type === "task" &&
      ["open", "pending_review", "rework"].includes(
        item.workflow_status || "open"
      ) &&
      item.is_overdue
    );

  } else if(stateFilter === "important") {
    result = result.filter(item =>
      item.type === "task" &&
      item.is_important
    );

  } else if(stateFilter === "done") {
    result = result.filter(item =>
      item.type === "task" &&
      item.workflow_status === "completed"
    );

  } else if(stateFilter === "closed") {
    result = result.filter(item =>
      item.type === "task" &&
      item.workflow_status === "closed"
    );

  } else if(stateFilter === "director") {
    result = result.filter(item =>
      item.has_director_comment
    );
  }

  // Автор.
  const authorFilter =
    document.getElementById("filterAuthor")?.value || "";

  if(authorFilter) {
    result = result.filter(item =>
      String(item.created_by_role || "").toLowerCase() ===
      authorFilter.toLowerCase()
    );
  }

  // Поставщик.
  const supplier =
    document.getElementById("filterSupplier")?.value || "";

  if(supplier) {
    result = result.filter(item =>
      String(item.manufacturer || "").trim() === supplier
    );
  }

  // Город.
  const cityFilter =
    document.getElementById("filterCity")?.value
      ?.trim()
      .toLowerCase() || "";

  if(cityFilter) {
    result = result.filter(item =>
      String(item.city || "").trim().toLowerCase() === cityFilter
    );
  }

  // Поиск по адресу.
  const addressSearch =
    document.getElementById("filterSearch")?.value
      ?.trim()
      .toLowerCase() || "";

  if(addressSearch) {
    result = result.filter(item =>
      String(item.address_name || "").trim().toLowerCase() === addressSearch
    );
  }

  // Сортировка.
  result.sort((a, b) => {
    let ta;
    let tb;

    if(sortMode === "number") {
      ta = Number(a.id) || 0;
      tb = Number(b.id) || 0;
    } else if(sortMode === "date") {
      ta = getTime(a.created_at);
      tb = getTime(b.created_at);
    } else {
      ta = getTime(a.last_message_at || a.created_at);
      tb = getTime(b.last_message_at || b.created_at);
    }

    const value = tb - ta;

    return sortDirection === "desc" ? value : -value;
  });

  return result;
};

let allFeedItems = [];

const refreshFeed = () => {
  const filtered = applyFilters(allFeedItems);
  renderFeed(filtered);
  updateStats(filtered);
};

const loadFeed = async () => {
  try {
    allFeedItems = await API("/api/feed");
    refreshFeed();
  } catch(e) {
    document.getElementById("feed").innerHTML = `
      <div style="
        text-align:center;
        color:#ef4444;
        padding:40px
      ">
        Ошибка загрузки ленты: ${esc(e.message)}
      </div>
    `;
  }
};


window.approveDirectorTask = async (taskId) => {
  if(!confirm("Подтвердить выполнение задания?")) return;

  try {
    await API(`/api/tasks/${taskId}/approve`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    });

    toast("✓ Задание выполнено");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка");
  }
};

window.reworkDirectorTask = async (taskId) => {
  if(!confirm("Отправить задание на доработку?")) return;

  try {
    await API(`/api/tasks/${taskId}/rework`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    });

    toast("Задание отправлено на доработку");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка");
  }
};

window.closeDirectorTask = async (taskId) => {
  if(!confirm("Действительно хотите закрыть задание?")) return;

  try {
    await API(`/api/tasks/${taskId}/close`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    });

    toast("✓ Задание закрыто");
    await loadFeed();
  } catch(e) {
    toast(e.message || "Ошибка");
  }
};

window.sendDirectorComment = async (type, id) => {
  const input = document.getElementById(`dc-input-${type}-${id}`);
  const mediaInput = document.getElementById(`dc-media-${type}-${id}`);
  const statusEl = document.getElementById(`dc-media-status-${type}-${id}`);
  const listEl = document.getElementById(`dc-media-list-${type}-${id}`);

  if(!input) return;

  const text = input.value.trim();
  const files = mediaInput
    ? Array.from(mediaInput.files || [])
    : [];

  if(!text && !files.length) {
    toast("Введите сообщение или прикрепите файл");
    return;
  }

  if(files.length > 10) {
    toast("Можно прикрепить не более 10 файлов", true);
    return;
  }

  expandedFeedItems.add(feedKey(type, id));

  const sendBtn = input.parentElement
    ? input.parentElement.querySelector(".send-chat-btn")
    : null;

  if(sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = "Отправка...";
  }

  try {
    let uploaded = [];

    if(files.length) {
      uploaded = await uploadCommentMedia(files, statusEl);
    }

    if(type === "monitoring") {
      await API("/api/director/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          item_type: "monitoring",
          item_id: id,
          text,
          media: uploaded
        })
      });
    } else if(type === "task") {
      await API("/api/comments", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          task_id: id,
          text,
          media: uploaded
        })
      });
    } else {
      throw new Error("Неизвестный тип сообщения");
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

    requestAnimationFrame(() => {
      const card = document.getElementById(`item-${type}-${id}`);

      if(card) {
        card.classList.add("expanded");
        card.scrollIntoView({
          behavior: "smooth",
          block: "nearest"
        });
      }
    });

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

    toast(e.message || "Ошибка отправки сообщения", true);
  } finally {
    if(sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = "Отправить";
    }
  }
};

let directorAddresses = [];

const loadAddresses = async () => {
  try {
    directorAddresses = await API("/api/addresses?limit=10000");

    const cityList = document.getElementById("taskCityList");

    const cities = [...new Set(
      directorAddresses
        .map(a => String(a.city || "").trim())
        .filter(Boolean)
    )].sort((a, b) => a.localeCompare(b, "ru"));

    if(cityList) {
      cityList.innerHTML = cities
        .map(city => `<option value="${esc(city)}"></option>`)
        .join("");
    }



    updateTaskAddressList();

  } catch(e) {
    directorAddresses = [];
    toast("Не удалось загрузить адреса");
  }
};

const updateTaskAddressList = () => {
  const cityInput = document.getElementById("taskCity");
  const addressInput = document.getElementById("taskAddressName");
  const addressId = document.getElementById("taskAddressId");
  const addressList = document.getElementById("taskAddressList");

  if(!cityInput || !addressInput || !addressId || !addressList) return;

  const city = cityInput.value.trim().toLowerCase();

  addressList.innerHTML = "";
  addressId.value = "";

  if(!city) {
    addressInput.value = "";
    addressInput.placeholder = "Сначала выберите город...";
    return;
  }

  const matches = directorAddresses
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
    .map(a => `<option value="${esc(a.address_name || "")}"></option>`)
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

const resolveTaskAddress = () => {
  const cityInput = document.getElementById("taskCity");
  const addressInput = document.getElementById("taskAddressName");
  const addressId = document.getElementById("taskAddressId");

  if(!cityInput || !addressInput || !addressId) return null;

  const city = cityInput.value.trim().toLowerCase();
  const name = addressInput.value.trim().toLowerCase();

  const address = directorAddresses.find(a =>
    String(a.city || "").trim().toLowerCase() === city &&
    String(a.address_name || "").trim().toLowerCase() === name
  );

  addressId.value = address ? address.id : "";

  return address || null;
};


window.openTaskModal = async () => {
  const modal = document.getElementById("taskModal");

  if(!modal) return;

  modal.classList.add("show");

  const city = document.getElementById("taskCity");
  const address = document.getElementById("taskAddressName");
  const addressId = document.getElementById("taskAddressId");
  const description = document.getElementById("taskDescription");
  const important = document.getElementById("taskImportant");

  if(city) city.value = "";
  if(address) {
    address.value = "";
    address.placeholder = "Сначала выберите город...";
  }
  if(addressId) addressId.value = "";
  if(description) description.value = "";
  if(important) important.checked = false;

  await loadAddresses();

  setTimeout(() => city?.focus(), 100);
};


window.closeTaskModal = () => {
  const modal = document.getElementById("taskModal");

  if(modal) {
    modal.classList.remove("show");
  }
};

window.createDirectorTask = async () => {
  const city = document.getElementById("taskCity");
  const addressName = document.getElementById("taskAddressName");
  const addressId = document.getElementById("taskAddressId");
  const description = document.getElementById("taskDescription");
  const important = document.getElementById("taskImportant");
  const mediaInput = document.getElementById("taskMedia");
  const files = mediaInput ? Array.from(mediaInput.files || []) : [];

  if(!city?.value.trim()) {
    toast("Выберите город");
    city?.focus();
    return;
  }

  const selectedAddress = resolveTaskAddress();

  if(!selectedAddress) {
    toast("Выберите адрес из списка");
    addressName?.focus();
    return;
  }

  if(!description?.value.trim()) {
    toast("Введите текст задания");
    description?.focus();
    return;
  }

  if(files.length > 10) {
    toast("Можно прикрепить не более 10 файлов");
    return;
  }

  const button = document.getElementById("createTaskBtn");

  if(button) {
    button.disabled = true;
    button.textContent = "Создание...";
  }

  try {
    const result = await API("/api/tasks", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        address_id: Number(addressId.value),
        description: description.value.trim(),
        is_important: Boolean(important?.checked)
      })
    });

    const taskId = result.task_id;
    const uploaded = [];

    for(const file of files) {
      const fd = new FormData();
      fd.append("file", file);

      const uploadResponse = await fetch("/api/upload", {
        method: "POST",
        headers: {
          ...(localStorage.getItem("token")
            ? {"Authorization": `Bearer ${localStorage.getItem("token")}`}
            : {})
        },
        body: fd
      });

      if(!uploadResponse.ok) {
        let message = "Ошибка загрузки файла";
        try {
          const error = await uploadResponse.json();
          message = error.detail || message;
        } catch(e) {}
        throw new Error(message);
      }

      const media = await uploadResponse.json();
      uploaded.push(media.filename);
    }

    if(uploaded.length) {
      await API(`/api/tasks/${taskId}/media`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          filenames: uploaded
        })
      });
    }

    if(mediaInput) {
      mediaInput.value = "";
    }

    closeTaskModal();

    toast(`Задание №${result.task_id} создано`);

    await loadFeed();

    const key = feedKey("task", result.task_id);
    expandedFeedItems.add(key);

    refreshFeed();

    requestAnimationFrame(() => {
      const card = document.getElementById(
        `item-task-${result.task_id}`
      );

      if(card) {
        card.classList.add("expanded");
        card.scrollIntoView({
          behavior: "smooth",
          block: "center"
        });
      }
    });

  } catch(e) {
    toast(e.message);

  } finally {
    if(button) {
      button.disabled = false;
      button.textContent = "Создать задание";
    }
  }
};

document.addEventListener("input", event => {
  if(event.target?.id === "taskCity") {
    const address = document.getElementById("taskAddressName");

    if(address) {
      address.value = "";
    }

    updateTaskAddressList();
  }

  if(event.target?.id === "taskAddressName") {
    resolveTaskAddress();
  }
});


const setActive = (selector, activeId) => {
  document.querySelectorAll(selector).forEach(el => {
    el.classList.toggle("active", el.id === activeId);
  });
};

const updateSortButtons = () => {
  const dateButton = document.getElementById("sortDate");
  const numberButton = document.getElementById("sortNumber");

  if(dateButton) {
    dateButton.classList.toggle("active", sortMode === "date");

    const icon = dateButton.querySelector(".tile-icon");

    if(icon) {
      icon.textContent =
        sortMode === "date" && sortDirection === "asc"
          ? "↑"
          : "↓";
    }
  }

  if(numberButton) {
    numberButton.classList.toggle("active", sortMode === "number");

    const icon = numberButton.querySelector(".tile-icon");

    if(icon) {
      icon.textContent =
        sortMode === "number" && sortDirection === "asc"
          ? "↑"
          : "↓";
    }
  }
};

document.addEventListener("DOMContentLoaded", async () => {

  // Сворачивание фильтров — подключаем сразу, до API-запросов.
  const filtersToggle =
    document.getElementById("filtersToggle");

  const filtersPanel =
    document.getElementById("filtersPanel");

  if(filtersToggle && filtersPanel) {
    filtersToggle.addEventListener("click", () => {
      const open = filtersPanel.classList.toggle("open");
      filtersToggle.classList.toggle("open", open);
    });
  }


  // Статистика.
  const statsToggle =
    document.getElementById("statsToggle");

  const statsPanel =
    document.getElementById("statsPanel");

  if(statsToggle && statsPanel) {
    statsToggle.addEventListener("click", () => {
      const open = statsPanel.classList.toggle("open");
      statsToggle.classList.toggle("open", open);
    });
  }


  // Авторизация.
  const authToggle =
    document.getElementById("authToggle");

  const authPanel =
    document.getElementById("authPanel");

  const authOnlineCount =
    document.getElementById("authOnlineCount");

  const authSessionsBody =
    document.getElementById("authSessionsBody");

  const formatAuthDateTime = (value) => {
    if(!value) return "—";

    const date = new Date(
      value.replace(" ", "T") + "Z"
    );

    if(Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  };

  const loadAuthSessions = async () => {
    if(!authSessionsBody) return;

    try {
      const data = await API("/api/auth-sessions");

      if(authOnlineCount) {
        authOnlineCount.textContent =
          String(data.online_count ?? 0);
      }

      const sessions = data.sessions || [];

      if(!sessions.length) {
        authSessionsBody.innerHTML = `
          <tr>
            <td colspan="3" class="auth-empty">
              Нет данных
            </td>
          </tr>
        `;
        return;
      }

      authSessionsBody.innerHTML = sessions.map(session => `
        <tr>
          <td>
            ${session.login || "—"}
            <div class="auth-role">
              ${session.role || ""}
            </div>
          </td>

          <td>
            ${formatAuthDateTime(session.login_at)}
          </td>

          <td class="${!session.logout_at ? "auth-active" : ""}">
            ${formatAuthDateTime(session.logout_at)}
          </td>
        </tr>
      `).join("");

    } catch(error) {
      console.error("Ошибка загрузки авторизаций:", error);

      if(authSessionsBody) {
        authSessionsBody.innerHTML = `
          <tr>
            <td colspan="3" class="auth-empty">
              Ошибка загрузки
            </td>
          </tr>
        `;
      }
    }
  };

  if(authToggle && authPanel) {
    authToggle.addEventListener("click", async () => {
      const open = authPanel.classList.toggle("open");
      authToggle.classList.toggle("open", open);

      if(open) {
        await loadAuthSessions();
      }
    });
  }

  const me = await API("/api/me").catch(() => null);

  if(!me || !["director", "admin"].includes(me.role)) {
    location.href = "/";
    return;
  }

  myLogin = me.login || "";
  myRole = me.role || "";

  const userInfo = document.getElementById("userInfo");

  if(userInfo) {
    userInfo.textContent =
      `${me.login} · ${me.role_label || me.role}`;
  }

  // Тип.
  document.querySelectorAll(".type-filter[data-filter]").forEach(button => {
    button.addEventListener("click", () => {
      currentFilter = button.dataset.filter || "all";

      document.querySelectorAll(".type-filter[data-filter]").forEach(b => {
        b.classList.toggle("active", b === button);
      });

      refreshFeed();
    });
  });

  // Состояние.
  document.querySelectorAll(".state-filter[data-state]").forEach(button => {
    button.addEventListener("click", () => {
      const value = button.dataset.state || "";

      if(stateFilter === value) {
        stateFilter = "";
        button.classList.remove("active");
      } else {
        stateFilter = value;

        document.querySelectorAll(".state-filter[data-state]").forEach(b => {
          b.classList.toggle("active", b === button);
        });
      }

      refreshFeed();
    });
  });

  // Автор.
  document.getElementById("filterAuthor")?.addEventListener(
    "change",
    refreshFeed
  );

  // Поставщик.
  document.getElementById("filterSupplier")?.addEventListener(
    "change",
    refreshFeed
  );

  // Сортировка.
  document.querySelectorAll(".sort-filter[data-sort]").forEach(button => {
    button.addEventListener("click", () => {
      const mode = button.dataset.sort;

      if(sortMode === mode) {
        sortDirection =
          sortDirection === "desc" ? "asc" : "desc";
      } else {
        sortMode = mode;
        sortDirection =
          mode === "number" ? "asc" : "desc";
      }

      document.querySelectorAll(".sort-filter[data-sort]").forEach(b => {
        b.classList.remove("active");

        const arrow = b.querySelector(".sort-direction");

        if(arrow) {
          arrow.textContent =
            b === button && sortDirection === "asc"
              ? "↑"
              : b === button
                ? "↓"
                : b.dataset.sort === "number"
                  ? "↑"
                  : "↓";
        }
      });

      button.classList.add("active");

      refreshFeed();
    });
  });

  // Города для фильтра — из общего справочника API
  const cities = await API("/api/cities");
  const citySel = document.getElementById("filterCity");

  if(citySel) {
    cities.forEach(c => {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      citySel.appendChild(o);
    });
  }

  await loadAddresses();

  const filterCity = document.getElementById("filterCity");
  const filterAddress = document.getElementById("filterSearch");

  const updateAddressFilter = () => {
    if(!filterAddress) return;

    const selectedCity =
      filterCity?.value?.trim().toLowerCase() || "";

    const addresses = directorAddresses
      .filter(a => {
        if(!selectedCity) return true;

        return String(a.city || "")
          .trim()
          .toLowerCase() === selectedCity;
      })
      .sort((a, b) =>
        String(a.address_name || "")
          .localeCompare(
            String(b.address_name || ""),
            "ru"
          )
      );

    filterAddress.innerHTML =
      `<option value="">Все адреса</option>` +
      addresses
        .map(a =>
          `<option value="${esc(a.address_name || "")}">${
            esc(a.address_name || "")
          }</option>`
        )
        .join("");
  };

  filterCity?.addEventListener(
    "change",
    () => {
      updateAddressFilter();
      refreshFeed();
    }
  );

  filterAddress?.addEventListener(
    "change",
    refreshFeed
  );

  updateAddressFilter();

  document.getElementById("taskModal")?.addEventListener(
    "click",
    event => {
      if(event.target.id === "taskModal") {
        closeTaskModal();
      }
    }
  );

  await loadFeed();
});


// Выход из системы
document.getElementById("logout").onclick = async () => {
  try {
    await API("/api/logout", {
      method: "POST"
    });
  } catch(error) {
    console.error("Ошибка выхода:", error);
  }

  localStorage.clear();
  location.href = "/";
};
