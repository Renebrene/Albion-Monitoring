"""
АЛЬБИОН. Мониторинг экранов
Главный сервер FastAPI
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from io import BytesIO

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image

from database import get_db, init_schema, UPLOADS_DIR, get_setting, set_setting
from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_role, require_any,
    ROLE_LABELS, ROLE_HOME,
)

init_schema()

app = FastAPI(title="АЛЬБИОН. Мониторинг экранов")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------- Ограничения для медиа ----------
# Фото
PHOTO_MAX_SIZE = 5 * 1024 * 1024           # 5 МБ (исходный)
PHOTO_COMPRESSED_SIZE = 2.5 * 1024 * 1024  # 2.5 МБ (после сжатия)
PHOTO_MAX_WIDTH = 1280
PHOTO_MAX_HEIGHT = 2048
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Видео
VIDEO_MAX_SIZE = 50 * 1024 * 1024          # 50 МБ
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}

MAX_MEDIA_PER_REPORT = 10   # фото+видео суммарно


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


class ReportIn(BaseModel):
    address_id: int
    text: str
    task_id: Optional[int] = None
    media: List[str] = []   # список имён загруженных файлов


class CrewReportIn(BaseModel):
    address_id: int
    text: str = ""
    media: List[str] = []   # список имён загруженных файлов


class TaskIn(BaseModel):
    address_id: int
    description: str
    is_important: bool = False


class SettingIn(BaseModel):
    key: str
    value: str


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
@app.post("/api/login")
def login(data: LoginIn):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, login, password_hash, role FROM users WHERE login = ?",
            (data.login,),
        ).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_token(row["id"], row["login"], row["role"])
    return {
        "token": token,
        "role": row["role"],
        "role_label": ROLE_LABELS[row["role"]],
        "login": row["login"],
        "home": ROLE_HOME[row["role"]],
    }


@app.get("/api/me")
def me(request: Request):
    user = get_current_user(request)
    return {
        "sub": user["sub"],
        "login": user["login"],
        "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"]),
    }


# ---------- Настройки медиа (только Админ) ----------
@app.get("/api/settings")
def get_settings(request: Request):
    """Получить все настройки. Доступно всем авторизованным."""
    get_current_user(request)
    return {
        "photos_enabled": get_setting("photos_enabled", "1") == "1",
        "videos_enabled": get_setting("videos_enabled", "1") == "1",
    }


@app.put("/api/settings")
def update_setting(data: SettingIn, request: Request):
    """Изменить настройку. Только Админ."""
    require_any(request, ["admin"])
    allowed_keys = {"photos_enabled", "videos_enabled"}
    if data.key not in allowed_keys:
        raise HTTPException(status_code=400, detail="Неизвестная настройка")
    if data.value not in ("0", "1"):
        raise HTTPException(status_code=400, detail="Значение должно быть 0 или 1")
    set_setting(data.key, data.value)
    return {"ok": True, "key": data.key, "value": data.value}


# ---------- Справочники ----------
@app.get("/api/cities")
def cities():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT city FROM addresses WHERE active = 1 ORDER BY city"
        ).fetchall()
    return [r["city"] for r in rows]


@app.get("/api/manufacturers")
def manufacturers():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT manufacturer FROM addresses "
            "WHERE active = 1 AND manufacturer IS NOT NULL AND manufacturer != '' "
            "ORDER BY manufacturer"
        ).fetchall()
    return [r["manufacturer"] for r in rows]


@app.get("/api/addresses")
def addresses(
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
):
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

    ext = Path(file.filename).suffix.lower()
    content = await file.read()

    # Определяем тип
    if ext in PHOTO_EXTENSIONS:
        media_type = "photo"
        if get_setting("photos_enabled", "1") != "1":
            raise HTTPException(status_code=403, detail="Загрузка фото отключена администратором")
        if len(content) > PHOTO_MAX_SIZE:
            raise HTTPException(status_code=400, detail="Фото слишком большое (макс. 5 МБ)")
        try:
            compressed = compress_image(content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка обработки фото: {e}")
        filename = f"temp_{uuid.uuid4().hex}.jpg"
        with open(UPLOADS_DIR / filename, "wb") as f:
            f.write(compressed)

    elif ext in VIDEO_EXTENSIONS:
        media_type = "video"
        if get_setting("videos_enabled", "1") != "1":
            raise HTTPException(status_code=403, detail="Загрузка видео отключена администратором")
        if len(content) > VIDEO_MAX_SIZE:
            raise HTTPException(status_code=400, detail="Видео слишком большое (макс. 50 МБ)")
        filename = f"temp_{uuid.uuid4().hex}{ext}"
        with open(UPLOADS_DIR / filename, "wb") as f:
            f.write(content)

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат. Разрешены: фото {sorted(PHOTO_EXTENSIONS)}, видео {sorted(VIDEO_EXTENSIONS)}",
        )

    return {
        "filename": filename,
        "url": f"/uploads/{filename}",
        "media_type": media_type,
    }


# ---------- Записи мониторинга ----------
@app.post("/api/reports")
def create_report(data: ReportIn, request: Request):
    user = require_any(request, ["admin", "service"])

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

        cursor = conn.execute(
            "INSERT INTO reports (address_id, user_id, task_id, text) VALUES (?, ?, ?, ?)",
            (data.address_id, user["sub"], data.task_id, data.text.strip()),
        )
        report_id = cursor.lastrowid

        # Привязываем медиа
        if data.media:
            if len(data.media) > MAX_MEDIA_PER_REPORT:
                raise HTTPException(
                    status_code=400,
                    detail=f"Слишком много файлов (макс. {MAX_MEDIA_PER_REPORT})",
                )
            for temp_name in data.media:
                if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                    continue
                old_path = UPLOADS_DIR / temp_name
                if not old_path.exists():
                    continue
                ext = old_path.suffix
                # Определяем тип по расширению
                media_type = "video" if ext in VIDEO_EXTENSIONS else "photo"
                new_name = f"r{report_id}_{uuid.uuid4().hex[:8]}{ext}"
                old_path.rename(UPLOADS_DIR / new_name)
                conn.execute(
                    "INSERT INTO media (report_id, filename, media_type) VALUES (?, ?, ?)",
                    (report_id, new_name, media_type),
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
    user = require_role(request, "crew")

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

        cursor = conn.execute(
            """
            INSERT INTO crew_monitorings
                (address_id, user_id, text)
            VALUES (?, ?, ?)
            """,
            (data.address_id, user["sub"], text),
        )

        report_id = cursor.lastrowid

        for temp_name in data.media:
            if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                continue

            old_path = UPLOADS_DIR / temp_name

            if not old_path.exists():
                continue

            ext = old_path.suffix.lower()

            if ext in VIDEO_EXTENSIONS:
                media_type = "video"
            elif ext in PHOTO_EXTENSIONS:
                media_type = "photo"
            else:
                continue

            new_name = f"cr{report_id}_{uuid.uuid4().hex[:8]}{ext}"

            old_path.rename(UPLOADS_DIR / new_name)

            conn.execute(
                """
                INSERT INTO crew_monitoring_media
                    (crew_monitoring_id, filename, media_type)
                VALUES (?, ?, ?)
                """,
                (report_id, new_name, media_type),
            )

    return {
        "ok": True,
        "report_id": report_id,
    }


@app.get("/api/crew/reports")
def list_crew_reports(request: Request):
    user = require_role(request, "crew")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                cr.id,
                cr.address_id,
                cr.created_at,
                cr.text,
                a.city,
                a.address_name,
                a.manufacturer
            FROM crew_monitorings cr
            JOIN addresses a ON a.id = cr.address_id
            WHERE cr.user_id = ?
            ORDER BY cr.created_at DESC, cr.id DESC
            """,
            (user["sub"],),
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
                "city": row["city"],
                "address_name": row["address_name"],
                "manufacturer": row["manufacturer"],
                "text": row["text"],
                "created_at": row["created_at"],
                "media": [
                    {
                        "id": media["id"],
                        "filename": media["filename"],
                        "url": f"/uploads/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
            })

    return result



