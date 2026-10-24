"""
АЛЬБИОН. Мониторинг экранов
Главный сервер FastAPI
"""
import asyncio
import fcntl
import ipaddress
import json
import logging
import mimetypes
import re
from contextlib import asynccontextmanager, contextmanager
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Union
from io import BytesIO

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image

from database import (
    get_db, init_schema, set_tenant_context, apply_tenant_settings,
    UPLOADS_DIR, PRIVATE_MEDIA_DIR, get_setting, set_setting,
    DEFAULT_TASK_DOMAIN, DEFAULT_TASK_TYPE,
)
from auth import (
    hash_password, verify_password, create_token, token_fingerprint,
    get_current_user, require_role, require_any, require_capability,
    require_capabilities, require_entity_scope, ensure_capability, user_has,
    available_entities, get_base_role, load_role, role_code_for_entity,
    ROLE_LABELS, ROLE_HOME, TOKEN_HOURS,
)
from permissions import (
    WORKPLACES, SCOPES, CAPABILITIES, capability_catalog, unknown_capabilities,
    normalize_scope, normalize_workplace, home_for, WORKFLOW_TRANSITIONS,
)

init_schema()


audit_logger = logging.getLogger("albion.audit")


def parent_entity_id(conn, table: str, row_id):
    """Юрлицо родительской записи (задача, отчёт, мониторинг) или None."""
    if row_id is None:
        return None
    row = conn.execute(
        f"SELECT legal_entity_id FROM {table} WHERE id = ?", (row_id,)
    ).fetchone()
    if not row:
        return None
    return int(row["legal_entity_id"] or 1)


def task_type_schemas(conn) -> dict:
    """Схемы доп. полей по id типа задания."""
    rows = conn.execute(
        "SELECT id, name, fields_schema FROM task_types"
    ).fetchall()
    schemas = {}
    for row in rows:
        try:
            fields = json.loads(row["fields_schema"] or "[]")
        except (TypeError, ValueError):
            fields = []
        schemas[row["id"]] = {"name": row["name"], "fields": fields}
    return schemas


def format_extra_fields(field_definitions, raw) -> list:
    """Доп. поля задачи в виде [{label, value}] для карточки."""
    values = decode_extra_fields(raw)
    if not field_definitions or not values:
        return []
    result = []
    for item in field_definitions:
        value = values.get(item.get("key"))
        if value not in (None, ""):
            result.append(
                {"label": item.get("label") or item.get("key"), "value": value}
            )
    return result


def own_scope_clause(user: dict, column: str):
    """Фильтр «только свои записи» для роли с областью данных own."""
    if user.get("data_scope") != "own":
        return "", []
    return f" AND {column} = ?", [user["sub"]]


def scope_entity_for_reply(conn, table: str, row_id, user: dict) -> int:
    """Юрлицо для записи, привязанной к существующему объекту.

    Комментарий, вложение или переход статуса наследуют юрлицо задачи/отчёта,
    поэтому они доступны и в режиме «Все юрлица».
    """
    entity_id = parent_entity_id(conn, table, row_id)
    if entity_id is not None:
        return entity_id
    return require_entity_scope(user)


def client_ip(request: Request = None):
    """IP клиента для аудита; некорректные значения (например, имя хоста) отбрасываются."""
    if request is None or request.client is None:
        return None
    host = (request.client.host or "").strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def write_audit(
    conn,
    user,
    action: str,
    object_type: str = None,
    object_id=None,
    before=None,
    after=None,
    request: Request = None,
):
    """Записывает действие в audit_log; аудит не должен ломать основной сценарий.

    Ошибку аудита нельзя просто проглотить: в PostgreSQL прерванная транзакция
    превращает последующий COMMIT в ROLLBACK, и тогда теряется вся работа
    запроса. Поэтому запись идёт через SAVEPOINT и откатывается только она.
    """
    savepoint_started = False
    try:
        conn.execute("SAVEPOINT audit_write")
        savepoint_started = True
        conn.execute(
            """
            INSERT INTO audit_log
                (actor_user_id, legal_entity_id, action, object_type, object_id,
                 before_json, after_json, ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (user or {}).get("sub"),
                (user or {}).get("legal_entity_id"),
                action,
                object_type,
                str(object_id) if object_id is not None else None,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
                client_ip(request),
            ),
        )
        conn.execute("RELEASE SAVEPOINT audit_write")
    except Exception as exc:
        if savepoint_started:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT audit_write")
                conn.execute("RELEASE SAVEPOINT audit_write")
            except Exception:
                pass
        audit_logger.warning("Не удалось записать аудит %s: %s", action, exc)


app = FastAPI(title="АЛЬБИОН. Мониторинг экранов")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------- Ограничения для медиа ----------
# Фото
PHOTO_MAX_SIZE = 10 * 1024 * 1024          # default 10 MiB, adjustable by admin
PHOTO_COMPRESSED_SIZE = 2.5 * 1024 * 1024  # 2.5 MiB target after compression
PHOTO_MAX_WIDTH = 1280
PHOTO_MAX_HEIGHT = 2048
PHOTO_MAX_PIXELS = 16_000_000
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Видео
VIDEO_MAX_SIZE = 100 * 1024 * 1024         # default 100 MiB, adjustable by admin
MAX_SINGLE_FILE_HARD_CAP_BYTES = 100 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}

MAX_MEDIA_PER_REPORT = 10   # фото+видео суммарно
ALLOWED_MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
PHOTO_UPLOAD_SETTING = "photo_max_upload_bytes"
VIDEO_UPLOAD_SETTING = "video_max_upload_bytes"
STORAGE_QUOTA_SETTING = "uploads_quota_bytes"
MAX_CONFIGURED_QUOTA_BYTES = 10 * 1024**4  # 10 TiB
MEDIA_PRIVATE_PREFIX = "pvt_"
ACTIVE_MEDIA_DIR = PRIVATE_MEDIA_DIR or UPLOADS_DIR
QUOTA_LOCK_PATH = ACTIVE_MEDIA_DIR / ".quota.lock"


def media_root_for_key(filename: str) -> Path:
    if filename.startswith(MEDIA_PRIVATE_PREFIX):
        if PRIVATE_MEDIA_DIR is None:
            raise HTTPException(status_code=503, detail="Внешнее хранилище медиа не настроено")
        return PRIVATE_MEDIA_DIR
    return UPLOADS_DIR


def is_private_media_key(filename: str) -> bool:
    return filename.startswith(MEDIA_PRIVATE_PREFIX)


def is_temp_media_key(filename: str) -> bool:
    return filename.startswith("temp_") or (is_private_media_key(filename) and filename[len(MEDIA_PRIVATE_PREFIX):].startswith("temp_"))


def media_path_for_key(filename: str) -> Path:
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or filename in {".", ".."}
    ):
        raise HTTPException(status_code=404, detail="Файл не найден")
    root = media_root_for_key(filename)
    if is_private_media_key(filename):
        ensure_active_media_dir()
    path = root / filename
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or resolved.parent != root.resolve() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Файл не найден")
    except (FileNotFoundError, OSError, RuntimeError):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return resolved


def media_filename(prefix: str, extension: str, *, private: bool | None = None) -> str:
    use_private = PRIVATE_MEDIA_DIR is not None if private is None else private
    key = f"{prefix}{uuid.uuid4().hex}{extension}"
    return f"{MEDIA_PRIVATE_PREFIX}{key}" if use_private else key


def media_filename_for_staged(staged: str, prefix: str, extension: str) -> str:
    return media_filename(prefix, extension, private=is_private_media_key(staged))


def ensure_active_media_dir() -> Path:
    root = ACTIVE_MEDIA_DIR
    if PRIVATE_MEDIA_DIR is None:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.exists() or not root.is_dir() or root.is_symlink():
        raise HTTPException(status_code=503, detail="Внешнее хранилище медиа недоступно")
    if PRIVATE_MEDIA_DIR is not None:
        try:
            uploads_root = UPLOADS_DIR.resolve()
            private_root = root.resolve(strict=True)
            if (
                private_root == uploads_root
                or uploads_root in private_root.parents
                or private_root in uploads_root.parents
            ):
                raise HTTPException(status_code=503, detail="Внешнее хранилище должно быть вне каталога локальных загрузок")
        except (OSError, RuntimeError):
            raise HTTPException(status_code=503, detail="Внешнее хранилище медиа недоступно")
    return root


def configured_media_roots() -> tuple[Path, ...]:
    return (UPLOADS_DIR,) if PRIVATE_MEDIA_DIR is None else (UPLOADS_DIR, PRIVATE_MEDIA_DIR)


@contextmanager
def uploads_quota_lock_sync():
    ensure_active_media_dir()
    try:
        lock_file_context = open(QUOTA_LOCK_PATH, "a+b")
    except OSError:
        raise HTTPException(status_code=503, detail="Нет доступа к хранилищу медиа")
    with lock_file_context as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@asynccontextmanager
async def uploads_quota_lock():
    ensure_active_media_dir()
    try:
        lock_file = open(QUOTA_LOCK_PATH, "a+b")
    except OSError:
        raise HTTPException(status_code=503, detail="Нет доступа к хранилищу медиа")
    try:
        await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        await asyncio.to_thread(fcntl.flock, lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def uploads_usage_bytes() -> int:
    total = 0
    if PRIVATE_MEDIA_DIR is not None:
        ensure_active_media_dir()
    for media_dir in configured_media_roots():
        if not media_dir.exists() or not media_dir.is_dir() or media_dir.is_symlink():
            continue
        root = media_dir.resolve()
        for path in media_dir.iterdir():
            if path.name == ".quota.lock" or path.name.startswith(".upload-"):
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
                    continue
                total += path.stat().st_size
            except OSError:
                continue
    return total


def parse_quota_bytes(raw: object, setting_name: str) -> int:
    if isinstance(raw, bool):
        raise HTTPException(status_code=400, detail=f"Некорректное значение {setting_name}")
    try:
        text = str(raw).strip()
        if not text or not text.isdecimal():
            raise ValueError
        value = int(text)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Значение {setting_name} должно быть целым числом байт")
    if value < 0 or value > MAX_CONFIGURED_QUOTA_BYTES:
        raise HTTPException(status_code=400, detail=f"Значение {setting_name} вне допустимого диапазона")
    return value


def configured_upload_limit(extension: str) -> int:
    setting = PHOTO_UPLOAD_SETTING if extension in PHOTO_EXTENSIONS else VIDEO_UPLOAD_SETTING
    fallback = PHOTO_MAX_SIZE if extension in PHOTO_EXTENSIONS else VIDEO_MAX_SIZE
    configured = configured_quota_setting(setting, fallback)
    if configured == 0:
        return MAX_SINGLE_FILE_HARD_CAP_BYTES
    return min(configured, MAX_SINGLE_FILE_HARD_CAP_BYTES)


def configured_quota_setting(setting: str, fallback: int) -> int:
    try:
        value = int(get_setting(setting, str(fallback)))
    except (TypeError, ValueError):
        return fallback
    if value < 0 or value > MAX_CONFIGURED_QUOTA_BYTES:
        return fallback
    return value


def configured_storage_quota() -> int:
    return configured_quota_setting(STORAGE_QUOTA_SETTING, 0)


def enforce_storage_quota(additional_bytes: int) -> None:
    quota = configured_storage_quota()
    if quota and uploads_usage_bytes() + additional_bytes > quota:
        raise HTTPException(status_code=413, detail="Недостаточно места: превышена общая квота хранилища")


@app.get("/api/media/{filename}")
def get_media(filename: str, request: Request):
    get_current_user(request)
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or filename in {".", ".."}
    ):
        raise HTTPException(status_code=404, detail="Файл не найден")

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_MEDIA_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Файл не найден")

    resolved = media_path_for_key(filename)

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_filename = filename.replace('"', "").replace("\\r", "").replace("\\n", "")
    return FileResponse(
        resolved,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------- Валидация и staging медиа ----------
async def read_upload_bounded(file: UploadFile, limit: int) -> bytes:
    chunks = []
    total = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)


def validate_image_content(image_data: bytes, extension: str) -> None:
    expected_formats = {
        ".jpg": {"JPEG"},
        ".jpeg": {"JPEG"},
        ".png": {"PNG"},
        ".webp": {"WEBP"},
    }
    try:
        with Image.open(BytesIO(image_data)) as image:
            if image.format not in expected_formats.get(extension, set()):
                raise ValueError("format mismatch")
            if image.width * image.height > PHOTO_MAX_PIXELS:
                raise ValueError("image is too large")
            image.verify()
        with Image.open(BytesIO(image_data)) as image:
            image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное изображение")


def write_atomic_upload(content: bytes, extension: str) -> str:
    media_dir = ensure_active_media_dir()
    filename = media_filename("temp_", extension)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=media_dir, prefix=".upload-", delete=False
        ) as output:
            temp_path = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, media_dir / filename)
        return filename
    except HTTPException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    except OSError:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        if PRIVATE_MEDIA_DIR is not None:
            raise HTTPException(status_code=503, detail="Нет доступа к внешнему хранилищу медиа")
        raise
    except Exception:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


async def stage_video_upload(file: UploadFile, limit: int, extension: str) -> str:
    media_dir = ensure_active_media_dir()
    filename = media_filename("temp_", extension)
    temp_path = None
    total = 0
    try:
        async with uploads_quota_lock():
            limit = configured_upload_limit(extension)
            quota = configured_storage_quota()
            usage = uploads_usage_bytes()
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=media_dir, prefix=".upload-", delete=False
            ) as output:
                temp_path = Path(output.name)
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
                    if quota and usage + total > quota:
                        raise HTTPException(status_code=413, detail="Недостаточно места: превышена общая квота хранилища")
                    output.write(chunk)
                if total == 0:
                    raise HTTPException(status_code=400, detail="Файл пустой")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, media_dir / filename)
        return filename
    except HTTPException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    except OSError:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        if PRIVATE_MEDIA_DIR is not None:
            raise HTTPException(status_code=503, detail="Нет доступа к внешнему хранилищу медиа")
        raise
    except Exception:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


# ---------- Сжатие фото ----------
def compress_image(image_data: bytes) -> bytes:
    img = Image.open(BytesIO(image_data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    if w > PHOTO_MAX_WIDTH or h > PHOTO_MAX_HEIGHT:
        ratio = min(PHOTO_MAX_WIDTH / w, PHOTO_MAX_HEIGHT / h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)

    output = BytesIO()
    quality = 85
    img.save(output, format="JPEG", quality=quality, optimize=True)
    data = output.getvalue()

    while len(data) > PHOTO_COMPRESSED_SIZE and quality > 50:
        quality -= 5
        output = BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        data = output.getvalue()

    return data


# ---------- Pydantic-модели ----------
class LoginIn(BaseModel):
    login: str
    password: str


class MediaUploadIn(BaseModel):
    filename: str
    original_filename: Optional[str] = None


class ReportIn(BaseModel):
    address_id: int
    text: str
    task_id: Optional[int] = None
    media: List[MediaUploadIn | str] = []


class CrewReportIn(BaseModel):
    address_id: int
    text: str = ""
    media: List[MediaUploadIn | str] = []


class TaskIn(BaseModel):
    address_id: int
    description: str
    is_important: bool = False
    task_type: Optional[Union[str, int]] = None
    extra_fields: Optional[dict] = None


class SettingIn(BaseModel):
    key: str
    value: str


class UploadQuotasIn(BaseModel):
    photo_max_upload_bytes: str
    video_max_upload_bytes: str
    uploads_quota_bytes: str


# ---------- Health ----------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------- Страницы ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/client", response_class=HTMLResponse)
def client_page(request: Request):
    return templates.TemplateResponse("client.html", {"request": request})


@app.get("/server", response_class=HTMLResponse)
def server_page(request: Request):
    return templates.TemplateResponse("server.html", {"request": request})


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request):
    return templates.TemplateResponse("tasks.html", {"request": request})


# ==========================================================
#                       API
# ==========================================================

# ---------- Авторизация ----------
@app.get("/api/auth-sessions")
def auth_sessions(request: Request):
    user = require_capability(request, "users.manage")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, login, role, login_at, logout_at
            FROM auth_sessions
            ORDER BY login_at DESC, id DESC
            """
        ).fetchall()

        online_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM auth_sessions
            WHERE logout_at IS NULL
            """
        ).fetchone()[0]

    return {
        "online_count": online_count,
        "sessions": [
            {
                "id": row["id"],
                "login": row["login"],
                "role": row["role"],
                "login_at": row["login_at"],
                "logout_at": row["logout_at"],
            }
            for row in rows
        ],
    }


@app.post("/api/logout")
def logout(request: Request):
    user = get_current_user(request)
    session_id = user.get("session_id")

    # Старый JWT без session_id не закрывает никакую сессию.
    # После обновления приложения пользователь просто войдёт заново.
    if not session_id:
        return {"ok": True}

    with get_db() as conn:
        conn.execute(
            """
            UPDATE auth_sessions
            SET logout_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND user_id = ?
              AND logout_at IS NULL
            """,
            (session_id, user["sub"]),
        )
        write_audit(conn, user, "auth.logout", "session", session_id, request=request)
        conn.commit()

    return {"ok": True}


@app.post("/api/login")
def login(data: LoginIn, request: Request):
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, login, password_hash, role, active, token_version, legal_entity_id
            FROM users
            WHERE login = ?
            """,
            (data.login,),
        ).fetchone()

        # Контекст запроса — юрлицо входящего: от него зависят RLS-проверки
        # при записи сессии и аудита.
        if row:
            set_tenant_context([row["legal_entity_id"] or 1])
            apply_tenant_settings(conn)

        if not row or not verify_password(data.password, row["password_hash"]):
            write_audit(conn, None, "auth.login.failed", "user", data.login, request=request)
            # Отказ входа обязан остаться в аудите: исключение откатит транзакцию.
            conn.commit()
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")

        if not row["active"]:
            write_audit(
                conn,
                {"sub": row["id"], "legal_entity_id": row["legal_entity_id"]},
                "auth.login.blocked",
                "user",
                row["id"],
                request=request,
            )
            conn.commit()
            raise HTTPException(status_code=403, detail="Пользователь заблокирован")

        # Создаём отдельную серверную сессию авторизации.
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """
            INSERT INTO auth_sessions (user_id, login, role, expires_at, legal_entity_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["id"], row["login"], row["role"], expires_at, row["legal_entity_id"] or 1),
        )

        # Членство по «домашнему» юрлицу: старые базы и прямые вставки в users
        # могли не создать строку, без неё редактор доступа покажет пустой набор.
        conn.execute(
            """
            INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
            SELECT ?, ?, ?, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM user_entities
                WHERE user_id = ? AND legal_entity_id = ?
            )
            """,
            (
                row["id"],
                row["legal_entity_id"] or 1,
                row["role"],
                row["id"],
                row["legal_entity_id"] or 1,
            ),
        )
        session_id = cur.lastrowid

        # Привязываем JWT к конкретной сессии и версии прав.
        token = create_token(
            row["id"],
            row["login"],
            row["role"],
            session_id=session_id,
            token_version=row["token_version"],
            legal_entity_id=row["legal_entity_id"],
        )
        conn.execute(
            "UPDATE auth_sessions SET token_hash = ? WHERE id = ?",
            (token_fingerprint(token), session_id),
        )
        write_audit(
            conn,
            {"sub": row["id"], "legal_entity_id": row["legal_entity_id"]},
            "auth.login",
            "user",
            row["id"],
            request=request,
        )
        conn.commit()

    # Рабочее место и возможности роли: у своих ролей своей страницы нет,
    # они используют интерфейс выбранного рабочего места.
    with get_db() as conn:
        role = load_role(conn, row["role"])

    if not role or not role["active"]:
        raise HTTPException(status_code=403, detail="Роль недоступна: обратитесь к администратору")

    return {
        "token": token,
        "role": role["code"],
        "role_label": role["name"],
        "login": row["login"],
        "workplace": role["workplace"],
        "capabilities": sorted(role["capabilities"]),
        "home": home_for(role["workplace"], role["code"], role["is_system"]),
    }


@app.get("/api/login-users")
def login_users():
    """
    Публичный список пользователей для формы входа.

    ВАЖНО:
    пароль и password_hash никогда не возвращаются.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT login, role FROM users ORDER BY login"
        ).fetchall()

    return [
        {
            "login": row["login"],
            "role": row["role"],
            "role_label": ROLE_LABELS.get(row["role"], row["role"]),
        }
        for row in rows
    ]


