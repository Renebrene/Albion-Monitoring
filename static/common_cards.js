class CommonCard {
    constructor(data) {
        this.type = data.type;
        this.id = data.id;
        this.addressId = data.address_id ?? null;

        this.city = data.city ?? "";
        this.addressName = data.address_name ?? "";
        this.manufacturer = data.manufacturer ?? "";

        this.author = data.author ?? "";
        this.authorRole = data.author_role ?? "";
        this.createdAt = data.created_at ?? "";
        this.text = data.text ?? "";

        this.media = Array.isArray(data.media)
            ? data.media
            : [];

        // Старый API мониторинга хранит медиа внутри первого сообщения.
        // Новый API может отдавать его непосредственно на карточке.
        if (
            this.media.length === 0 &&
            Array.isArray(data.messages) &&
            data.messages.length > 0 &&
            Array.isArray(data.messages[0].media)
        ) {
            this.media = data.messages[0].media;
        }

        this.messages = Array.isArray(data.messages)
            ? data.messages
            : [];

        this.directorComments = Array.isArray(data.director_comments)
            ? data.director_comments
            : [];

        this.hasDirectorComment = Boolean(data.has_director_comment);

        this.lastMessageAt = data.last_message_at ?? null;
        this.lastMessageAuthor = data.last_message_author ?? null;
    }
}


class MonitoringCard extends CommonCard {
    constructor(data) {
        super(data);

        this.type = "monitoring";

        this.monitoringDate = data.monitoring_date ?? data.created_at ?? "";

        this.status = data.status ?? "open";
        this.isImportant = Boolean(data.is_important);
        this.closeReason = data.close_reason ?? null;
    }
}


class TaskCard extends CommonCard {
    constructor(data) {
        super(data);

        this.type = "task";

        this.status = data.status ?? "";
        this.workflowStatus = data.workflow_status ?? "";
        this.workflowStatusLabel = data.workflow_status_label ?? "";

        this.createdByRole = data.created_by_role ?? "";

        this.isImportant = Boolean(data.is_important);
        this.closeReason = data.close_reason ?? null;

        this.statusHistory = Array.isArray(data.status_history)
            ? data.status_history
            : [];

        this.statusChangedAt = data.status_changed_at ?? null;

        this.isNew = Boolean(data.is_new);
        this.isOverdue = Boolean(data.is_overdue);
    }
}


function createCommonCard(data) {
    if (!data || typeof data !== "object") {
        throw new TypeError("Card data must be an object");
    }

    if (data.type === "monitoring") {
        return new MonitoringCard(data);
    }

    if (data.type === "task") {
        return new TaskCard(data);
    }

    return new CommonCard(data);
}


if (typeof window !== "undefined") {
    window.CommonCard = CommonCard;
    window.MonitoringCard = MonitoringCard;
    window.TaskCard = TaskCard;
    window.createCommonCard = createCommonCard;
}


if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        CommonCard,
        MonitoringCard,
        TaskCard,
        createCommonCard,
    };
}
