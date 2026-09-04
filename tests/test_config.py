"""config 모듈 테스트. 실제 인증키를 사용하지 않는다."""

import pytest

from grant_radar.config import (
    API_KEY_ENV_VAR,
    MISSING_KEY_MESSAGE,
    ConfigError,
    Settings,
    load_settings,
    parse_env_file,
)

FAKE_KEY = "fake-test-key-not-real"


def test_parse_env_file_basic(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# 주석\n"
        "\n"
        f"KSTARTUP_API_KEY={FAKE_KEY}\n"
        'QUOTED="hello"\n'
        "SPACED =  value  \n"
        "no_equals_line_ignored_is_not_here\n",
        encoding="utf-8",
    )
    values = parse_env_file(env)
    assert values["KSTARTUP_API_KEY"] == FAKE_KEY
    assert values["QUOTED"] == "hello"
    assert values["SPACED"] == "value"


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert parse_env_file(tmp_path / ".env") == {}


def test_parse_env_file_with_bom(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbf" + f"KSTARTUP_API_KEY={FAKE_KEY}\n".encode("utf-8"))
    assert parse_env_file(env)["KSTARTUP_API_KEY"] == FAKE_KEY


def test_load_settings_env_var_priority(tmp_path, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "from-env-var")
    (tmp_path / ".env").write_text(f"{API_KEY_ENV_VAR}=from-file\n", encoding="utf-8")
    settings = load_settings(project_root=tmp_path)
    assert settings.api_key == "from-env-var"


def test_load_settings_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    (tmp_path / ".env").write_text(f"{API_KEY_ENV_VAR}={FAKE_KEY}\n", encoding="utf-8")
    settings = load_settings(project_root=tmp_path)
    assert settings.api_key == FAKE_KEY


def test_load_settings_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(project_root=tmp_path)
    assert str(exc_info.value) == MISSING_KEY_MESSAGE


def test_load_settings_empty_value_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    (tmp_path / ".env").write_text(f"{API_KEY_ENV_VAR}=\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(project_root=tmp_path)


def test_parse_env_file_utf16_gives_config_error(tmp_path):
    # PowerShell 5.1의 `>` 리다이렉션은 UTF-16으로 저장될 수 있다
    env = tmp_path / ".env"
    env.write_bytes(f"KSTARTUP_API_KEY={FAKE_KEY}\n".encode("utf-16"))
    with pytest.raises(ConfigError) as exc_info:
        parse_env_file(env)
    assert "UTF-8" in str(exc_info.value)
    assert FAKE_KEY not in str(exc_info.value)
    # 예외 체인에 원문 바이트를 가진 UnicodeDecodeError가 남지 않아야 한다
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_settings_repr_masks_key():
    settings = Settings(api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
