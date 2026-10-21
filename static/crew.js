const API = (path, opts = {}) => window.apiFetch(path, opts);

const authHeaders = () => ({
  "Authorization": "Bearer " + localStorage.getItem("token")
});

let crewAddresses = [];
let selectedMediaFiles = [];

const esc = value => {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};


/* ============================================================
   ДАТА
   ============================================================ */

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
   СООБЩЕНИЯ
   ============================================================ */

const showMessage = (text, type = "") => {
  const el = document.getElementById("crewFormMessage");

  if (!el) return;

  el.textContent = text;
  el.className = "form-message" + (type ? " " + type : "");
};


/* ============================================================
   АДРЕСА
   ============================================================ */

const loadCrewAddresses = async () => {
  try {
    crewAddresses = await API("/api/addresses?limit=10000");

    const cityList = document.getElementById("crewCityList");

    if (cityList) {
      const cities = [
        ...new Set(
          crewAddresses
            .map(a => String(a.city || "").trim())
            .filter(Boolean)
        )
      ].sort((a, b) => a.localeCompare(b, "ru"));

      cityList.innerHTML = cities
        .map(city => `<option value="${esc(city)}"></option>`)
        .join("");
    }

    updateAddressList();

  } catch (e) {
    crewAddresses = [];
    showMessage("Не удалось загрузить список адресов", "error");
  }
};


const updateAddressList = () => {
  const cityInput = document.getElementById("crewCity");
  const addressInput = document.getElementById("crewAddress");
  const addressId = document.getElementById("crewAddressId");
  const addressList = document.getElementById("crewAddressList");

  if (!cityInput || !addressInput || !addressId || !addressList) {
    return;
  }

  const city = cityInput.value.trim().toLowerCase();

  addressList.innerHTML = "";
  addressId.value = "";

  if (!city) {
    addressInput.value = "";
    addressInput.placeholder = "Сначала выберите город...";
    return;
  }

  const matches = crewAddresses
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
    .map(a =>
      `<option value="${esc(a.address_name || "")}"></option>`
    )
    .join("");

  addressInput.placeholder =
    matches.length
      ? "Начните вводить адрес..."
      : "Адресов для города нет";

  resolveAddress();
};


const resolveAddress = () => {
  const cityInput = document.getElementById("crewCity");
  const addressInput = document.getElementById("crewAddress");
  const addressId = document.getElementById("crewAddressId");

  if (!cityInput || !addressInput || !addressId) {
    return null;
  }

  const city = cityInput.value.trim().toLowerCase();
  const name = addressInput.value.trim().toLowerCase();

  const address = crewAddresses.find(a =>
    String(a.city || "").trim().toLowerCase() === city &&
    String(a.address_name || "").trim().toLowerCase() === name
  );

  addressId.value = address ? address.id : "";

  return address || null;
};


/* ============================================================
   ВЫБОР МЕДИА
   ============================================================ */

const renderSelectedMedia = () => {
  const container = document.getElementById("crewSelectedMedia");

  if (!container) return;

  if (!selectedMediaFiles.length) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = selectedMediaFiles
    .map((file, index) => {
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
    })
    .join("");

  container.querySelectorAll("[data-media-index]").forEach(button => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.mediaIndex);

      selectedMediaFiles.splice(index, 1);

      renderSelectedMedia();
    });
  });
};


const handleMediaSelection = event => {
  const files = Array.from(event.target.files || []);

  if (!files.length) {
    return;
  }

  if (selectedMediaFiles.length + files.length > 10) {
    showMessage("Можно прикрепить не более 10 файлов", "error");
    event.target.value = "";
    return;
  }

  selectedMediaFiles.push(...files);

  event.target.value = "";

  renderSelectedMedia();
};


/* ============================================================
   ЗАГРУЗКА ФАЙЛОВ
   ============================================================ */

