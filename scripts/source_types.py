"""Closed source classification for GitHub single-file Skills."""
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit


def is_github_skill_url(url: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com", "raw.githubusercontent.com"}:
        return False
    filename = PurePosixPath(unquote(parsed.path)).name.lower()
    return filename == "skill.md" or filename.endswith(".skill.md")


def skill_collector_url(url: str) -> str:
    """SkillMind v2.5 downloads blob files; raw URLs otherwise enter Git cloning."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host == "raw.githubusercontent.com":
        parts = parsed.path.lstrip("/").split("/", 3)
        if len(parts) == 4:
            owner, repo, revision, file_path = parts
            return urlunsplit((parsed.scheme, "github.com", f"/{owner}/{repo}/blob/{revision}/{file_path}", parsed.query, parsed.fragment))
    if host == "www.github.com":
        return urlunsplit((parsed.scheme, "github.com", parsed.path, parsed.query, parsed.fragment))
    return url
