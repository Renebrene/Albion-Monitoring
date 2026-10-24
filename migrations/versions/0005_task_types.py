"""task types: configurable fields and task type reference

Revision ID: 0005_task_types
Revises: 0004_roles_and_capabilities
Create Date: 2026-10-23
"""

from alembic import op

revision = "0005_task_types"
down_revision = "0004_roles_and_capabilities"
branch_labels = None
depends_on = None


UPGRADE_STATEMENTS = [
    # Флаг active уже smallint (см. 0004); здесь — признак системного типа.
    "ALTER TABLE core.task_types ADD COLUMN IF NOT EXISTS is_system smallint NOT NULL DEFAULT 0",
    """
    ALTER TABLE monitoring.tasks
        ADD COLUMN IF NOT EXISTS task_type_id bigint REFERENCES core.task_types(id),
        ADD COLUMN IF NOT EXISTS extra_fields jsonb
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_task_type ON monitoring.tasks(task_type_id)",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS monitoring.idx_tasks_task_type")
    op.execute(
        "ALTER TABLE monitoring.tasks "
        "DROP COLUMN IF EXISTS task_type_id, DROP COLUMN IF EXISTS extra_fields"
    )
    op.execute("ALTER TABLE core.task_types DROP COLUMN IF EXISTS is_system")
