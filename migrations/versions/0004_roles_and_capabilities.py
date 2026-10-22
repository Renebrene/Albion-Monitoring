"""roles and capabilities: workspace, scope and code-based capabilities

Revision ID: 0004_roles_and_capabilities
Revises: 0003_entity_scope
Create Date: 2026-10-23
"""

from alembic import op

revision = "0004_roles_and_capabilities"
down_revision = "0003_entity_scope"
branch_labels = None
depends_on = None


UPGRADE_STATEMENTS = [
    # Флаги приложения — smallint (0/1), а не boolean: так их пишет код,
    # работающий и на SQLite.
    """
    ALTER TABLE core.roles
        ALTER COLUMN is_system DROP DEFAULT,
        ALTER COLUMN is_system TYPE smallint USING (CASE WHEN is_system THEN 1 ELSE 0 END),
        ALTER COLUMN is_system SET DEFAULT 0
    """,
    """
    ALTER TABLE core.domains
        ALTER COLUMN active DROP DEFAULT,
        ALTER COLUMN active TYPE smallint USING (CASE WHEN active THEN 1 ELSE 0 END),
        ALTER COLUMN active SET DEFAULT 1
    """,
    """
    ALTER TABLE core.task_types
        ALTER COLUMN active DROP DEFAULT,
        ALTER COLUMN active TYPE smallint USING (CASE WHEN active THEN 1 ELSE 0 END),
        ALTER COLUMN active SET DEFAULT 1
    """,
    """
    ALTER TABLE core.workflow_definitions
        ALTER COLUMN is_active DROP DEFAULT,
        ALTER COLUMN is_active TYPE smallint USING (CASE WHEN is_active THEN 1 ELSE 0 END),
        ALTER COLUMN is_active SET DEFAULT 0
    """,
    # Рабочее место (какую страницу получает роль) и область данных роли.
    """
    ALTER TABLE core.roles
        ADD COLUMN IF NOT EXISTS workplace text NOT NULL DEFAULT 'service',
        ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'entity',
        ADD COLUMN IF NOT EXISTS active smallint NOT NULL DEFAULT 1
    """,
    # Возможности адресуются кодом из каталога в коде приложения.
    "ALTER TABLE core.capabilities ALTER COLUMN domain_code SET DEFAULT ''",
    "ALTER TABLE core.role_capabilities ADD COLUMN IF NOT EXISTS capability_code text",
    """
    UPDATE core.role_capabilities rc
    SET capability_code = c.code
    FROM core.capabilities c
    WHERE c.id = rc.capability_id AND rc.capability_code IS NULL
    """,
    "DELETE FROM core.role_capabilities WHERE capability_code IS NULL",
    "ALTER TABLE core.role_capabilities ALTER COLUMN capability_code SET NOT NULL",
    "ALTER TABLE core.role_capabilities DROP CONSTRAINT IF EXISTS role_capabilities_pkey",
    "ALTER TABLE core.role_capabilities DROP COLUMN IF EXISTS capability_id",
    "ALTER TABLE core.role_capabilities ADD PRIMARY KEY (role_id, capability_code)",
    """
    ALTER TABLE core.role_capabilities
    ADD CONSTRAINT role_capabilities_capability_code_fkey
    FOREIGN KEY (capability_code) REFERENCES core.capabilities(code) ON DELETE CASCADE
    """,
    "CREATE INDEX IF NOT EXISTS idx_role_capabilities_code "
    "ON core.role_capabilities(capability_code)",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS core.idx_role_capabilities_code")
    op.execute(
        "ALTER TABLE core.role_capabilities "
        "DROP CONSTRAINT IF EXISTS role_capabilities_capability_code_fkey"
    )
    op.execute("ALTER TABLE core.role_capabilities DROP CONSTRAINT IF EXISTS role_capabilities_pkey")
    op.execute("ALTER TABLE core.role_capabilities ADD COLUMN IF NOT EXISTS capability_id bigint")
    op.execute(
        """
        UPDATE core.role_capabilities rc
        SET capability_id = c.id
        FROM core.capabilities c
        WHERE c.code = rc.capability_code AND rc.capability_id IS NULL
        """
    )
    op.execute("DELETE FROM core.role_capabilities WHERE capability_id IS NULL")
    op.execute("ALTER TABLE core.role_capabilities DROP COLUMN IF EXISTS capability_code")
    op.execute("ALTER TABLE core.role_capabilities ADD PRIMARY KEY (role_id, capability_id)")
    op.execute(
        "ALTER TABLE core.roles "
        "DROP COLUMN IF EXISTS workplace, DROP COLUMN IF EXISTS scope, "
        "DROP COLUMN IF EXISTS active"
    )
    for table, column, default in (
        ("core.roles", "is_system", "false"),
        ("core.domains", "active", "true"),
        ("core.task_types", "active", "true"),
        ("core.workflow_definitions", "is_active", "false"),
    ):
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} DROP DEFAULT, "
            f"ALTER COLUMN {column} TYPE boolean USING ({column} <> 0), "
            f"ALTER COLUMN {column} SET DEFAULT {default}"
        )
