#!/usr/bin/env python3
"""Tell the active Codex turn to run its final draft through the translator."""

from __future__ import annotations

import json
import os
import sys


INSTRUCTIONS = (
    "Before sending your final assistant response, prepare the complete response as a draft. "
    "Then call the sloppish-translator rewrite_response tool exactly once with that entire "
    "draft and the current user request in user_question. Use the returned text verbatim as "
    "your entire final response: do not edit, label, "
    "summarize, preface, or mention the rewrite or tool. If the tool is unavailable or returns "
    "an error, send the original draft normally. This is only a presentation pass; it must not "
    "change the task, facts, code, citations, completed actions, or safety decisions."
)


def setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"SLOPPISH_{name}", default)


def emit(payload: dict[str, object]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def main() -> int:
    if setting("ENABLED") == "0":
        emit({})
        return 0
    if setting("TRANSLATOR_CHILD") == "1":
        emit({})
        return 0

    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        emit({})
        return 0
    if not isinstance(event, dict):
        emit({})
        return 0

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": INSTRUCTIONS,
            }
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