@app.get("/api/me")
def me(request: Request):
    user = get_current_user(request)
    return {
        "sub": user["sub"],
        "login": user["login"],
        "role": user["role"],
        "role_label": user.get("role_label") or ROLE_LABELS.get(user["role"], user["role"]),
        "workplace": user.get("workplace"),
        "data_scope": user.get("data_scope"),
        "capabilities": user.get("capabilities", []),
        "legal_entity_id": user["legal_entity_id"],
        "all_entities": user["all_entities"],
        "can_view_all": can_view_all_entities(user),
    }


class EntityScopeIn(BaseModel):
    legal_entity_id: Optional[int] = None  # None — режим «Все юрлица»


def user_entity_options(user: dict) -> list:
    with get_db() as conn:
        return available_entities(
            conn,
            user["sub"],
            user["legal_entity_id"] or (user.get("scope_entity_ids") or [1])[0],
            include_all=bool(user.get("is_group_admin")),
        )


def can_view_all_entities(user: dict) -> bool:
    """Сводный режим доступен только групповому администратору и при 2+ юрлицах."""
    if not user.get("is_group_admin"):
        return False
    # Режим определяется доступными юрлицами, а не текущей областью видимости.
    return len(user_entity_options(user)) > 1


@app.get("/api/legal-entities")
def list_legal_entities(request: Request):
    """Доступные юрлица и текущий выбор — для переключателя в интерфейсе."""
    user = get_current_user(request)
    return {
        "current": {
            "legal_entity_id": user["legal_entity_id"],
            "all_entities": user["all_entities"],
        },
        "can_view_all": can_view_all_entities(user),
        "options": user_entity_options(user),
    }


@app.post("/api/session/legal-entity")
def switch_legal_entity(data: EntityScopeIn, request: Request):
    """Переключает активное юрлицо серверной сессии."""
    user = get_current_user(request)

    if data.legal_entity_id is None:
        if not can_view_all_entities(user):
            raise HTTPException(
                status_code=403, detail="Режим «Все юрлица» недоступен"
            )
        entity_id, all_entities = None, 1
    else:
        allowed_ids = {entity["id"] for entity in user_entity_options(user)}
        entity_id = int(data.legal_entity_id)
        if entity_id not in allowed_ids:
            raise HTTPException(status_code=403, detail="Нет доступа к этому юрлицу")
        all_entities = 0

    with get_db() as conn:
        conn.execute(
            """
            UPDATE auth_sessions
            SET legal_entity_id = ?, all_entities = ?
            WHERE id = ? AND user_id = ?
            """,
            (entity_id, all_entities, user["session_id"], user["sub"]),
        )
        write_audit(
            conn,
            user,
            "auth.entity.switch",
            "session",
            user["session_id"],
            after={"legal_entity_id": entity_id, "all_entities": bool(all_entities)},
            request=request,
        )

    return {
        "ok": True,
        "legal_entity_id": entity_id,
        "all_entities": bool(all_entities),
    }


# ---------- Настройки медиа (только Админ) ----------
@app.get("/api/settings")
def get_settings(request: Request):
    """Get operational settings for authenticated users."""
    get_current_user(request)
    usage = uploads_usage_bytes()
    quota = configured_storage_quota()
    return {
        "photos_enabled": get_setting("photos_enabled", "1") == "1",
        "videos_enabled": get_setting("videos_enabled", "1") == "1",
        "photo_max_upload_bytes": configured_quota_setting(PHOTO_UPLOAD_SETTING, PHOTO_MAX_SIZE),
        "video_max_upload_bytes": configured_quota_setting(VIDEO_UPLOAD_SETTING, VIDEO_MAX_SIZE),
        "uploads_quota_bytes": quota,
        "uploads_usage_bytes": usage,
        "uploads_quota_exceeded": bool(quota and usage > quota),
    }


@app.put("/api/settings/quotas")
def update_upload_quotas(data: UploadQuotasIn, request: Request):
    user = require_capability(request, "settings.manage")
    photo_bytes = parse_quota_bytes(data.photo_max_upload_bytes, PHOTO_UPLOAD_SETTING)
    video_bytes = parse_quota_bytes(data.video_max_upload_bytes, VIDEO_UPLOAD_SETTING)
    storage_bytes = parse_quota_bytes(data.uploads_quota_bytes, STORAGE_QUOTA_SETTING)
    if photo_bytes > MAX_SINGLE_FILE_HARD_CAP_BYTES or video_bytes > MAX_SINGLE_FILE_HARD_CAP_BYTES:
        raise HTTPException(status_code=400, detail="Лимит файла не может превышать 100 MiB")

    with uploads_quota_lock_sync():
        with get_db() as conn:
            for key, value in (
                (PHOTO_UPLOAD_SETTING, photo_bytes),
                (VIDEO_UPLOAD_SETTING, video_bytes),
                (STORAGE_QUOTA_SETTING, storage_bytes),
            ):
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                    (key, str(value)),
                )
        usage = uploads_usage_bytes()

    with get_db() as conn:
        write_audit(
            conn,
            user,
            "settings.quotas.update",
            "settings",
            None,
            after={
                "photo_max_upload_bytes": photo_bytes,
                "video_max_upload_bytes": video_bytes,
                "uploads_quota_bytes": storage_bytes,
            },
            request=request,
        )

    return {
        "ok": True,
        "photo_max_upload_bytes": photo_bytes,
        "video_max_upload_bytes": video_bytes,
        "uploads_quota_bytes": storage_bytes,
        "uploads_usage_bytes": usage,
        "uploads_quota_exceeded": bool(storage_bytes and usage > storage_bytes),
    }


@app.put("/api/settings")
def update_setting(data: SettingIn, request: Request):
    """Изменить настройки. Изменения квот доступны только администратору."""
    user = require_capability(request, "settings.manage")
    if data.key in {"photos_enabled", "videos_enabled"}:
        if data.value not in ("0", "1"):
            raise HTTPException(status_code=400, detail="Значение должно быть 0 или 1")
        set_setting(data.key, data.value)
        with get_db() as conn:
            write_audit(
                conn,
                user,
                "settings.update",
                "settings",
                data.key,
                after={"key": data.key, "value": data.value},
                request=request,
            )
        return {"ok": True, "key": data.key, "value": data.value}

    quota_keys = {PHOTO_UPLOAD_SETTING, VIDEO_UPLOAD_SETTING, STORAGE_QUOTA_SETTING}
    if data.key not in quota_keys:
        raise HTTPException(status_code=400, detail="Неизвестная настройка")
    value = parse_quota_bytes(data.value, data.key)
    if data.key == PHOTO_UPLOAD_SETTING and value > MAX_SINGLE_FILE_HARD_CAP_BYTES:
        raise HTTPException(status_code=400, detail="Лимит фото не может превышать 100 MiB")
    if data.key == VIDEO_UPLOAD_SETTING and value > MAX_SINGLE_FILE_HARD_CAP_BYTES:
        raise HTTPException(status_code=400, detail="Лимит видео не может превышать 100 MiB")
    with uploads_quota_lock_sync():
        set_setting(data.key, str(value))
    with get_db() as conn:
        write_audit(
            conn,
            user,
            "settings.update",
            "settings",
            data.key,
            after={"key": data.key, "value": value},
            request=request,
        )
    return {"ok": True, "key": data.key, "value": value, "uploads_usage_bytes": uploads_usage_bytes()}


# ---------- Справочники ----------
# Справочники адресов закрыты авторизацией: без неё не определить юрлицо
# пользователя, а сам список адресов — данные организации, а не публичные.
@app.get("/api/cities")
def cities(request: Request):
    get_current_user(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT city FROM addresses WHERE active = 1 ORDER BY city"
        ).fetchall()
    return [r["city"] for r in rows]


@app.get("/api/manufacturers")
def manufacturers(request: Request):
    get_current_user(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT manufacturer FROM addresses "
            "WHERE active = 1 AND manufacturer IS NOT NULL AND manufacturer != '' "
            "ORDER BY manufacturer"
        ).fetchall()
    return [r["manufacturer"] for r in rows]


@app.get("/api/addresses")
def addresses(
    request: Request,
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
):
    get_current_user(request)
    limit = max(1, min(limit, 1000))
    with get_db() as conn:
        sql = "SELECT id, city, address_name, manufacturer FROM addresses WHERE active = 1"
        params: list = []
        if city:
            sql += " AND city = ?"; params.append(city)
        if manufacturer:
            sql += " AND manufacturer = ?"; params.append(manufacturer)
        if q:
            sql += " AND address_name LIKE ?"; params.append(f"%{q}%")
        sql += " ORDER BY city, address_name LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------- Загрузка медиа (фото ИЛИ видео) ----------
@app.post("/api/upload")
async def upload_media(request: Request, file: UploadFile = File(...)):
    """Загрузка фото или видео. Проверяет настройки."""
    get_current_user(request)

    original_filename = Path(file.filename or "").name
    if not original_filename:
        raise HTTPException(status_code=400, detail="Имя файла не определено")

    ext = Path(original_filename).suffix.lower()

    # Определяем тип и ограничиваем чтение до configured maximum.
    if ext in PHOTO_EXTENSIONS:
        media_type = "photo"
        if get_setting("photos_enabled", "1") != "1":
            await file.close()
            raise HTTPException(status_code=403, detail="Загрузка фото отключена администратором")
        file_limit = configured_upload_limit(ext)
        content = await read_upload_bounded(file, file_limit or MAX_SINGLE_FILE_HARD_CAP_BYTES)
        validate_image_content(content, ext)
        try:
            compressed = compress_image(content)
        except (Image.DecompressionBombError, OSError):
            raise HTTPException(status_code=400, detail="Некорректное изображение")
        async with uploads_quota_lock():
            if file_limit and len(content) > configured_upload_limit(ext):
                raise HTTPException(status_code=413, detail="Лимит размера фото был изменён во время загрузки")
            enforce_storage_quota(len(compressed))
            filename = write_atomic_upload(compressed, ".jpg")

    elif ext in VIDEO_EXTENSIONS:
        media_type = "video"
        if get_setting("videos_enabled", "1") != "1":
            await file.close()
            raise HTTPException(status_code=403, detail="Загрузка видео отключена администратором")
        filename = await stage_video_upload(file, VIDEO_MAX_SIZE, ext)

    else:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат. Разрешены: фото {sorted(PHOTO_EXTENSIONS)}, видео {sorted(VIDEO_EXTENSIONS)}",
        )

    return {
        "filename": filename,
        "original_filename": original_filename,
        "url": f"/api/media/{filename}",
        "media_type": media_type,
    }


