# Using sidequest from Claude Code

## Install

```bash
./claude/install.sh
```

That copies two files into `~/.claude/`:

- `skills/sidequest/SKILL.md` — teaches Claude when to delegate, how to pick a model, how to size batches
- `agents/sidequest.md` — a subagent that forwards to the CLI and returns only the summary

Both are plain markdown. Read them before installing; they are short.

## What changes in a session

Without it, "extract the contact email from each of these 4,000 pages" means Claude reads 4,000 pages. Its context fills, it slows down, and you pay premium per-token rates to look at boilerplate.

With it, Claude runs:

```bash
sidequest map --items pages.jsonl --out emails.jsonl \
  --instruction "Extract the contact email. Return field: email." \
  --require email --batch 40 --workers 16
```

and reads four lines back. The 4,000 results are in `emails.jsonl`, ready for the next step.

## Why a subagent and not just a Bash call

Claude can call the CLI directly, and for a small job that is fine. The subagent matters when the job is long or chatty: it runs in its own context, so intermediate output, retries and progress lines never enter your main conversation. It forwards the request, waits, and returns the summary.

This is the same shape as OpenAI's Codex plugin for Claude Code, which uses a `tools: Bash` subagent as a deliberate context firewall. It is the cheapest good idea in that design and it is worth copying.

<a name="why-not-mcp"></a>
## Why not an MCP server?

MCP is a good fit for capabilities an agent should reach for opportunistically — image generation, a scraper, a database query. It is a poor fit for bulk delegation, for four reasons:

**Results land in your context.** An MCP tool call returns into the conversation. That is the entire thing you were trying to avoid: you saved on the model and paid it back in context tokens, at the expensive model's rate.

**No job control.** MCP calls are synchronous request/response inside a turn. A twenty-minute map cannot run. Many MCP servers also carry a fixed timeout — NanoGPT's official MCP server defaults to 120 seconds and forces non-streaming.

**No knowledge layer.** A tool schema cannot teach an agent *which* of 600 models fits a task, or that batch size matters more than model choice. A skill can.

**Tool-surface cost.** A broad MCP server can add dozens of tool definitions to every request. NanoGPT's is 32 tools and roughly 12,000 tokens of schema if loaded eagerly.

None of this makes MCP bad — it makes it the wrong layer for this job. If you want NanoGPT's image, video and scraping tools inside a chat client, install their MCP server; it is good at that. Use `sidequest` for bulk text work.

## Suggested division of labour

| Work | Where |
|---|---|
| Bulk classify / extract / rewrite over many items | `sidequest map` |
| One cheap question mid-task | `sidequest ask` |
| Image, video, audio, scraping | NanoGPT's MCP server |
| Reasoning about your actual code | Claude itself — do not delegate this |

The last row matters. `sidequest` is for volume, not for judgment. Handing a hard design question to a 3B model to save a fraction of a cent is a false economy.
