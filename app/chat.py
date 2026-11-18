"""Chat tab backend: Claude Code run headless (`claude -p`) with only the ar-radar MCP tools.

$0 extra on the user's Claude subscription, and local only: it needs the `claude` CLI on this
machine. Built-in tools are switched off (--tools ""), so text typed in the browser can't make
Claude run shell commands or read files; it can only call the read-only ar-radar tools.
Follow-up questions continue the same Claude Code session (--resume).
"""

import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TIMEOUT_S = 180

SYSTEM = (
    "You are the chat inside the A&R Radar web app. Answer only with the ar-radar tools. "
    "Be concise: one or two sentences, then a short list or table. Always give show dates, "
    "venues, price (or 'price not listed'), whether the artist is headlining or opening, and "
    "ticket status. Mention once if the data is more than two days old. Never predict that an "
    "artist will break."
)

TOOL_LABELS = {
    "shows_for_me": "Finding shows for you",
    "discover_for_me": "Looking for new artists you'd like",
    "my_taste_profile": "Reading your Last.fm taste",
    "upcoming_shows": "Checking upcoming shows",
    "top_breakout_artists": "Ranking rising artists",
    "artist_profile": "Looking up the artist",
    "data_status": "Checking how fresh the data is",
}


def available() -> bool:
    return shutil.which("claude") is not None


def command(question: str, session_id: str | None = None) -> list[str]:
    cmd = [
        "claude", "-p", question,
        "--mcp-config", str(REPO / ".mcp.json"), "--strict-mcp-config",
        "--tools", "",  # no shell, no file access: only the MCP tools below
        "--allowedTools", "mcp__ar-radar",
        "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", SYSTEM,
    ]  # fmt: skip
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def label(tool_name: str) -> str:
    short = tool_name.split("__")[-1]
    return TOOL_LABELS.get(short, f"Calling {short}")


def ask(
    question: str,
    session_id: str | None = None,
    on_tool: Callable[[str], None] | None = None,
    popen=subprocess.Popen,
    timeout: float = TIMEOUT_S,
) -> dict:
    """Run one turn. Returns {"text", "session_id", "tools", "error"}; never raises."""
    out = {"text": "", "session_id": session_id, "tools": [], "error": None}
    try:
        proc = popen(
            command(question, session_id),
            cwd=REPO,  # .mcp.json uses repo-relative paths
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as e:
        out["error"] = f"Couldn't start Claude Code: {e}"
        return out
    timer = threading.Timer(timeout, proc.kill)  # a stuck run must not hang the page
    timer.start()
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        out["tools"].append(block["name"].split("__")[-1])
                        if on_tool:
                            on_tool(block["name"])
            elif event.get("type") == "result":
                out["text"] = event.get("result") or ""
                out["session_id"] = event.get("session_id") or session_id
                if event.get("is_error"):
                    out["error"] = out["text"] or "Claude Code reported an error."
        proc.wait()
    finally:
        timer.cancel()
    if not out["text"] and not out["error"]:
        out["error"] = "No answer (timed out or Claude Code isn't logged in)."
    return out
