import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

# Секретный ключ для подписи JWT (берём из ENV, иначе дефолт)
SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production-abc123xyz")
ALGORITHM = "HS256"
TOKEN_HOURS = 24 * 7  # токен живёт 7 дней


def hash_password(password: str) -> str:
    """Хэширует пароль через bcrypt"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hash_: str) -> bool:
    """Проверяет пароль против хэша"""
    return bcrypt.checkpw(password.encode("utf-8"), hash_.encode("utf-8"))


def create_token(
    user_id: int,
    login: str,
    role: str,
    session_id: Optional[int] = None,
) -> str:
    """Создаёт JWT-токен для пользователя."""
    payload = {
        "sub": user_id,
        "login": login,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }

    if session_id is not None:
        payload["session_id"] = session_id

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
    """Получает текущего пользователя из заголовка Authorization"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нет токена")
    return decode_token(auth[len("Bearer "):])


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

