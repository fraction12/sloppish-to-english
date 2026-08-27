#!/usr/bin/env python3
"""Small codex executable stand-in used by the hook tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "exec":
        return 2
    args_file = os.environ.get("FAKE_CODEX_ARGS_FILE")
    if args_file:
        Path(args_file).write_text(json.dumps(sys.argv), encoding="utf-8")
    output_index = sys.argv.index("-o") + 1
    original = sys.stdin.read()
    stdin_file = os.environ.get("FAKE_CODEX_STDIN_FILE")
    if stdin_file:
        Path(stdin_file).write_text(original, encoding="utf-8")
    if os.environ.get("FAKE_CODEX_ECHO") == "1":
        rewritten = original.replace(
            os.environ.get("FAKE_CODEX_REPLACE_FROM", ""),
            os.environ.get("FAKE_CODEX_REPLACE_TO", ""),
        )
    else:
        rewritten = os.environ.get("FAKE_CODEX_OUTPUT", f"Plain: {original}")
    Path(sys.argv[output_index]).write_text(rewritten, encoding="utf-8")
    return int(os.environ.get("FAKE_CODEX_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
