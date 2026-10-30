"""measurement source

Revision ID: 3f1a9b2c7d40
Revises: eecaed76eebb
Create Date: 2026-10-30 06:40:00
"""

from alembic import op
import sqlalchemy as sa

revision = '3f1a9b2c7d40'
down_revision = 'eecaed76eebb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('measurements', sa.Column('recorded_by', sa.String(length=16), nullable=True))
    op.add_column('measurements', sa.Column('source_message_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('measurements', 'source_message_ids')
    op.drop_column('measurements', 'recorded_by')