# ---------- Записи мониторинга ----------
@app.post("/api/reports")
def create_report(data: ReportIn, request: Request):
    user = require_capability(request, "monitoring.send")

    with get_db() as conn:
        addr = conn.execute(
            "SELECT id FROM addresses WHERE id = ? AND active = 1",
            (data.address_id,),
        ).fetchone()
        if not addr:
            raise HTTPException(status_code=400, detail="Адрес не найден")
        if not data.text.strip():
            raise HTTPException(status_code=400, detail="Пустой текст")

        if data.task_id:
            task = conn.execute(
                "SELECT id, status FROM tasks WHERE id = ?", (data.task_id,)
            ).fetchone()
            if not task:
                raise HTTPException(status_code=400, detail="Задание не найдено")
            if task["status"] != "open":
                raise HTTPException(status_code=400, detail="Задание уже закрыто")

        # Отчёт по заданию наследует юрлицо задания, свободный мониторинг —
        # юрлицо сессии (в режиме «Все юрлица» создание запрещено).
        entity_id = (
            scope_entity_for_reply(conn, "tasks", data.task_id, user)
            if data.task_id
            else require_entity_scope(user)
        )

        cursor = conn.execute(
            """
            INSERT INTO reports (address_id, user_id, task_id, text, legal_entity_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (data.address_id, user["sub"], data.task_id, data.text.strip(), entity_id),
        )
        report_id = cursor.lastrowid

        # Привязываем медиа
        if data.media:
            if len(data.media) > MAX_MEDIA_PER_REPORT:
                raise HTTPException(
                    status_code=400,
                    detail=f"Слишком много файлов (макс. {MAX_MEDIA_PER_REPORT})",
                )
            for media_item in data.media:
                if isinstance(media_item, str):
                    temp_name = media_item
                    original_filename = None
                else:
                    temp_name = media_item.filename
                    original_filename = media_item.original_filename

                if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                    continue

                old_path = media_path_for_key(temp_name)
                if not old_path.exists():
                    continue

                ext = old_path.suffix
                # Определяем тип по расширению
                media_type = "video" if ext in VIDEO_EXTENSIONS else "photo"
                new_name = media_filename_for_staged(temp_name, f"r{report_id}_", ext)
                old_path.rename(media_root_for_key(new_name) / new_name)

                conn.execute(
                    """
                    INSERT INTO media
                        (report_id, filename, media_type, original_filename, legal_entity_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (report_id, new_name, media_type, original_filename, entity_id),
                )

        if data.task_id:
            conn.execute(
                "UPDATE tasks SET status='done', closed_at=CURRENT_TIMESTAMP, "
                "closed_by=? WHERE id=?",
                (user["sub"], data.task_id),
            )

    return {"ok": True, "report_id": report_id}


# ---------- Отчёты Экипажа ----------
@app.post("/api/crew/reports")
def create_crew_report(data: CrewReportIn, request: Request):
    user = require_capability(request, "crew.report.send")

    text = data.text.strip()

    if not text and not data.media:
        raise HTTPException(
            status_code=400,
            detail="Добавьте описание или хотя бы один файл",
        )

    if len(data.media) > MAX_MEDIA_PER_REPORT:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много файлов (макс. {MAX_MEDIA_PER_REPORT})",
        )

    with get_db() as conn:
        addr = conn.execute(
            """
            SELECT id
            FROM addresses
            WHERE id = ? AND active = 1
            """,
            (data.address_id,),
        ).fetchone()

        if not addr:
            raise HTTPException(status_code=400, detail="Адрес не найден")

        entity_id = require_entity_scope(user)

        cursor = conn.execute(
            """
            INSERT INTO crew_monitorings
                (address_id, user_id, text, legal_entity_id)
            VALUES (?, ?, ?, ?)
            """,
            (data.address_id, user["sub"], text, entity_id),
        )

        report_id = cursor.lastrowid

        for media_item in data.media:
            if isinstance(media_item, str):
                temp_name = media_item
                original_filename = None
            else:
                temp_name = media_item.filename
                original_filename = media_item.original_filename

            if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                continue

            old_path = media_path_for_key(temp_name)

            if not old_path.exists():
                continue

            ext = old_path.suffix.lower()

            if ext in VIDEO_EXTENSIONS:
                media_type = "video"
            elif ext in PHOTO_EXTENSIONS:
                media_type = "photo"
            else:
                continue

            new_name = media_filename_for_staged(temp_name, f"cr{report_id}_", ext)

            old_path.rename(media_root_for_key(new_name) / new_name)

            conn.execute(
                """
                INSERT INTO crew_monitoring_media
                    (crew_monitoring_id, filename, media_type,
                     original_filename, legal_entity_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_id, new_name, media_type, original_filename, entity_id),
            )

    return {
        "ok": True,
        "report_id": report_id,
    }


@app.get("/api/crew/reports")
def list_crew_reports(request: Request):
    user = require_capability(request, "crew.report.view")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                cr.id,
                cr.address_id,
                cr.user_id,
                cr.created_at,
                cr.text,
                u.login AS crew_login,
                a.city,
                a.address_name,
                a.manufacturer
            FROM crew_monitorings cr
            JOIN users u ON u.id = cr.user_id
            JOIN addresses a ON a.id = cr.address_id
            WHERE u.role = 'crew'
              AND cr.hidden = 0
            ORDER BY cr.created_at DESC, cr.id DESC
            """,
        ).fetchall()

        result = []

        for row in rows:
            media_rows = conn.execute(
                """
                SELECT id, filename, media_type, created_at
                FROM crew_monitoring_media
                WHERE crew_monitoring_id = ?
                ORDER BY id
                """,
                (row["id"],),
            ).fetchall()

            comment_rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.user_id,
                    c.text,
                    c.created_at,
                    u.login AS author_login,
                    u.role AS author_role
                FROM crew_report_comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.crew_monitoring_id = ?
                  AND u.role = 'head'
                ORDER BY c.created_at ASC, c.id ASC
                """,
                (row["id"],),
            ).fetchall()

            comments = [
                {
                    "id": comment["id"],
                    "user_id": comment["user_id"],
                    "author_login": comment["author_login"],
                    "author_role": comment["author_role"],
                    "text": comment["text"],
                    "created_at": comment["created_at"],
                }
                for comment in comment_rows
            ]

            result.append({
                "id": row["id"],
                "address_id": row["address_id"],
                "user_id": row["user_id"],
                "crew_login": row["crew_login"],
                "is_mine": str(row["user_id"]) == str(user["sub"]),
                "city": row["city"],
                "address_name": row["address_name"],
                "manufacturer": row["manufacturer"],
                "text": row["text"],
                "created_at": row["created_at"],
                "media": [
                    {
                        "id": media["id"],
                        "filename": media["filename"],
                        "url": f"/api/media/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
                "comments": comments,
                "last_head_comment": comments[-1] if comments else None,
            })

    return result



# ============================================================
# HEAD / РУКОВОДИТЕЛЬ — отчёты экипажа
# ============================================================

@app.get("/api/head/reports")
def list_head_reports(request: Request):
    user = require_capability(request, "crew.report.moderate")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                cr.id,
                cr.address_id,
                cr.user_id,
                cr.created_at,
                cr.text,
                a.city,
                a.address_name,
                a.manufacturer,
                u.login AS author_login
            FROM crew_monitorings cr
            JOIN addresses a ON a.id = cr.address_id
            JOIN users u ON u.id = cr.user_id
            WHERE cr.hidden = 0
            ORDER BY cr.created_at DESC, cr.id DESC
            """
        ).fetchall()

        result = []

        for row in rows:
            media_rows = conn.execute(
                """
                SELECT id, filename, media_type, created_at
                FROM crew_monitoring_media
                WHERE crew_monitoring_id = ?
                ORDER BY id
                """,
                (row["id"],),
            ).fetchall()

            result.append({
                "id": row["id"],
                "address_id": row["address_id"],
                "user_id": row["user_id"],
                "city": row["city"],
                "address_name": row["address_name"],
                "manufacturer": row["manufacturer"],
                "author_login": row["author_login"],
                "text": row["text"],
                "created_at": row["created_at"],
                "media": [
                    {
                        "id": media["id"],
                        "filename": media["filename"],
                        "url": f"/api/media/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
            })

    return result


@app.get("/api/head/reports/{report_id}/comments")
def list_head_report_comments(report_id: int, request: Request):
    user = require_capability(request, "crew.report.moderate")

    with get_db() as conn:
        report = conn.execute(
            "SELECT id FROM crew_monitorings WHERE id = ?",
            (report_id,),
        ).fetchone()

        if not report:
            raise HTTPException(status_code=404, detail="Отчёт не найден")

        rows = conn.execute(
            """
            SELECT
                c.id,
                c.crew_monitoring_id,
                c.user_id,
                c.text,
                c.created_at,
                u.login AS author_login,
                u.role AS author_role
            FROM crew_report_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.crew_monitoring_id = ?
            ORDER BY c.created_at ASC, c.id ASC
            """,
            (report_id,),
        ).fetchall()

        result = []

        for row in rows:
            media_rows = conn.execute(
                """
                SELECT id, filename, media_type, created_at
                FROM crew_report_comment_media
                WHERE comment_id = ?
                ORDER BY id
                """,
                (row["id"],),
            ).fetchall()

            result.append({
                "id": row["id"],
                "crew_monitoring_id": row["crew_monitoring_id"],
                "user_id": row["user_id"],
                "author_login": row["author_login"],
                "author_role": row["author_role"],
                "text": row["text"],
                "created_at": row["created_at"],
                "media": [
                    {
                        "id": media["id"],
                        "filename": media["filename"],
                        "url": f"/api/media/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
            })

    return result


class HeadReportCommentIn(BaseModel):
    text: str = ""
    media: List[MediaUploadIn | str] = []


@app.post("/api/head/reports/{report_id}/comments")
def create_head_report_comment(
    report_id: int,
    data: HeadReportCommentIn,
    request: Request,
):
    user = require_capability(request, "crew.report.moderate")

    text = data.text.strip()

    if not text and not data.media:
        raise HTTPException(
            status_code=400,
            detail="Добавьте комментарий или хотя бы один файл",
        )

    if len(data.media) > MAX_MEDIA_PER_REPORT:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много файлов (макс. {MAX_MEDIA_PER_REPORT})",
        )

    with get_db() as conn:
        report = conn.execute(
            "SELECT id FROM crew_monitorings WHERE id = ?",
            (report_id,),
        ).fetchone()

        if not report:
            raise HTTPException(status_code=404, detail="Отчёт не найден")

        entity_id = scope_entity_for_reply(conn, "crew_monitorings", report_id, user)

        cursor = conn.execute(
            """
            INSERT INTO crew_report_comments
                (crew_monitoring_id, user_id, text, legal_entity_id)
            VALUES (?, ?, ?, ?)
            """,
            (report_id, user["sub"], text, entity_id),
        )

        comment_id = cursor.lastrowid

        for media_item in data.media:
            if isinstance(media_item, str):
                temp_name = media_item
                original_filename = None
            else:
                temp_name = media_item.filename
                original_filename = media_item.original_filename

            if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                continue

            old_path = media_path_for_key(temp_name)

            if not old_path.exists():
                continue

            ext = old_path.suffix.lower()

            if ext in VIDEO_EXTENSIONS:
                media_type = "video"
            elif ext in PHOTO_EXTENSIONS:
                media_type = "photo"
            else:
                continue

            new_name = media_filename_for_staged(temp_name, f"crc{comment_id}_", ext)
            old_path.rename(media_root_for_key(new_name) / new_name)

            conn.execute(
                """
                INSERT INTO crew_report_comment_media
                    (comment_id, filename, media_type, original_filename, legal_entity_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (comment_id, new_name, media_type, original_filename, entity_id),
            )

    return {
        "ok": True,
        "comment_id": comment_id,
    }


@app.get("/api/reports")
def list_reports(
    request: Request,
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
    address_id: Optional[int] = None,
    user_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "desc",
    limit: int = 200,
):
    require_capability(request, "monitoring.view")

    with get_db() as conn:
        sql = """
            SELECT r.id, r.created_at, r.text, r.task_id,
                   a.id AS address_id, a.city, a.address_name, a.manufacturer,
                   u.login AS author
            FROM reports r
            JOIN addresses a ON a.id = r.address_id
            JOIN users u ON u.id = r.user_id
            WHERE r.hidden = 0
        """
        params: list = []
        if city:
            sql += " AND a.city = ?"; params.append(city)
        if manufacturer:
            sql += " AND a.manufacturer = ?"; params.append(manufacturer)
        if address_id:
            sql += " AND r.address_id = ?"; params.append(address_id)
        if user_id:
            sql += " AND r.user_id = ?"; params.append(user_id)
        if date_from:
            sql += " AND date(r.created_at) >= ?"; params.append(date_from)
        if date_to:
            sql += " AND date(r.created_at) <= ?"; params.append(date_to)

        if sort == "asc":
            sql += " ORDER BY r.created_at ASC, r.id ASC"
        elif sort == "city":
            sql += " ORDER BY a.city, a.address_name, r.created_at DESC"
        elif sort == "address":
            sql += " ORDER BY a.address_name, r.created_at DESC"
        elif sort == "manufacturer":
            sql += " ORDER BY a.manufacturer, r.created_at DESC"
        else:
            sql += " ORDER BY r.created_at DESC, r.id DESC"

        sql += " LIMIT ?"; params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        reports = []
        for row in rows:
            report = dict(row)
            media = conn.execute(
                "SELECT filename, media_type FROM media WHERE report_id = ? ORDER BY created_at",
                (report["id"],),
            ).fetchall()

            # Если это ответ на задание, добавляем медиа самого задания.
            if report["task_id"]:
                task_media = conn.execute(
                    """
                    SELECT filename, media_type
                    FROM media
                    WHERE task_id = ? AND report_id IS NULL
                    ORDER BY created_at
                    """,
                    (report["task_id"],),
                ).fetchall()
                media = list(task_media) + list(media)

            report["media"] = [
                {"url": f"/api/media/{m['filename']}", "type": m["media_type"]}
                for m in media
            ]
            reports.append(report)

    return reports


# ---------- Задания ----------
@app.post("/api/tasks")
def create_task(data: TaskIn, request: Request):
    user = require_capability(request, "task.create")
    with get_db() as conn:
        addr = conn.execute(
            "SELECT id FROM addresses WHERE id = ? AND active = 1",
            (data.address_id,),
        ).fetchone()
        if not addr:
            raise HTTPException(status_code=400, detail="Адрес не найден")
        if not data.description.strip():
            raise HTTPException(status_code=400, detail="Пустое описание")

        entity_id = require_entity_scope(user)

        # Тип задания: код опционален, значение по умолчанию — «Обычное задание».
        task_type_id = None
        extra_values = getattr(data, "extra_fields", None)
        if getattr(data, "task_type", None):
            type_row = conn.execute(
                "SELECT id, code, name, fields_schema, active FROM task_types "
                "WHERE code = ? OR id = ?",
                (str(data.task_type), data.task_type if isinstance(data.task_type, int) else -1),
            ).fetchone()
            if not type_row or not type_row["active"]:
                raise HTTPException(status_code=400, detail="Тип задания недоступен")
            task_type_id = type_row["id"]
        elif extra_values:
            type_row = conn.execute(
                "SELECT id, code, name, fields_schema FROM task_types WHERE code = ?",
                (DEFAULT_TASK_TYPE["code"],),
            ).fetchone()
            task_type_id = type_row["id"] if type_row else None

        extra_fields_json = parse_extra_fields(
            conn.execute(
                "SELECT fields_schema FROM task_types WHERE id = ?", (task_type_id,)
            ).fetchone() if task_type_id else None,
            extra_values,
            strict=True,
        ) if task_type_id else None

        cursor = conn.execute(
            """INSERT INTO tasks
               (address_id, created_by, created_by_role,
                description, is_important, workflow_status, legal_entity_id,
                task_type_id, extra_fields)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.address_id,
                user["sub"],
                user["workplace"],
                data.description.strip(),
                1 if data.is_important else 0,
                "open",
                entity_id,
                task_type_id,
                extra_fields_json,
            ),
        )

        task_id = cursor.lastrowid

        # Фиксируем начальное состояние в истории.
        conn.execute(
            """
            INSERT INTO task_status_history
                (task_id, status, changed_by, changed_at, comment, legal_entity_id)
            VALUES (?, 'open', ?, CURRENT_TIMESTAMP, ?, ?)
            """,
            (
                task_id,
                user["sub"],
                "Создание задания",
                entity_id,
            ),
        )
        write_audit(
            conn,
            user,
            "task.create",
            "task",
            task_id,
            after={"address_id": data.address_id, "description": data.description.strip()},
            request=request,
        )
    return {"ok": True, "task_id": task_id}


@app.post("/api/tasks/{task_id}/media")
async def add_task_media(
    task_id: int,
    request: Request,
):
    """
    Привязать ранее загруженные временные файлы к заданию.
    Файлы загружаются через /api/upload, а здесь получают постоянное
    имя и запись в таблице media.
    """
    user = require_capability(request, "task.media")

    data = await request.json()
    filenames = data.get("filenames", [])

    if not isinstance(filenames, list):
        raise HTTPException(status_code=400, detail="Неверный список файлов")

    if len(filenames) > 10:
        raise HTTPException(
            status_code=400,
            detail="К заданию можно прикрепить не более 10 файлов"
        )

    with get_db() as conn:
        task = conn.execute(
            """
            SELECT id, workflow_status
            FROM tasks
            WHERE id = ?
            """,
            (task_id,)
        ).fetchone()

        if not task:
            raise HTTPException(status_code=404, detail="Задание не найдено")

        if task["workflow_status"] in ("completed", "closed"):
            raise HTTPException(
                status_code=403,
                detail="Задание завершено или отменено: изменение недоступно"
            )

        if (
            user["workplace"] == "service"
            and task["workflow_status"] not in ("open", "rework")
        ):
            raise HTTPException(
                status_code=403,
                detail="К этому заданию сейчас нельзя добавлять медиа"
            )

        current_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM media WHERE task_id = ? AND report_id IS NULL",
            (task_id,)
        ).fetchone()["cnt"]

        if current_count + len(filenames) > 10:
            raise HTTPException(
                status_code=400,
                detail="К заданию можно прикрепить не более 10 файлов"
            )

        result = []
        entity_id = scope_entity_for_reply(conn, "tasks", task_id, user)

        for media_item in filenames:
            if isinstance(media_item, str):
                temp_name = media_item
                original_filename = None
            elif isinstance(media_item, dict):
                temp_name = media_item.get("filename")
                original_filename = media_item.get("original_filename")
            else:
                raise HTTPException(status_code=400, detail="Неверное имя файла")

            if not isinstance(temp_name, str) or not temp_name:
                raise HTTPException(status_code=400, detail="Неверное имя файла")

            # Разрешаем только временные файлы, созданные /api/upload.
            if not is_temp_media_key(temp_name):
                raise HTTPException(status_code=400, detail="Недопустимый файл")

            temp_path = media_path_for_key(temp_name)

            if not temp_path.exists() or not temp_path.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"Файл не найден: {temp_name}"
                )

            ext = temp_path.suffix.lower()

            if ext in PHOTO_EXTENSIONS:
                media_type = "photo"
                prefix = "t"
            elif ext in VIDEO_EXTENSIONS:
                media_type = "video"
                prefix = "t"
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Недопустимый формат файла: {ext}"
                )

            # /api/upload уже проверил глобальную настройку,
            # но проверяем её ещё раз на этапе окончательной привязки.
            if media_type == "photo" and get_setting("photos_enabled", "1") != "1":
                raise HTTPException(
                    status_code=403,
                    detail="Загрузка фото отключена администратором"
                )

            if media_type == "video" and get_setting("videos_enabled", "1") != "1":
                raise HTTPException(
                    status_code=403,
                    detail="Загрузка видео отключена администратором"
                )

            final_name = media_filename_for_staged(temp_name, f"{prefix}{task_id}_", ext)
            final_path = media_root_for_key(final_name) / final_name

            temp_path.replace(final_path)

            conn.execute(
                """
                INSERT INTO media
                    (task_id, filename, media_type, original_filename, legal_entity_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, final_name, media_type, original_filename, entity_id)
            )

            result.append({
                "filename": final_name,
                "url": f"/api/media/{final_name}",
                "type": media_type,
            })

    return {"ok": True, "media": result}


@app.get("/api/tasks")
def list_tasks(
    request: Request,
    status: Optional[str] = None,
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
    task_type_id: Optional[int] = None,
):
    user = get_current_user(request)
    if user["workplace"] == "service" and status != "open":
        status = "open"

    with get_db() as conn:
        schemas_by_id = task_type_schemas(conn)
        sql = """
            SELECT t.id, t.description, t.status, t.created_at, t.closed_at,
                   t.is_important, t.close_reason,
                   t.created_by_role, t.workflow_status,
                   t.extra_fields, t.task_type_id, tt.name AS task_type_name,
                   a.city, a.address_name, a.manufacturer,
                   u.login AS author,
                   cu.login AS closed_by_login
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            LEFT JOIN users cu ON cu.id = t.closed_by
            LEFT JOIN task_types tt ON tt.id = t.task_type_id
            WHERE t.hidden = 0
        """
        params: list = []
        scope_sql, scope_params = own_scope_clause(user, "t.created_by")
        sql += scope_sql
        params.extend(scope_params)
        if status and status != "all":
            sql += " AND t.status = ?"; params.append(status)
        if city:
            sql += " AND a.city = ?"; params.append(city)
        if manufacturer:
            sql += " AND a.manufacturer = ?"; params.append(manufacturer)
        if task_type_id:
            sql += " AND t.task_type_id = ?"; params.append(task_type_id)
        sql += " ORDER BY t.created_at DESC"
        rows = conn.execute(sql, params).fetchall()

        import json

        tasks = []
        task_ids = [row["id"] for row in rows]
        media_by_task = {task_id: [] for task_id in task_ids}
        comments_by_task = {task_id: [] for task_id in task_ids}

        # Batch media and comments instead of N+1 queries per task.
        for start in range(0, len(task_ids), 500):
            chunk = task_ids[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)

            media_rows = conn.execute(
                f"""
                SELECT task_id, filename, media_type, original_filename
                FROM media
                WHERE task_id IN ({placeholders})
                  AND report_id IS NULL
                ORDER BY created_at, id
                """,
                chunk,
            ).fetchall()
            for media in media_rows:
                media_by_task[media["task_id"]].append({
                    "url": f"/api/media/{media['filename']}",
                    "type": media["media_type"],
                    "filename": media["original_filename"] or media["filename"],
                })

            comment_rows = conn.execute(
                f"""
                SELECT c.task_id, c.id, c.text, c.media_json, c.created_at,
                       u.login, u.role
                FROM comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.task_id IN ({placeholders})
                ORDER BY c.created_at ASC, c.id ASC
                """,
                chunk,
            ).fetchall()
            for comment in comment_rows:
                comments_by_task[comment["task_id"]].append({
                    "id": comment["id"],
                    "text": comment["text"],
                    "media": json.loads(comment["media_json"]) if comment["media_json"] else [],
                    "created_at": comment["created_at"],
                    "author": comment["login"],
                    "role": comment["role"],
                    "is_director_comment": False,
                })

            director_rows = conn.execute(
                f"""
                SELECT dc.item_id AS task_id, dc.id, dc.text, dc.media_json,
                       dc.created_at, u.login
                FROM director_comments dc
                JOIN users u ON u.id = dc.user_id
                WHERE dc.item_type = 'task'
                  AND dc.item_id IN ({placeholders})
                ORDER BY dc.created_at ASC, dc.id ASC
                """,
                chunk,
            ).fetchall()
            for comment in director_rows:
                comments_by_task[comment["task_id"]].append({
                    "id": comment["id"],
                    "text": comment["text"],
                    "media": json.loads(comment["media_json"]) if comment["media_json"] else [],
                    "created_at": comment["created_at"],
                    "author": comment["login"],
                    "role": "director",
                    "is_director_comment": True,
                })

        for row in rows:
            task = dict(row)
            task["task_type"] = row["task_type_name"] or "Обычное задание"
            task["extra_fields"] = format_extra_fields(
                (schemas_by_id.get(task.get("task_type_id")) or {}).get("fields"),
                row["extra_fields"],
            )
            task["media"] = media_by_task.get(task["id"], [])
            task["comments"] = sorted(
                comments_by_task.get(task["id"], []),
                key=lambda item: item["created_at"],
            )

            if task["status"] == "done":
                task["urgency"] = "done"
            else:
                created = datetime.fromisoformat(task["created_at"].replace(" ", "T"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
                if age_hours < 24:
                    task["urgency"] = "new"
                elif age_hours < 48:
                    task["urgency"] = "warning"
                else:
                    task["urgency"] = "overdue"
            tasks.append(task)
    return tasks



# ==========================================================
#                  WORKFLOW ЗАДАНИЙ
# ==========================================================

TASK_WORKFLOW_STATUSES = {
    "open": "Открыто",
    "pending_review": "На проверке",
    "completed": "Выполнено",
    "rework": "На доработке",
    "closed": "Отменено",
}


def get_task_for_workflow(conn, task_id: int):
    task = conn.execute(
        """
        SELECT
            id,
            address_id,
            created_by,
            created_by_role,
            workflow_status,
            status,
            description
        FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Задание не найдено"
        )

    return task


