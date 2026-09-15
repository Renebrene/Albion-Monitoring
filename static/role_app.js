class RoleApp {
    constructor(role, capabilities) {
        this.role = role;
        this.capabilities = capabilities || {};
    }

    can(name) {
        return Boolean(this.capabilities[name]);
    }

    canCreateTask() {
        return this.can("can_create_tasks");
    }

    canEditTask() {
        return this.can("can_edit_tasks");
    }

    canReviewTask() {
        return this.can("can_review_tasks");
    }

    canCreateMonitoring() {
        return this.can("can_create_monitoring");
    }

    canEditMonitoring() {
        return this.can("can_edit_monitoring");
    }

    canComment() {
        return this.can("can_comment");
    }

    canUploadMedia() {
        return this.can("can_upload_media");
    }

    canViewSettings() {
        return this.can("can_view_settings");
    }
}


// ===== ROLE FEED FILTER STATE =====
let roleAllFeed = [];
let roleCurrentFilter = "all";
let roleStateFilter = "";
let roleSortMode = "message";
let roleSortDirection = "desc";


const ROLE_BASE_ROLE = {
    service: "staff",
    admin: "administrator",
    director: "curator",
    staff: "staff",
    administrator: "administrator",
    curator: "curator",
};

function getRoleAppRole(role) {
    return ROLE_BASE_ROLE[role] || role;
}

const ROLE_CAPABILITIES_CLIENT = {
    staff: {
        can_view_monitoring: true,
        can_create_monitoring: true,
        can_edit_monitoring: true,
        can_view_tasks: true,
        can_comment: true,
        can_upload_media: true,
    },

    administrator: {
        can_view_monitoring: true,
        can_create_monitoring: true,
        can_edit_monitoring: true,
        can_view_tasks: true,
        can_create_tasks: true,
        can_edit_tasks: true,
        can_review_tasks: true,
        can_view_settings: true,
        can_comment: true,
        can_upload_media: true,
    },

    curator: {
        can_view_monitoring: true,
        can_create_monitoring: true,
        can_edit_monitoring: true,
        can_view_tasks: true,
        can_create_tasks: true,
        can_edit_tasks: true,
        can_review_tasks: true,
        can_comment: true,
        can_upload_media: true,
    }
};


function createRoleApp(role) {
    const capabilities = ROLE_CAPABILITIES_CLIENT[role] || {};
    return new RoleApp(role, capabilities);
}


function roleAuthHeaders() {
    const token = localStorage.getItem("token");

    return token
        ? {
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json",
        }
        : {
            "Content-Type": "application/json",
        };
}


