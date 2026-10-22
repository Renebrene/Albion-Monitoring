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


def get_current_user(request: Request) -> dict:
    """Проверяет JWT и серверную сессию, возвращает актуальную роль из БД."""
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

    legal_entity_id = row["legal_entity_id"] or 1
    set_tenant_context(
        legal_entity_id,
        get_base_role(row["user_role"]) == "admin",
    )

    return {
        "sub": row["user_id"],
        "login": row["user_login"],
        "role": row["user_role"],
        "session_id": row["id"],
        "legal_entity_id": legal_entity_id,
        "token_version": int(row["token_version"] or 1),
    }


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