def enforce_director_task_scope(user: dict, task) -> None:
    if (
        user["workplace"] == "director"
        and get_base_role(task["created_by_role"] or "") != "director"
    ):
        raise HTTPException(
            status_code=403,
            detail="Директор может проверять только задания, созданные директором",
        )


def add_task_status_history(
    conn,
    task_id: int,
    status: str,
    user_id: int,
    comment: str = None,
):
    conn.execute(
        """
        INSERT INTO task_status_history
            (task_id, status, changed_by, changed_at, comment, legal_entity_id)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        (
            task_id,
            status,
            user_id,
            comment,
            # Переход статуса принадлежит юрлицу задания, а не сессии:
            # проверять и закрывать задания можно и в режиме «Все юрлица».
            parent_entity_id(conn, "tasks", task_id) or 1,
        ),
    )


def load_workflow_permissions(conn) -> dict:
    """Настроенные возможности переходов (только известные коды)."""
    from permissions import WORKFLOW_TRANSITIONS

    permissions_by_code = {}
    for row in conn.execute(
        "SELECT code, capabilities FROM workflow_transitions"
    ).fetchall():
        try:
            codes = json.loads(row["capabilities"] or "[]")
        except (TypeError, ValueError):
            codes = []
        valid = [c for c in codes if c in CAPABILITIES]
        # Пустой/битый список = переход по умолчанию из каталога.
        if not valid:
            valid = [WORKFLOW_TRANSITIONS[row["code"]]["default_capability"]]
        permissions_by_code[row["code"]] = valid
    return permissions_by_code


def ensure_transition_allowed(user: dict, permissions_by_code: dict, code: str) -> None:
    allowed = permissions_by_code.get(code) or []
    if not any(user_has(user, capability) for capability in allowed):
        raise HTTPException(
            status_code=403,
            detail="У вашей роли нет прав на это действие",
        )


def set_task_workflow_status(
    conn,
    task,
    new_status: str,
    user_id: int,
    comment: str = None,
):
    old_status = task["workflow_status"]

    if old_status == new_status:
        raise HTTPException(
            status_code=400,
            detail="Задание уже имеет этот статус"
        )

    conn.execute(
        """
        UPDATE tasks
        SET workflow_status = ?
        WHERE id = ?
        """,
        (
            new_status,
            task["id"],
        ),
    )

    add_task_status_history(
        conn,
        task["id"],
        new_status,
        user_id,
        comment,
    )


# ----------------------------------------------------------
# Сервис: Готово
# open/rework -> pending_review
# ----------------------------------------------------------

@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, request: Request):
    user = get_current_user(request)

    with get_db() as conn:
        ensure_transition_allowed(user, load_workflow_permissions(conn), "complete")
        task = get_task_for_workflow(conn, task_id)

        if task["workflow_status"] not in ("open", "rework"):
            raise HTTPException(
                status_code=400,
                detail="Это задание сейчас нельзя отправить на проверку"
            )

        set_task_workflow_status(
            conn,
            task,
            "pending_review",
            user["sub"],
            "Сервис отправил задание на проверку",
        )
        write_audit(
            conn,
            user,
            "task.complete",
            "task",
            task_id,
            after={"workflow_status": "pending_review"},
            request=request,
        )

    return {
        "ok": True,
        "task_id": task_id,
        "workflow_status": "pending_review",
    }


# ----------------------------------------------------------
# Проверка: Выполнено
#
# Админ может проверить любое задание.
# Директор может проверить только задание, созданное директором.
# ----------------------------------------------------------

@app.post("/api/tasks/{task_id}/approve")
def approve_task(task_id: int, request: Request):
    user = get_current_user(request)

    with get_db() as conn:
        ensure_transition_allowed(
            user, load_workflow_permissions(conn), "approve"
        )
        task = get_task_for_workflow(conn, task_id)
        enforce_director_task_scope(user, task)

        if task["workflow_status"] != "pending_review":
            raise HTTPException(
                status_code=400,
                detail="Задание не находится на проверке"
            )

        set_task_workflow_status(
            conn,
            task,
            "completed",
            user["sub"],
            "Задание принято",
        )
        write_audit(
            conn,
            user,
            "task.approve",
            "task",
            task_id,
            after={"workflow_status": "completed"},
            request=request,
        )

    return {
        "ok": True,
        "task_id": task_id,
        "workflow_status": "completed",
    }


# ----------------------------------------------------------
# На доработку
# pending_review -> rework
# ----------------------------------------------------------

@app.post("/api/tasks/{task_id}/rework")
def rework_task(task_id: int, request: Request):
    user = get_current_user(request)

    with get_db() as conn:
        ensure_transition_allowed(
            user, load_workflow_permissions(conn), "rework"
        )
        task = get_task_for_workflow(conn, task_id)
        enforce_director_task_scope(user, task)

        if task["workflow_status"] != "pending_review":
            raise HTTPException(
                status_code=400,
                detail="Задание не находится на проверке"
            )

        set_task_workflow_status(
            conn,
            task,
            "rework",
            user["sub"],
            "Задание отправлено на доработку",
        )
        write_audit(
            conn,
            user,
            "task.rework",
            "task",
            task_id,
            after={"workflow_status": "rework"},
            request=request,
        )

    return {
        "ok": True,
        "task_id": task_id,
        "workflow_status": "rework",
    }


# ----------------------------------------------------------
# Закрытие
#
# Админ может закрыть любое задание.
# Директор может закрыть только своё задание.
# Закрывать можно open/rework.
# ----------------------------------------------------------

@app.post("/api/tasks/{task_id}/close")
def close_task(task_id: int, request: Request):
    user = get_current_user(request)

    with get_db() as conn:
        ensure_transition_allowed(
            user, load_workflow_permissions(conn), "close"
        )
        task = get_task_for_workflow(conn, task_id)
        enforce_director_task_scope(user, task)

        if task["workflow_status"] not in ("open", "rework"):
            raise HTTPException(
                status_code=400,
                detail="Это задание сейчас нельзя закрыть"
            )

        set_task_workflow_status(
            conn,
            task,
            "closed",
            user["sub"],
            "Задание закрыто",
        )
        write_audit(
            conn,
            user,
            "task.close",
            "task",
            task_id,
            after={"workflow_status": "closed"},
            request=request,
        )

        # Сохраняем старую модель status для совместимости.
        conn.execute(
            """
            UPDATE tasks
            SET
                status = 'done',
                closed_at = CURRENT_TIMESTAMP,
                closed_by = ?
            WHERE id = ?
            """,
            (
                user["sub"],
                task_id,
            ),
        )

    return {
        "ok": True,
        "task_id": task_id,
        "workflow_status": "closed",
    }


# ----------------------------------------------------------
# История статусов задания
# ----------------------------------------------------------

@app.get("/api/tasks/{task_id}/status-history")
def task_status_history(task_id: int, request: Request):
    get_current_user(request)

    with get_db() as conn:
        task = conn.execute(
            "SELECT id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        if not task:
            raise HTTPException(
                status_code=404,
                detail="Задание не найдено"
            )

        rows = conn.execute(
            """
            SELECT
                h.id,
                h.task_id,
                h.status,
                h.changed_at,
                h.comment,
                u.login AS changed_by_login,
                u.role AS changed_by_role
            FROM task_status_history h
            JOIN users u ON u.id = h.changed_by
            WHERE h.task_id = ?
            ORDER BY h.changed_at ASC, h.id ASC
            """,
            (task_id,),
        ).fetchall()

        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "status": row["status"],
                "status_label": TASK_WORKFLOW_STATUSES.get(
                    row["status"],
                    row["status"],
                ),
                "changed_at": row["changed_at"],
                "comment": row["comment"],
                "changed_by": row["changed_by_login"],
                "changed_by_role": row["changed_by_role"],
            }
            for row in rows
        ]


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, request: Request):
    # Оставляем старый endpoint для совместимости,
    # но физического удаления больше нет.
    user = require_capability(request, "task.manage")

    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Задание не найдено",
            )

        conn.execute(
            "UPDATE tasks SET hidden = 1 WHERE id = ?",
            (task_id,),
        )
        write_audit(conn, user, "task.hide", "task", task_id, request=request)

    return {"ok": True, "hidden": True}


@app.post("/api/feed/{item_type}/{item_id}/hide")
def hide_feed_item(item_type: str, item_id: int, request: Request):
    """Скрыть запись из отображения всех ролей без физического удаления."""
    user = require_capability(request, "feed.hide")

    table_by_type = {
        "task": "tasks",
        "monitoring": "reports",
        "crew_report": "crew_monitorings",
    }

    table = table_by_type.get(item_type)

    if table is None:
        raise HTTPException(
            status_code=400,
            detail="Неизвестный тип записи",
        )

    with get_db() as conn:
        row = conn.execute(
            f"SELECT id FROM {table} WHERE id = ?",
            (item_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Запись не найдена",
            )

        conn.execute(
            f"UPDATE {table} SET hidden = 1 WHERE id = ?",
            (item_id,),
        )
        write_audit(
            conn,
            user,
            "feed.hide",
            item_type,
            item_id,
            request=request,
        )

    return {
        "ok": True,
        "hidden": True,
        "type": item_type,
        "id": item_id,
    }


# ---------- Экспорт в Excel ----------

@app.get("/api/export/excel")
def export_excel(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
):
    require_capability(request, "export.excel")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    with get_db() as conn:
        schemas_by_id = task_type_schemas(conn)
        tasks = conn.execute(
            """
            SELECT t.id, t.description, t.status, t.created_at, t.closed_at,
                   t.extra_fields, t.task_type_id, tt.name AS task_type_name,
                   a.city, a.address_name, a.manufacturer,
                   u.login AS author, cu.login AS closed_by
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            LEFT JOIN users cu ON cu.id = t.closed_by
            LEFT JOIN task_types tt ON tt.id = t.task_type_id
            WHERE (:df IS NULL OR date(t.created_at) >= :df)
              AND (:dt IS NULL OR date(t.created_at) <= :dt)
              AND (:city IS NULL OR a.city = :city)
              AND (:manufacturer IS NULL OR a.manufacturer = :manufacturer)
            ORDER BY t.created_at DESC
            """,
            {
                "df": date_from,
                "dt": date_to,
                "city": city,
                "manufacturer": manufacturer,
            },
        ).fetchall()

        reports = conn.execute(
            """
            SELECT r.id, r.created_at, r.text,
                   a.city, a.address_name, a.manufacturer,
                   u.login AS author
            FROM reports r
            JOIN addresses a ON a.id = r.address_id
            JOIN users u ON u.id = r.user_id
            WHERE (:df IS NULL OR date(r.created_at) >= :df)
              AND (:dt IS NULL OR date(r.created_at) <= :dt)
              AND (:city IS NULL OR a.city = :city)
              AND (:manufacturer IS NULL OR a.manufacturer = :manufacturer)
            ORDER BY r.created_at DESC
            """,
            {
                "df": date_from,
                "dt": date_to,
                "city": city,
                "manufacturer": manufacturer,
            },
        ).fetchall()

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1E40AF")

    # Динамические колонки доп. полей: по всем типам в выборке.
    extra_labels: list = []
    key_by_label: dict = {}
    for t in tasks:
        for item in (schemas_by_id.get(t["task_type_id"]) or {}).get("fields", []):
            key = item["key"]
            label = item.get("label") or key
            if label not in key_by_label.values():
                key_by_label[label] = key
                extra_labels.append(label)

    ws1 = wb.active
    ws1.title = "Задания"
    headers1 = ["№", "Город", "Адрес", "Производитель", "Тип задания", "Описание",
                *extra_labels, "Статус",
                "Создано", "Автор", "Закрыто", "Исполнитель"]
    ws1.append(headers1)
    for cell in ws1[1]:
        cell.font = header_font
        cell.fill = header_fill
    for t in tasks:
        values = decode_extra_fields(t["extra_fields"])
        extra_values = [values.get(key_by_label[label], "") for label in extra_labels]
        ws1.append([
            t["id"], t["city"], t["address_name"], t["manufacturer"] or "",
            t["task_type_name"] or "Обычное задание",
            t["description"],
            *extra_values,
            "Выполнено" if t["status"] == "done" else "Открыто",
            t["created_at"], t["author"],
            t["closed_at"] or "", t["closed_by"] or "",
        ])

    ws2 = wb.create_sheet("Мониторинг")
    headers2 = ["№", "Дата", "Город", "Адрес", "Производитель", "Автор", "Комментарий"]
    ws2.append(headers2)
    for cell in ws2[1]:
        cell.font = header_font
        cell.fill = header_fill
    for r in reports:
        ws2.append([
            r["id"], r["created_at"], r["city"], r["address_name"],
            r["manufacturer"] or "", r["author"], r["text"],
        ])

    for ws in [ws1, ws2]:
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    out = BytesIO()
    wb.save(out)
    out.seek(0)

    filename = f"albion_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------- Запуск ----------

# ========== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ И НАСТРОЙКАМИ (АДМИН) ==========
class UserIn(BaseModel):
    login: str
    password: str
    role: str
    legal_entity_id: Optional[int] = None


class PasswordIn(BaseModel):
    new_password: str


class MembershipEntityIn(BaseModel):
    legal_entity_id: int
    is_default: bool = False


class MembershipIn(BaseModel):
    role: str
    entities: List[MembershipEntityIn]


class LegalEntityIn(BaseModel):
    # Все поля опциональны: PUT приходит частичным. Обязательность названия
    # проверяется отдельно, чтобы отвечать 400, а не 422.
    name: Optional[str] = None
    short_name: Optional[str] = None
    inn: Optional[str] = None
    active: Optional[bool] = None


@app.get("/api/users")
def list_users_api(request: Request):
    # Сначала определяем область видимости, потом открываем соединение:
    # контекст юрлица применяется при выдаче соединения.
    user = require_capability(request, "users.manage")
    placeholders = ",".join(["?"] * len(user["scope_entity_ids"]))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, login, role FROM users WHERE legal_entity_id IN ({placeholders}) "
            "ORDER BY login",
            tuple(user["scope_entity_ids"]),
        ).fetchall()
    return [{"id": r["id"], "login": r["login"], "role": r["role"]} for r in rows]


def user_in_scope(conn, user: dict, target_user_id: int) -> bool:
    """Доступен ли администратору этот пользователь."""
    if user.get("all_entities"):
        return True
    row = conn.execute(
        "SELECT legal_entity_id FROM users WHERE id = ?", (target_user_id,)
    ).fetchone()
    if not row:
        return False
    return int(row["legal_entity_id"] or 1) in set(user["scope_entity_ids"])


@app.post("/api/users")
def create_user_api(data: UserIn, request: Request):
    user = require_capability(request, "users.manage")
    if not load_role_checked(data.role):
        raise HTTPException(status_code=400, detail="Роль недоступна")

    if data.legal_entity_id is not None:
        allowed_ids = {option["id"] for option in user_entity_options(user)}
        if int(data.legal_entity_id) not in allowed_ids:
            raise HTTPException(status_code=403, detail="Нет доступа к этому юрлицу")
        entity_id = int(data.legal_entity_id)
    else:
        entity_id = require_entity_scope(user)
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE login = ?", (data.login,)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Логин уже занят")
        cursor = conn.execute(
            "INSERT INTO users (login, password_hash, role, legal_entity_id) VALUES (?, ?, ?, ?)",
            (
                data.login,
                hash_password(data.password),
                data.role,
                entity_id,
            )
        )
        # Членство нового пользователя: его юрлицо по умолчанию.
        # INSERT ... SELECT ... WHERE NOT EXISTS работает и в SQLite, и в PostgreSQL.
        conn.execute(
            """
            INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
            SELECT ?, ?, ?, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM user_entities
                WHERE user_id = ? AND legal_entity_id = ?
            )
            """,
            (cursor.lastrowid, entity_id, data.role, cursor.lastrowid, entity_id),
        )
        write_audit(
            conn,
            user,
            "user.create",
            "user",
            data.login,
            after={"login": data.login, "role": data.role, "legal_entity_id": entity_id},
            request=request,
        )
    return {"ok": True}


@app.put("/api/users/{user_id}/password")
def change_password_api(user_id: int, data: PasswordIn, request: Request):
    user = require_capability(request, "users.manage")
    with get_db() as conn:
        if not user_in_scope(conn, user, user_id):
            raise HTTPException(status_code=403, detail="Пользователь вне вашего юрлица")
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?,
                token_version = COALESCE(token_version, 1) + 1
            WHERE id = ?
            """,
            (hash_password(data.new_password), user_id)
        )
        # Смена пароля немедленно закрывает все активные сессии пользователя.
        conn.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
              AND revoked_at IS NULL
            """,
            (user_id,),
        )
        write_audit(
            conn,
            user,
            "user.password.change",
            "user",
            user_id,
            request=request,
        )
    return {"ok": True}


# ---------- Юрлица группы и членство ----------
def load_role_checked(code: str):
    """Роль из конструктора: существует и активна."""
    with get_db() as conn:
        role = load_role(conn, code)
    if not role or not role["active"]:
        return None
    return role


def require_group_scope_manager(request: Request) -> dict:
    """Управление юрлицами доступно групповому администратору в сводном режиме."""
    user = require_capability(request, "users.manage")
    if not (user.get("is_group_admin") and user.get("all_entities")):
        raise HTTPException(
            status_code=403,
            detail="Управление юрлицами доступно в режиме «Все юрлица»",
        )
    return user


def entity_usage(conn, entity_id: int) -> dict:
    """Сколько данных привязано к юрлицу — для предупреждений в интерфейсе."""

    def count(table: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE legal_entity_id = ?", (entity_id,)
        ).fetchone()[0]

    return {
        "users": conn.execute(
            "SELECT COUNT(*) FROM user_entities WHERE legal_entity_id = ?",
            (entity_id,),
        ).fetchone()[0],
        "addresses": count("addresses"),
        "tasks": count("tasks"),
        "reports": count("reports"),
    }


def entity_payload(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "inn": row["inn"],
        "active": bool(row["active"]),
    }


@app.get("/api/admin/legal-entities")
def admin_list_legal_entities(request: Request):
    require_group_scope_manager(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, short_name, inn, active FROM legal_entities ORDER BY id"
        ).fetchall()
        return [
            {**entity_payload(row), "usage": entity_usage(conn, row["id"])}
            for row in rows
        ]


@app.post("/api/admin/legal-entities")
def admin_create_legal_entity(data: LegalEntityIn, request: Request):
    user = require_group_scope_manager(request)
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название юрлица")

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO legal_entities (name, short_name, inn, active)
            VALUES (?, ?, ?, 1)
            """,
            (
                name,
                (data.short_name or "").strip() or None,
                (data.inn or "").strip() or None,
            ),
        )
        entity_id = cursor.lastrowid
        write_audit(
            conn,
            user,
            "legal_entity.create",
            "legal_entity",
            entity_id,
            after={"name": name, "short_name": data.short_name, "inn": data.inn},
            request=request,
        )

    return {"ok": True, "id": entity_id}


