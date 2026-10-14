"""Create tenant-scoped CRM tables and PostgreSQL row policies."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_products_tenant_id"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_table(
        "customers",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id"),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_table(
        "conversations",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("customer_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_table(
        "messages",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_messages_tenant_id"),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_table(
        "reply_drafts",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("message_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(120), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_drafts_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["messages.tenant_id", "messages.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "message_id", name="uq_drafts_tenant_message"),
    )
    op.create_index("ix_reply_drafts_tenant_id", "reply_drafts", ["tenant_id"])
    op.create_table(
        "webhook_events",
        sa.Column("tenant_id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("external_event_id", sa.String(160), primary_key=True),
        sa.Column("message_id", Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    tables = ("tenants", "products", "customers", "conversations", "messages", "reply_drafts", "webhook_events")
    for table in tables:
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        tenant_expression = "id" if table == "tenants" else "tenant_id"
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation ON \"{table}\" "
                f"USING ({tenant_expression}::text = current_setting('app.tenant_id', true)) "
                f"WITH CHECK ({tenant_expression}::text = current_setting('app.tenant_id', true))"
            )
        )


def downgrade() -> None:
    for table in ("webhook_events", "reply_drafts", "messages", "conversations", "customers", "products", "tenants"):
        op.drop_table(table)