# ============================================================
# HEAD / РУКОВОДИТЕЛЬ — отчёты экипажа
# ============================================================

@app.get("/api/head/reports")
def list_head_reports(request: Request):
    user = require_role(request, "head")

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
                        "url": f"/uploads/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
            })

    return result


@app.get("/api/head/reports/{report_id}/comments")
def list_head_report_comments(report_id: int, request: Request):
    user = require_role(request, "head")

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
                        "url": f"/uploads/{media['filename']}",
                        "media_type": media["media_type"],
                        "created_at": media["created_at"],
                    }
                    for media in media_rows
                ],
            })

    return result


class HeadReportCommentIn(BaseModel):
    text: str = ""
    media: List[str] = []


@app.post("/api/head/reports/{report_id}/comments")
def create_head_report_comment(
    report_id: int,
    data: HeadReportCommentIn,
    request: Request,
):
    user = require_role(request, "head")

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

        cursor = conn.execute(
            """
            INSERT INTO crew_report_comments
                (crew_monitoring_id, user_id, text)
            VALUES (?, ?, ?)
            """,
            (report_id, user["sub"], text),
        )

        comment_id = cursor.lastrowid

        for temp_name in data.media:
            if "/" in temp_name or "\\" in temp_name or ".." in temp_name:
                continue

            old_path = UPLOADS_DIR / temp_name

            if not old_path.exists():
                continue

            ext = old_path.suffix.lower()

            if ext in VIDEO_EXTENSIONS:
                media_type = "video"
            elif ext in PHOTO_EXTENSIONS:
                media_type = "photo"
            else:
                continue

            new_name = f"crc{comment_id}_{uuid.uuid4().hex[:8]}{ext}"
            old_path.rename(UPLOADS_DIR / new_name)

            conn.execute(
                """
                INSERT INTO crew_report_comment_media
                    (comment_id, filename, media_type)
                VALUES (?, ?, ?)
                """,
                (comment_id, new_name, media_type),
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
    require_any(request, ["admin", "director"])

    with get_db() as conn:
        sql = """
            SELECT r.id, r.created_at, r.text, r.task_id,
                   a.id AS address_id, a.city, a.address_name, a.manufacturer,
                   u.login AS author
            FROM reports r
            JOIN addresses a ON a.id = r.address_id
            JOIN users u ON u.id = r.user_id
            WHERE 1=1
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
            report["media"] = [
                {"url": f"/uploads/{m['filename']}", "type": m["media_type"]}
                for m in media
            ]
            reports.append(report)

    return reports


# ---------- Задания ----------
@app.post("/api/tasks")
def create_task(data: TaskIn, request: Request):
    user = require_any(request, ["admin", "director"])
    with get_db() as conn:
        addr = conn.execute(
            "SELECT id FROM addresses WHERE id = ? AND active = 1",
            (data.address_id,),
        ).fetchone()
        if not addr:
            raise HTTPException(status_code=400, detail="Адрес не найден")
        if not data.description.strip():
            raise HTTPException(status_code=400, detail="Пустое описание")

        cursor = conn.execute(
            """INSERT INTO tasks
               (address_id, created_by, created_by_role,
                description, is_important, workflow_status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.address_id,
                user["sub"],
                user["role"],
                data.description.strip(),
                1 if data.is_important else 0,
                "open",
            ),
        )

        task_id = cursor.lastrowid

        # Фиксируем начальное состояние в истории.
        conn.execute(
            """
            INSERT INTO task_status_history
                (task_id, status, changed_by, changed_at, comment)
            VALUES (?, 'open', ?, CURRENT_TIMESTAMP, ?)
            """,
            (
                task_id,
                user["sub"],
                "Создание задания",
            ),
        )
        task_id = cursor.lastrowid
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
    user = require_any(request, ["admin", "director", "service"])

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
            user["role"] == "service"
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

        for temp_name in filenames:
            if not isinstance(temp_name, str) or not temp_name:
                raise HTTPException(status_code=400, detail="Неверное имя файла")

            # Разрешаем только временные файлы, созданные /api/upload.
            if not temp_name.startswith("temp_"):
                raise HTTPException(status_code=400, detail="Недопустимый файл")

            temp_path = UPLOADS_DIR / temp_name

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

            final_name = f"{prefix}{task_id}_{uuid.uuid4().hex}{ext}"
            final_path = UPLOADS_DIR / final_name

            temp_path.replace(final_path)

            conn.execute(
                """
                INSERT INTO media (task_id, filename, media_type)
                VALUES (?, ?, ?)
                """,
                (task_id, final_name, media_type)
            )

            result.append({
                "filename": final_name,
                "url": f"/uploads/{final_name}",
                "type": media_type,
            })

    return {"ok": True, "media": result}