@app.put("/api/admin/legal-entities/{entity_id}")
def admin_update_legal_entity(entity_id: int, data: LegalEntityIn, request: Request):
    user = require_group_scope_manager(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, short_name, inn, active FROM legal_entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Юрлицо не найдено")

        name = (data.name or "").strip() or row["name"]
        active = int(row["active"]) if data.active is None else (1 if data.active else 0)

        if not active and int(row["active"]):
            others = conn.execute(
                "SELECT COUNT(*) FROM legal_entities WHERE active = 1 AND id <> ?",
                (entity_id,),
            ).fetchone()[0]
            if not others:
                raise HTTPException(
                    status_code=400, detail="Нельзя выключить последнее активное юрлицо"
                )
            # Пользователи, для которых это юрлицо — единственное: они потеряют доступ.
            stranded = conn.execute(
                """
                SELECT COUNT(*) FROM user_entities current
                WHERE current.legal_entity_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM user_entities other
                      JOIN legal_entities other_entity ON other_entity.id = other.legal_entity_id
                      WHERE other.user_id = current.user_id
                        AND other.legal_entity_id <> ?
                        AND other_entity.active = 1
                  )
                """,
                (entity_id, entity_id),
            ).fetchone()[0]
            if stranded:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Нельзя выключить юрлицо: {stranded} пользовател(ь/я/ей) "
                        "не имеют доступа к другим активным юрлицам"
                    ),
                )

        before = entity_payload(row)
        conn.execute(
            """
            UPDATE legal_entities
            SET name = ?, short_name = ?, inn = ?, active = ?
            WHERE id = ?
            """,
            (
                name,
                (data.short_name if data.short_name is not None else row["short_name"]) or None,
                (data.inn if data.inn is not None else row["inn"]) or None,
                active,
                entity_id,
            ),
        )
        after = {
            "id": entity_id,
            "name": name,
            "short_name": data.short_name,
            "inn": data.inn,
            "active": bool(active),
        }
        write_audit(
            conn,
            user,
            "legal_entity.update",
            "legal_entity",
            entity_id,
            before=before,
            after=after,
            request=request,
        )

    return {"ok": True}


ROLE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class RoleIn(BaseModel):
    code: Optional[str] = None
    name: str
    workplace: str = "service"
    scope: str = "entity"
    capabilities: List[str] = []
    active: Optional[bool] = None


def roles_with_capabilities(conn) -> list:
    role_rows = conn.execute(
        """
        SELECT id, code, name, workplace, scope, is_system, active
        FROM roles
        ORDER BY is_system DESC, name
        """
    ).fetchall()
    links = conn.execute(
        "SELECT role_id, capability_code FROM role_capabilities"
    ).fetchall()
    usage = conn.execute(
        "SELECT role, COUNT(*) AS cnt FROM user_entities GROUP BY role"
    ).fetchall()

    capabilities_by_role = {}
    for link in links:
        capabilities_by_role.setdefault(link["role_id"], []).append(link["capability_code"])

    usage_by_code = {row["role"]: row["cnt"] for row in usage}

    return [
        {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "workplace": row["workplace"],
            "scope": row["scope"],
            "is_system": bool(row["is_system"]),
            "active": bool(row["active"]),
            "capabilities": sorted(capabilities_by_role.get(row["id"], [])),
            "users": usage_by_code.get(row["code"], 0),
        }
        for row in role_rows
    ]


