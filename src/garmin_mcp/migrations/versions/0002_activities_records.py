"""Activity summaries keyed by Garmin activity ID, without GPS or raw payloads."""
from alembic import op
import sqlalchemy as sa
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('activities_records',
        sa.Column('profile_id', sa.String(80), sa.ForeignKey('profiles.id'), primary_key=True),
        sa.Column('activity_id', sa.String(32), primary_key=True),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('activities_records')
