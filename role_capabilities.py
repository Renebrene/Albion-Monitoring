from dataclasses import dataclass


@dataclass(frozen=True)
class RoleCapabilities:
    """
    Возможности функциональной роли.

    Это декларативный слой.
    Существующие роли admin/service/director
    пока не изменяются.
    """

    can_view_monitoring: bool = False
    can_create_monitoring: bool = False
    can_edit_monitoring: bool = False

    can_view_tasks: bool = False
    can_create_tasks: bool = False
    can_edit_tasks: bool = False
    can_review_tasks: bool = False

    can_view_settings: bool = False

    can_comment: bool = False
    can_upload_media: bool = False


ROLE_CAPABILITIES = {
    # Бригада — функциональный профиль Сервиса.
    "staff": RoleCapabilities(
        can_view_monitoring=True,
        can_create_monitoring=True,
        can_edit_monitoring=True,
        can_view_tasks=True,
        can_comment=True,
        can_upload_media=True,
    ),

    # Администратор — функциональный профиль Админа.
    "administrator": RoleCapabilities(
        can_view_monitoring=True,
        can_create_monitoring=True,
        can_edit_monitoring=True,
        can_view_tasks=True,
        can_create_tasks=True,
        can_edit_tasks=True,
        can_review_tasks=True,
        can_view_settings=True,
        can_comment=True,
        can_upload_media=True,
    ),

    # Куратор — функциональный профиль Директора.
    "curator": RoleCapabilities(
        can_view_monitoring=True,
        can_create_monitoring=True,
        can_edit_monitoring=True,
        can_view_tasks=True,
        can_create_tasks=True,
        can_edit_tasks=True,
        can_review_tasks=True,
        can_comment=True,
        can_upload_media=True,
    ),
}


def get_role_capabilities(role: str) -> RoleCapabilities:
    """
    Возвращает возможности конкретной роли.
    """
    return ROLE_CAPABILITIES.get(role, RoleCapabilities())