def validate_role_payload(data: RoleIn, existing_code: str = None):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название роли")

    workplace = normalize_workplace(data.workplace)
    scope = normalize_scope(data.scope)

    unknown = unknown_capabilities(data.capabilities or [])
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестные возможности: {', '.join(unknown)}",
        )

    code = (data.code or existing_code or "").strip().lower()
    if not ROLE_CODE_RE.match(code):
        raise HTTPException(
            status_code=400,
            detail="Код роли: латиница, цифры и подчёркивание, начинается с буквы",
        )

    return code, name, workplace, scope, sorted(set(data.capabilities or []))


@app.get("/api/admin/roles")
def admin_list_roles(request: Request):
    require_capability(request, "roles.manage")
    with get_db() as conn:
        return {
            "capabilities": capability_catalog(),
            "workplaces": list(WORKPLACES),
            "scopes": list(SCOPES),
            "roles": roles_with_capabilities(conn),
        }


@app.post("/api/admin/roles")
def admin_create_role(data: RoleIn, request: Request):
    user = require_capability(request, "roles.manage")
    code, name, workplace, scope, capabilities = validate_role_payload(data)

    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM roles WHERE code = ?", (code,)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Роль с таким кодом уже есть")

        cursor = conn.execute(
            """
            INSERT INTO roles (code, name, workplace, scope, is_system, active)
            VALUES (?, ?, ?, ?, 0, 1)
            """,
            (code, name, workplace, scope),
        )
        role_id = cursor.lastrowid
        for capability_code in capabilities:
            conn.execute(
                "INSERT INTO role_capabilities (role_id, capability_code) VALUES (?, ?)",
                (role_id, capability_code),
            )
        write_audit(
            conn,
            user,
            "role.create",
            "role",
            code,
            after={"name": name, "workplace": workplace, "scope": scope,
                   "capabilities": capabilities},
            request=request,
        )

    return {"ok": True, "id": role_id, "code": code}


@app.put("/api/admin/roles/{role_id}")
def admin_update_role(role_id: int, data: RoleIn, request: Request):
    user = require_capability(request, "roles.manage")
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, code, name, workplace, scope, is_system, active FROM roles WHERE id = ?",
            (role_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Роль не найдена")
        if row["is_system"]:
            raise HTTPException(
                status_code=400,
                detail="Системную роль изменить нельзя — создайте свою на её основе",
            )

        code, name, workplace, scope, capabilities = validate_role_payload(
            data, existing_code=row["code"]
        )
        active = int(row["active"]) if data.active is None else (1 if data.active else 0)

        if not active and int(row["active"]):
            assigned = conn.execute(
                "SELECT COUNT(*) FROM user_entities WHERE role = ?", (row["code"],)
            ).fetchone()[0]
            if assigned:
                raise HTTPException(
                    status_code=400,
                    detail=f"Роль назначена {assigned} пользовател(ю/ям): сначала смените им роль",
                )

        before = {
            "name": row["name"],
            "workplace": row["workplace"],
            "scope": row["scope"],
            "active": bool(row["active"]),
        }

        conn.execute(
            """
            UPDATE roles
            SET name = ?, workplace = ?, scope = ?, active = ?
            WHERE id = ?
            """,
            (name, workplace, scope, active, role_id),
        )
        conn.execute("DELETE FROM role_capabilities WHERE role_id = ?", (role_id,))
        for capability_code in capabilities:
            conn.execute(
                "INSERT INTO role_capabilities (role_id, capability_code) VALUES (?, ?)",
                (role_id, capability_code),
            )

        write_audit(
            conn,
            user,
            "role.update",
            "role",
            row["code"],
            before=before,
            after={"name": name, "workplace": workplace, "scope": scope,
                   "active": bool(active), "capabilities": capabilities},
            request=request,
        )

    return {"ok": True}


# ---------- Типы заданий ----------
FIELD_TYPES = ("text", "number", "date", "select")
FIELDS_LIMIT = 5


class TaskTypeField(BaseModel):
    key: str
    label: str
    type: str = "text"
    options: List[str] = []
    required: bool = False


class TaskTypeIn(BaseModel):
    code: Optional[str] = None
    name: str
    fields: List[TaskTypeField] = []
    active: Optional[bool] = None


def normalize_fields_schema(fields: List[TaskTypeField]):
    """Проверяет и нормализует набор доп. полей типа задания."""
    result = []
    seen = set()
    if len(fields) > FIELDS_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"Не больше {FIELDS_LIMIT} дополнительных полей",
        )
    for item in fields:
        key = (item.key or "").strip().lower().replace(" ", "_")
        label = (item.label or "").strip()
        if not key or not re.match(r"^[a-zа-я0-9_]{1,40}$", key, re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"Некорректный ключ поля: {key!r}")
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Дублирующийся ключ поля: {key}")
        if not label:
            raise HTTPException(status_code=400, detail=f"У поля {key} нет подписи")
        if item.type not in FIELD_TYPES:
            raise HTTPException(status_code=400, detail=f"Неизвестный тип поля: {item.type}")
        options = [option.strip() for option in (item.options or []) if option.strip()]
        if item.type == "select" and not options:
            raise HTTPException(
                status_code=400, detail=f"У списка {key} нет вариантов"
            )
        seen.add(key)
        result.append(
            {
                "key": key,
                "label": label,
                "type": item.type,
                "options": options,
                "required": bool(item.required),
            }
        )
    return result


def parse_extra_fields(task_type_row, raw_values, strict: bool):
    """Приводит значения доп. полей к типам; возвращает JSON-строку или None."""
    if not task_type_row:
        return None
    try:
        schema = json.loads(task_type_row["fields_schema"] or "[]")
    except (TypeError, ValueError):
        schema = []
    if not schema:
        return None

    values = raw_values if isinstance(raw_values, dict) else {}
    result = {}
    for item in schema:
        key = item["key"]
        value = values.get(key)
        if value in (None, ""):
            if item.get("required") and strict:
                raise HTTPException(
                    status_code=400,
                    detail=f"Заполните поле «{item['label']}»",
                )
            continue
        if item["type"] == "number":
            try:
                value = float(value) if not isinstance(value, (int, float)) else value
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Поле «{item['label']}» должно быть числом",
                )
            if float(value) == int(float(value)):
                value = int(float(value))
        elif item["type"] == "date":
            try:
                datetime.strptime(str(value), "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Поле «{item['label']}» должно быть датой ГГГГ-ММ-ДД",
                )
        elif item["type"] == "select" and str(value) not in item["options"]:
            raise HTTPException(
                status_code=400,
                detail=f"Поле «{item['label']}»: недопустимое значение",
            )
        result[key] = value

    return json.dumps(result, ensure_ascii=False) if result else None


def encode_extra_fields(values) -> str:
    return json.dumps(values, ensure_ascii=False)


def decode_extra_fields(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


@app.get("/api/task-types")
def list_task_types(request: Request):
    """Активные типы заданий — для формы создания и фильтров."""
    get_current_user(request)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, code, name, fields_schema
            FROM task_types
            WHERE active = 1
            ORDER BY is_system DESC, name
            """
        ).fetchall()

    return [
        {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "fields": json.loads(row["fields_schema"] or "[]"),
        }
        for row in rows
    ]


@app.get("/api/admin/task-types")
def admin_list_task_types(request: Request):
    require_capability(request, "task_types.manage")
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, code, name, fields_schema, is_system, active,
                   (SELECT COUNT(*) FROM tasks t WHERE t.task_type_id = task_types.id) AS tasks
            FROM task_types
            ORDER BY is_system DESC, name
            """
        ).fetchall()

    return [
        {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "fields": json.loads(row["fields_schema"] or "[]"),
            "is_system": bool(row["is_system"]),
            "active": bool(row["active"]),
            "tasks": row["tasks"],
        }
        for row in rows
    ]


def _task_type_payload(data: TaskTypeIn, existing_code=None):
    name = (data.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название типа задания")
    code = (data.code or existing_code or "").strip().lower().replace(" ", "_")
    if not re.match(r"^[a-z][a-z0-9_]{1,31}$", code):
        raise HTTPException(
            status_code=400,
            detail="Код типа: латиница, цифры и подчёркивание, начинается с буквы",
        )
    return code, name, normalize_fields_schema(data.fields or [])


@app.post("/api/admin/task-types")
def admin_create_task_type(data: TaskTypeIn, request: Request):
    user = require_capability(request, "task_types.manage")
    code, name, fields = _task_type_payload(data)

    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM task_types WHERE code = ?", (code,)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Тип с таким кодом уже есть")

        cursor = conn.execute(
            """
            INSERT INTO task_types (domain_code, code, name, fields_schema, is_system, active)
            VALUES (?, ?, ?, ?, 0, 1)
            """,
            (DEFAULT_TASK_DOMAIN[0], code, name, json.dumps(fields, ensure_ascii=False)),
        )
        write_audit(
            conn,
            user,
            "task_type.create",
            "task_type",
            code,
            after={"name": name, "fields": fields},
            request=request,
        )

    return {"ok": True, "id": cursor.lastrowid, "code": code}


@app.put("/api/admin/task-types/{type_id}")
def admin_update_task_type(type_id: int, data: TaskTypeIn, request: Request):
    user = require_capability(request, "task_types.manage")
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, code, name, fields_schema, is_system, active
            FROM task_types WHERE id = ?
            """,
            (type_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Тип не найден")

        code, name, fields = _task_type_payload(data, existing_code=row["code"])
        active = int(row["active"]) if data.active is None else (1 if data.active else 0)

        before_fields = json.loads(row["fields_schema"] or "[]")
        assigned = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_type_id = ?", (type_id,)
        ).fetchone()[0]
        if assigned and fields != before_fields:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Тип уже используется в {assigned} задани(и/ях): "
                    "состав полей менять нельзя — создайте новый тип"
                ),
            )

        before = {"name": row["name"], "active": bool(row["active"])}
        conn.execute(
            """
            UPDATE task_types
            SET name = ?, active = ?
            WHERE id = ?
            """,
            (name, active, type_id),
        )
        write_audit(
            conn,
            user,
            "task_type.update",
            "task_type",
            row["code"],
            before=before,
            after={"name": name, "active": bool(active), "fields": fields},
            request=request,
        )

    return {"ok": True}


# ---------- Переходы workflow ----------
class WorkflowTransitionIn(BaseModel):
    capabilities: List[str]


@app.get("/api/admin/workflow")
def admin_get_workflow(request: Request):
    require_capability(request, "workflow.manage")
    with get_db() as conn:
        configured = load_workflow_permissions(conn)

    transitions = []
    for code, config in WORKFLOW_TRANSITIONS.items():
        transitions.append(
            {
                "code": code,
                "label": config["label"],
                "from": list(config["from"]),
                "from_label": ", ".join(
                    TASK_WORKFLOW_STATUSES.get(status, status)
                    for status in config["from"]
                ),
                "to": config["to"],
                "to_label": TASK_WORKFLOW_STATUSES.get(config["to"], config["to"]),
                "capabilities": configured.get(code) or [config["default_capability"]],
                "default": config["default_capability"],
            }
        )

    return {"transitions": transitions, "capabilities": capability_catalog()}


@app.put("/api/admin/workflow/{code}")
def admin_update_workflow_transition(code: str, data: WorkflowTransitionIn, request: Request):
    user = require_capability(request, "workflow.manage")
    if code not in WORKFLOW_TRANSITIONS:
        raise HTTPException(status_code=404, detail="Переход не найден")

    unknown = unknown_capabilities(data.capabilities or [])
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестные возможности: {', '.join(unknown)}",
        )
    capabilities = sorted(set(data.capabilities or []))
    if not capabilities:
        raise HTTPException(
            status_code=400,
            detail="Добавьте хотя бы одну возможность или верните значение по умолчанию",
        )

    with get_db() as conn:
        row = conn.execute(
            "SELECT capabilities FROM workflow_transitions WHERE code = ?", (code,)
        ).fetchone()
        try:
            before = json.loads(row["capabilities"] or "[]") if row else []
        except (TypeError, ValueError):
            before = []

        conn.execute(
            "UPDATE workflow_transitions SET capabilities = ? WHERE code = ?",
            (json.dumps(capabilities, ensure_ascii=False), code),
        )
        write_audit(
            conn,
            user,
            "workflow.update",
            "workflow",
            code,
            before={"capabilities": before},
            after={"capabilities": capabilities},
            request=request,
        )

    return {"ok": True, "capabilities": capabilities}


@app.get("/api/admin/users")
def admin_list_users(request: Request):
    """Пользователи с членством по юрлицам — для управления доступом."""
    require_group_scope_manager(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, login, role, active, legal_entity_id FROM users ORDER BY login"
        ).fetchall()
        membership_rows = conn.execute(
            """
            SELECT user_id, legal_entity_id, role, is_default
            FROM user_entities
            ORDER BY legal_entity_id
            """
        ).fetchall()

    memberships = {}
    for row in membership_rows:
        memberships.setdefault(row["user_id"], []).append(
            {
                "legal_entity_id": row["legal_entity_id"],
                "role": row["role"],
                "is_default": bool(row["is_default"]),
            }
        )

    payload = []
    for row in rows:
        entities = memberships.get(row["id"], [])
        home_entity_id = row["legal_entity_id"] or 1
        # Собственное юрлицо всегда доступно, даже если строки членства нет.
        if not any(item["legal_entity_id"] == home_entity_id for item in entities):
            entities = [
                {
                    "legal_entity_id": home_entity_id,
                    "role": row["role"],
                    "is_default": not entities,
                },
                *entities,
            ]

        payload.append(
            {
                "id": row["id"],
                "login": row["login"],
                "role": row["role"],
                "active": bool(row["active"]),
                "legal_entity_id": home_entity_id,
                "entities": entities,
            }
        )

    return payload


@app.put("/api/admin/users/{user_id}/entities")
def admin_update_user_entities(user_id: int, data: MembershipIn, request: Request):
    user = require_group_scope_manager(request)
    if not load_role_checked(data.role):
        raise HTTPException(status_code=400, detail="Роль недоступна")

    requested = {item.legal_entity_id: bool(item.is_default) for item in data.entities}
    if not requested:
        raise HTTPException(status_code=400, detail="Выберите хотя бы одно юрлицо")

    with get_db() as conn:
        target = conn.execute(
            "SELECT id, login, role, legal_entity_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        active_ids = {
            row["id"]
            for row in conn.execute("SELECT id FROM legal_entities WHERE active = 1").fetchall()
        }
        unavailable = sorted(set(requested) - active_ids)
        if unavailable:
            raise HTTPException(
                status_code=400,
                detail=f"Юрлица недоступны или выключены: {', '.join(map(str, unavailable))}",
            )

        # В группе обязан остаться хотя бы один администратор.
        target_role = load_role(conn, data.role)
        if not target_role or not target_role["active"]:
            raise HTTPException(status_code=400, detail="Роль недоступна")

        if target_role["workplace"] != "admin":
            other_admins = conn.execute(
                """
                SELECT COUNT(*) FROM users u
                JOIN roles r ON r.code = u.role
                WHERE u.id <> ? AND r.workplace = 'admin'
                """,
                (user_id,),
            ).fetchone()[0]
            if not other_admins:
                raise HTTPException(
                    status_code=400,
                    detail="Нельзя убрать администратора: в группе не останется ни одного",
                )

        defaults = [entity_id for entity_id, is_default in requested.items() if is_default]
        default_entity_id = defaults[0] if defaults else sorted(requested)[0]

        before_rows = conn.execute(
            "SELECT legal_entity_id, role, is_default FROM user_entities WHERE user_id = ? "
            "ORDER BY legal_entity_id",
            (user_id,),
        ).fetchall()
        before = [
            {
                "legal_entity_id": row["legal_entity_id"],
                "role": row["role"],
                "is_default": bool(row["is_default"]),
            }
            for row in before_rows
        ]

        conn.execute("DELETE FROM user_entities WHERE user_id = ?", (user_id,))
        for entity_id in sorted(requested):
            conn.execute(
                """
                INSERT INTO user_entities (user_id, legal_entity_id, role, is_default)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, entity_id, data.role, 1 if entity_id == default_entity_id else 0),
            )
        # Основное юрлицо и роль пользователя следуют за выбором администратора.
        conn.execute(
            "UPDATE users SET role = ?, legal_entity_id = ? WHERE id = ?",
            (data.role, default_entity_id, user_id),
        )
        write_audit(
            conn,
            user,
            "user.entities.update",
            "user",
            user_id,
            before={"entities": before},
            after={
                "role": data.role,
                "default_entity_id": default_entity_id,
                "entities": sorted(requested),
            },
            request=request,
        )

    return {"ok": True, "default_entity_id": default_entity_id}


