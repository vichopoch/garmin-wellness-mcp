"""Minimal normalized wellness tables; no raw responses or credentials."""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('profiles', sa.Column('id',sa.String(80),primary_key=True),sa.Column('timezone',sa.String(80),nullable=False))
    for name in ('daily_health','sleep','hrv','body_battery','training','activities','sync_state'):
        op.create_table(name,sa.Column('profile_id',sa.String(80),sa.ForeignKey('profiles.id'),primary_key=True),sa.Column('date',sa.Date(),primary_key=True),sa.Column('data',sa.JSON(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for name in ('sync_state','activities','training','body_battery','hrv','sleep','daily_health','profiles'):
        op.drop_table(name)
