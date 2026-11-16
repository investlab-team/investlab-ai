from investlab_ai.neon_environment import load_neon_environment


def test_restricted_profile_does_not_inject_project_owner_credentials(tmp_path, monkeypatch):
    keys = ("DATABASE_URL", "DATABASE_URL_UNPOOLED", "RESEARCH_DB_URL", "RESEARCH_DB_DIRECT_URL",
            "AWS_ACCESS_KEY_ID", "INVESTLAB_AI_OBJECT_BUCKET", "INVESTLAB_AI_USE_DOCKER_DB",
            "INVESTLAB_AI_ENABLE_LOCAL_CHAT")
    for key in keys:
        monkeypatch.setenv(key, "")
    (tmp_path / ".env").write_text("")
    (tmp_path / ".env.local").write_text(
        "DATABASE_URL=postgresql://owner:admin-secret@example/neondb\n"
        "DATABASE_URL_UNPOOLED=postgresql://owner:admin-secret@example/neondb\n"
        "AWS_ACCESS_KEY_ID=fake-test-only-key\n")
    (tmp_path / ".env.neon").write_text(
        "RESEARCH_DB_URL=postgresql://research:app-secret@example/investlab_research\n"
        "RESEARCH_DB_DIRECT_URL=postgresql://research:app-secret@example/investlab_research\n"
        "INVESTLAB_AI_OBJECT_BUCKET=disclosures\n")
    load_neon_environment(tmp_path)
    import os
    assert not os.environ["DATABASE_URL"] and not os.environ["DATABASE_URL_UNPOOLED"]
    assert os.environ["AWS_ACCESS_KEY_ID"] == "fake-test-only-key"
    assert "research:" in os.environ["RESEARCH_DB_URL"]
