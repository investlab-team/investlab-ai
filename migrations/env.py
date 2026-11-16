from alembic import context
from sqlalchemy import create_engine

from investlab_ai import sec_storage  # noqa: F401 -- register SEC tables for autogeneration
from investlab_ai.config import Settings
from investlab_ai.storage import Base, database_url

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # The research database may contain other AI domains. Never autogenerate their removal.
    return type_ != "table" or name.startswith("rag_")


if context.is_offline_mode():
    context.configure(
        url=database_url(Settings(), direct=True), target_metadata=target_metadata,
        literal_binds=True, include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    context.configure(connection=context.config.attributes["connection"],
                      target_metadata=target_metadata, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url(Settings(), direct=True))
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