# ========== ЧАТ И КОММЕНТАРИИ ==========
class CommentIn(BaseModel):
    task_id: int
    text: str
    media: List[str] = []

@app.get("/api/tasks/{task_id}/comments")
def get_comments(task_id: int, request: Request):
    get_current_user(request)
    import json
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.text, c.media_json, c.created_at, u.login, u.role
            FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.task_id = ?
            ORDER BY c.created_at ASC
        """, (task_id,)).fetchall()
        
        comments = []
        for r in rows:
            media = json.loads(r["media_json"]) if r["media_json"] else []
            comments.append({
                "id": r["id"], "text": r["text"], "media": media,
                "created_at": r["created_at"], "author": r["login"], "role": r["role"],
                "is_director_comment": False
            })
        
        # Добавляем комментарии директора (видны всем)
        dc_rows = conn.execute("""
            SELECT dc.id, dc.text, dc.media_json, dc.created_at, u.login
            FROM director_comments dc
            JOIN users u ON u.id = dc.user_id
            WHERE dc.item_type = 'task' AND dc.item_id = ?
            ORDER BY dc.created_at ASC
        """, (task_id,)).fetchall()
        
        for dc in dc_rows:
            media = json.loads(dc["media_json"]) if dc["media_json"] else []
            comments.append({
                "id": dc["id"], "text": dc["text"], "media": media,
                "created_at": dc["created_at"], "author": dc["login"], "role": "director",
                "is_director_comment": True
            })
        
        # Сортируем по времени
        comments.sort(key=lambda x: x["created_at"])
        return comments

@app.post("/api/comments")
def add_comment(data: CommentIn, request: Request):
    user = require_capability(request, "task.comment")
    import json

    with get_db() as conn:
        task = conn.execute(
            """
            SELECT id, workflow_status
            FROM tasks
            WHERE id = ?
            """,
            (data.task_id,),
        ).fetchone()

        if not task:
            raise HTTPException(
                status_code=404,
                detail="Задание не найдено"
            )

        if task["workflow_status"] in ("completed", "closed"):
            raise HTTPException(
                status_code=403,
                detail="Задание завершено или отменено: изменение недоступно"
            )

        if (
            user["workplace"] == "service"
            and task["workflow_status"] not in ("open", "rework")
        ):
            raise HTTPException(
                status_code=403,
                detail="К этому заданию сейчас нельзя добавлять сообщения"
            )

        conn.execute("""
            INSERT INTO comments (task_id, user_id, text, media_json, legal_entity_id)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.task_id,
            user["sub"],
            data.text.strip(),
            json.dumps(data.media),
            scope_entity_for_reply(conn, "tasks", data.task_id, user),
        ))
        
        # Автозакрытие убрано — закрывает только Админ
            
    return {"ok": True}
# ========================================


# ========== ОБНОВЛЕНИЕ ЗАДАНИЯ (АДМИН) ==========
class TaskUpdateIn(BaseModel):
    is_important: bool = None
    status: str = None
    close_reason: str = None

@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, data: TaskUpdateIn, request: Request):
    require_capability(request, "task.comment")
    with get_db() as conn:
        task = conn.execute(
            """
            SELECT
                id,
                status,
                workflow_status,
                created_by_role
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        
        updates = []
        params = []
        
        if data.is_important is not None:
            updates.append("is_important = ?")
            params.append(1 if data.is_important else 0)
        
        if data.status is not None:
            if data.status not in ("open", "done"):
                raise HTTPException(
                    status_code=400,
                    detail="Неверный статус"
                )

            current_user = get_current_user(request)

            # Старый API оставляем для совместимости.
            # Но не позволяем им обходить новый workflow.
            if data.status == "done":
                if task["workflow_status"] not in ("open", "rework"):
                    raise HTTPException(
                        status_code=400,
                        detail="Используйте проверку задания для текущего workflow"
                    )

                updates.append("status = ?")
                params.append("done")
                updates.append("closed_at = CURRENT_TIMESTAMP")
                updates.append("closed_by = ?")
                params.append(current_user["sub"])

                updates.append("workflow_status = ?")
                params.append("closed")

                add_task_status_history(
                    conn,
                    task_id,
                    "closed",
                    current_user["sub"],
                    "Задание закрыто через совместимый API",
                )

            else:
                updates.append("status = ?")
                params.append("open")
                updates.append("closed_at = NULL")
                updates.append("closed_by = NULL")

                if task["workflow_status"] == "closed":
                    updates.append("workflow_status = ?")
                    params.append("open")

                    add_task_status_history(
                        conn,
                        task_id,
                        "open",
                        current_user["sub"],
                        "Задание повторно открыто через совместимый API",
                    )
        
        if data.close_reason is not None:
            updates.append("close_reason = ?")
            params.append(data.close_reason.strip() if data.close_reason.strip() else None)
        
        if updates:
            params.append(task_id)
            conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
    
    return {"ok": True}
# ================================================


# ========== МОНИТОРИНГ (ЧАТЫ ПО АДРЕСАМ) ==========
@app.get("/api/monitoring/{address_id}")
def get_monitoring(address_id: int, request: Request):
    """Получить сообщения мониторинга по адресу"""
    get_current_user(request)
    import json
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.id, r.text, r.created_at, u.login, u.role
            FROM reports r
            JOIN users u ON u.id = r.user_id
            WHERE r.address_id = ?
              AND r.task_id IS NULL
              AND r.hidden = 0
            ORDER BY r.created_at ASC
        """, (address_id,)).fetchall()
        
        messages = []
        for r in rows:
            media = conn.execute(
                "SELECT filename, media_type FROM media WHERE report_id = ?",
                (r["id"],)
            ).fetchall()
            messages.append({
                "id": r["id"],
                "text": r["text"],
                "created_at": r["created_at"],
                "author": r["login"],
                "role": r["role"],
                "media": [{"url": f"/api/media/{m['filename']}", "type": m["media_type"]} for m in media],
                "is_director_comment": False
            })
        
        # Добавляем комментарии директора (видны всем)
        dc_rows = conn.execute("""
            SELECT dc.id, dc.text, dc.media_json, dc.created_at, u.login
            FROM director_comments dc
            JOIN users u ON u.id = dc.user_id
            WHERE dc.item_type = 'monitoring'
              AND (
                  dc.report_id IN (
                      SELECT id FROM reports
                      WHERE address_id = ? AND task_id IS NULL
                  )
                  OR (dc.report_id IS NULL AND dc.item_id = ?)
              )
            ORDER BY dc.created_at ASC
        """, (address_id, address_id)).fetchall()
        
        for dc in dc_rows:
            messages.append({
                "id": dc["id"],
                "text": dc["text"],
                "media": json.loads(dc["media_json"]) if dc["media_json"] else [],
                "created_at": dc["created_at"],
                "author": dc["login"],
                "role": "director",
                "is_director_comment": True
            })
        
        messages.sort(key=lambda x: x["created_at"])
        return messages