const uploadCrewMedia = async files => {
  const uploaded = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];

    showMessage(
      `Загрузка файла ${i + 1} из ${files.length}: ${file.name}`
    );

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
   ОТПРАВКА ОТЧЁТА
   ============================================================ */

const sendCrewReport = async () => {
  const address = resolveAddress();

  const addressId =
    document.getElementById("crewAddressId")?.value || "";

  const text =
    document.getElementById("crewText")?.value.trim() || "";

  const button =
    document.getElementById("sendCrewReport");

  if (!address || !addressId) {
    showMessage("Выберите адрес из списка", "error");
    return;
  }

  if (!text && !selectedMediaFiles.length) {
    showMessage(
      "Добавьте описание или хотя бы один файл",
      "error"
    );
    return;
  }

  if (selectedMediaFiles.length > 10) {
    showMessage(
      "Можно прикрепить не более 10 файлов",
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
      selectedMediaFiles.length
        ? await uploadCrewMedia(selectedMediaFiles)
        : [];

    const result = await API("/api/crew/reports", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        address_id: Number(addressId),
        text,
        media: uploaded
      })
    });

    if (!result || !result.report_id) {
      throw new Error("Отчёт не был создан");
    }

    showMessage(
      "Отчёт успешно отправлен",
      "success"
    );

    document.getElementById("crewCity").value = "";
    document.getElementById("crewAddress").value = "";
    document.getElementById("crewAddressId").value = "";
    document.getElementById("crewText").value = "";

    selectedMediaFiles = [];
    renderSelectedMedia();

    updateAddressList();

    await loadCrewHistory();

  } catch (e) {
    showMessage(
      e.message || "Не удалось отправить отчёт",
      "error"
    );

  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Отправить отчёт";
    }
  }
};


/* ============================================================
   ИСТОРИЯ ОТЧЁТОВ
   ============================================================ */

const renderReportMedia = media => {
  if (!Array.isArray(media) || !media.length) {
    return "";
  }

  return `
    <div class="report-media">
      ${media.map(item => {
        const url =
          typeof item === "string"
            ? `/uploads/${encodeURIComponent(item)}`
            : item.url;

        const type =
          typeof item === "string"
            ? ""
            : item.media_type;

        if (!url) {
          return "";
        }

        if (type === "video") {
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
              alt="Фото отчёта"
              loading="lazy"
            >
          </a>
        `;
      }).join("")}
    </div>
  `;
};


const renderCrewHistory = reports => {
  const container = document.getElementById("crewHistory");

  if (!container) return;

  if (!Array.isArray(reports) || !reports.length) {
    container.innerHTML = `
      <div class="history-empty">
        Предыдущих отчётов пока нет
      </div>
    `;
    return;
  }

  container.innerHTML = reports.map(report => {
    const address = report.address_name || "Адрес";
    const city = report.city || "";
    const date = formatDate(report.created_at);

    const text = String(report.text || "").trim();

    const mediaHtml = renderReportMedia(report.media);

    const mediaCount =
      Array.isArray(report.media)
        ? report.media.length
        : 0;

    const lastHeadComment =
      report.last_head_comment &&
      typeof report.last_head_comment === "object"
        ? report.last_head_comment
        : null;

    const lastHeadCommentText =
      lastHeadComment &&
      String(lastHeadComment.text || "").trim()
        ? String(lastHeadComment.text || "")
            .replace(/\n/g, " ")
            .trim()
            .substring(0, 140)
        : "...";

    const headComments =
      Array.isArray(report.comments)
        ? report.comments
        : [];

    const headCommentsHtml =
      headComments.length
        ? `
          <div class="crew-head-comments">
            <div class="crew-head-comments-title">
              Комментарии Директора
            </div>

            ${headComments.map(comment => {
              const commentText =
                String(comment.text || "")
                  .replace(/\n/g, " ")
                  .trim();

              return `
                <div class="crew-head-comment">
                  <div class="crew-head-comment-meta">
                    <b><i>Директор</i></b>
                    ${comment.created_at ? `
                      <b><i>(${esc(comment.created_at)})</i></b>
                    ` : ""}
                    <b>:</b>
                  </div>

                  <div class="crew-head-comment-text">
                    <i>"${esc(commentText || "...")}"</i>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        `
        : "";

    const content =
      text || mediaHtml
        ? `
          ${text
            ? `<div class="report-text">${esc(text)}</div>`
            : ""
          }

          ${mediaHtml}
        `
        : `
          <div class="report-no-content">
            Дополнительных данных нет
          </div>
        `;

    return `
      <article class="report-card-history">

        <button
          type="button"
          class="report-summary"
          data-report-toggle="${report.id}"
        >
          <div class="report-summary-main">
            <div class="report-number">
              № ${esc(String(report.id))}
              ${report.crew_login ? `
                <span class="${report.is_mine ? "crew-name-own" : "crew-name-other"}">
                  (${esc(report.crew_login)})
                </span>
              ` : ""}
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

        ${lastHeadComment ? `
          <div class="crew-head-comment-preview">
            <span>
              <b><i>Директор</i></b>
              ${lastHeadComment.created_at ? `
                <b><i>(${esc(lastHeadComment.created_at)})</i></b>
              ` : ""}
              <b>:</b>
              <i>"${esc(lastHeadCommentText)}"</i>
            </span>
          </div>
        ` : ""}

        <div
          class="report-details"
          id="report-details-${report.id}"
        >
          ${content}

          ${headCommentsHtml}
        </div>

      </article>
    `;
  }).join("");

  container
    .querySelectorAll("[data-report-toggle]")
    .forEach(button => {
      button.addEventListener("click", () => {
        const id = button.dataset.reportToggle;
        const details =
          document.getElementById(`report-details-${id}`);

        if (!details) return;

        details.classList.toggle("open");
      });
    });
};


