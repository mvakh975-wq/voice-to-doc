import pytest

from voice_to_doc.config import load_settings, save_api_key


def test_defaults_when_env_file_missing(tmp_path):
    s = load_settings(tmp_path / ".env")
    assert s.groq_api_key == ""
    assert s.llm_model == "openai/gpt-oss-120b"
    assert s.whisper_model == "whisper-large-v3"
    assert (s.chunk_seconds, s.block_tokens, s.llm_tpm) == (600, 2500, 8000)
    assert s.chars_per_token == 3.5  # measured 3.78 for Russian on gpt-oss; slightly conservative
    assert s.env_path == tmp_path / ".env"


def test_reads_values_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KEY=gsk_abc\nCHUNK_SECONDS=300\nVTD_FAKE_GROQ=1\nDATA_DIR=/tmp/x\n")
    s = load_settings(env)
    assert (s.groq_api_key, s.chunk_seconds, s.fake_groq) == ("gsk_abc", 300, True)
    assert str(s.data_dir) == "/tmp/x"


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=a\n")
    monkeypatch.setenv("LLM_MODEL", "b")
    assert load_settings(env).llm_model == "b"


def test_save_api_key_replaces_old_key_and_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=x\nGROQ_API_KEY=old\n")
    save_api_key("  gsk_new  ", env)
    assert env.read_text() == "LLM_MODEL=x\nGROQ_API_KEY=gsk_new\n"
    assert load_settings(env).groq_api_key == "gsk_new"


@pytest.mark.parametrize("bad", ["", "   ", "gsk a", "gsk\nX=1"])
def test_save_api_key_rejects_empty_and_whitespace(tmp_path, bad):
    with pytest.raises(ValueError):
        save_api_key(bad, tmp_path / ".env")


def test_saved_key_is_readable_only_by_owner(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=x\n")
    env.chmod(0o644)
    save_api_key("gsk_new", env)
    assert env.stat().st_mode & 0o777 == 0o600


def test_invalid_number_gives_readable_error(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CHUNK_SECONDS=десять\n")
    with pytest.raises(ValueError, match="CHUNK_SECONDS"):
        load_settings(env)
