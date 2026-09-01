from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from consilium.adapters import (
    ApiAdapter,
    HumanAdapter,
    NativeAdapter,
    WebHumanAdapter,
    build_adapter,
)
from consilium.wrappers import WRAPPER_PRESETS, resolve_wrapper

EXPECTED_WRAPPERS = {
    "claude",
    "codex",
    "gemini",
    "human",
    "human-with-web-gui",
    "opencode",
    "qwen-code",
    "kimi",
    "glm-code",
    "deepseek.coder",
}


def test_initial_wrapper_set_is_exactly_the_supported_roster():
    assert set(WRAPPER_PRESETS) == EXPECTED_WRAPPERS


def test_wrapper_defaults_are_overridable_and_metadata_is_merged():
    resolved = resolve_wrapper(
        {
            "name": "critic",
            "wrapper": "claude",
            "command": ["custom-claude", "{prompt}"],
            "meta": {"model": "sonnet"},
        }
    )
    assert resolved["command"][0] == "custom-claude"
    assert resolved["meta"] == {
        "wrapper": "claude",
        "brand": "anthropic",
        "model": "sonnet",
    }

    with pytest.raises(ValueError, match="unknown wrapper"):
        resolve_wrapper({"wrapper": "missing"})


@pytest.mark.parametrize("name", sorted(EXPECTED_WRAPPERS))
def test_every_wrapper_builds_one_of_the_shared_adapters(name: str, tmp_path: Path):
    resolved = resolve_wrapper({"name": name, "wrapper": name})
    adapter = build_adapter(resolved, root=tmp_path)
    expected = (
        HumanAdapter
        if name == "human"
        else WebHumanAdapter
        if "web" in name
        else ApiAdapter
        if name == "deepseek.coder"
        else NativeAdapter
    )
    assert isinstance(adapter, expected)
    adapter.close()


def test_native_adapter_uses_argv_and_private_cwd_for_resume(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[list[str], dict]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=f"answer {len(calls)}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    adapter = NativeAdapter(
        command=["agent", "{prompt}"],
        resume_command=["agent", "--continue", "{prompt}"],
    )
    try:
        adapter.open("SYSTEM")
        assert adapter.send_turn("first").text == "answer 1"
        assert adapter.send_turn("second").text == "answer 2"
    finally:
        adapter.close()

    assert calls[0][0] == ["agent", "SYSTEM\n\nfirst"]
    assert calls[1][0] == ["agent", "--continue", "second"]
    assert calls[0][1]["cwd"] == calls[1][1]["cwd"]
    assert "shell" not in calls[0][1]


def test_native_adapter_requires_real_session_resume(tmp_path: Path):
    with pytest.raises(ValueError, match="resume_command"):
        build_adapter(
            {"name": "stateless", "adapter": "native", "command": ["agent"]},
            root=tmp_path,
        )


def test_failed_resume_declares_memory_loss_without_a_duplicate_call(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = 0

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            0 if calls == 1 else 1,
            stdout="first answer" if calls == 1 else "",
            stderr="session missing",
        )

    monkeypatch.setattr(subprocess, "run", run)
    adapter = NativeAdapter(
        command=["agent", "{prompt}"],
        resume_command=["agent", "-c", "{prompt}"],
    )
    try:
        adapter.open("SYSTEM")
        adapter.send_turn("first")
        with pytest.raises(RuntimeError, match="session missing"):
            adapter.send_turn("second")
        assert adapter.memory_lost is True
        assert calls == 2
    finally:
        adapter.close()


def test_web_human_escapes_context_and_queues_moderator_commands():
    adapter = WebHumanAdapter(timeout_s=0, port=0)
    assert adapter.send_turn("<script>alert(1)</script>").text == ""
    page = adapter._page()
    assert "&lt;script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert 'http-equiv="refresh"' in page

    adapter.away = True
    assert adapter._submit("late contribution") is False
    assert adapter._submit("/back") is True
    line = adapter._submissions.get_nowait()
    assert adapter._turn_result(line).text == ""
    assert adapter.pending_command is not None
    assert adapter.pending_command.verb == "back"
    assert adapter.away is False

    with pytest.raises(ValueError, match="loopback"):
        WebHumanAdapter(host="0.0.0.0")
