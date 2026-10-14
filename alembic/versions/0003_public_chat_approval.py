"""Connect public chat turns to the CRM approval workflow."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0003_public_chat_approval"
down_revision = "0002_public_shop_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reply_drafts") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(20), nullable=False, server_default="webhook")
        )
        batch_op.create_index(
            "uq_reply_drafts_one_pending_per_conversation",
            ["tenant_id", "conversation_id"],
            unique=True,
            postgresql_where=sa.text("status = 'pending' AND source = 'chat'"),
            sqlite_where=sa.text("status = 'pending' AND source = 'chat'"),
        )

    with op.batch_alter_table("public_chat_sessions") as batch_op:
        batch_op.add_column(sa.Column("conversation_id", Uuid(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_public_sessions_tenant_conversation",
            "conversations",
            ["tenant_id", "conversation_id"],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_public_sessions_tenant_conversation", ["tenant_id", "conversation_id"]
        )

    with op.batch_alter_table("public_chat_messages") as batch_op:
        batch_op.add_column(sa.Column("crm_message_id", Uuid(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_public_messages_tenant_crm_message",
            "messages",
            ["tenant_id", "crm_message_id"],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("public_chat_messages") as batch_op:
        batch_op.drop_constraint("fk_public_messages_tenant_crm_message", type_="foreignkey")
        batch_op.drop_column("crm_message_id")

    with op.batch_alter_table("public_chat_sessions") as batch_op:
        batch_op.drop_constraint("uq_public_sessions_tenant_conversation", type_="unique")
        batch_op.drop_constraint("fk_public_sessions_tenant_conversation", type_="foreignkey")
        batch_op.drop_column("conversation_id")

    with op.batch_alter_table("reply_drafts") as batch_op:
        batch_op.drop_index("uq_reply_drafts_one_pending_per_conversation")
        batch_op.drop_column("source")
