# CLAUDE.md

이 프로젝트는 나의 투자 의사 결정을 돕는 도구들로 구성되어 있다.
작성된 코드 및 스크립트는 `invagent` 파이썬 패키지안에 포함된다.
또한 Claude Code에서 사용될 수 있는 여러 Skill들은 `.claude/skills` 아래에 위치한다.

## Features

- 텔레그램 "저장된 메시지" 저장 및 핵심 요약
- 텔레그램에서 PDF 형태의 리포트 일괄 다운로드
- 종목별 목표 주가 및 투자 의견 변경 트랙킹
- 관심 종목 뉴스 및 공시 트랙킹
- 투자 대가들의 관점에서 투자 조언

## Tech Stack

- Package manager: `uv`
- Python: >=3.12

## Quick Start

```bash
# Install dependencies
uv sync

# Verify installation
uv run python -c "import invagent; print('invagent ready')"
```

## Project Structure

- `src/invagent/` - Main Python package
- `.claude/settings.local.json` - Local Claude Code configuration
- `pyproject.toml` - Package metadata and dependencies
- `context/` - Contexts, e.g., 관심 종목
- `template/` - Contains output templates
- `output/` - Outputs will be located here

## Development Workflow

- Work on feature branches (not main)
- Commit changes with clear messages
- Submit PRs for review before merging to main
