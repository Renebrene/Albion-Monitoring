"""access stage: user token version, session view, audit log isolation

Revision ID: 0002_access_stage
Revises: 0001_core_and_monitoring
Create Date: 2026-10-22
"""

from alembic import op

revision = "0002_access_stage"
down_revision = "0001_core_and_monitoring"
branch_labels = None
depends_on = None


# Приложение работает под ролью albion_app и обращается к public.auth_sessions.
# Вьюха обязана отдавать поля отзыва/срока/отпечатка, иначе проверка сессии
# на PostgreSQL падает с "column does not exist".
UPGRADE_STATEMENTS = [
    "ALTER TABLE core.users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 1",
    "DROP VIEW IF EXISTS public.auth_sessions",
    """
    CREATE VIEW public.auth_sessions AS
        SELECT id, user_id, login, role, legal_entity_id, token_hash,
               login_at, logout_at, revoked_at, expires_at
        FROM core.sessions
    """,
    "GRANT SELECT, INSERT, UPDATE, DELETE ON public.auth_sessions TO albion_app",
    "ALTER TABLE core.audit_log ENABLE ROW LEVEL SECURITY",
    """
    CREATE POLICY tenant_isolation ON core.audit_log
    USING (
        core.is_group_admin()
        OR legal_entity_id IS NULL
        OR legal_entity_id = ANY (core.current_legal_entity_ids())
    )
    """,
    """
    CREATE POLICY tenant_isolation_write ON core.audit_log
    FOR INSERT
    WITH CHECK (
        core.is_group_admin()
        OR legal_entity_id IS NULL
        OR legal_entity_id = ANY (core.current_legal_entity_ids())
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON core.audit_log(action, occurred_at DESC)",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_write ON core.audit_log")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON core.audit_log")
    op.execute("ALTER TABLE core.audit_log DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS core.idx_audit_action")
    op.execute("ALTER TABLE core.users DROP COLUMN IF EXISTS token_version")
    op.execute("DROP VIEW IF EXISTS public.auth_sessions")
    op.execute("""
        CREATE VIEW public.auth_sessions AS
            SELECT id, user_id, login, role, login_at, logout_at
            FROM core.sessions
    """)
