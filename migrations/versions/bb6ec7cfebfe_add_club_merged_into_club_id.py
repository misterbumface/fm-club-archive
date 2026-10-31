"""add club merged_into_club_id

Revision ID: bb6ec7cfebfe
Revises: a7d3f8b1c942
Create Date: 2026-07-21 23:59:42.631895

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bb6ec7cfebfe'
down_revision = 'a7d3f8b1c942'
branch_labels = None
depends_on = None


def upgrade():
    # Unrelated pre-existing drift on national_teams' unique index was
    # excluded from this migration -- not part of this change.
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('merged_into_club_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_clubs_merged_into_club_id', 'clubs', ['merged_into_club_id'], ['id']
        )


def downgrade():
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_clubs_merged_into_club_id', type_='foreignkey')
        batch_op.drop_column('merged_into_club_id')
