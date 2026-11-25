"""
plugins/github_sentinel.py — GitHub & Local Git Repository Sentinel for JARVIS.

Inspects GitHub repositories, issues, pull requests, user profiles, and local git status.
Works with public GitHub data without any key, and supports Personal Access Tokens for private repos.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

_API_BASE = "https://api.github.com"

PLUGIN = {
    "name": "github_sentinel",
    "label": "GitHub Sentinel",
    "level": "basic",
    "summary": "Inspects GitHub repositories, issues, pull requests, user profiles, and local git status.",
    "setup": [
        "Works out of the box for public GitHub data.",
        "Add a GitHub token in Plugin Settings to access private repositories.",
        "Say: 'Jarvis, check my GitHub profile', 'show issues for owner/repo', 'what is the git status of my project?'.",
    ],
    "description": (
        "GitHub and Git repository assistant. "
        "Use this tool when the user asks about GitHub, repos, pull requests, issues, commit logs, "
        "or local git branch status. "
        "Actions: "
        "'user_profile' (profile info, followers, public repos for a user), "
        "'list_repos' (repositories for a user or org), "
        "'repo_info' (stars, forks, open issues, description of a specific owner/repo), "
        "'issues' (open issues and PRs for a repository), "
        "'search_repos' (search GitHub for projects), "
        "'local_git_status' (check local git status, branch, and uncommitted files in current or specified folder), "
        "'git_pull' (pull latest changes in a local git folder)."
    ),
    "brief": "Inspect GitHub repositories, issues, user profiles, and local git branch status.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "user_profile", "list_repos", "repo_info", "issues",
                    "search_repos", "local_git_status", "git_pull"
                ],
                "description": "The GitHub or Git action to perform."
            },
            "target": {
                "type": "STRING",
                "description": (
                    "The target username (for user_profile/list_repos), repository as 'owner/repo' "
                    "(for repo_info/issues), search query (for search_repos), or directory path (for local_git_status)."
                )
            },
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of items to return (default 5)."
            }
        },
        "required": ["action"]
    }
}

PLUGIN_SETTINGS = {
    "namespace": "github_sentinel",
    "title": "🐙  GITHUB SENTINEL",
    "fields": [
        {
            "key": "github_token",
            "label": "GitHub Personal Access Token (classic or fine-grained)",
            "type": "password",
            "placeholder": "ghp_xxxxxxxxxxxx"
        },
        {
            "key": "default_user",
            "label": "Default GitHub Username",
            "type": "text",
            "placeholder": "e.g. your_username"
        }
    ]
}


def _get_token() -> str:
    """Retrieve token from plugin settings, environment, or config."""
    try:
        from memory.config_manager import get_plugin_setting
        token = get_plugin_setting("github_sentinel", "github_token", "")
        if token:
            return str(token).strip()
    except Exception:
        pass

    env_tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_tok:
        return env_tok.strip()

    try:
        from memory.config_manager import load_api_keys
        return str(load_api_keys().get("github_token") or "").strip()
    except Exception:
        return ""


def _default_user() -> str:
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting("github_sentinel", "default_user", "")
        if val:
            return str(val).strip()
    except Exception:
        pass
    return ""


def _fetch_github_json(endpoint: str) -> tuple[int, any]:
    """Execute authenticated or anonymous request to GitHub API."""
    url = endpoint if endpoint.startswith("http") else f"{_API_BASE}{endpoint}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "JARVIS-Assistant-Sentinel/1.0")
    req.add_header("Accept", "application/vnd.github.v3+json")

    token = _get_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return 200, data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
            err_json = json.loads(body)
            msg = err_json.get("message", str(e))
        except Exception:
            msg = str(e)
        return e.code, msg
    except Exception as e:
        return 0, str(e)


def _run_git_cmd(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    """Execute git CLI command in current or target directory."""
    try:
        target_dir = str(cwd) if cwd and cwd.exists() else str(Path.cwd())
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=target_dir
        )
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, res.stderr.strip() or res.stdout.strip()
    except FileNotFoundError:
        return False, "Git CLI is not installed or not in system PATH."
    except Exception as e:
        return False, str(e)


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "local_git_status").strip().lower()
    target = str(p.get("target") or "").strip()
    limit = int(p.get("limit") or 5)
    limit = max(1, min(limit, 20))

    try:
        # ── 1. Local Git Status ──────────────────────────────────────────────
        if action == "local_git_status":
            cwd = Path(target) if target and Path(target).is_dir() else Path.cwd()
            ok, branch = _run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
            if not ok:
                return f"No git repository found in {cwd.name}."

            _, status_out = _run_git_cmd(["status", "--short"], cwd=cwd)
            _, last_commit = _run_git_cmd(["log", "-1", "--pretty=format:%h - %s (%cr)"], cwd=cwd)

            lines = [l for l in status_out.splitlines() if l.strip()]
            status_desc = f"{len(lines)} modified/untracked files" if lines else "clean working tree"
            return (
                f"Local Git ({cwd.name}): On branch '{branch}', {status_desc}. "
                f"Latest commit: {last_commit or 'none'}."
            )

        # ── 2. Local Git Pull ────────────────────────────────────────────────
        if action == "git_pull":
            cwd = Path(target) if target and Path(target).is_dir() else Path.cwd()
            ok, out = _run_git_cmd(["pull"], cwd=cwd)
            if ok:
                return f"Git pull in {cwd.name} successful: {out[:120]}."
            return f"Git pull failed: {out[:150]}."

        # ── 3. GitHub User Profile ───────────────────────────────────────────
        if action == "user_profile":
            user = target or _default_user()
            endpoint = f"/users/{user}" if user else "/user"
            status, data = _fetch_github_json(endpoint)
            if status != 200:
                return f"Could not fetch GitHub user '{user or 'authenticated'}': {data}."
            login = data.get("login")
            name = data.get("name") or login
            repos = data.get("public_repos", 0)
            followers = data.get("followers", 0)
            bio = data.get("bio") or "No bio provided"
            return (
                f"GitHub user {name} (@{login}): {repos} public repositories, "
                f"{followers} followers. Bio: '{bio}'."
            )

        # ── 4. List Repositories ─────────────────────────────────────────────
        if action == "list_repos":
            user = target or _default_user()
            endpoint = f"/users/{user}/repos?sort=updated&per_page={limit}" if user else f"/user/repos?sort=updated&per_page={limit}"
            status, data = _fetch_github_json(endpoint)
            if status != 200 or not isinstance(data, list):
                return f"Could not list repositories: {data}."
            if not data:
                return f"No repositories found for {user or 'account'}."

            repo_summaries = []
            for r in data[:limit]:
                name = r.get("name")
                stars = r.get("stargazers_count", 0)
                lang = r.get("language") or "Code"
                desc = r.get("description") or ""
                repo_summaries.append(f"{name} ({lang}, ⭐{stars})")
            return f"Top repositories for {user or 'you'}: {', '.join(repo_summaries)}."

        # ── 5. Repository Info ───────────────────────────────────────────────
        if action == "repo_info":
            if not target or "/" not in target:
                return "Please provide the repository in 'owner/repo' format (e.g. 'torvalds/linux')."
            status, data = _fetch_github_json(f"/repos/{target}")
            if status != 200:
                return f"Could not fetch repository '{target}': {data}."
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)
            issues = data.get("open_issues_count", 0)
            desc = data.get("description") or "No description"
            lang = data.get("language") or "General"
            return (
                f"Repository '{target}' ({lang}): ⭐ {stars} stars, 🍴 {forks} forks, "
                f"❗ {issues} open issues. {desc}."
            )

        # ── 6. Issues and Pull Requests ──────────────────────────────────────
        if action == "issues":
            if not target or "/" not in target:
                return "Please provide the repository in 'owner/repo' format to check issues."
            status, data = _fetch_github_json(f"/repos/{target}/issues?state=open&per_page={limit}")
            if status != 200 or not isinstance(data, list):
                return f"Could not fetch issues for '{target}': {data}."
            if not data:
                return f"No open issues or pull requests found in {target}."

            issues_list = []
            for item in data[:limit]:
                num = item.get("number")
                title = item.get("title", "")[:50]
                is_pr = "pull_request" in item
                kind = "PR" if is_pr else "Issue"
                issues_list.append(f"#{num} [{kind}] {title}")
            return f"Open items in {target}: {'; '.join(issues_list)}."

        # ── 7. Search Repositories ───────────────────────────────────────────
        if action == "search_repos":
            if not target:
                return "Please specify what repositories to search for on GitHub."
            query = urllib.parse.quote(target)
            status, data = _fetch_github_json(f"/search/repositories?q={query}&sort=stars&per_page={limit}")
            if status != 200:
                return f"GitHub search failed: {data}."
            items = data.get("items", [])
            if not items:
                return f"No GitHub repositories found matching '{target}'."
            results = []
            for r in items[:limit]:
                results.append(f"{r.get('full_name')} (⭐{r.get('stargazers_count', 0)})")
            return f"GitHub results for '{target}': {', '.join(results)}."

        return f"Unknown GitHub Sentinel action '{action}'."

    except Exception as e:
        return f"GitHub Sentinel encountered an error: {e}"