@app.get("/api/tasks")
def list_tasks(
    request: Request,
    status: Optional[str] = None,
    city: Optional[str] = None,
    manufacturer: Optional[str] = None,
):
    user = get_current_user(request)
    if user["role"] == "service" and status != "open":
        status = "open"

    with get_db() as conn:
        sql = """
            SELECT t.id, t.description, t.status, t.created_at, t.closed_at,
                   t.is_important, t.close_reason,
                   t.created_by_role, t.workflow_status,
                   a.city, a.address_name, a.manufacturer,
                   u.login AS author,
                   cu.login AS closed_by_login
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            LEFT JOIN users cu ON cu.id = t.closed_by
            WHERE 1=1
        """
        params: list = []
        if status and status != "all":
            sql += " AND t.status = ?"; params.append(status)
        if city:
            sql += " AND a.city = ?"; params.append(city)
        if manufacturer:
            sql += " AND a.manufacturer = ?"; params.append(manufacturer)
        sql += " ORDER BY t.created_at DESC"
        rows = conn.execute(sql, params).fetchall()

        tasks = []
        for row in rows:
            task = dict(row)
            media = conn.execute(
                "SELECT filename, media_type FROM media WHERE task_id = ? AND report_id IS NULL",
                (task["id"],),
            ).fetchall()
            task["media"] = [
                {"url": f"/uploads/{m['filename']}", "type": m["media_type"]}
                for m in media
            ]

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
            (task_id, status, changed_by, changed_at, comment)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
        """,
        (
            task_id,
            status,
            user_id,
            comment,
        ),
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
    user = require_any(request, ["service"])

    with get_db() as conn:
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
    user = require_any(request, ["admin", "director"])

    with get_db() as conn:
        task = get_task_for_workflow(conn, task_id)

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
    user = require_any(request, ["admin", "director"])

    with get_db() as conn:
        task = get_task_for_workflow(conn, task_id)

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
    user = require_any(request, ["admin", "director"])

    with get_db() as conn:
        task = get_task_for_workflow(conn, task_id)

        if task["workflow_status"] not in ("open", "rework"):
            raise HTTPException(
                status_code=400,
                detail="Это задание сейчас нельзя закрыть"
            )

        if (
            user["role"] == "director"
            and task["created_by_role"] != "director"
        ):
            raise HTTPException(
                status_code=403,
                detail="Директор может закрывать только свои задания"
            )

        set_task_workflow_status(
            conn,
            task,
            "closed",
            user["sub"],
            "Задание закрыто",
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
    require_any(request, ["admin"])
    with get_db() as conn:
        media = conn.execute(
            "SELECT filename FROM media WHERE task_id = ? AND report_id IS NULL",
            (task_id,),
        ).fetchall()
        for m in media:
            try:
                (UPLOADS_DIR / m["filename"]).unlink(missing_ok=True)
            except Exception:
                pass
        conn.execute("DELETE FROM media WHERE task_id = ? AND report_id IS NULL", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return {"ok": True}


# ---------- Экспорт в Excel ----------
@app.get("/api/export/excel")
def export_excel(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    require_any(request, ["admin", "director"])
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    with get_db() as conn:
        tasks = conn.execute(
            """
            SELECT t.id, t.description, t.status, t.created_at, t.closed_at,
                   a.city, a.address_name, a.manufacturer,
                   u.login AS author, cu.login AS closed_by
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            LEFT JOIN users cu ON cu.id = t.closed_by
            WHERE (:df IS NULL OR date(t.created_at) >= :df)
              AND (:dt IS NULL OR date(t.created_at) <= :dt)
            ORDER BY t.created_at DESC
            """,
            {"df": date_from, "dt": date_to},
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
            ORDER BY r.created_at DESC
            """,
            {"df": date_from, "dt": date_to},
        ).fetchall()

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1E40AF")

    ws1 = wb.active
    ws1.title = "Задания"
    headers1 = ["№", "Город", "Адрес", "Производитель", "Описание", "Статус",
                "Создано", "Автор", "Закрыто", "Исполнитель"]
    ws1.append(headers1)
    for cell in ws1[1]:
        cell.font = header_font
        cell.fill = header_fill
    for t in tasks:
        ws1.append([
            t["id"], t["city"], t["address_name"], t["manufacturer"] or "",
            t["description"],
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

class PasswordIn(BaseModel):
    new_password: str

@app.get("/api/users")
def list_users_api(request: Request):
    require_any(request, ["admin"])
    with get_db() as conn:
        rows = conn.execute("SELECT id, login, role FROM users ORDER BY login").fetchall()
    return [{"id": r["id"], "login": r["login"], "role": r["role"]} for r in rows]

@app.post("/api/users")
def create_user_api(data: UserIn, request: Request):
    require_any(request, ["admin"])
    if data.role not in ("admin", "service", "director", "crew", "head"):
        raise HTTPException(status_code=400, detail="Неверная роль")
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE login = ?", (data.login,)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="Логин уже занят")
        conn.execute(
            "INSERT INTO users (login, password_hash, role) VALUES (?, ?, ?)",
            (data.login, hash_password(data.password), data.role)
        )
    return {"ok": True}

@app.put("/api/users/{user_id}/password")
def change_password_api(user_id: int, data: PasswordIn, request: Request):
    require_any(request, ["admin"])
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(data.new_password), user_id)
        )
    return {"ok": True}
