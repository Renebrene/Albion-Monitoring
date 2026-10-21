const API = (path, opts = {}) => window.apiFetch(path, opts);

const authHeaders = () => ({
  "Authorization": "Bearer " + localStorage.getItem("token")
});

const esc = value => {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

const formatDate = value => {
  if (!value) return "";

  const d = new Date(
    String(value).includes("T")
      ? value
      : String(value) + "Z"
  );

  if (Number.isNaN(d.getTime())) {
    return String(value);
  }

  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  });
};


/* ============================================================
   MEDIA
   ============================================================ */

const renderMedia = media => {
  if (!Array.isArray(media) || !media.length) {
    return "";
  }

  return `
    <div class="report-media">
      ${media.map(item => {
        const url = item?.url || "";

        if (!url) {
          return "";
        }

        if (item.media_type === "video") {
          return `
            <a
              href="${esc(url)}"
              target="_blank"
              rel="noopener"
            >
              <video
                src="${esc(url)}"
                controls
                preload="metadata"
              ></video>
            </a>
          `;
        }

        return `
          <a
            href="${esc(url)}"
            target="_blank"
            rel="noopener"
          >
            <img
              src="${esc(url)}"
              alt="Фото"
              loading="lazy"
            >
          </a>
        `;
      }).join("")}
    </div>
  `;
};


/* ============================================================
   COMMENT MEDIA
   ============================================================ */

const selectedMedia = {};

const renderSelectedMedia = reportId => {
  const container =
    document.getElementById(`selected-media-${reportId}`);

  if (!container) return;

  const files = selectedMedia[reportId] || [];

  if (!files.length) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = files.map((file, index) => {
    const type =
      file.type.startsWith("video/")
        ? "видео"
        : "фото";

    return `
      <div class="selected-media-item">
        <span class="selected-media-name">
          ${esc(file.name)}
        </span>

        <span class="selected-media-type">
          ${type}

          <button
            type="button"
            data-remove-media="${reportId}"
            data-media-index="${index}"
            style="
              margin-left:6px;
              border:0;
              background:transparent;
              color:#dc2626;
              cursor:pointer;
              font-size:16px;
            "
            title="Удалить"
          >×</button>
        </span>
      </div>
    `;
  }).join("");

  container
    .querySelectorAll("[data-remove-media]")
    .forEach(button => {
      button.addEventListener("click", () => {
        const id = Number(button.dataset.removeMedia);
        const index = Number(button.dataset.mediaIndex);

        if (!selectedMedia[id]) return;

        selectedMedia[id].splice(index, 1);
        renderSelectedMedia(id);
      });
    });
};


const handleMediaSelection = (reportId, event) => {
  const files = Array.from(event.target.files || []);

  if (!files.length) {
    return;
  }

  if (!selectedMedia[reportId]) {
    selectedMedia[reportId] = [];
  }

  if (selectedMedia[reportId].length + files.length > 10) {
    showFormMessage(
      reportId,
      "Можно прикрепить не более 10 файлов",
      "error"
    );

    event.target.value = "";
    return;
  }

  selectedMedia[reportId].push(...files);

  event.target.value = "";

  renderSelectedMedia(reportId);
};


/* ============================================================
   UPLOAD
   ============================================================ */

const uploadMedia = async files => {
  const uploaded = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];

    const formData = new FormData();
    formData.append("file", file);

    const data = await window.apiFetch("/api/upload", {
      method: "POST",
      body: formData
    });

    if (data.filename) {
      uploaded.push(data.filename);
    }
  }

  return uploaded;
};


/* ============================================================
   FORM MESSAGE
   ============================================================ */

const showFormMessage = (reportId, text, type = "") => {
  const el =
    document.getElementById(`form-message-${reportId}`);

  if (!el) return;

  el.textContent = text;
  el.className =
    "form-message" +
    (type ? " " + type : "");
};


/* ============================================================
   COMMENTS
   ============================================================ */

const loadComments = async reportId => {
  const container =
    document.getElementById(`comments-${reportId}`);

  if (!container) return;

  container.innerHTML = `
    <div class="comment-empty">
      Загрузка комментариев...
    </div>
  `;

  try {
    const comments =
      await API(`/api/head/reports/${reportId}/comments`, { headers: authHeaders() });

    renderComments(reportId, comments);

  } catch (e) {
    container.innerHTML = `
      <div class="comment-empty">
        Не удалось загрузить комментарии
      </div>
    `;
  }
};


const renderComments = (reportId, comments) => {
  const container =
    document.getElementById(`comments-${reportId}`);

  if (!container) return;

  if (!Array.isArray(comments) || !comments.length) {
    container.innerHTML = `
      <div class="comment-empty">
        Комментариев пока нет
      </div>
    `;
    return;
  }

  container.innerHTML = comments.map(comment => `
    <div class="comment">
      <div class="comment-meta">
        ${esc(comment.author_login || "")}
        ·
        ${esc(formatDate(comment.created_at))}
      </div>

      ${
        comment.text
          ? `<div class="comment-text">${esc(comment.text)}</div>`
          : ""
      }

      ${renderMedia(comment.media)}
    </div>
  `).join("");
};


/* ============================================================
   SEND COMMENT
   ============================================================ */

