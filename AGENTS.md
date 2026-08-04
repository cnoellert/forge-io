# FORGE IO Agent Guide

## Flame Python API knowledge base

For anything touching the Flame Python API (`flame` module, the FORGE bridge,
hooks, Wiretap/backend, Action, publish/export, timeline/editorial), use the
**forge-flame-kb** knowledge base
([cnoellert/forge-flame-kb](https://github.com/cnoellert/forge-flame-kb)) — the
single source of truth, live-verified against a running Flame host and graded by
provenance. Do not answer Flame API questions from memory or generic web docs first.

- **Claude agents:** the `flame-api` skill is installed globally — invoke it.
- **Other agents:** query the `forge-flame-kb` MCP server
  (`flame_kb_search` / `flame_kb_context` / `flame_kb_facets`).
- **Direct:** in a `forge-flame-kb` checkout,
  `python kb/rag/query.py "<question>" -k 6`, or read
  `docs/FLAME_PYTHON_API_DEFINITIVE.md`.

Bias to `[LIVE]` facts; check the Drift Ledger (`--tag DRIFT`) where docs and
reality disagree. Never copy KB content into this repo — reference it, so there
is one source of truth.