const loadCrewHistory = async () => {
  const container = document.getElementById("crewHistory");

  if (container) {
    container.innerHTML = `
      <div class="history-empty">
        Загрузка...
      </div>
    `;
  }

  try {
    const reports = await API("/api/crew/reports", { headers: authHeaders() });

    renderCrewHistory(reports);

  } catch (e) {
    if (container) {
      container.innerHTML = `
        <div class="history-empty">
          Не удалось загрузить предыдущие отчёты
        </div>
      `;
    }
  }
};


/* ============================================================
   ИНИЦИАЛИЗАЦИЯ
   ============================================================ */

document.addEventListener("DOMContentLoaded", async () => {

  const cityInput =
    document.getElementById("crewCity");

  const addressInput =
    document.getElementById("crewAddress");

  const mediaInput =
    document.getElementById("crewMedia");

  const sendButton =
    document.getElementById("sendCrewReport");

  const logoutButton =
    document.getElementById("logout");

  if (cityInput) {
    cityInput.addEventListener(
      "input",
      updateAddressList
    );

    cityInput.addEventListener("click", () => {
      if (typeof cityInput.showPicker === "function") {
        try {
          cityInput.showPicker();
        } catch (e) {}
      }
    });
  }

  if (addressInput) {
    addressInput.addEventListener(
      "input",
      resolveAddress
    );

    addressInput.addEventListener("mousedown", () => {
      if (!cityInput || !cityInput.value.trim()) {
        return;
      }

      updateAddressList();

      if (typeof addressInput.showPicker === "function") {
        try {
          addressInput.showPicker();
        } catch (e) {}
      }
    });
  }

  if (mediaInput) {
    mediaInput.addEventListener(
      "change",
      handleMediaSelection
    );
  }

  if (sendButton) {
    sendButton.addEventListener(
      "click",
      sendCrewReport
    );
  }

  if (logoutButton) {
    logoutButton.addEventListener("click", () => logoutCurrentSession("/"));
  }

  await loadCrewAddresses();
  await loadCrewHistory();
});
