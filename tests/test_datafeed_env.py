"""`invagent.datafeed.env` — 저장소 경로와 `.env` 값 해석."""

from pathlib import Path

from invagent.datafeed import env


def test_repo_root_is_the_directory_holding_pyproject() -> None:
    root = env.repo_root()

    assert (root / "pyproject.toml").is_file()
    assert (root / ".agents" / "skills").is_dir()


def test_output_dir_defaults_under_the_repo_root() -> None:
    assert env.output_dir() == env.repo_root() / "output"


def test_output_dir_honours_the_override_and_expands_home(monkeypatch) -> None:
    monkeypatch.setenv(env.ENV_OUTPUT_DIR, "~/somewhere/else")

    assert env.output_dir() == Path("~/somewhere/else").expanduser()


def test_env_value_prefers_the_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "  from-env  ")

    assert env.env_value("SOME_TOKEN") == "from-env"


def test_env_value_falls_back_to_the_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SOME_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "# 주석\nOTHER=x\nSOME_TOKEN='quoted value'\n", encoding="utf-8"
    )

    assert env.env_value("SOME_TOKEN", start=tmp_path) == "quoted value"


def test_env_value_reads_any_key_not_just_the_cookie(tmp_path, monkeypatch) -> None:
    """`.env`의 다른 키도 읽힌다 — 예전 워커는 쿠키 하나만 알아봤다."""
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    (tmp_path / ".env").write_text("TELEGRAM_API_ID=12345\n", encoding="utf-8")

    assert env.env_value("TELEGRAM_API_ID", start=tmp_path) == "12345"


def test_env_value_is_none_when_nothing_defines_it(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SOME_TOKEN", raising=False)

    assert env.env_value("SOME_TOKEN", start=tmp_path) is None
