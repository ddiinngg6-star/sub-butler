import pytest

from sub_butler.cli import main
from sub_butler.config import ConfigError, config_from_dict, load_config


def test_load_config_expands_env_and_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_TEST_KEY", raising=False)
    monkeypatch.setenv("SB_TEST_TOKEN", "from-env")
    (tmp_path / ".env").write_text('SB_TEST_KEY="from-dotenv"\nSB_TEST_TOKEN=ignored\n# comment\n')
    (tmp_path / "config.yaml").write_text(
        "llm:\n  api_key: ${SB_TEST_KEY}\nnotion:\n  token: ${SB_TEST_TOKEN}\n  database_id: abc\n"
    )
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.llm.api_key == "from-dotenv"
    assert cfg.notion.token == "from-env", "已有环境变量优先于 .env"
    assert cfg.notion.fields["due"] == "续费/到期日"


def test_unknown_keys_are_rejected():
    with pytest.raises(ConfigError, match="imap_hots"):
        config_from_dict({"mail": {"imap_hots": "x"}})
    with pytest.raises(ConfigError, match="foo"):
        config_from_dict({"foo": 1})


def test_example_config_is_valid(monkeypatch):
    from pathlib import Path

    cfg = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert "telegram" in cfg.notify


def test_cli_missing_config_returns_2(tmp_path, capsys):
    assert main(["-c", str(tmp_path / "nope.yaml"), "check"]) == 2
    assert "找不到配置文件" in capsys.readouterr().err
