import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url


def _current_revisions(database_url: str) -> list[str]:
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn) as conn:
        if conn.execute("SELECT to_regclass('public.alembic_version')").fetchone() == (None,):
            return []
        return [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]


def test_migration_history_is_linear(alembic_config: Config) -> None:
    script = ScriptDirectory.from_config(alembic_config)

    assert len(script.get_heads()) == 1, "multiple Alembic heads: merge or rebase migrations"


def test_upgrade_to_head_and_downgrade_to_base_round_trip(alembic_config: Config, database_url: str) -> None:
    head = ScriptDirectory.from_config(alembic_config).get_current_head()

    command.upgrade(alembic_config, "head")
    assert _current_revisions(database_url) == [head]

    command.downgrade(alembic_config, "base")
    assert _current_revisions(database_url) == []

    # Re-applying after a full downgrade must work (downgrades leave no residue).
    command.upgrade(alembic_config, "head")
    assert _current_revisions(database_url) == [head]
    command.downgrade(alembic_config, "base")