# =========================================================================


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
    user = get_current_user(request)
    import json
    if user["role"] == "director":
        raise HTTPException(
            status_code=403,
            detail="Директор пишет только отдельные комментарии"
        )

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
            user["role"] == "service"
            and task["workflow_status"] not in ("open", "rework")
        ):
            raise HTTPException(
                status_code=403,
                detail="К этому заданию сейчас нельзя добавлять сообщения"
            )

        conn.execute("""
            INSERT INTO comments (task_id, user_id, text, media_json)
            VALUES (?, ?, ?, ?)
        """, (
            data.task_id,
            user["sub"],
            data.text.strip(),
            json.dumps(data.media)
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
    require_any(request, ["admin"])
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
            WHERE r.address_id = ? AND r.task_id IS NULL
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
                "media": [{"url": f"/uploads/{m['filename']}", "type": m["media_type"]} for m in media],
                "is_director_comment": False
            })
        
        # Добавляем комментарии директора (видны всем)
        dc_rows = conn.execute("""
            SELECT dc.id, dc.text, dc.created_at, u.login
            FROM director_comments dc
            JOIN users u ON u.id = dc.user_id
            WHERE dc.item_type = 'monitoring' AND dc.item_id = ?
            ORDER BY dc.created_at ASC
        """, (address_id,)).fetchall()
        
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

        # Комментарий директора хранится отдельно
        if user["role"] == "director":
            cursor = conn.execute(
                """
                INSERT INTO director_comments
                    (item_type, item_id, user_id, text)
                VALUES
                    ('monitoring', ?, ?, ?)
                """,
                (
                    data.address_id,
                    user["sub"],
                    data.text.strip()
                )
            )

            comment_id = cursor.lastrowid

            return {
                "ok": True,
                "comment_id": comment_id,
                "type": "director_comment"
            }

        # Обычное сообщение мониторинга
        cursor = conn.execute(
            """
            INSERT INTO reports
                (address_id, user_id, task_id, text)
            VALUES
                (?, ?, NULL, ?)
            """,
            (
                data.address_id,
                user["sub"],
                data.text.strip()
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

                old_path = UPLOADS_DIR / temp_name

                if not old_path.exists():
                    continue

                ext = old_path.suffix.lower()

                media_type = (
                    "video"
                    if ext in VIDEO_EXTENSIONS
                    else "photo"
                )

                new_name = (
                    f"m{report_id}_"
                    f"{uuid.uuid4().hex[:8]}"
                    f"{ext}"
                )

                new_path = UPLOADS_DIR / new_name

                old_path.rename(new_path)

                conn.execute(
                    """
                    INSERT INTO media
                        (report_id, filename, media_type)
                    VALUES
                        (?, ?, ?)
                    """,
                    (
                        report_id,
                        new_name,
                        media_type
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
    get_current_user(request)
    with get_db() as conn:
        # Задания
        tasks = conn.execute("""
            SELECT 'task' as type, t.id, t.description as text, t.created_at,
                   a.city, a.address_name, a.manufacturer,
                   u.login as author, t.status, t.is_important, t.close_reason
            FROM tasks t
            JOIN addresses a ON a.id = t.address_id
            JOIN users u ON u.id = t.created_by
            ORDER BY t.created_at DESC LIMIT 200
        """).fetchall()
        
        # Мониторинг (последние записи)
        monitoring = conn.execute("""
            SELECT 'monitoring' as type, r.id, r.text, r.created_at,
                   a.city, a.address_name, a.manufacturer,
                   u.login as author, NULL as status, 0 as is_important, NULL as close_reason
            FROM reports r
            JOIN addresses a ON a.id = r.address_id
            JOIN users u ON u.id = r.user_id
            WHERE r.task_id IS NULL
            ORDER BY r.created_at DESC LIMIT 200
        """).fetchall()
        
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
    user = get_current_user(request)
    if user["role"] not in ("admin", "director"):
        raise HTTPException(status_code=403, detail="Только админ или директор")
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
                    (item_type, item_id, user_id, text, media_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    data.item_type,
                    data.item_id,
                    user["sub"],
                    data.text.strip(),
                    json.dumps(data.media, ensure_ascii=False)
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
                    (item_type, item_id, report_id, user_id, text, media_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    data.item_type,
                    data.item_id,
                    data.item_id,
                    user["sub"],
                    data.text.strip(),
                    json.dumps(data.media, ensure_ascii=False)
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


# ========== ЕДИНАЯ ЛЕНТА (МОНИТОРИНГ + ЗАДАНИЯ) ==========
@app.get("/api/feed")
def get_unified_feed(request: Request):
    """Единая лента заданий и мониторингов.

    Задание = отдельная карточка по task.id.
    Мониторинг = отдельная карточка по address_id.

    ВАЖНО:
    - messages содержит только обычные сообщения;
    - director_comments хранится отдельно;
    - комментарии директора не влияют на last_message_*;
    - комментарии директора не влияют на сортировку карточек.
    """
    get_current_user(request)

    from datetime import datetime
    import json

    with get_db() as conn:
        result = []

        # =========================================================
        # 1. ЗАДАНИЯ
        # =========================================================
        tasks = conn.execute("""
            SELECT
                t.id,
                t.address_id,
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
            ORDER BY t.created_at DESC
            LIMIT 300
        """).fetchall()

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
                "author_role": task["author_role"],
                "status": task["status"],
                "workflow_status": task["workflow_status"],
                "workflow_status_label": TASK_WORKFLOW_STATUSES.get(
                    task["workflow_status"],
                    task["workflow_status"],
                ),
                "created_by_role": task["created_by_role"],
                "is_important": bool(task["is_important"]),
                "close_reason": task["close_reason"],
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
                    "url": f"/uploads/{m['filename']}",
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
        monitoring_reports = conn.execute("""
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
              AND a.active = 1
            ORDER BY r.created_at DESC
            LIMIT 300
        """).fetchall()

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
                        "url": f"/uploads/{m['filename']}",
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

                "status": "open",
                "is_important": False,
                "close_reason": None,

                # Одна карточка = одна запись мониторинга.
                "messages": [message],

                "director_comments": director_comments,
                "has_director_comment": bool(director_comments),

                "last_message_at": r["created_at"],
                "last_message_author": r["login"]
            }

            result.append(item)

        # =========================================================
        # 3. ПРОСРОЧЕННЫЕ ЗАДАНИЯ
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

        return result

# ==========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
