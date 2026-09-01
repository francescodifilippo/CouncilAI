"""Built-in participant presets.

A wrapper selects defaults; adapters still own transport. Explicit participant
configuration always wins, so a preset never locks a user to one CLI version.
"""

from __future__ import annotations

from typing import Any

_CLAUDE = (
    "claude",
    "--print",
    "--output-format",
    "text",
    "--safe-mode",
    "--disable-slash-commands",
    "--tools",
    "",
)
_QWEN = (
    "qwen",
    "--safe-mode",
    "--approval-mode",
    "plan",
    "--exclude-tools",
    "shell,write,edit,agent",
    "--max-tool-calls",
    "25",
)

WRAPPER_PRESETS: dict[str, dict[str, Any]] = {
    "claude": {
        "adapter": "native",
        "command": [*_CLAUDE, "{prompt}"],
        "resume_command": [*_CLAUDE, "--continue", "{prompt}"],
        "meta": {"brand": "anthropic"},
    },
    "codex": {
        "adapter": "native",
        "command": [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-",
        ],
        "resume_command": [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "resume",
            "--last",
            "-",
        ],
        "meta": {"brand": "openai"},
    },
    "gemini": {
        "adapter": "native",
        "command": ["gemini", "-e", "none", "--output-format", "text", "-p", "{prompt}"],
        "resume_command": [
            "gemini",
            "-e",
            "none",
            "--resume",
            "latest",
            "--output-format",
            "text",
            "-p",
            "{prompt}",
        ],
        "meta": {"brand": "google"},
    },
    "human": {"adapter": "human", "meta": {"brand": "human"}},
    "human-with-web-gui": {
        "adapter": "human_web",
        "meta": {"brand": "human", "interface": "web"},
    },
    "opencode": {
        "adapter": "native",
        "command": ["opencode", "run", "--agent", "plan", "{prompt}"],
        "resume_command": ["opencode", "run", "--agent", "plan", "--continue", "{prompt}"],
        "meta": {"brand": "opencode"},
    },
    "qwen-code": {
        "adapter": "native",
        "command": [*_QWEN, "--output-format", "text", "-p", "{prompt}"],
        "resume_command": [
            *_QWEN,
            "--continue",
            "--output-format",
            "text",
            "-p",
            "{prompt}",
        ],
        "meta": {"brand": "qwen"},
    },
    "kimi": {
        "adapter": "native",
        "command": ["kimi", "-p", "{prompt}", "--output-format", "text"],
        "resume_command": ["kimi", "-c", "-p", "{prompt}", "--output-format", "text"],
        "meta": {"brand": "moonshot-ai"},
    },
    "glm-code": {
        "adapter": "native",
        "command": [*_CLAUDE, "{prompt}"],
        "resume_command": [*_CLAUDE, "--continue", "{prompt}"],
        "env": {"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"},
        "env_from": {"ANTHROPIC_AUTH_TOKEN": "ZAI_API_KEY"},
        "meta": {"brand": "z.ai"},
    },
    "deepseek.coder": {
        "adapter": "api",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "meta": {"brand": "deepseek", "model": "deepseek-v4-pro"},
    },
}


def resolve_wrapper(spec: dict[str, Any]) -> dict[str, Any]:
    """Apply one named preset while keeping every explicit field authoritative."""
    name = spec.get("wrapper")
    if not name:
        return spec
    try:
        preset = WRAPPER_PRESETS[str(name)]
    except KeyError as exc:
        raise ValueError(f"unknown wrapper {name!r}") from exc

    resolved = {**preset, **spec}
    resolved["meta"] = {
        "wrapper": str(name),
        **preset.get("meta", {}),
        **(spec.get("meta") or {}),
    }
    return resolved
