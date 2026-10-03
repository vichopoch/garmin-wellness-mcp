from alembic import context
from sqlalchemy import create_engine
from garmin_mcp.storage import metadata
engine = create_engine(context.config.attributes['connection_url'], hide_parameters=True)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
