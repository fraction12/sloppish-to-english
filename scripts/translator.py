#!/usr/bin/env python3
"""Run an isolated Codex call that rewrites an assistant draft."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
DEFAULT_PROMPT = PLUGIN_ROOT / "prompts" / "plain-language.txt"
TLDR_PROMPT = PLUGIN_ROOT / "prompts" / "tldr.txt"
TRANSLATOR_INSTRUCTIONS = PLUGIN_ROOT / "prompts" / "translator-agent.txt"
VALID_EFFORTS = {"low", "medium", "high", "xhigh"}


def setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"SLOPPISH_{name}", default)


FENCE_OPEN = re.compile(r"(?m)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)")
PROTECTED_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?s)<(oai-[A-Za-z0-9_-]+)(?:\s[^>]*)?>.*?</\1>"), 0),
    (re.compile(r"(?m)^::[A-Za-z0-9_-]+\{[^\n]*\}[ \t]*(?:\n|$)"), 0),
    (re.compile(r"(?<!`)``([^`\n]+)``(?!`)"), 0),
    (re.compile(r"(?<!`)`([^`\n]+)`(?!`)"), 0),
    (re.compile(r"(?<![A-Za-z0-9_])(?:https?://|codex://)[^\s<>]+"), 0),
    (re.compile(r"\]\((<?/[^)\n]+>?)\)"), 1),
)


def protected_spans(message: str) -> list[tuple[int, int]]:
    candidates: list[tuple[int, int]] = []

    cursor = 0
    while match := FENCE_OPEN.search(message, cursor):
        fence = match.group(1)
        closing = re.compile(
            rf"(?m)^[ \t]{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\n|$)"
        ).search(message, match.end())
        end = closing.end() if closing else len(message)
        candidates.append((match.start(), end))
        cursor = end

    for pattern, group in PROTECTED_PATTERNS:
        for match in pattern.finditer(message):
            candidates.append(match.span(group))

    selected: list[tuple[int, int]] = []
    for start, end in sorted(candidates, key=lambda span: (span[0], -span[1])):
        if start == end:
            continue
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end))
    return selected


def protect_message(message: str) -> tuple[str, list[tuple[str, str]]]:
    spans = protected_spans(message)
    if not spans:
        return message, []

    marker = "SLOPPISH_PROTECTED_SEGMENT"
    while marker in message:
        marker += "_X"

    pieces: list[str] = []
    protected: list[tuple[str, str]] = []
    cursor = 0
    for index, (start, end) in enumerate(spans):
        token = f"@@{marker}_{index:04d}@@"
        pieces.extend((message[cursor:start], token))
        protected.append((token, message[start:end]))
        cursor = end
    pieces.append(message[cursor:])
    return "".join(pieces), protected


def restore_message(
    rewritten: str, protected: list[tuple[str, str]]
) -> str | None:
    previous = -1
    for token, _segment in protected:
        if rewritten.count(token) != 1:
            return None
        position = rewritten.find(token)
        if position <= previous:
            return None
        previous = position

    restored = rewritten
    for token, segment in protected:
        restored = restored.replace(token, segment)
    return restored


def read_prompt(user_question: str | None = None) -> str | None:
    configured = setting("PROMPT_FILE")
    custom_prompt = False
    prompt = ""
    if configured:
        try:
            prompt = Path(configured).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            prompt = ""
        custom_prompt = bool(prompt)
    if not prompt:
        style = (setting("STYLE", "") or "").strip().lower()
        prompt_path = TLDR_PROMPT if style == "tldr" else DEFAULT_PROMPT
        try:
            prompt = prompt_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    language = (setting("LANGUAGE", "") or "").strip()
    if language and not custom_prompt:
        prompt += (
            f"\n\nWrite the rewrite in {language} instead, whatever language the assistant "
            f"response uses. Use {language} for all prose, including headings and lists. "
            "Keep code, identifiers, file paths, commands, and quoted output exactly as they are."
        )

    if user_question and user_question.strip():
        context = json.dumps(user_question.strip()[:800], ensure_ascii=False)
        prompt += (
            "\n\nFor context, the user asked the assistant: "
            f"{context}. Use this only to understand the response. Do not rewrite, answer, "
            "or repeat the user's question. Rewrite only the assistant response supplied "
            "separately."
        )
    return prompt or None


def minimum_chars() -> int:
    try:
        value = int(setting("MIN_CHARS", "200") or "200")
    except ValueError:
        return 200
    return max(value, 0)


def prose_length(message: str) -> int:
    masked, protected = protect_message(message)
    for token, _segment in protected:
        masked = masked.replace(token, "")
    return sum(1 for character in masked if not character.isspace())


def timeout_seconds() -> float:
    try:
        value = float(setting("TIMEOUT_SECONDS", "45") or "45")
    except ValueError:
        return 45.0
    return min(max(value, 5.0), 145.0)


def translator_command(output_path: Path, work_dir: Path, prompt: str) -> list[str] | None:
    codex_bin = setting("CODEX_BIN", "codex") or "codex"
    if not shutil.which(codex_bin):
        return None

    effort = (setting("CODEX_EFFORT", "low") or "low").lower()
    if effort not in VALID_EFFORTS:
        effort = "low"

    command = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-C",
        str(work_dir),
        "-c",
        "features.hooks=false",
        "-c",
        f'model_instructions_file="{TRANSLATOR_INSTRUCTIONS}"',
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-o",
        str(output_path),
    ]

    model = (setting("CODEX_MODEL", "") or "").strip()
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    return command


def translate(message: str, user_question: str | None = None) -> str | None:
    if setting("ENABLED") == "0":
        return message or None
    if prose_length(message) < minimum_chars():
        return message or None

    prompt = read_prompt(user_question)
    if not prompt:
        return message or None

    masked_message, protected = protect_message(message)
    if protected:
        prompt += (
            "\n\nThe response contains protected placeholder tokens beginning with "
            "@@SLOPPISH_PROTECTED_SEGMENT. Copy every token exactly once, in the same "
            "order. Do not move, edit, duplicate, or remove a token."
        )

    plugin_data = os.environ.get("PLUGIN_DATA")
    temp_parent: Path | None = None
    if plugin_data:
        try:
            temp_parent = Path(plugin_data)
            temp_parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            temp_parent = None

    try:
        with tempfile.TemporaryDirectory(prefix="rewrite-", dir=temp_parent) as temp_dir:
            work_dir = Path(temp_dir)
            output_path = work_dir / "last-message.txt"
            command = translator_command(output_path, work_dir, prompt)
            if not command:
                return message or None

            child_env = os.environ.copy()
            child_env["SLOPPISH_TRANSLATOR_CHILD"] = "1"
            result = subprocess.run(
                command,
                input=masked_message,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
                timeout=timeout_seconds(),
                check=False,
            )
            if result.returncode != 0:
                return message or None
            try:
                rewritten = output_path.read_text(encoding="utf-8").strip()
            except OSError:
                return message or None
    except (OSError, subprocess.SubprocessError):
        return message or None

    if not rewritten:
        return message or None
    restored = restore_message(rewritten, protected)
    return restored if restored is not None else message
