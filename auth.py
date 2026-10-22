import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

from database import get_db, set_tenant_context

# JWT signing must use an explicit deployment secret; never fall back to a known key.
SECRET = os.environ.get("JWT_SECRET", "").strip()
if len(SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters")
ALGORITHM = "HS256"
TOKEN_HOURS = 24 * 7  # токен живёт 7 дней


def hash_password(password: str) -> str:
    """Хэширует пароль через bcrypt"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hash_: str) -> bool:
    """Проверяет пароль против хэша"""
    return bcrypt.checkpw(password.encode("utf-8"), hash_.encode("utf-8"))


def token_fingerprint(token: str) -> str:
    """SHA-256 токена для привязки сессии к конкретному JWT."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(
    user_id: int,
    login: str,
    role: str,
    session_id: Optional[int] = None,
    token_version: int = 1,
    legal_entity_id: Optional[int] = None,
) -> str:
    """Создаёт JWT-токен для пользователя."""
    payload = {
        "sub": user_id,
        "login": login,
        "role": role,
        "token_version": int(token_version or 1),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }

    if session_id is not None:
        payload["session_id"] = session_id

    if legal_entity_id is not None:
        payload["legal_entity_id"] = legal_entity_id

    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Декодирует JWT-токен"""
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен истёк")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен")


def available_entities(
    conn,
    user_id: int,
    default_entity_id: int = None,
    include_all: bool = False,
) -> list:
    """Юрлица, доступные пользователю.

    Обычные роли — членство (`user_entities`) плюс собственное юрлицо;
    групповой администратор дополнительно видит все активные юрлица группы.
    """
    base_query = """
        SELECT e.id, e.name, e.short_name
        FROM legal_entities e
        WHERE e.active = 1
    """
    if include_all:
        rows = conn.execute(base_query + " ORDER BY e.id").fetchall()
    else:
        rows = conn.execute(
            base_query
            + """
              AND (
                    e.id = ?
                    OR EXISTS (
                        SELECT 1 FROM user_entities ue
                        WHERE ue.user_id = ? AND ue.legal_entity_id = e.id
                    )
              )
            ORDER BY e.id
            """,
            (int(default_entity_id or 1), user_id),
        ).fetchall()

    return [
        {"id": row["id"], "name": row["name"], "short_name": row["short_name"]}
        for row in rows
    ]


def load_role(conn, code: str):
    """Роль из конструктора: рабочее место, область данных и возможности."""
    row = conn.execute(
        """
        SELECT id, code, name, workplace, scope, is_system, active
        FROM roles
        WHERE code = ?
        """,
        (code,),
    ).fetchone()
    if not row:
        return None

    capabilities = {
        item["capability_code"]
        for item in conn.execute(
            "SELECT capability_code FROM role_capabilities WHERE role_id = ?",
            (row["id"],),
        ).fetchall()
    }

    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "workplace": row["workplace"] or "service",
        "scope": row["scope"] or "entity",
        "is_system": bool(row["is_system"]),
        "active": bool(row["active"]),
        "capabilities": capabilities,
    }


def role_code_for_entity(conn, user_id: int, entity_id: int, fallback: str) -> str:
    """Роль пользователя в конкретном юрлице (иначе — его основная роль)."""
    row = conn.execute(
        """
        SELECT role FROM user_entities
        WHERE user_id = ? AND legal_entity_id = ?
        """,
        (user_id, entity_id),
    ).fetchone()
    return (row["role"] if row and row["role"] else fallback) or fallback


def get_current_user(request: Request) -> dict:
    """Проверяет JWT и серверную сессию, возвращает актуальную роль и область видимости."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нет токена")

    token = auth[len("Bearer "):]
    claims = decode_token(token)
    session_id = claims.get("session_id")

    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия не найдена, войдите заново",
        )

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                s.id, s.user_id, s.token_hash,
                s.legal_entity_id AS session_entity_id,
                s.all_entities,
                u.login AS user_login,
                u.role AS user_role,
                u.active,
                u.token_version,
                u.legal_entity_id
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = ?
              AND s.revoked_at IS NULL
              AND s.logout_at IS NULL
              AND (s.expires_at IS NULL OR s.expires_at > CURRENT_TIMESTAMP)
            """,
            (session_id,),
        ).fetchone()

        allowed = []
        if row:
            allowed = available_entities(
                conn,
                row["user_id"],
                row["legal_entity_id"],
                include_all=get_base_role(row["user_role"]) == "admin",
            )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия закрыта или истекла",
        )

    if not row["active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь заблокирован",
        )

    if int(row["token_version"] or 1) != int(claims.get("token_version", 1)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия устарела, войдите заново",
        )

    if row["token_hash"] and row["token_hash"] != token_fingerprint(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный токен",
        )

    is_group_admin = get_base_role(row["user_role"]) == "admin"
    allowed_ids = [entity["id"] for entity in allowed]
    default_entity_id = row["legal_entity_id"] or (allowed_ids[0] if allowed_ids else 1)

    # Режим «Все юрлица» доступен только групповому администратору.
    all_entities = bool(row["all_entities"]) and is_group_admin

    active_entity_id = None
    if all_entities:
        scope_entity_ids = allowed_ids or [default_entity_id]
    else:
        active_entity_id = row["session_entity_id"]
        if active_entity_id not in allowed_ids:
            # Членство могло измениться: возвращаемся к юрлицу по умолчанию.
            active_entity_id = default_entity_id
        scope_entity_ids = [active_entity_id]

    # Роль берётся в активном юрлице: у пользователя она может отличаться.
    role_entity_id = active_entity_id or default_entity_id
    with get_db() as conn:
        role_code = role_code_for_entity(
            conn, row["user_id"], role_entity_id, row["user_role"]
        )
        role = load_role(conn, role_code)

    if not role or not role["active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Роль недоступна: обратитесь к администратору",
        )

    is_group_admin = is_group_admin or role["workplace"] == "admin"
    set_tenant_context(scope_entity_ids, is_group_admin=all_entities)

    return {
        "sub": row["user_id"],
        "login": row["user_login"],
        "role": role["code"],
        "role_label": role["name"],
        "workplace": role["workplace"],
        "data_scope": role["scope"],
        "capabilities": sorted(role["capabilities"]),
        "session_id": row["id"],
        "legal_entity_id": active_entity_id,
        "scope_entity_ids": scope_entity_ids,
        "all_entities": all_entities,
        "is_group_admin": is_group_admin,
        "token_version": int(row["token_version"] or 1),
    }


def user_has(user: dict, capability: str) -> bool:
    return capability in (user.get("capabilities") or ())


def require_entity_scope(user: dict) -> int:
    """Юрлицо для новой записи. В режиме «Все юрлица» создание запрещено."""
    entity_id = user.get("legal_entity_id")
    if user.get("all_entities") or not entity_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Выберите конкретное юрлицо: в режиме «Все юрлица» создание недоступно",
        )
    return int(entity_id)


# Базовый функциональный профиль новых ролей.
#
# Важно: role пользователя остаётся настоящей ролью:
# staff / administrator / curator.
# base_role используется только для определения унаследованных
# возможностей существующих service / admin / director.
ROLE_BASE_ROLE = {
    "staff": "service",
    "administrator": "admin",
    "curator": "director",
}


def get_base_role(role: str) -> str:
    """Возвращает базовый функциональный профиль роли."""
    return ROLE_BASE_ROLE.get(role, role)


def ensure_capability(user: dict, capability: str) -> None:
    """Проверяет возможность у уже полученного пользователя."""
    if not user_has(user, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        )


def require_capability(request: Request, capability: str) -> dict:
    """Требует конкретную возможность роли."""
    user = get_current_user(request)
    if not user_has(user, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        )
    return user


def require_capabilities(request: Request, capabilities: list) -> dict:
    """Требует любую из перечисленных возможностей."""
    user = get_current_user(request)
    if not any(user_has(user, code) for code in capabilities):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        )
    return user


def require_role(request: Request, role: str) -> dict:
    """Проверяет роль с учётом базового профиля новых ролей."""
    user = get_current_user(request)
    base_role = get_base_role(user["role"])

    if base_role != role and base_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        )
    return user


def require_any(request: Request, roles: list) -> dict:
    """Проверяет разрешённые роли с учётом базового профиля."""
    user = get_current_user(request)
    base_role = get_base_role(user["role"])

    allowed_base_roles = {get_base_role(role) for role in roles}

    if base_role not in allowed_base_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        )
    return user


# Метки ролей для интерфейса
ROLE_LABELS = {
    "admin": "Админ",
    "service": "Сервис",
    "director": "Директор",
    "crew": "Экипаж",
    "head": "Руководитель",

    "staff": "Бригада",
    "administrator": "Администратор",
    "curator": "Куратор",
}

# На какую страницу попадает каждая роль после входа
ROLE_HOME = {
    "admin": "/admin",
    "service": "/client",
    "director": "/director",
    "crew": "/crew",
    "head": "/head",

    "staff": "/staff",
    "administrator": "/administrator",
    "curator": "/curator",
}

