"""workflow transitions: configurable capability lists

Revision ID: 0006_workflow_transitions
Revises: 0005_task_types
Create Date: 2026-10-24
"""

from alembic import op

revision = "0006_workflow_transitions"
down_revision = "0005_task_types"
branch_labels = None
depends_on = None


UPGRADE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS core.workflow_transitions (
        code text PRIMARY KEY,
        from_status text NOT NULL,
        to_status text NOT NULL,
        label text NOT NULL,
        capabilities text NOT NULL DEFAULT '[]',
        is_system smallint NOT NULL DEFAULT 1
    )
    """,
    # Строки переходов досеивает приложение при старте (sync_workflow_transitions).
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS core.workflow_transitions")
