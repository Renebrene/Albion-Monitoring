"""
Инициализация БД:
- создаёт таблицы
- добавляет пользователей по умолчанию (кириллица)
- импортирует адреса из addresses.csv
"""
import csv
from pathlib import Path

from database import get_db, init_schema, DB_PATH
from auth import hash_password

# Пользователи по умолчанию — кириллические логины, пароль "альбион"
DEFAULT_USERS = [
    ("админ",    "альбион",  "admin"),
    ("сервис1",  "альбион",  "service"),
    ("сервис2",  "альбион",  "service"),
    ("директор", "альбион",  "director"),
]

CSV_PATH = Path(__file__).parent / "addresses.csv"


def seed_users():
    with get_db() as conn:
        for login, pwd, role in DEFAULT_USERS:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE login = ?", (login,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO users (login, password_hash, role) VALUES (?, ?, ?)",
                    (login, hash_password(pwd), role),
                )
                print(f"  + создан: {login} ({role})")
            else:
                print(f"  = уже есть: {login}")


def import_addresses():
    if not CSV_PATH.exists():
        print(f"  ! файл {CSV_PATH} не найден — пропуск")
        return

    with get_db() as conn:
        added = updated = 0
        with CSV_PATH.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr_id = int(row["id"])
                city = row["city"].strip()
                name = row["address"].strip()
                manufacturer = row.get("manufacturer", "").strip()
                filter1 = row.get("filter1", "").strip()
                filter2 = row.get("filter2", "").strip()
                filter3 = row.get("filter3", "").strip()

                existing = conn.execute(
                    "SELECT id FROM addresses WHERE id = ?", (addr_id,)
                ).fetchone()

                if not existing:
                    conn.execute(
                        """INSERT INTO addresses 
                           (id, city, address_name, manufacturer, filter1, filter2, filter3, active) 
                           VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                        (addr_id, city, name, manufacturer, filter1, filter2, filter3),
                    )
                    added += 1
                else:
                    conn.execute(
                        """UPDATE addresses 
                           SET city=?, address_name=?, manufacturer=?, filter1=?, filter2=?, filter3=?,
                               updated_at=CURRENT_TIMESTAMP 
                           WHERE id=?""",
                        (city, name, manufacturer, filter1, filter2, filter3, addr_id),
                    )
                    updated += 1

    print(f"  адреса: +{added} новых, ~{updated} обновлено")


if __name__ == "__main__":
    print(f"Инициализация БД: {DB_PATH}")
    init_schema()
    print("Таблицы созданы.")
    print("Добавляю пользователей:")
    seed_users()
    print("Импортирую адреса:")
    import_addresses()
    print()
    print("✓ Готово.")
    print()
    print("Логины для входа:")
    print("  админ    / альбион   — Администратор")
    print("  сервис1  / альбион   — Сервис")
    print("  сервис2  / альбион   — Сервис")
    print("  директор / альбион   — Руководитель")
