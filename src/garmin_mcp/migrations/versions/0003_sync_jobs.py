"""Durable historical day/page cursors, without raw upstream payloads."""
from alembic import op
import sqlalchemy as sa
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sync_jobs',
                    sa.Column('profile_id', sa.String(80), sa.ForeignKey('profiles.id'), primary_key=True),
                    sa.Column('job_key', sa.String(40), primary_key=True),
                    sa.Column('data', sa.JSON(), nullable=False),
                    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('sync_jobs')
