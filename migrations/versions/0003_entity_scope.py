"""entity scope: session entity mode and user membership

Revision ID: 0003_entity_scope
Revises: 0002_access_stage
Create Date: 2026-10-23
"""

from alembic import op

revision = "0003_entity_scope"
down_revision = "0002_access_stage"
branch_labels = None
depends_on = None


UPGRADE_STATEMENTS = [
    # Активный режим сессии: конкретное юрлицо или сводный просмотр группы.
    # Тип smallint — как у остальных флагов приложения (active/shared/hidden).
    "ALTER TABLE core.sessions ADD COLUMN IF NOT EXISTS all_entities smallint NOT NULL DEFAULT 0",
    # Приложение работает через вьюху, ей тоже нужен новый столбец.
    "DROP VIEW IF EXISTS public.auth_sessions",
    """
    CREATE VIEW public.auth_sessions AS
        SELECT id, user_id, login, role, legal_entity_id, all_entities, token_hash,
               login_at, logout_at, revoked_at, expires_at
        FROM core.sessions
    """,
    "GRANT SELECT, INSERT, UPDATE, DELETE ON public.auth_sessions TO albion_app",
    "CREATE INDEX IF NOT EXISTS idx_user_entities_user ON core.user_entities(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_user_entities_entity ON core.user_entities(legal_entity_id)",
    # Членство для уже существующих пользователей: их текущее юрлицо.
    """
    INSERT INTO core.user_entities (user_id, legal_entity_id, role, is_default)
    SELECT users.id, users.legal_entity_id, users.role, true
    FROM core.users
    WHERE users.legal_entity_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM core.user_entities
          WHERE core.user_entities.user_id = users.id
            AND core.user_entities.legal_entity_id = users.legal_entity_id
      )
    """,
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS core.idx_user_entities_entity")
    op.execute("DROP INDEX IF EXISTS core.idx_user_entities_user")
    # Вьюха зависит от столбца: сначала пересоздаём её, потом удаляем столбец.
    op.execute("DROP VIEW IF EXISTS public.auth_sessions")
    op.execute("ALTER TABLE core.sessions DROP COLUMN IF EXISTS all_entities")
    op.execute("""
        CREATE VIEW public.auth_sessions AS
            SELECT id, user_id, login, role, legal_entity_id, token_hash,
                   login_at, logout_at, revoked_at, expires_at
            FROM core.sessions
    """)
