#!/usr/bin/env python3
"""Dependency-free stdio MCP server for the plain-language translator."""

from __future__ import annotations

import json
import sys
from typing import Any

from translator import translate


SERVER_NAME = "sloppish-translator"
SERVER_VERSION = "0.1.0"
TOOL_NAME = "rewrite_response"


def tool_definition() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "title": "Rewrite response in plain language",
        "description": (
            "Rewrite a completed assistant response in plain language without changing its "
            "facts, code, citations, directives, or decisions. Structured content is protected "
            "mechanically, and any protection failure returns the original draft. Pass the full "
            "draft and the current user request, then return the tool result verbatim."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft": {
                    "type": "string",
                    "description": "The complete assistant response draft.",
                },
                "user_question": {
                    "type": "string",
                    "description": (
                        "The current user request, used only as context for the rewrite."
                    ),
                }
            },
            "required": ["draft"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def response(request_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle(request: dict[str, Any]) -> dict[str, object] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        params = request.get("params")
        requested_protocol = params.get("protocolVersion") if isinstance(params, dict) else None
        protocol = requested_protocol if isinstance(requested_protocol, str) else "2025-06-18"
        return response(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": [tool_definition()]})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
            return error(request_id, -32602, "Unknown tool")
        arguments = params.get("arguments")
        draft = arguments.get("draft") if isinstance(arguments, dict) else None
        user_question = (
            arguments.get("user_question") if isinstance(arguments, dict) else None
        )
        if not isinstance(draft, str) or not draft.strip():
            return error(request_id, -32602, "draft must be a non-empty string")
        if user_question is not None and not isinstance(user_question, str):
            return error(request_id, -32602, "user_question must be a string")

        rewritten = translate(draft, user_question)
        if not rewritten:
            return response(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "The plain-language rewrite failed. Use the original draft.",
                        }
                    ],
                    "isError": True,
                },
            )
        return response(
            request_id,
            {"content": [{"type": "text", "text": rewritten}], "isError": False},
        )
    if request_id is None:
        return None
    return error(request_id, -32601, "Method not found")


def main() -> int:
    for line in sys.stdin:
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("request must be an object")
            payload = handle(value)
        except (json.JSONDecodeError, ValueError) as exc:
            payload = error(None, -32700, str(exc))

        if payload is not None:
            json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
            sys.stdout.write("\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
