#!/usr/bin/env python3
"""Dependency-free tests for the prompt hook and translator MCP server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK = PLUGIN_ROOT / "scripts" / "user_prompt_submit.py"
MCP_SERVER = PLUGIN_ROOT / "scripts" / "mcp_server.py"
MCP_CONFIG = PLUGIN_ROOT / ".mcp.json"
FAKE_CODEX = PLUGIN_ROOT / "tests" / "fake_codex.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from translator import (  # noqa: E402
    prose_length,
    protect_message,
    read_prompt,
    restore_message,
    translator_command,
)


class PromptHookTests(unittest.TestCase):
    def run_hook(self, event: object, **overrides: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as plugin_data:
            env = os.environ.copy()
            env.update(
                {
                    "PLUGIN_ROOT": str(PLUGIN_ROOT),
                    "PLUGIN_DATA": plugin_data,
                    "SLOPPISH_CODEX_BIN": str(FAKE_CODEX),
                }
            )
            env.update(overrides)
            result = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps(event),
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_adds_in_turn_rewrite_instruction(self) -> None:
        result = self.run_hook({"prompt": "Explain it"})
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertIn("rewrite_response", str(output["additionalContext"]))
        self.assertIn("user_question", str(output["additionalContext"]))
        self.assertNotIn("decision", result)

    def test_child_marker_does_not_recurse(self) -> None:
        result = self.run_hook(
            {"prompt": "Explain it"},
            SLOPPISH_TRANSLATOR_CHILD="1",
        )
        self.assertEqual(result, {})

    def test_enabled_switch_disables_hook(self) -> None:
        result = self.run_hook(
            {"prompt": "Explain it"},
            SLOPPISH_ENABLED="0",
        )
        self.assertEqual(result, {})

    def test_invalid_input_fails_open(self) -> None:
        result = self.run_hook(["not", "an", "event"])
        self.assertEqual(result, {})


class McpServerTests(unittest.TestCase):
    def run_server(
        self, requests: list[dict[str, object]], **overrides: str
    ) -> list[dict[str, object]]:
        with tempfile.TemporaryDirectory() as plugin_data:
            env = os.environ.copy()
            env.update(
                {
                    "PLUGIN_ROOT": str(PLUGIN_ROOT),
                    "PLUGIN_DATA": plugin_data,
                    "SLOPPISH_CODEX_BIN": str(FAKE_CODEX),
                }
            )
            env.update(overrides)
            source = "\n".join(json.dumps(request) for request in requests) + "\n"
            result = subprocess.run(
                [sys.executable, "-B", str(MCP_SERVER)],
                input=source,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_lists_read_only_rewrite_tool(self) -> None:
        responses = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            ]
        )
        self.assertEqual(
            responses[0]["result"]["serverInfo"]["name"], "sloppish-translator"
        )
        tool = responses[1]["result"]["tools"][0]
        self.assertEqual(tool["name"], "rewrite_response")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("user_question", tool["inputSchema"]["properties"])

    def test_config_registers_only_the_public_server_name(self) -> None:
        config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
        servers = config["mcpServers"]
        self.assertEqual(list(servers), ["sloppish-translator"])

    def test_rewrites_full_draft_with_user_context(self) -> None:
        draft = "This is the original assistant response. " * 10
        with tempfile.TemporaryDirectory() as temp_dir:
            args_file = Path(temp_dir) / "args.json"
            responses = self.run_server(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "rewrite_response",
                            "arguments": {
                                "draft": draft,
                                "user_question": "How does this work?",
                            },
                        },
                    }
                ],
                FAKE_CODEX_OUTPUT="Plain version",
                FAKE_CODEX_ARGS_FILE=str(args_file),
            )
            command = json.loads(args_file.read_text(encoding="utf-8"))
        result = responses[0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], "Plain version")
        self.assertIn("How does this work?", command[-1])

    def test_short_draft_is_returned_without_calling_model(self) -> None:
        responses = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "rewrite_response",
                        "arguments": {
                            "draft": "The implementation is complete.",
                            "user_question": "Is it done?",
                        },
                    },
                }
            ],
            FAKE_CODEX_EXIT="1",
        )
        result = responses[0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(
            result["content"][0]["text"], "The implementation is complete."
        )

    def test_translator_failure_returns_original_draft(self) -> None:
        draft = "This assistant response needs a long plain-language rewrite. " * 10
        responses = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "rewrite_response",
                        "arguments": {"draft": draft},
                    },
                }
            ],
            FAKE_CODEX_EXIT="1",
        )
        result = responses[0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], draft)

    def test_structured_content_never_reaches_rewriter_and_is_restored_exactly(self) -> None:
        memory = (
            "<oai-mem-citation>\n<citation_entries>\n"
            "MEMORY.md:12-14|note=[exact note]\n</citation_entries>\n"
            "<rollout_ids>\n01a00000-0000-7000-8000-000000000000\n"
            "</rollout_ids>\n</oai-mem-citation>"
        )
        directive = (
            '::code-comment{title="Keep this", body="Exact body", '
            'file="/tmp/example.py", start=7 priority=2}'
        )
        fence = "```python\nprint(\"keep exactly\")\n```"
        inline = "`literal_identifier`"
        web_url = "https://example.com/path?q=one"
        codex_link = "codex://plugins/example?mode=share"
        file_target = "/Users/example/My File.md:3"
        draft = (
            "It is important to note that this response contains a long explanation that "
            "needs a plain rewrite while every structured section remains exact. " * 3
            + f"\n\n{fence}\n\nUse {inline} with {web_url}.\n"
            + f"[Open file](<{file_target}>)\n{directive}\n{codex_link}\n\n{memory}"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            stdin_file = Path(temp_dir) / "stdin.txt"
            responses = self.run_server(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "tools/call",
                        "params": {
                            "name": "rewrite_response",
                            "arguments": {"draft": draft},
                        },
                    }
                ],
                FAKE_CODEX_ECHO="1",
                FAKE_CODEX_REPLACE_FROM="It is important to note that ",
                FAKE_CODEX_REPLACE_TO="",
                FAKE_CODEX_STDIN_FILE=str(stdin_file),
            )
            rewriter_input = stdin_file.read_text(encoding="utf-8")

        result = responses[0]["result"]
        self.assertFalse(result["isError"])
        rewritten = result["content"][0]["text"]
        self.assertNotIn("It is important to note that", rewritten)
        for exact_segment in (
            memory,
            directive,
            fence,
            inline,
            web_url,
            codex_link,
            file_target,
        ):
            self.assertIn(exact_segment, rewritten)
            self.assertNotIn(exact_segment, rewriter_input)

    def test_missing_placeholder_returns_complete_original_draft(self) -> None:
        memory = "<oai-mem-citation>exact metadata</oai-mem-citation>"
        draft = ("This long response needs a rewrite. " * 12) + "\n\n" + memory
        responses = self.run_server(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "rewrite_response",
                        "arguments": {"draft": draft},
                    },
                }
            ],
            FAKE_CODEX_OUTPUT="The rewriter dropped the placeholder.",
        )
        result = responses[0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], draft)


class TranslatorCommandTests(unittest.TestCase):
    def test_uses_codex_default_model_with_low_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {"SLOPPISH_CODEX_BIN": str(FAKE_CODEX)},
                clear=False,
            ):
                os.environ.pop("SLOPPISH_CODEX_MODEL", None)
                os.environ.pop("SLOPPISH_CODEX_EFFORT", None)
                command = translator_command(root / "output.txt", root, "Rewrite")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertNotIn("--model", command)

    def test_prose_length_ignores_fenced_code_and_whitespace(self) -> None:
        self.assertEqual(prose_length("A B\n```python\nignored()\n```\nC"), 3)

    def test_protection_rejects_duplicate_or_reordered_placeholders(self) -> None:
        message = "Before `one` middle `two` after"
        masked, protected = protect_message(message)
        self.assertNotIn("`one`", masked)
        self.assertEqual(restore_message(masked, protected), message)

        first = protected[0][0]
        second = protected[1][0]
        self.assertIsNone(restore_message(masked + first, protected))
        swapped = masked.replace(first, "@@TEMP@@", 1)
        swapped = swapped.replace(second, first, 1).replace("@@TEMP@@", second, 1)
        self.assertIsNone(restore_message(swapped, protected))

    def test_prompt_uses_original_plain_language_rules(self) -> None:
        prompt = read_prompt("What changed?")
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertIn("much simpler, plain language", prompt)
        self.assertIn("short sentences and everyday words", prompt)
        self.assertIn("What changed?", prompt)
        self.assertIn("return it unchanged", prompt)

    def test_bad_custom_prompt_path_falls_back_to_default(self) -> None:
        with patch.dict(
            os.environ,
            {"SLOPPISH_PROMPT_FILE": "/path/that/does/not/exist"},
            clear=False,
        ):
            prompt = read_prompt()
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertIn("much simpler, plain language", prompt)


if __name__ == "__main__":
    unittest.main()
