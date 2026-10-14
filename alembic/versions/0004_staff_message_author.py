"""Record which staff member authored CRM messages."""

from alembic import op
import sqlalchemy as sa


revision = "0004_staff_message_author"
down_revision = "0003_public_chat_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("author", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("author")

