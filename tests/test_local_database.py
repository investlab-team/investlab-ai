from dotenv import dotenv_values

from investlab_ai.local_database import LocalDatabaseError, prepare_environment


def test_preparation_keeps_api_keys_and_sqlite_active_until_successful_transfer(tmp_path):
    env = tmp_path / ".env"
    original = ("INVESTLAB_AI_OPENAI_API_KEY=test-only-key\n"
                "INVESTLAB_AI_LOCAL_SQLITE_PATH=.local/research.db\nRESEARCH_DB_HOST=localhost\n")
    env.write_text(original, encoding="utf-8")
    prepare_environment(tmp_path)
    first = dotenv_values(env)
    assert first["INVESTLAB_AI_OPENAI_API_KEY"] == "test-only-key"
    assert first["INVESTLAB_AI_LOCAL_SQLITE_PATH"] == ".local/research.db"
    assert len(first["RESEARCH_DB_PASSWORD"]) >= 32
    assert first["RESEARCH_DB_PASSWORD"] != first["RESEARCH_DB_ADMIN_PASSWORD"]
    assert (tmp_path / ".local/backups/env-before-postgres.env").read_text() == original
    prepare_environment(tmp_path)
    assert dotenv_values(env)["RESEARCH_DB_PASSWORD"] == first["RESEARCH_DB_PASSWORD"]


def test_local_docker_setup_never_replaces_a_team_server_configuration(tmp_path):
    env = tmp_path / ".env"
    original = "RESEARCH_DB_HOST=team-db.example\nRESEARCH_DB_PASSWORD=test-only-password\n"
    env.write_text(original, encoding="utf-8")
    try:
        prepare_environment(tmp_path)
    except LocalDatabaseError:
        pass
    else:
        raise AssertionError("Team database settings must be preserved")
    assert env.read_text() == original