async function roleApi(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: {
            ...roleAuthHeaders(),
            ...(options.headers || {}),
        },
    });

    if (response.status === 401) {
        localStorage.clear();
        location.href = "/login";
        throw new Error("Сессия истекла");
    }

    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Ошибка ${response.status}`);
    }

    return response.json();
}


function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function createFeedCard(card) {
    const isTask = card.type === "task";

    const typeBadge = isTask
        ? (
            card.createdByRole === "director"
                ? '<span class="type-badge type-task-director">ЗАДАНИЕ</span>'
                : '<span class="type-badge type-task">ЗАДАНИЕ</span>'
        )
        : '<span class="type-badge type-monitoring">МОНИТОРИНГ</span>';

    let statusBadge = "";

    if (isTask) {
        const ws = card.workflowStatus || "open";
        const label =
            card.workflowStatusLabel ||
            card.workflowStatus ||
            "";

        let cls = "status-open-admin";

        if (ws === "pending_review") {
            cls = "status-review";
        } else if (ws === "rework") {
            cls = "status-rework-admin";
        } else if (ws === "completed" || ws === "closed") {
            cls = "status-done";
        } else if (card.isOverdue) {
            cls = "status-overdue";
        } else if (card.createdByRole === "director") {
            cls = "status-open-director";
        }

        const overdueMark =
            card.isOverdue && ["open", "rework"].includes(ws)
                ? " !!!"
                : "";

        const statusDate = card.statusChangedAt
            ? ` · ${card.statusChangedAt}`
            : "";

        statusBadge = `
            <span class="status-badge ${cls}">
                ${escapeHtml(label)}${overdueMark}${escapeHtml(statusDate)}
            </span>
        `;
    }

    const importantIcon =
        isTask && card.isImportant
            ? '<span class="important-star" title="Важное">⭐</span>'
            : "";

    const mediaCount =
        Array.isArray(card.media)
            ? card.media.length
            : 0;

    const mediaIndicator = mediaCount
        ? `
            <span
                class="client-media-indicator"
                title="Прикреплено файлов: ${mediaCount}"
            >
                📎 ${mediaCount}
            </span>
        `
        : "";

    const numberLabel = isTask
        ? `№${card.id}`
        : (
            card.monitoringDate ||
            card.createdAt ||
            ""
        );

    const sourceText = String(card.text || "");

    const normalizedText = sourceText
        .replace(/\n/g, " ")
        .trim();

    const shortDesc =
        normalizedText.length > 110
            ? normalizedText.substring(0, 110) + "..."
            : normalizedText;

    let lastMsgHtml =
        '<span style="color:var(--muted)">Сообщений нет</span>';

    if (card.lastMessageAt) {
        const author =
            card.lastMessageAuthor || "Неизвестно";

        lastMsgHtml = `
            <span>
                ${escapeHtml(card.lastMessageAt)}
                ·
                ${escapeHtml(author)}
            </span>
        `;
    }

    const directorMark =
        card.hasDirectorComment
            ? '<span class="director-mark" title="Есть комментарий директора">💬</span>'
            : "";

    const lastDirectorComment =
        Array.isArray(card.directorComments) &&
        card.directorComments.length
            ? card.directorComments[
                card.directorComments.length - 1
              ]
            : null;

    const directorCommentPreview =
        lastDirectorComment &&
        String(lastDirectorComment.text || "").trim()
            ? String(lastDirectorComment.text || "")
                .replace(/\n/g, " ")
                .trim()
                .substring(0, 140)
            : "...";

    const directorCommentsHtml =
        Array.isArray(card.directorComments) &&
        card.directorComments.length
            ? `
                <div class="role-director-comments">
                    <div class="role-director-comments-title">
                        Комментарии директора
                    </div>

                    ${card.directorComments.map(comment => {
                        const commentText =
                            String(comment.text || "")
                                .replace(/\n/g, " ")
                                .trim();

                        const commentMedia =
                            Array.isArray(comment.media)
                                ? comment.media
                                : [];

                        return `
                            <div class="role-director-comment">
                                <div class="role-director-comment-meta">
                                    ${escapeHtml(comment.author || "Директор")}
                                    ·
                                    ${escapeHtml(comment.created_at || "")}
                                </div>

                                ${commentText ? `
                                    <div class="role-director-comment-text">
                                        ${escapeHtml(commentText)}
                                    </div>
                                ` : ""}

                                ${commentMedia.length ? `
                                    <div class="role-director-comment-media">
                                        ${commentMedia.map((media, index) => {
                                            const url = String(media.url || "");
                                            if (!url) return "";

                                            const type = String(
                                                media.type ||
                                                media.media_type ||
                                                ""
                                            );

                                            const isVideo =
                                                type === "video" ||
                                                /\.(mp4|mov|webm|m4v)$/i.test(url);

                                            return `
                                                <a
                                                    class="role-director-media-link"
                                                    href="${escapeHtml(url)}"
                                                    target="_blank"
                                                    rel="noopener"
                                                >
                                                    📎 ${isVideo ? "Видео" : "Фото"} ${index + 1}
                                                </a>
                                            `;
                                        }).join("")}
                                    </div>
                                ` : ""}
                            </div>
                        `;
                    }).join("")}
                </div>
            `
            : "";

    let borderClass = "";

    if (card.isNew) {
        if (card.type === "monitoring") {
            borderClass = "new-monitoring";
        } else {
            borderClass = "new-admin";
        }
    }

    return `
        <article
            class="feed-item ${borderClass}"
            id="item-${card.type}-${card.id}"
            onclick="toggleRoleCard('${card.type}', ${card.id}, event)"
        >
            <div class="feed-summary">
                <div class="left">

                    <div class="top-row">
                        <div class="top-row-main">
                            ${typeBadge}

                            <span class="feed-number">
                                ${escapeHtml(numberLabel)}
                            </span>

                            ${!isTask ? `
                                <span class="feed-created">
                                    ${escapeHtml(card.monitoringDate || card.createdAt || "")}
                                </span>
                            ` : ""}
                        </div>

                        <div class="top-row-status">
                            ${statusBadge}
                            ${mediaIndicator}
                            ${importantIcon}
                        </div>
                    </div>

                    <div class="addr-line">
                        <span
                            class="addr"
                            style="font-size:15px;font-weight:700"
                        >
                            ${escapeHtml(card.city)}
                            ·
                            ${escapeHtml(card.addressName)}
                        </span>
                    </div>

                    ${shortDesc ? `
                        <div class="closed-short-desc">
                            ${escapeHtml(shortDesc)}
                        </div>
                    ` : ""}

                    <div class="bottom-row">
                        <span>
                            Последнее сообщение: ${lastMsgHtml}
                        </span>
                    </div>

                    ${lastDirectorComment ? `
                        <div class="closed-director-comment">
                            <span>
                                <b><i>Директор</i></b>
                                ${lastDirectorComment.created_at ? `
                                    <b><i>(${escapeHtml(lastDirectorComment.created_at)})</i></b>
                                ` : ""}
                                <b>:</b>
                                <i>"${escapeHtml(directorCommentPreview)}"</i>
                            </span>
                        </div>
                    ` : ""}

                </div>
            </div>

            <div
                class="feed-details"
                onclick="event.stopPropagation()"
            >
                <div class="full-text">
                    ${escapeHtml(sourceText).replace(/\n/g, "<br>")}
                </div>

                ${card.closeReason ? `
                    <div class="close-reason">
                        📌 ${escapeHtml(card.closeReason)}
                    </div>
                ` : ""}

                ${directorCommentsHtml}

                ${mediaCount ? `
                    <div class="client-task-attachments">
                        <div class="client-task-attachments-title">
                            Прикреплённые фото и видео
                        </div>

                        <div class="client-task-attachments-list">
                            ${card.media.map((media, index) => {
                                const url = String(media.url || "");
                                const type = String(
                                    media.type ||
                                    media.media_type ||
                                    ""
                                );

                                const isVideo =
                                    type === "video" ||
                                    /\.(mp4|mov|webm|m4v)$/i.test(url);

                                return isVideo
                                    ? `
                                        <video
                                            src="${escapeHtml(url)}"
                                            controls
                                            preload="metadata"
                                            title="Вложение ${index + 1}"
                                        ></video>
                                    `
                                    : `
                                        <img
                                            src="${escapeHtml(url)}"
                                            alt="Вложение ${index + 1}"
                                            onclick="window.open('${escapeHtml(url)}', '_blank')"
                                        >
                                    `;
                            }).join("")}
                        </div>
                    </div>
                ` : ""}
            ${isTask && window.roleAppInstance?.role === "staff" &&
                ["open", "rework"].includes(card.workflowStatus)
                ? `
                    <div class="task-actions">
                        <button
                            type="button"
                            class="task-action-btn task-action-complete"
                            onclick="event.stopPropagation(); roleCompleteTask(${card.id})"
                        >
                            ✓ Выполнено
                        </button>
                    </div>
                `
                : ""}
            </div>
        </article>
    `;
}


async function roleCompleteTask(taskId) {
    try {
        await roleApi(`/api/tasks/${taskId}/complete`, {
            method: "POST",
            body: JSON.stringify({})
        });

        await reloadRoleFeed();
    } catch (error) {
        console.error("Complete task:", error);
        alert(error.message || "Не удалось отправить задание на проверку");
    }
}

async function roleApproveTask(taskId) {
    try {
        await roleApi(`/api/tasks/${taskId}/approve`, {
            method: "POST",
            body: JSON.stringify({})
        });

        await reloadRoleFeed();
    } catch (error) {
        console.error("Approve task:", error);
        alert(error.message || "Не удалось принять задание");
    }
}

async function roleReworkTask(taskId) {
    try {
        await roleApi(`/api/tasks/${taskId}/rework`, {
            method: "POST",
            body: JSON.stringify({})
        });

        await reloadRoleFeed();
    } catch (error) {
        console.error("Rework task:", error);
        alert(error.message || "Не удалось отправить задание на доработку");
    }
}


function toggleRoleCard(type, id, event) {
    if (
        event &&
        (
            event.target.tagName === "BUTTON" ||
            event.target.tagName === "TEXTAREA" ||
            event.target.tagName === "INPUT" ||
            event.target.closest(".chat-input-area") ||
            event.target.closest(".modal-overlay") ||
            event.target.closest(".task-actions")
        )
    ) {
        return;
    }

    const el = document.getElementById(
        `item-${type}-${id}`
    );

    if (!el) {
        return;
    }

    el.classList.toggle("expanded");
}


function getRoleFeedTime(value) {
    if (!value) return 0;

    const time = new Date(value).getTime();

    if (!Number.isNaN(time)) {
        return time;
    }

    return 0;
}


function updateRoleStats(items = roleAllFeed) {
    const tasks = items.filter(item => item.type === "task");
    const monitoring = items.filter(item => item.type === "monitoring");

    const setStat = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    };

    setStat("statTasksTotal", tasks.length);

    setStat(
        "statTasksNew",
        tasks.filter(item =>
            item.workflow_status === "open" ||
            item.workflowStatus === "open"
        ).length
    );

    setStat(
        "statTasksOverdue",
        tasks.filter(item =>
            ["open", "pending_review", "rework"].includes(
                item.workflow_status ||
                item.workflowStatus ||
                "open"
            ) &&
            Boolean(item.is_overdue ?? item.isOverdue)
        ).length
    );

    setStat(
        "statTasksImportant",
        tasks.filter(item =>
            Boolean(item.is_important ?? item.isImportant)
        ).length
    );

    setStat(
        "statTasksDone",
        tasks.filter(item =>
            ["completed", "closed"].includes(
                item.workflow_status ||
                item.workflowStatus ||
                ""
            )
        ).length
    );

    setStat("statMonitoringTotal", monitoring.length);

    setStat(
        "statMonitoringMessages",
        monitoring.filter(item =>
            Boolean(item.last_message_at || item.lastMessageAt)
        ).length
    );

    const today = new Date().toISOString().slice(0, 10);

    setStat(
        "statMonitoringToday",
        monitoring.filter(item =>
            String(
                item.monitoring_date ||
                item.monitoringDate ||
                item.created_at ||
                item.createdAt ||
                ""
            ).slice(0, 10) === today
        ).length
    );

    setStat(
        "statDirectorComments",
        items.filter(item =>
            Boolean(item.has_director_comment ?? item.hasDirectorComment)
        ).length
    );
}


function applyRoleFilters() {
    const city =
        document.getElementById("filterCity")?.value || "";

    const search =
        String(
            document.getElementById("filterSearch")?.value || ""
        )
        .trim()
        .toLowerCase();

    const authorFilter =
        document.getElementById("filterAuthor")?.value || "";

    const supplierFilter =
        document.getElementById("filterSupplier")?.value || "";

    let filtered = roleAllFeed.map(item =>
        createCommonCard(item)
    );

    // Тип.
    if (roleCurrentFilter === "task") {
        filtered = filtered.filter(item =>
            item.type === "task"
        );
    } else if (roleCurrentFilter === "monitoring") {
        filtered = filtered.filter(item =>
            item.type === "monitoring"
        );
    }

    // Состояние.
    if (roleStateFilter === "new") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.workflowStatus === "open"
        );
    } else if (roleStateFilter === "pending_review") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.workflowStatus === "pending_review"
        );
    } else if (roleStateFilter === "rework") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.workflowStatus === "rework"
        );
    } else if (roleStateFilter === "overdue") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            ["open", "pending_review", "rework"].includes(
                item.workflowStatus || "open"
            ) &&
            item.isOverdue
        );
    } else if (roleStateFilter === "important") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.isImportant
        );
    } else if (roleStateFilter === "done") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.workflowStatus === "completed"
        );
    } else if (roleStateFilter === "closed") {
        filtered = filtered.filter(item =>
            item.type === "task" &&
            item.workflowStatus === "closed"
        );
    } else if (roleStateFilter === "director") {
        filtered = filtered.filter(item =>
            item.hasDirectorComment
        );
    }

    // Автор.
    if (authorFilter) {
        filtered = filtered.filter(item =>
            String(
                item.authorRole ||
                item.createdByRole ||
                ""
            ).toLowerCase() === authorFilter.toLowerCase()
        );
    }

    // Поставщик.
    if (supplierFilter) {
        filtered = filtered.filter(item =>
            String(item.manufacturer || "").trim() === supplierFilter
        );
    }

    // Город.
    if (city) {
        filtered = filtered.filter(item =>
            String(item.city || "") === city
        );
    }

    // Поиск по адресу.
    if (search) {
        filtered = filtered.filter(item =>
            String(item.addressName || "")
                .toLowerCase()
                .includes(search)
        );
    }

    // Сортировка.
    if (roleSortMode === "message") {
        filtered.sort((a, b) => {
            const av = getRoleFeedTime(
                a.lastMessageAt || a.createdAt
            );
            const bv = getRoleFeedTime(
                b.lastMessageAt || b.createdAt
            );

            return roleSortDirection === "asc"
                ? av - bv
                : bv - av;
        });
    } else if (roleSortMode === "date") {
        filtered.sort((a, b) => {
            const av = getRoleFeedTime(a.createdAt);
            const bv = getRoleFeedTime(b.createdAt);

            return roleSortDirection === "asc"
                ? av - bv
                : bv - av;
        });
    } else if (roleSortMode === "number") {
        filtered.sort((a, b) => {
            const av = a.type === "task"
                ? Number(a.id)
                : getRoleFeedTime(
                    a.monitoringDate || a.createdAt
                );

            const bv = b.type === "task"
                ? Number(b.id)
                : getRoleFeedTime(
                    b.monitoringDate || b.createdAt
                );

            return roleSortDirection === "asc"
                ? av - bv
                : bv - av;
        });
    }

    renderRoleFeed(filtered.map(item => {
        if (item instanceof MonitoringCard ||
            item instanceof TaskCard ||
            item instanceof CommonCard) {
            return item;
        }

        return createCommonCard(item);
    }));
}


function renderRoleFeed(items) {
    const feed = document.getElementById("feed");

    if (!feed) {
        return;
    }

    if (!Array.isArray(items) || !items.length) {
        feed.innerHTML = `
            <div class="role-empty">
                Нет данных
            </div>
        `;
        return;
    }

    const cards = items.map(item => {
        const card =
            item instanceof MonitoringCard ||
            item instanceof TaskCard ||
            item instanceof CommonCard
                ? item
                : createCommonCard(item);

        return createFeedCard(card);
    });

    feed.innerHTML = cards.join("");
}



let monitoringAddressesLoaded = false;

async function loadMonitoringAddresses() {
    const select = document.getElementById("monitoring-address");

    if (!select || monitoringAddressesLoaded) {
        return;
    }

    const response = await roleApi("/api/addresses?limit=1000");

    if (!Array.isArray(response)) {
        throw new Error("Не удалось загрузить список адресов");
    }

    select.innerHTML = '<option value="">Выберите адрес</option>';

    response.forEach(address => {
        const option = document.createElement("option");
        option.value = String(address.id);

        const city = String(address.city || "").trim();
        const name = String(address.address_name || "").trim();
        const manufacturer = String(address.manufacturer || "").trim();

        let label = [city, name]
            .filter(Boolean)
            .join(" · ");

        if (manufacturer) {
            label += ` · ${manufacturer}`;
        }

        option.textContent = label || `Адрес №${address.id}`;
        select.appendChild(option);
    });

    monitoringAddressesLoaded = true;
}

function showMonitoringFormError(message) {
    const error = document.getElementById("monitoring-form-error");

    if (!error) {
        return;
    }

    error.textContent = message || "";
    error.style.display = message ? "block" : "none";
}

function resetMonitoringForm() {
    const select = document.getElementById("monitoring-address");
    const text = document.getElementById("monitoring-text");
    const input = document.getElementById("monitoring-media");
    const list = document.getElementById("monitoring-media-list");

    if (select) select.value = "";
    if (text) text.value = "";
    if (input) input.value = "";
    if (list) list.innerHTML = "";

    showMonitoringFormError("");
}

function renderMonitoringMediaPreview() {
    const input = document.getElementById("monitoring-media");
    const list = document.getElementById("monitoring-media-list");

    if (!input || !list) {
        return;
    }

    list.innerHTML = "";

    const files = Array.from(input.files || []);

    files.slice(0, 10).forEach(file => {
        const url = URL.createObjectURL(file);

        if (file.type.startsWith("video/")) {
            const video = document.createElement("video");
            video.className = "monitoring-media-preview";
            video.src = url;
            video.controls = true;
            video.muted = true;
            list.appendChild(video);
        } else {
            const img = document.createElement("img");
            img.className = "monitoring-media-preview";
            img.src = url;
            img.alt = file.name;
            list.appendChild(img);
        }
    });

    if (files.length > 10) {
        showMonitoringFormError(
            "Можно прикрепить не более 10 файлов."
        );
    }
}

async function openMonitoringForm() {
    const form = document.getElementById("monitoring-form");

    if (!form) {
        return;
    }

    showMonitoringFormError("");

    try {
        await loadMonitoringAddresses();

        form.style.display = "block";
        form.scrollIntoView({
            behavior: "smooth",
            block: "start",
        });
    } catch (error) {
        console.error("Monitoring form:", error);
        showMonitoringFormError(error.message);
        form.style.display = "block";
    }
}

function closeMonitoringForm() {
    const form = document.getElementById("monitoring-form");

    if (!form) {
        return;
    }

    resetMonitoringForm();
    form.style.display = "none";
}

async function uploadMonitoringFile(file) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch("/api/upload", {
        method: "POST",
        headers: {
            Authorization:
                `Bearer ${localStorage.getItem("token") || ""}`,
        },
        body: formData,
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.detail ||
            `Ошибка загрузки файла: ${response.status}`
        );
    }

    return {
        filename: data.filename,
        original_filename: data.original_filename || file.name,
    };
}

async function submitMonitoring() {
    const select = document.getElementById("monitoring-address");
    const text = document.getElementById("monitoring-text");
    const input = document.getElementById("monitoring-media");
    const button = document.querySelector(".monitoring-submit-btn");

    if (!select || !text || !input) {
        return;
    }

    showMonitoringFormError("");

    const addressId = Number(select.value);
    const description = text.value.trim();
    const files = Array.from(input.files || []);

    if (!addressId) {
        showMonitoringFormError("Выберите адрес.");
        return;
    }

    if (!description) {
        showMonitoringFormError("Введите описание мониторинга.");
        return;
    }

    if (files.length > 10) {
        showMonitoringFormError(
            "Можно прикрепить не более 10 файлов."
        );
        return;
    }

    if (button) {
        button.disabled = true;
        button.textContent = "Отправка...";
    }

    try {
        const media = [];

        for (const file of files) {
            media.push(await uploadMonitoringFile(file));
        }

        const response = await roleApi("/api/reports", {
            method: "POST",
            body: JSON.stringify({
                address_id: addressId,
                text: description,
                media,
            }),
        });

        if (!response || response.ok !== true) {
            throw new Error("Не удалось создать мониторинг.");
        }

        resetMonitoringForm();

        const form = document.getElementById("monitoring-form");
        if (form) {
            form.style.display = "none";
        }

        await reloadRoleFeed();

    } catch (error) {
        console.error("Create monitoring:", error);
        showMonitoringFormError(error.message);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Отправить";
        }
    }
}

function initRoleFilters() {
    // ===== ТИП =====
    document
        .querySelectorAll(".type-filter[data-filter]")
        .forEach(button => {
            button.addEventListener("click", () => {
                document
                    .querySelectorAll(".type-filter[data-filter]")
                    .forEach(item => item.classList.remove("active"));

                button.classList.add("active");
                roleCurrentFilter = button.dataset.filter;

                applyRoleFilters();
            });
        });

    // ===== СОСТОЯНИЕ =====
    document
        .querySelectorAll(".state-filter[data-state]")
        .forEach(button => {
            button.addEventListener("click", () => {
                const value = button.dataset.state;

                if (roleStateFilter === value) {
                    roleStateFilter = "";
                    button.classList.remove("active");
                } else {
                    document
                        .querySelectorAll(".state-filter[data-state]")
                        .forEach(item => item.classList.remove("active"));

                    roleStateFilter = value;
                    button.classList.add("active");
                }

                applyRoleFilters();
            });
        });

    // ===== АВТОР =====
    document
        .getElementById("filterAuthor")
        ?.addEventListener("change", applyRoleFilters);

    // ===== ПОСТАВЩИК =====
    document
        .getElementById("filterSupplier")
        ?.addEventListener("change", applyRoleFilters);

    // ===== ГОРОД =====
    document
        .getElementById("filterCity")
        ?.addEventListener("change", applyRoleFilters);

    // ===== ПОИСК =====
    document
        .getElementById("filterSearch")
        ?.addEventListener("input", applyRoleFilters);

    // ===== СОРТИРОВКА =====
    document
        .querySelectorAll(".sort-filter[data-sort]")
        .forEach(button => {
            button.addEventListener("click", () => {
                const mode = button.dataset.sort;

                if (roleSortMode === mode) {
                    roleSortDirection =
                        roleSortDirection === "desc"
                            ? "asc"
                            : "desc";
                } else {
                    roleSortMode = mode;
                    roleSortDirection =
                        mode === "number"
                            ? "asc"
                            : "desc";
                }

                document
                    .querySelectorAll(".sort-filter[data-sort]")
                    .forEach(item =>
                        item.classList.remove("active")
                    );

                button.classList.add("active");
                button.dataset.direction =
                    roleSortDirection;

                document
                    .querySelectorAll(".sort-filter[data-sort]")
                    .forEach(item => {
                        const arrow =
                            item.querySelector(".sort-direction");

                        if (
                            arrow &&
                            item.classList.contains("active")
                        ) {
                            arrow.textContent =
                                roleSortDirection === "asc"
                                    ? "↑"
                                    : "↓";
                        }
                    });

                applyRoleFilters();
            });
        });

    // ===== РАСКРЫТИЕ ФИЛЬТРОВ =====
    const filtersToggle =
        document.getElementById("filtersToggle");

    const filtersPanel =
        document.getElementById("filtersPanel");

    if (filtersToggle && filtersPanel) {
        filtersToggle.addEventListener("click", () => {
            filtersPanel.classList.toggle("open");
        });
    }

    // ===== РАСКРЫТИЕ СТАТИСТИКИ =====
    const statsToggle =
        document.getElementById("statsToggle");

    const statsPanel =
        document.getElementById("statsPanel");

    if (statsToggle && statsPanel) {
        statsToggle.addEventListener("click", () => {
            statsPanel.classList.toggle("open");
        });
    }
}


async function reloadRoleFeed() {
    const items = await roleApi("/api/feed");

    roleAllFeed = Array.isArray(items)
        ? items
        : [];

    updateRoleStats(roleAllFeed);
    applyRoleFilters();

    return roleAllFeed;
}


async function initRoleApp() {
    const feed = document.getElementById("feed");

    if (!feed) {
        return;
    }

    try {
        const me = await roleApi("/api/me");
        const roleAppRole = getRoleAppRole(me.role);
        const app = createRoleApp(roleAppRole);
        window.roleAppInstance = app;

        if (!ROLE_CAPABILITIES_CLIENT[roleAppRole]) {
            throw new Error(`Новая роль не поддерживается: ${me.role}`);
        }

        const roleName = document.getElementById("roleName");

        if (roleName) {
            roleName.textContent = me.role_label || me.role;
        }

        const whoami = document.getElementById("whoami");

        if (whoami) {
            whoami.textContent = me.login || "";
        }

        const items = await roleApi("/api/feed");

        roleAllFeed = Array.isArray(items)
            ? items
            : [];

        initRoleFilters();

        const citySelect =
            document.getElementById("filterCity");

        if (citySelect) {
            const cities = [
                ...new Set(
                    roleAllFeed
                        .map(item => String(item.city || "").trim())
                        .filter(Boolean)
                )
            ].sort((a, b) => a.localeCompare(b, "ru"));

            citySelect.innerHTML =
                '<option value="">Все города</option>' +
                cities.map(city =>
                    `<option value="${escapeHtml(city)}">${escapeHtml(city)}</option>`
                ).join("");
        }

        updateRoleStats(roleAllFeed);
        applyRoleFilters();

        console.log("ROLE APP:", {
            role: app.role,
            canCreateTask: app.canCreateTask(),
            canCreateMonitoring: app.canCreateMonitoring(),
            canReviewTask: app.canReviewTask(),
            items: roleAllFeed.length,
        });

    } catch (error) {
        console.error("Role app:", error);

        feed.innerHTML = `
            <div class="role-diagnostic-error">
                ${escapeHtml(error.message)}
            </div>
        `;
    }
}


document.addEventListener("DOMContentLoaded", () => {
    const mediaInput = document.getElementById("monitoring-media");

    if (mediaInput) {
        mediaInput.addEventListener(
            "change",
            renderMonitoringMediaPreview
        );
    }
});


if (typeof window !== "undefined") {
    window.openMonitoringForm = openMonitoringForm;
    window.closeMonitoringForm = closeMonitoringForm;
    window.submitMonitoring = submitMonitoring;
    window.renderMonitoringMediaPreview = renderMonitoringMediaPreview;
    window.roleCompleteTask = roleCompleteTask;
    window.roleApproveTask = roleApproveTask;
    window.roleReworkTask = roleReworkTask;

    window.RoleApp = RoleApp;
    window.ROLE_CAPABILITIES_CLIENT = ROLE_CAPABILITIES_CLIENT;
    window.createRoleApp = createRoleApp;
    window.roleApi = roleApi;
    window.renderRoleFeed = renderRoleFeed;

    document.addEventListener("DOMContentLoaded", initRoleApp);
}


if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        RoleApp,
        ROLE_CAPABILITIES_CLIENT,
        createRoleApp,
        createFeedCard,
    };
}
