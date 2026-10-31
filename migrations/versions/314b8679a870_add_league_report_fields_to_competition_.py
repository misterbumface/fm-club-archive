"""add league report fields to competition editions

Revision ID: 314b8679a870
Revises: 7b9c2d1e4f60
Create Date: 2026-07-24 00:29:50.197292

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '314b8679a870'
down_revision = '7b9c2d1e4f60'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('competition_editions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('league_report_narrative', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('league_report_generated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('league_report_model', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('league_report_as_of_date', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('competition_editions', schema=None) as batch_op:
        batch_op.drop_column('league_report_as_of_date')
        batch_op.drop_column('league_report_model')
        batch_op.drop_column('league_report_generated_at')
        batch_op.drop_column('league_report_narrative')
