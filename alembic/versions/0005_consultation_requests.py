"""Store customer consultation form submissions separately from chat."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0005_consultation_requests"
down_revision = "0004_staff_message_author"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultation_requests",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("shop_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("name_ciphertext", sa.Text(), nullable=False),
        sa.Column("phone_ciphertext", sa.Text(), nullable=False),
        sa.Column("desired_time_ciphertext", sa.Text(), nullable=False),
        sa.Column("note_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["public_chat_sessions.tenant_id", "public_chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_consultation_tenant_session"),
    )
    op.create_index(
        "ix_consultation_requests_tenant_shop_created",
        "consultation_requests",
        ["tenant_id", "shop_id", "created_at"],
    )
    op.create_index("ix_consultation_requests_tenant_id", "consultation_requests", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('ALTER TABLE "consultation_requests" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "consultation_requests" FORCE ROW LEVEL SECURITY'))
        rule = (
            "(tenant_id::text = current_setting('app.tenant_id', true) "
            "OR current_setting('app.is_platform_admin', true) = 'true')"
        )
        op.execute(sa.text(f'CREATE POLICY tenant_isolation ON "consultation_requests" USING {rule} WITH CHECK {rule}'))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('DROP POLICY IF EXISTS tenant_isolation ON "consultation_requests"'))
    op.drop_index("ix_consultation_requests_tenant_id", table_name="consultation_requests")
    op.drop_index("ix_consultation_requests_tenant_shop_created", table_name="consultation_requests")
    op.drop_table("consultation_requests")
