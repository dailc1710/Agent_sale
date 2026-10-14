"""Add public shops, isolated anonymous sessions, and admin-scoped RLS."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid

revision = "0002_public_shop_portal"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "tenants",
    "products",
    "customers",
    "conversations",
    "messages",
    "reply_drafts",
    "webhook_events",
)


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=False),
        sa.Column("theme_color", sa.String(7), nullable=False),
        sa.Column("allowed_origins", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_shops_tenant_id_unique"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_shops_tenant_id"),
        sa.UniqueConstraint("slug", name="uq_shops_slug"),
    )
    op.create_index("ix_shops_tenant_id", "shops", ["tenant_id"])
    op.create_index("ix_shops_slug", "shops", ["slug"])
    op.create_table(
        "public_chat_sessions",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("shop_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("embed_origin", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_public_chat_sessions_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_public_chat_sessions_token_hash"),
    )
    op.create_index("ix_public_chat_sessions_tenant_id", "public_chat_sessions", ["tenant_id"])
    op.create_index("ix_public_chat_sessions_token_hash", "public_chat_sessions", ["token_hash"])
    op.create_index("ix_public_chat_sessions_expires_at", "public_chat_sessions", ["expires_at"])
    op.create_table(
        "public_chat_messages",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("session_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("request_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("direction IN ('user', 'assistant')", name="ck_public_message_direction"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["public_chat_sessions.tenant_id", "public_chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "session_id", "request_id", "direction",
            name="uq_public_message_request_direction",
        ),
    )
    op.create_index("ix_public_chat_messages_tenant_id", "public_chat_messages", ["tenant_id"])

    # Admin access is enabled only by a server-side session flag after credential validation.
    for table in (*TENANT_TABLES, "shops", "public_chat_sessions", "public_chat_messages"):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"'))
        tenant_expression = "id" if table == "tenants" else "tenant_id"
        condition = (
            f"({tenant_expression}::text = current_setting('app.tenant_id', true) "
            "OR current_setting('app.is_platform_admin', true) = 'true')"
        )
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING {condition} WITH CHECK {condition}"
            )
        )
    op.execute(
        sa.text(
            "CREATE POLICY public_shop_lookup ON shops FOR SELECT "
            "USING (slug = current_setting('app.public_shop_slug', true) AND enabled IS TRUE)"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY public_session_lookup ON public_chat_sessions FOR SELECT "
            "USING (token_hash = current_setting('app.public_session_token_hash', true))"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS public_session_lookup ON public_chat_sessions"))
    op.execute(sa.text("DROP POLICY IF EXISTS public_shop_lookup ON shops"))
    for table in ("public_chat_messages", "public_chat_sessions", "shops"):
        op.drop_table(table)
    for table in TENANT_TABLES:
        op.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"'))
        tenant_expression = "id" if table == "tenants" else "tenant_id"
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "{table}" '
                f"USING ({tenant_expression}::text = current_setting('app.tenant_id', true)) "
                f"WITH CHECK ({tenant_expression}::text = current_setting('app.tenant_id', true))"
            )
        )