const sendComment = async reportId => {
  const textarea =
    document.getElementById(`comment-text-${reportId}`);

  const button =
    document.getElementById(`comment-send-${reportId}`);

  const text =
    textarea?.value.trim() || "";

  const files =
    selectedMedia[reportId] || [];

  if (!text && !files.length) {
    showFormMessage(
      reportId,
      "Добавьте комментарий или файл",
      "error"
    );
    return;
  }

  try {
    if (button) {
      button.disabled = true;
      button.textContent = "Отправка...";
    }

    const uploaded =
      files.length
        ? await uploadMedia(files)
        : [];

    await API(
      `/api/head/reports/${reportId}/comments`,
      {
        method: "POST",
        headers: {
          ...authHeaders(),
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          text,
          media: uploaded
        })
      }
    );

    if (textarea) {
      textarea.value = "";
    }

    selectedMedia[reportId] = [];
    renderSelectedMedia(reportId);

    showFormMessage(
      reportId,
      "Комментарий добавлен",
      "success"
    );

    await loadComments(reportId);

  } catch (e) {
    showFormMessage(
      reportId,
      e.message || "Не удалось отправить комментарий",
      "error"
    );

  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Отправить комментарий";
    }
  }
};


/* ============================================================
   REPORTS
   ============================================================ */

const renderReports = reports => {
  const container =
    document.getElementById("headReports");

  const message =
    document.getElementById("headMessage");

  if (!container) return;

  if (!Array.isArray(reports) || !reports.length) {
    container.innerHTML = "";

    if (message) {
      message.textContent =
        "Отчётов экипажа пока нет";
    }

    return;
  }

  if (message) {
    message.textContent =
      `Всего отчётов: ${reports.length}`;
  }

  container.innerHTML = reports.map(report => {
    const city =
      report.city || "";

    const address =
      report.address_name || "Адрес";

    const date =
      formatDate(report.created_at);

    const text =
      String(report.text || "").trim();

    const mediaCount =
      Array.isArray(report.media)
        ? report.media.length
        : 0;

    return `
      <article class="report-card">

        <button
          type="button"
          class="report-summary"
          data-report-toggle="${report.id}"
        >
          <div class="report-summary-main">
            <div class="report-number">
              Мониторинг №${report.id}
              ${mediaCount ? `
                <span
                  class="crew-media-indicator"
                  title="Прикреплено файлов: ${mediaCount}"
                >
                  📎 ${mediaCount}
                </span>
              ` : ""}
            </div>

            <div class="report-address">
              ${esc(address)}
            </div>

            <div class="report-city">
              ${esc(city)}
            </div>
          </div>

          <div class="report-date">
            ${esc(date)}
          </div>
        </button>

        <div
          class="report-details"
          id="report-details-${report.id}"
        >

          <div class="report-meta">
            Автор отчёта:
            <strong>${esc(report.author_login || "—")}</strong>
          </div>

          ${
            text
              ? `<div class="report-text">${esc(text)}</div>`
              : ""
          }

          ${renderMedia(report.media)}

          <div class="comments-title">
            Комментарии
          </div>

          <div id="comments-${report.id}">
            <div class="comment-empty">
              Загрузка комментариев...
            </div>
          </div>

          <div class="comment-form">

            <textarea
              class="comment-textarea"
              id="comment-text-${report.id}"
              placeholder="Введите комментарий..."
            ></textarea>

            <label
              class="media-button"
              for="comment-media-${report.id}"
            >
              <span class="media-button-icon">📷</span>
              <span>Добавить фото/видео</span>
            </label>

            <input
              class="media-input"
              id="comment-media-${report.id}"
              type="file"
              accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v"
              multiple
            >

            <div
              class="selected-media"
              id="selected-media-${report.id}"
            ></div>

            <button
              type="button"
              class="comment-send"
              id="comment-send-${report.id}"
              data-send-comment="${report.id}"
            >
              Отправить комментарий
            </button>

            <div
              class="form-message"
              id="form-message-${report.id}"
            ></div>

          </div>

        </div>

      </article>
    `;
  }).join("");

  container
    .querySelectorAll("[data-report-toggle]")
    .forEach(button => {
      button.addEventListener("click", async () => {
        const id =
          Number(button.dataset.reportToggle);

        const details =
          document.getElementById(
            `report-details-${id}`
          );

        if (!details) return;

        const wasOpen =
          details.classList.contains("open");

        details.classList.toggle("open");

        if (!wasOpen) {
          await loadComments(id);
        }
      });
    });

  container
    .querySelectorAll("[data-send-comment]")
    .forEach(button => {
      button.addEventListener("click", () => {
        sendComment(
          Number(button.dataset.sendComment)
        );
      });
    });

  reports.forEach(report => {
    const input =
      document.getElementById(
        `comment-media-${report.id}`
      );

    if (input) {
      input.addEventListener(
        "change",
        event =>
          handleMediaSelection(
            report.id,
            event
          )
      );
    }
  });
};


const loadReports = async () => {
  const message =
    document.getElementById("headMessage");

  try {
    const reports =
      await API("/api/head/reports", { headers: authHeaders() });

    renderReports(reports);

  } catch (e) {
    if (message) {
      message.textContent =
        "Не удалось загрузить отчёты";
    }
  }
};


/* ============================================================
   INIT
   ============================================================ */

document.addEventListener("DOMContentLoaded", async () => {

  const logout =
    document.getElementById("logout");

  if (logout) {
    logout.addEventListener("click", () => logoutCurrentSession("/"));
  }

  await loadReports();
});
