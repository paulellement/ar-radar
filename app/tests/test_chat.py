import io
import json

import chat


class FakeProc:
    def __init__(self, events):
        self.stdout = io.StringIO("".join(json.dumps(e) + "\n" for e in events) + "not json\n")
        self.killed = False

    def wait(self):
        return 0

    def kill(self):
        self.killed = True


TOOL = {"type": "tool_use", "name": "mcp__ar-radar__shows_for_me", "input": {}}
EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "s1"},
    {"type": "assistant", "message": {"content": [TOOL]}},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Two shows.",
        "session_id": "s1",
    },  # noqa: E501
]


def test_command_only_allows_the_mcp_tools():
    cmd = chat.command("hi")
    assert cmd[cmd.index("--tools") + 1] == ""  # no shell or file tools
    assert "--strict-mcp-config" in cmd and "mcp__ar-radar" in cmd
    assert "--resume" not in cmd
    assert chat.command("again", "s1")[-2:] == ["--resume", "s1"]


def test_ask_streams_tool_progress_and_returns_the_answer():
    seen = []
    out = chat.ask("shows?", on_tool=seen.append, popen=lambda *a, **k: FakeProc(EVENTS))
    assert out == {
        "text": "Two shows.",
        "session_id": "s1",
        "tools": ["shows_for_me"],
        "error": None,
    }  # noqa: E501
    assert [chat.label(n) for n in seen] == ["Finding shows for you"]


def test_ask_reports_errors_instead_of_raising():
    failed = [{"type": "result", "is_error": True, "result": "Not logged in", "session_id": "s2"}]
    assert chat.ask("x", popen=lambda *a, **k: FakeProc(failed))["error"] == "Not logged in"
    assert chat.ask("x", popen=lambda *a, **k: FakeProc([]))["error"].startswith("No answer")

    def missing(*a, **k):
        raise FileNotFoundError("claude")

    assert "Couldn't start" in chat.ask("x", popen=missing)["error"]
