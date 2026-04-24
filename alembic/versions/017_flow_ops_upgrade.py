"""Flow ops upgrade: notification logs, bank name, and delivery progress card fields.

Revision ID: flow_ops_upgrade
Revises: add_other_gadgets_category
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "flow_ops_upgrade"
down_revision = "add_other_gadgets_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("seller_profiles", sa.Column("bank_name", sa.String(length=255), nullable=True))

    op.add_column("deliveries", sa.Column("agent_progress_chat_id", sa.String(length=255), nullable=True))
    op.add_column("deliveries", sa.Column("agent_progress_message_id", sa.Integer(), nullable=True))

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("context_ref", sa.String(length=255), nullable=True),
        sa.Column("message", sa.String(length=2000), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_notification_logs_user_id", "notification_logs", ["user_id"], unique=False)
    op.create_index("ix_notification_logs_event_type", "notification_logs", ["event_type"], unique=False)
    op.create_index("ix_notification_logs_status", "notification_logs", ["status"], unique=False)
    op.create_index("ix_notification_logs_created_at", "notification_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_notification_logs_event_status",
        "notification_logs",
        ["event_type", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notification_logs_event_status", table_name="notification_logs")
    op.drop_index("ix_notification_logs_created_at", table_name="notification_logs")
    op.drop_index("ix_notification_logs_status", table_name="notification_logs")
    op.drop_index("ix_notification_logs_event_type", table_name="notification_logs")
    op.drop_index("ix_notification_logs_user_id", table_name="notification_logs")
    op.drop_table("notification_logs")

    op.drop_column("deliveries", "agent_progress_message_id")
    op.drop_column("deliveries", "agent_progress_chat_id")

    op.drop_column("seller_profiles", "bank_name")
