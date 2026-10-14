"""Store consented Google Forms responses for the staff consultation inbox."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.types import Uuid


revision = "0006_google_forms_bridge"
down_revision = "0005_consultation_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_form_submissions",
        sa.Column("id", Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("shop_id", Uuid(as_uuid=True), nullable=False),
        sa.Column("response_id", sa.String(256), nullable=False),
        sa.Column("name_ciphertext", sa.Text(), nullable=False),
        sa.Column("phone_ciphertext", sa.Text(), nullable=False),
        sa.Column("desired_time_ciphertext", sa.Text(), nullable=False),
        sa.Column("note_ciphertext", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id"], ["shops.tenant_id", "shops.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("tenant_id", "response_id", name="uq_google_form_tenant_response"),
    )
    op.create_index("ix_google_form_submissions_tenant_id", "google_form_submissions", ["tenant_id"])
    op.create_index(
        "ix_google_form_submissions_tenant_shop_submitted",
        "google_form_submissions",
        ["tenant_id", "shop_id", "submitted_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('ALTER TABLE "google_form_submissions" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "google_form_submissions" FORCE ROW LEVEL SECURITY'))
        rule = (
            "(tenant_id::text = current_setting('app.tenant_id', true) "
            "OR current_setting('app.is_platform_admin', true) = 'true')"
        )
        op.execute(
            sa.text(
                f'CREATE POLICY tenant_isolation ON "google_form_submissions" USING {rule} WITH CHECK {rule}'
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text('DROP POLICY IF EXISTS tenant_isolation ON "google_form_submissions"'))
    op.drop_index("ix_google_form_submissions_tenant_shop_submitted", table_name="google_form_submissions")
    op.drop_index("ix_google_form_submissions_tenant_id", table_name="google_form_submissions")
    op.drop_table("google_form_submissions")