@app.post("/api/monitoring")
def send_monitoring(data: ReportIn, request: Request):
    """Отправить сообщение мониторинга"""
    user = get_current_user(request)

    with get_db() as conn:
        addr = conn.execute(
            "SELECT id FROM addresses WHERE id = ? AND active = 1",
            (data.address_id,)
        ).fetchone()

        if not addr:
            raise HTTPException(
                status_code=400,
                detail="Адрес не найден"
            )

        if not data.text or not data.text.strip():
            raise HTTPException(
                status_code=400,
                detail="Пустой текст"
            )

        entity_id = require_entity_scope(user)

        # Комментарий директора хранится отдельно
        if user["workplace"] == "director":
            ensure_capability(user, "monitoring.comment")
            cursor = conn.execute(
                """
                INSERT INTO director_comments
                    (item_type, item_id, user_id, text, legal_entity_id)
                VALUES
                    ('monitoring', ?, ?, ?, ?)
                """,
                (
                    data.address_id,
                    user["sub"],
                    data.text.strip(),
                    entity_id,
                )
            )

            comment_id = cursor.lastrowid

            return {
                "ok": True,
                "comment_id": comment_id,
                "type": "director_comment"
            }

        # Обычное сообщение мониторинга
        ensure_capability(user, "monitoring.send")
        cursor = conn.execute(
            """
            INSERT INTO reports
                (address_id, user_id, task_id, text, legal_entity_id)
            VALUES
                (?, ?, NULL, ?, ?)
            """,
            (
                data.address_id,
                user["sub"],
                data.text.strip(),
                entity_id,
            )
        )

        report_id = cursor.lastrowid

        # Медиа прикрепляется к обычному отчету
        if data.media:
            for temp_name in data.media:

                if (
                    "/" in temp_name
                    or "\\" in temp_name
                    or ".." in temp_name
                ):
                    continue

                old_path = media_path_for_key(temp_name)

                if not old_path.exists():
                    continue

                ext = old_path.suffix.lower()

                media_type = (
                    "video"
                    if ext in VIDEO_EXTENSIONS
                    else "photo"
                )

                new_name = media_filename_for_staged(temp_name, f"m{report_id}_", ext)

                new_path = media_root_for_key(new_name) / new_name

                old_path.rename(new_path)

                conn.execute(
                    """
                    INSERT INTO media
                        (report_id, filename, media_type, legal_entity_id)
                    VALUES
                        (?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        new_name,
                        media_type,
                        entity_id
                    )
                )

    return {
        "ok": True,
        "report_id": report_id,
        "type": "monitoring"
    }

# ================================================


# ========== ЛЕНТА ДИРЕКТОРА ==========
@app.get("/api/director/feed")
def director_feed(request: Request):
    """Смешанная лента заданий и мониторинга для директора"""
    import json
    with get_db() as conn:
        user = get_current_user(request)
        task_scope_sql, task_scope_params = own_scope_clause(user, "t.created_by")
        report_scope_sql, report_scope_params = own_scope_clause(user, "r.user_id")

        # Задания
        tasks = conn.execute(f"""
            SELECT 'task' as type, t.id, t.description as text, t.created_at,
                   a.city, a.address_name, a.manufacturer,
                   u.login as author, t.status, t.workflow_status, t.is_important, t.close_reason
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            WHERE t.hidden = 0{task_scope_sql}
            ORDER BY t.created_at DESC LIMIT 200
        """, tuple(task_scope_params)).fetchall()
        
        # Мониторинг (последние записи)
        monitoring = conn.execute(f"""
            SELECT 'monitoring' as type, r.id, r.text, r.created_at,
                   a.city, a.address_name, a.manufacturer,
                   u.login as author, NULL as status, 0 as is_important, NULL as close_reason
            FROM reports r
            JOIN addresses a ON a.id = r.address_id
            JOIN users u ON u.id = r.user_id
            WHERE r.task_id IS NULL
              AND r.hidden = 0{report_scope_sql}
            ORDER BY r.created_at DESC LIMIT 200
        """, tuple(report_scope_params)).fetchall()
        
        # Объединяем и сортируем по дате
        feed = []
        for row in tasks:
            feed.append(dict(row))
        for row in monitoring:
            feed.append(dict(row))
        
        feed.sort(key=lambda x: x["created_at"], reverse=True)
        
        # Добавляем комментарии директора к каждому элементу
        result = []
        for item in feed:
            comments = conn.execute("""
                SELECT dc.text, dc.media_json, dc.created_at, u.login
                FROM director_comments dc
                JOIN users u ON u.id = dc.user_id
                WHERE dc.item_type = ? AND dc.item_id = ?
                ORDER BY dc.created_at ASC
            """, (item["type"], item["id"])).fetchall()
            
            item["director_comments"] = [
                {
                    "text": c["text"],
                    "media": json.loads(c["media_json"]) if c["media_json"] else [],
                    "created_at": c["created_at"],
                    "author": c["login"]
                }
                for c in comments
            ]
            result.append(item)
        
        # Определяем просроченные задания (нет ответов более 24 часов)
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        for item in result:
            if (
                item["type"] == "task"
                and item.get("workflow_status", "open") in ("open", "rework")
            ):
                # Просрочка считается только от момента создания задания.
                # Новые сообщения и комментарии директора срок НЕ продлевают.
                created_time = datetime.fromisoformat(item["created_at"])
                hours_since = (now - created_time).total_seconds() / 3600
                item["is_overdue"] = hours_since > 24
            else:
                item["is_overdue"] = False
        
        return result

class DirectorCommentIn(BaseModel):
    item_type: str  # 'task' или 'monitoring'
    item_id: int
    text: str
    media: List[str] = []

@app.post("/api/director/comments")
def add_director_comment(data: DirectorCommentIn, request: Request):
    """Директор оставляет комментарий"""
    user = require_capability(request, "monitoring.comment")
    if data.item_type not in ("task", "monitoring"):
        raise HTTPException(status_code=400, detail="Неверный тип")
    if not data.text.strip() and not data.media:
        raise HTTPException(
            status_code=400,
            detail="Комментарий не может быть пустым: добавьте текст или файл"
        )
    
    import json

    if len(data.media) > 10:
        raise HTTPException(
            status_code=400,
            detail="Можно прикрепить не более 10 файлов"
        )

    with get_db() as conn:
        # Завершённые/отменённые задания полностью неизменяемы.
        if data.item_type == "task":
            task = conn.execute(
                "SELECT workflow_status FROM tasks WHERE id = ?",
                (data.item_id,)
            ).fetchone()

            if not task:
                raise HTTPException(
                    status_code=404,
                    detail="Задание не найдено"
                )

            if task["workflow_status"] in ("completed", "closed"):
                raise HTTPException(
                    status_code=403,
                    detail="Задание завершено или отменено: изменение недоступно"
                )

            conn.execute(
                """
                INSERT INTO director_comments
                    (item_type, item_id, user_id, text, media_json, legal_entity_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data.item_type,
                    data.item_id,
                    user["sub"],
                    data.text.strip(),
                    json.dumps(data.media, ensure_ascii=False),
                    scope_entity_for_reply(conn, "tasks", data.item_id, user),
                )
            )

        else:
            # Новый мониторинг = конкретная запись reports.
            # item_id для новых комментариев также содержит report_id,
            # а report_id используется как основная привязка.
            report = conn.execute(
                """
                SELECT id
                FROM reports
                WHERE id = ?
                  AND task_id IS NULL
                """,
                (data.item_id,)
            ).fetchone()

            if not report:
                raise HTTPException(
                    status_code=404,
                    detail="Мониторинг не найден"
                )

            conn.execute(
                """
                INSERT INTO director_comments
                    (item_type, item_id, report_id, user_id, text, media_json,
                     legal_entity_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.item_type,
                    data.item_id,
                    data.item_id,
                    user["sub"],
                    data.text.strip(),
                    json.dumps(data.media, ensure_ascii=False),
                    scope_entity_for_reply(conn, "reports", data.item_id, user),
                )
            )

    return {"ok": True}
# ======================================



@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/director", response_class=HTMLResponse)
def director_page(request: Request):
    return templates.TemplateResponse("director.html", {"request": request})


@app.get("/crew", response_class=HTMLResponse)
def crew_page(request: Request):
    return templates.TemplateResponse("crew.html", {"request": request})


@app.get("/head", response_class=HTMLResponse)
def head_page(request: Request):
    return templates.TemplateResponse("head.html", {"request": request})


# =========================================================
# НОВЫЕ ФУНКЦИОНАЛЬНЫЕ РОЛИ
# =========================================================

@app.get("/staff", response_class=HTMLResponse)
def staff_page(request: Request):
    # staff — функциональный профиль service.
    return templates.TemplateResponse("client.html", {"request": request})


@app.get("/administrator", response_class=HTMLResponse)
def administrator_page(request: Request):
    # administrator — функциональный профиль admin.
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/curator", response_class=HTMLResponse)
def curator_page(request: Request):
    # curator — функциональный профиль director.
    return templates.TemplateResponse("director.html", {"request": request})


# ========== ЕДИНАЯ ЛЕНТА (МОНИТОРИНГ + ЗАДАНИЯ) ==========
@app.get("/api/feed")
def get_unified_feed(request: Request, limit: int = 100, offset: int = 0):
    """Единая лента заданий и мониторингов.

    Задание = отдельная карточка по task.id.
    Мониторинг = отдельная карточка по address_id.

    ВАЖНО:
    - messages содержит только обычные сообщения;
    - director_comments хранится отдельно;
    - комментарии директора не влияют на last_message_*;
    - комментарии директора не влияют на сортировку карточек.
    """
    user = get_current_user(request)

    from datetime import datetime
    import json

    # Unified feed merges three independent tables, so pagination is applied
    # after merge+sort. Fetch enough rows from each type to cover the page.
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    fetch_limit = limit + offset

    with get_db() as conn:
        result = []

        # Роль с областью «только свои» видит лишь собственные записи.
        task_scope_sql, task_scope_params = own_scope_clause(user, "t.created_by")
        report_scope_sql, report_scope_params = own_scope_clause(user, "r.user_id")

        # =========================================================
        # 1. ЗАДАНИЯ
        # =========================================================
        task_type_schemas_by_id = task_type_schemas(conn)
        tasks = conn.execute(f"""
            SELECT
                t.id,
                t.address_id,
                t.extra_fields,
                t.task_type_id,
                tt.name AS task_type_name,
                t.description AS text,
                t.created_at,
                a.city,
                a.address_name,
                a.manufacturer,
                u.login AS author,
                u.role AS author_role,
                t.status,
                t.workflow_status,
                t.created_by_role,
                t.is_important,
                t.close_reason
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            LEFT JOIN task_types tt ON tt.id = t.task_type_id
            WHERE t.hidden = 0{task_scope_sql}
            ORDER BY t.created_at DESC
            LIMIT ?
        """, (*task_scope_params, fetch_limit)).fetchall()

        for task in tasks:
            item = {
                "type": "task",
                "id": task["id"],
                "address_id": task["address_id"],
                "text": task["text"],
                "created_at": task["created_at"],
                "city": task["city"],
                "address_name": task["address_name"],
                "manufacturer": task["manufacturer"],
                "author": task["author"],
                "author_role": get_base_role(task["author_role"] or ""),
                "status": task["status"],
                "workflow_status": task["workflow_status"],
                "workflow_status_label": TASK_WORKFLOW_STATUSES.get(
                    task["workflow_status"],
                    task["workflow_status"],
                ),
                "created_by_role": get_base_role(
                    task["created_by_role"] or ""
                ),
                "is_important": bool(task["is_important"]),
                "close_reason": task["close_reason"],
                "task_type": task["task_type_name"] or "Обычное задание",
                "extra_fields": format_extra_fields(
                    (task_type_schemas_by_id.get(task["task_type_id"]) or {}).get("fields"),
                    task["extra_fields"],
                ),
                "media": [],
                "messages": [],
                "director_comments": []
            }

            # Медиа самого задания.
            # Медиа сообщений чата хранится отдельно в comments.media_json.
            task_media = conn.execute("""
                SELECT
                    filename,
                    media_type
                FROM media
                WHERE task_id = ?
                  AND report_id IS NULL
                ORDER BY created_at ASC, id ASC
            """, (task["id"],)).fetchall()

            item["media"] = [
                {
                    "url": f"/api/media/{m['filename']}",
                    "type": m["media_type"]
                }
                for m in task_media
            ]

            # Обычный чат задания.
            comments = conn.execute("""
                SELECT
                    c.id,
                    c.text,
                    c.media_json,
                    c.created_at,
                    u.login,
                    u.role
                FROM comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.task_id = ?
                ORDER BY c.created_at ASC
            """, (task["id"],)).fetchall()

            for c in comments:
                item["messages"].append({
                    "id": c["id"],
                    "text": c["text"],
                    "media": json.loads(c["media_json"]) if c["media_json"] else [],
                    "created_at": c["created_at"],
                    "author": c["login"],
                    "role": c["role"],
                    "is_director_comment": False
                })

            # Комментарии директора — ОТДЕЛЬНО от обычного чата.
            director_comments = conn.execute("""
                SELECT
                    dc.id,
                    dc.text,
                    dc.media_json,
                    dc.created_at,
                    u.login
                FROM director_comments dc
                JOIN users u ON u.id = dc.user_id
                WHERE dc.item_type = 'task'
                  AND dc.item_id = ?
                ORDER BY dc.created_at ASC
            """, (task["id"],)).fetchall()

            for dc in director_comments:
                item["director_comments"].append({
                    "id": dc["id"],
                    "text": dc["text"],
                    "media": json.loads(dc["media_json"]) if dc["media_json"] else [],
                    "created_at": dc["created_at"],
                    "author": dc["login"],
                    "role": "director"
                })

            item["has_director_comment"] = bool(item["director_comments"])

            # История workflow-статусов.
            history_rows = conn.execute("""
                SELECT
                    h.id,
                    h.status,
                    h.changed_at,
                    h.comment,
                    u.login AS changed_by,
                    u.role AS changed_by_role
                FROM task_status_history h
                JOIN users u ON u.id = h.changed_by
                WHERE h.task_id = ?
                ORDER BY h.changed_at ASC, h.id ASC
            """, (task["id"],)).fetchall()

            item["status_history"] = [
                {
                    "id": h["id"],
                    "status": h["status"],
                    "status_label": TASK_WORKFLOW_STATUSES.get(
                        h["status"],
                        h["status"],
                    ),
                    "changed_at": h["changed_at"],
                    "changed_by": h["changed_by"],
                    "changed_by_role": h["changed_by_role"],
                    "comment": h["comment"],
                }
                for h in history_rows
            ]

            item["status_changed_at"] = (
                history_rows[-1]["changed_at"]
                if history_rows
                else None
            )

            # Последнее обычное сообщение.
            if item["messages"]:
                last_message = item["messages"][-1]
                item["last_message_at"] = last_message["created_at"]
                item["last_message_author"] = last_message["author"]
            else:
                item["last_message_at"] = None
                item["last_message_author"] = None

            result.append(item)

        # =========================================================
        # 2. МОНИТОРИНГ
        # =========================================================
        # Каждая запись reports = отдельная карточка мониторинга.
        # Сообщения одного адреса больше не объединяются.
        monitoring_reports = conn.execute(f"""
            SELECT
                r.id,
                r.address_id,
                r.text,
                r.created_at,
                u.login,
                u.role,
                a.city,
                a.address_name,
                a.manufacturer
            FROM reports r
            JOIN users u
              ON u.id = r.user_id
            JOIN addresses a
              ON a.id = r.address_id
            WHERE r.task_id IS NULL
              AND r.hidden = 0
              AND a.active = 1{report_scope_sql}
            ORDER BY r.created_at DESC
            LIMIT ?
        """, (*report_scope_params, fetch_limit)).fetchall()

        for r in monitoring_reports:
            report_id = r["id"]
            address_id = r["address_id"]

            media = conn.execute("""
                SELECT filename, media_type
                FROM media
                WHERE report_id = ?
                ORDER BY id ASC
            """, (report_id,)).fetchall()

            message = {
                "id": report_id,
                "text": r["text"],
                "created_at": r["created_at"],
                "author": r["login"],
                "role": r["role"],
                "media": [
                    {
                        "url": f"/api/media/{m['filename']}",
                        "type": m["media_type"]
                    }
                    for m in media
                ],
                "is_director_comment": False
            }

            # На первом этапе новые комментарии директора
            # будут привязываться к конкретному report_id.
            director_comments_rows = conn.execute("""
                SELECT
                    dc.id,
                    dc.text,
                    dc.media_json,
                    dc.created_at,
                    u.login
                FROM director_comments dc
                JOIN users u
                  ON u.id = dc.user_id
                WHERE dc.item_type = 'monitoring'
                  AND (
                      dc.report_id = ?
                      OR (
                          dc.report_id IS NULL
                          AND dc.item_id = ?
                          AND ? = (
                              SELECT MAX(r2.id)
                              FROM reports r2
                              WHERE r2.address_id = ?
                                AND r2.task_id IS NULL
                          )
                      )
                  )
                ORDER BY dc.created_at ASC
            """, (
                report_id,
                address_id,
                report_id,
                address_id
            )).fetchall()

            director_comments = []

            for dc in director_comments_rows:
                director_comments.append({
                    "id": dc["id"],
                    "text": dc["text"],
                    "media": (
                        json.loads(dc["media_json"])
                        if dc["media_json"] else []
                    ),
                    "created_at": dc["created_at"],
                    "author": dc["login"],
                    "role": "director"
                })

            item = {
                "type": "monitoring",

                # Теперь ID карточки = ID записи reports.
                "id": report_id,

                # ID адреса сохраняется отдельно.
                "address_id": address_id,

                "text": r["text"],
                "created_at": r["created_at"],
                "monitoring_date": r["created_at"],

                "city": r["city"],
                "address_name": r["address_name"],
                "manufacturer": r["manufacturer"],
                "author": r["login"],
                "author_role": get_base_role(r["role"] or ""),

                "status": "open",
                "is_important": False,
                "close_reason": None,

                # Медиа мониторинга — отдельное поле карточки.
                "media": message["media"],

                # Одна карточка = одна запись мониторинга.
                "messages": [message],

                "director_comments": director_comments,
                "has_director_comment": bool(director_comments),

                "last_message_at": r["created_at"],
                "last_message_author": r["login"]
            }

            result.append(item)

        # =========================================================
        # 3. ОТЧЁТЫ ЭКИПАЖА
        # =========================================================
        # Отчёты экипажа видят только Admin и Director.
        # Они идут отдельным типом crew_report.
        if user.get("workplace") in ("admin", "director", "head"):
            crew_reports = conn.execute("""
                SELECT
                    cr.id,
                    cr.address_id,
                    cr.user_id,
                    cr.text,
                    cr.created_at,
                    u.login AS crew_login,
                    u.role AS crew_role,
                    a.city,
                    a.address_name,
                    a.manufacturer
                FROM crew_monitorings cr
                JOIN users u
                  ON u.id = cr.user_id
                JOIN addresses a
                  ON a.id = cr.address_id
                WHERE a.active = 1
                  AND cr.hidden = 0
                ORDER BY cr.created_at DESC
                LIMIT ?
            """, (fetch_limit,)).fetchall()

            for cr in crew_reports:
                report_id = cr["id"]

                media_rows = conn.execute("""
                    SELECT
                        filename,
                        media_type
                    FROM crew_monitoring_media
                    WHERE crew_monitoring_id = ?
                    ORDER BY created_at ASC, id ASC
                """, (report_id,)).fetchall()

                report_media = [
                    {
                        "url": f"/api/media/{m['filename']}",
                        "type": m["media_type"]
                    }
                    for m in media_rows
                ]

                # Комментарии Head вместе с их фото/видео.
                head_comment_rows = conn.execute("""
                    SELECT
                        c.id,
                        c.text,
                        c.created_at,
                        u.login AS author
                    FROM crew_report_comments c
                    JOIN users u
                      ON u.id = c.user_id
                    WHERE c.crew_monitoring_id = ?
                      AND u.role = 'head'
                    ORDER BY c.created_at ASC, c.id ASC
                """, (report_id,)).fetchall()

                head_comments = []

                for hc in head_comment_rows:
                    comment_media_rows = conn.execute("""
                        SELECT
                            filename,
                            media_type
                        FROM crew_report_comment_media
                        WHERE comment_id = ?
                        ORDER BY created_at ASC, id ASC
                    """, (hc["id"],)).fetchall()

                    head_comments.append({
                        "id": hc["id"],
                        "text": hc["text"],
                        "created_at": hc["created_at"],
                        "author": hc["author"],
                        "role": "head",
                        "media": [
                            {
                                "url": f"/api/media/{m['filename']}",
                                "type": m["media_type"]
                            }
                            for m in comment_media_rows
                        ]
                    })

                message = {
                    "id": report_id,
                    "text": cr["text"],
                    "created_at": cr["created_at"],
                    "author": cr["crew_login"],
                    "role": "crew",
                    "media": report_media,
                    "is_director_comment": False
                }

                item = {
                    "type": "crew_report",
                    "id": report_id,
                    "address_id": cr["address_id"],
                    "text": cr["text"],
                    "description": cr["text"],
                    "created_at": cr["created_at"],
                    "monitoring_date": cr["created_at"],
                    "city": cr["city"],
                    "address_name": cr["address_name"],
                    "manufacturer": cr["manufacturer"],
                    "author": cr["crew_login"],
                    "author_role": get_base_role(cr["crew_role"] or ""),
                    "status": "open",
                    "is_important": False,
                    "close_reason": None,
                    "media": report_media,
                    "messages": [message],
                    "director_comments": [],
                    "head_comments": head_comments,
                    "has_director_comment": False,
                    "last_message_at": cr["created_at"],
                    "last_message_author": cr["crew_login"]
                }

                result.append(item)

        # =========================================================
        # 4. ПРОСРОЧЕННЫЕ ЗАДАНИЯ
        # =========================================================
        now = datetime.utcnow()

        for item in result:
            if item["type"] == "task":
                # Новая карточка — первые 6 часов.
                # Просроченное задание — после 24 часов.
                # Срок считается от создания задания.
                try:
                    created_time = datetime.fromisoformat(item["created_at"])
                    hours_since = (
                        now - created_time
                    ).total_seconds() / 3600

                    item["is_new"] = hours_since < 6

                    item["is_overdue"] = (
                        item["workflow_status"] in ("open", "rework")
                        and hours_since > 24
                    )
                except (ValueError, TypeError):
                    item["is_new"] = False
                    item["is_overdue"] = False
            else:
                item["is_new"] = False
                item["is_overdue"] = False

        # Сначала самые недавно активные карточки.
        # Комментарии директора здесь НЕ участвуют.
        result.sort(
            key=lambda x: (
                x.get("last_message_at")
                or x.get("created_at")
                or ""
            ),
            reverse=True
        )

        page = result[offset:offset + limit]

        return {
            "items": page,
            "has_more": len(result) > offset + limit,
            "limit": limit,
            "offset": offset,
        }

# ==========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
