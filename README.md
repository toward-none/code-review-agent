# Code Review Agent

This repository contains a small GitHub Pull Request review agent built with:

- [pydantic-ai](https://github.com/pydantic/pydantic-ai) for tool-using agents
- [PyGithub](https://pygithub.readthedocs.io/) for interacting with the GitHub API
- OpenAI-compatible chat models (via `OPENAI_API_KEY` / `OPENAI_BASE_URL`)
- [uv](https://github.com/astral-sh/uv) for Python dependency management

The agent is designed to run in CI on pull requests, but you can also run it locally against any PR.

## What It Does

For a given pull request, the agent:

1. Fetches PR metadata (author, title, body, state, head SHA, commit SHAs).
2. Fetches details for the commits (changed files, patches, etc.).
3. Reads selected files from the repository (e.g. `README.md`) for project context.
4. Analyses the diff and prepares up to 10 inline comments with concrete, code-level suggestions.
5. Posts a markdown summary review covering:
   - What’s good about the PR.
   - Test coverage / migrations.
   - Structural or design improvements (SOLID, patterns, etc.).

All orchestration is implemented in `src/code_review_agent/main.py` using a `pydantic_ai.Agent` and a set of tools:

- `fetch_pr_details` – get basic PR metadata.
- `pr_commits_details` – list changed files for a given commit SHA.
- `fetch_github_file` – read file contents from the repo.
- `create_inline_comment` – post inline comments on specific lines.
- `post_review` – post a final summary review comment.

---

## Project Structure

- `src/code_review_agent/main.py` – main agent, tools, and workflow entrypoint.
- `.github/workflows/ci.yml` – GitHub Actions workflow that runs the agent on pull requests.
- `pyproject.toml` – Python project configuration (dependencies, metadata).
- `README.md` – this file.

---

## Requirements

- Python 3.13 (or a recent 3.11+ for local use).
- `uv` installed (for dependency and virtualenv management).
- A GitHub Personal Access Token (PAT) with at least `repo` read access.
- An OpenAI-compatible API endpoint and API key.

---

## Installation

**Install uv**

If you don’t have `uv` installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# ensure ~/.local/bin is on your PATH
```

**Install dependencies**

```bash
uv sync
```

**Configuration (.env)**

Create a .env file in the project root (same directory as pyproject.toml) with:

You need also to add permissions for the repo in Github.

# GitHub
GITHUB_TOKEN=your_github_pat_here
REPOSITORY=owner/repo-name        # e.g. toward-none/code-review-agent
PR_NUMBER=1                       # the PR number to review

# OpenAI / LLM
OPENAI_MODEL=gpt-5.4              # or another model name
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1

**Running locally**

```bash
uv run python -m code_review_agent.main
```
