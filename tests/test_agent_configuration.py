"""Validate shared Claude Code and Codex repository configuration."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
SKILL_NAMES = (
    "advice",
    "monthly-investment-review",
    "opendart",
    "summarize-telegram",
)


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_is_shared_with_claude(skill_name: str) -> None:
    canonical = SKILLS_ROOT / skill_name
    claude_link = REPO_ROOT / ".claude" / "skills" / skill_name

    assert (canonical / "SKILL.md").is_file()
    assert claude_link.is_symlink()
    assert claude_link.resolve() == canonical.resolve()


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_skill_has_valid_frontmatter_and_ui_metadata(skill_name: str) -> None:
    skill_dir = SKILLS_ROOT / skill_name
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)

    assert frontmatter_match is not None
    frontmatter = frontmatter_match.group(1)
    assert re.search(rf"^name:\s*[\"']?{re.escape(skill_name)}[\"']?\s*$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s*", frontmatter, re.MULTILINE)

    metadata = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "display_name:" in metadata
    assert "short_description:" in metadata
    assert f"${skill_name}" in metadata


def test_skills_contain_no_credentials_or_machine_paths() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SKILLS_ROOT.rglob("*")
        if path.is_file() and path.suffix in {".md", ".py", ".yaml"}
    )

    assert not re.search(r"opendart=[0-9a-f]{20,}", combined)
    assert "/Users/sesise" not in combined
    assert ".claude/skills" not in combined


def test_project_codex_config_is_credential_free() -> None:
    config_path = REPO_ROOT / ".codex" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert config["approval_policy"] == "on-request"
    assert config["sandbox_mode"] == "workspace-write"
    assert "mcp_servers" not in config


def test_market_signal_parser_with_embedded_payload(capsys: pytest.CaptureFixture[str]) -> None:
    script_path = SKILLS_ROOT / "summarize-telegram" / "scripts" / "fetch_market_signals.py"
    spec = importlib.util.spec_from_file_location("fetch_market_signals", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = (
        r'\"initialMarketIndicesData\":'
        r'{\"short_term_signal\":\"green\",\"long_term_signal\":\"yellow\",'
        r'\"indices\":[]}'
    )
    data = module.extract_json(payload, "initialMarketIndicesData")

    assert data["short_term_signal"] == "green"
    module.print_signals(data)
    assert "green" in capsys.readouterr().out
