"""Каталог возможностей и системные роли.

Возможности фиксированы в коде: каждая что-то значит в приложении. Админ
собирает из них роли; системные роли описывают текущее поведение и защищены
от изменения, чтобы интерфейсные страницы и сценарии не ломались.

Рабочее место (`workplace`) определяет, какую страницу получает роль:
service → /staff, admin → /administrator, director → /curator, crew → /crew,
head → /head. Системные роли остаются на своих прежних страницах.

Область данных (`scope`):
- entity — все данные юрлица (по умолчанию);
- own — только собственные записи пользователя.
"""

from typing import Optional

WORKPLACES = ("admin", "service", "director", "crew", "head")

SCOPES = ("entity", "own")

# code: (домен, описание для интерфейса)
CAPABILITIES = {
    "monitoring.send": ("monitoring", "Отправлять мониторинг по адресу"),
    "monitoring.comment": ("monitoring", "Комментировать мониторинг и ленту"),
    "monitoring.view": ("monitoring", "Смотреть мониторинги юрлица"),
    "task.create": ("tasks", "Создавать задания"),
    "task.comment": ("tasks", "Писать сообщения в заданиях"),
    "task.media": ("tasks", "Прикреплять файлы к заданиям"),
    "task.complete": ("tasks", "Отправлять задание на проверку"),
    "task.approve": ("tasks", "Принимать задания"),
    "task.rework": ("tasks", "Возвращать задания на доработку"),
    "task.close": ("tasks", "Закрывать и отменять задания"),
    "task.manage": ("tasks", "Править задания: важность, скрытие"),
    "feed.hide": ("feed", "Скрывать записи в ленте"),
    "crew.report.send": ("crew", "Отправлять отчёты Экипажа"),
    "crew.report.view": ("crew", "Смотреть свои отчёты Экипажа"),
    "crew.report.moderate": ("crew", "Смотреть отчёты Экипажа и комментировать"),
    "export.excel": ("export", "Выгружать задания и отчёты в Excel"),
    "settings.manage": ("settings", "Управлять настройками и квотами"),
    "users.manage": ("users", "Управлять пользователями"),
    "entities.manage": ("admin", "Управлять юрлицами и доступом"),
    "roles.manage": ("admin", "Управлять ролями и возможностями"),
    "task_types.manage": ("tasks", "Управлять типами заданий"),
    "workflow.manage": ("tasks", "Управлять переходами заданий"),
}

ALL_CAPABILITIES = tuple(sorted(CAPABILITIES))

# Системные роли: воспроизводят текущее поведение приложения.
SYSTEM_ROLES = {
    "admin": {
        "name": "Админ",
        "workplace": "admin",
        "scope": "entity",
        "capabilities": ALL_CAPABILITIES,
        "home": "/admin",
    },
    "administrator": {
        "name": "Администратор",
        "workplace": "admin",
        "scope": "entity",
        "capabilities": ALL_CAPABILITIES,
        "home": "/administrator",
    },
    "service": {
        "name": "Сервис",
        "workplace": "service",
        "scope": "entity",
        "capabilities": (
            "monitoring.send",
            "task.comment",
            "task.media",
            "task.complete",
        ),
        "home": "/client",
    },
    "staff": {
        "name": "Бригада",
        "workplace": "service",
        "scope": "entity",
        "capabilities": (
            "monitoring.send",
            "task.comment",
            "task.media",
            "task.complete",
        ),
        "home": "/staff",
    },
    "director": {
        "name": "Директор",
        "workplace": "director",
        "scope": "entity",
        "capabilities": (
            "monitoring.comment",
            "monitoring.view",
            "task.create",
            "task.media",
            "task.approve",
            "task.rework",
            "task.close",
            "export.excel",
        ),
        "home": "/director",
    },
    "curator": {
        "name": "Куратор",
        "workplace": "director",
        "scope": "entity",
        "capabilities": (
            "monitoring.comment",
            "monitoring.view",
            "task.create",
            "task.media",
            "task.approve",
            "task.rework",
            "task.close",
            "export.excel",
        ),
        "home": "/curator",
    },
    "crew": {
        "name": "Экипаж",
        "workplace": "crew",
        "scope": "entity",
        "capabilities": ("crew.report.send", "crew.report.view"),
        "home": "/crew",
    },
    "head": {
        "name": "Руководитель",
        "workplace": "head",
        "scope": "entity",
        "capabilities": ("crew.report.moderate",),
        "home": "/head",
    },
}

# Фиксированные переходы workflow: код, откуда, куда, подпись, возможность
# по умолчанию. Список возможностей перехода редактируется в админке.
WORKFLOW_TRANSITIONS = {
    "complete": {
        "from": ("open", "rework"),
        "to": "pending_review",
        "label": "Отправить на проверку",
        "default_capability": "task.complete",
    },
    "approve": {
        "from": ("pending_review",),
        "to": "completed",
        "label": "Принять задание",
        "default_capability": "task.approve",
    },
    "rework": {
        "from": ("pending_review",),
        "to": "rework",
        "label": "Вернуть на доработку",
        "default_capability": "task.rework",
    },
    "close": {
        "from": ("open", "pending_review", "rework"),
        "to": "closed",
        "label": "Закрыть задание",
        "default_capability": "task.close",
    },
}

WORKPLACE_HOME = {
    "admin": "/administrator",
    "service": "/staff",
    "director": "/curator",
    "crew": "/crew",
    "head": "/head",
}


def capability_catalog() -> list:
    """Каталог возможностей для интерфейса управления ролями."""
    return [
        {"code": code, "domain": domain, "description": description}
        for code, (domain, description) in sorted(CAPABILITIES.items())
    ]


def unknown_capabilities(codes) -> list:
    return sorted({code for code in codes if code not in CAPABILITIES})


def system_role_codes() -> tuple:
    return tuple(SYSTEM_ROLES)


def home_for(workplace: str, role_code: str = "", is_system: bool = False) -> str:
    """Страница входа: системные роли сохраняют прежние адреса."""
    if is_system and role_code in SYSTEM_ROLES:
        return SYSTEM_ROLES[role_code]["home"]
    return WORKPLACE_HOME.get(workplace, "/client")


def normalize_workplace(value: Optional[str]) -> str:
    workplace = (value or "").strip().lower()
    return workplace if workplace in WORKPLACES else "service"


def normalize_scope(value: Optional[str]) -> str:
    scope = (value or "").strip().lower()
    return scope if scope in SCOPES else "entity"
