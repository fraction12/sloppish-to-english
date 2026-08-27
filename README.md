# Sloppish to English

A Codex plugin that rewrites completed assistant responses in plain language before they are shown.

The plugin uses a local MCP tool and a separate `codex exec` call with your existing Codex login. It does not need another API key or a local model.

## Requirements

- Codex CLI, signed in
- Python 3

## Install

Add this repository as a Codex marketplace, then install the plugin:

```shell
codex plugin marketplace add fraction12/sloppish-to-english
codex plugin add sloppish-to-english@sloppish-to-english
```

Start a new Codex task and open `/hooks`. Review and trust the plugin's `UserPromptSubmit` hook. Codex asks again if the hook definition changes.

## How it works

The hook asks the active Codex turn to send its completed draft to the bundled `rewrite_response` tool. The tool starts an ephemeral `codex exec` process in an empty temporary directory with a read-only sandbox. It returns the rewritten response to the active turn.

Drafts with fewer than 200 non-space prose characters skip the extra model call. Missing commands, timeouts, non-zero exits, empty output, and placeholder errors return the original draft unchanged.

Each eligible response makes another Codex call. This adds latency and usage. Codex has no response-replacement hook, so the plugin also depends on the active turn following the hook instruction and calling the tool.

## Content protection

Before the rewrite model sees a draft, the plugin masks:

- Codex metadata blocks and app directives
- fenced and inline code
- HTTP and Codex URLs
- absolute Markdown file targets

The plugin restores those sections byte for byte. If the rewrite removes, duplicates, or reorders a placeholder, the complete original draft is returned. Other prose, including names, numbers, commands outside code formatting, and relative paths, relies on the rewrite model following the prompt.

## Settings

The plugin reads these optional environment variables from the Codex process:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SLOPPISH_ENABLED` | `1` | Set to `0` to skip rewrites. |
| `SLOPPISH_STYLE` | plain language | Set to `tldr` for a summary that targets half the editable prose or less. Protected code and metadata remain exact. |
| `SLOPPISH_CODEX_MODEL` | Codex default | Use a specific model for the rewrite call. |
| `SLOPPISH_CODEX_EFFORT` | `low` | Set the rewrite reasoning effort. |
| `SLOPPISH_TIMEOUT_SECONDS` | `45` | Set the child timeout, capped at 145 seconds. |
| `SLOPPISH_MIN_CHARS` | `200` | Skip the model call below this many non-space prose characters. Fenced code does not count. |
| `SLOPPISH_LANGUAGE` | input language | Force the response language. |
| `SLOPPISH_PROMPT_FILE` | bundled prompt | Use a custom rewrite instruction. |

## Attribution

This project adapts [gvzdv/claudish-to-english](https://github.com/gvzdv/claudish-to-english), an MIT-licensed Claude Code plugin by Mike Gvozdev. This repository contains a separate Codex hook, MCP server, and Python implementation for Codex's turn model.
