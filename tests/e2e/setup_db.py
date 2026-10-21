import os
import sys
import tempfile
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2]
root = Path(os.environ.get("E2E_ROOT") or tempfile.mkdtemp(prefix="albion-e2e-"))
root.mkdir(parents=True, exist_ok=True)
os.environ["DB_PATH"] = str(root / "e2e.db")
os.environ["UPLOADS_DIR"] = str(root / "uploads")
os.environ.setdefault("JWT_SECRET", "e2e-only-secret-0123456789abcdefghij")
sys.path.insert(0, str(app_dir))

from database import get_db, init_schema  # noqa: E402
from auth import hash_password  # noqa: E402

PASSWORD = "TestPass1!"

USERS = [
    ("admin", "admin"),
    ("service", "service"),
    ("director", "director"),
    ("crew", "crew"),
    ("head", "head"),
    ("staff", "staff"),
    ("administrator", "administrator"),
    ("curator", "curator"),
]

ADDRESSES = [
    (1, "Ростов-на-Дону", "Вавилова 59/101", "Вижн Лед"),
    (2, "Ростов-на-Дону", "1-й Конной Армии 32", "Лед Гарант"),
    (3, "Ростов-на-Дону", "40 Лет Победы 154", "Вижн Лед"),
    (4, "Батайск", "Куйбышева 116", "Лед Гарант"),
]

init_schema()
with get_db() as conn:
    for login, role in USERS:
        conn.execute("DELETE FROM users WHERE login = ?", (login,))
        conn.execute(
            "INSERT INTO users (login, password_hash, role) VALUES (?, ?, ?)",
            (login, hash_password(PASSWORD), role),
        )

    for address_id, city, name, manufacturer in ADDRESSES:
        conn.execute("DELETE FROM addresses WHERE id = ?", (address_id,))
        conn.execute(
            """INSERT INTO addresses
               (id, city, address_name, manufacturer, active)
               VALUES (?, ?, ?, ?, 1)""",
            (address_id, city, name, manufacturer),
        )

print("E2E DB ready:", root / "e2e.db")
print("Users:", ", ".join(f"{login}:{role}" for login, role in USERS))
print("Password:", PASSWORD)
