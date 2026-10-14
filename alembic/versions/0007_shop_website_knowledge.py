"""Store public website knowledge per tenant shop."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0007_shop_website_knowledge"
down_revision = "0006_google_forms_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_knowledge",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("shop_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("api_fingerprint", sa.String(64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "shop_id", name="uq_shop_knowledge_tenant_shop"),
    )
    op.create_index("ix_shop_knowledge_tenant_id", "shop_knowledge", ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('ALTER TABLE "shop_knowledge" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "shop_knowledge" FORCE ROW LEVEL SECURITY'))
        rule = (
            "(tenant_id::text = current_setting('app.tenant_id', true) "
            "OR current_setting('app.is_platform_admin', true) = 'true')"
        )
        op.execute(sa.text(f'CREATE POLICY tenant_isolation ON "shop_knowledge" USING {rule} WITH CHECK {rule}'))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('DROP POLICY IF EXISTS tenant_isolation ON "shop_knowledge"'))
    op.drop_index("ix_shop_knowledge_tenant_id", table_name="shop_knowledge")
    op.drop_table("shop_knowledge")
