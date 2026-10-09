---
name: llm-wiki-evolution
description: Evolve and govern a Karpathy-style LLM wiki into a reliable decision-grade knowledge system. Use when users ask to improve wiki structure, naming canon, evidence traceability, quality gates, learning tours, ADR/decision records, or long-term knowledge graph maintenance.
---

# LLM Wiki Evolution

Upgrade an LLM wiki from article collection to governed knowledge system with repeatable audits, canonical naming, evidence links, and CI-style quality gates.

## Workflow

1. Detect scope and maturity:
- Count unresolved wikilinks, aliases, orphan pages, and duplicate concepts.
- Classify maturity with `scripts/wiki_health_check.py`.
- For Obsidian-style wikilink resolution (handles `[[concepts/dsh]]` and bare-name fallback), prefer `scripts/wiki_health_v2.py` — it matches the actual wiki engine behavior more closely and is the recommended scanner for medium/large wikis (>500 pages).

0. Standardize ingestion entrypoints:
- Use one stable ingestion script entrypoint from `scripts/ingest_url_to_wiki.js` inside this skill package.
- Do not generate one-off `fetch_xxx.js` scripts per URL.
- Require idempotent naming or URL-hash based dedup to avoid duplicate source pages.
- For `mp.weixin.qq.com` article URLs, prefer the dedicated `wechat-http-to-llm-wiki` skill instead of the generic ingest script. That flow uses direct HTTP extraction tuned for `activity-name` and `js_content`.
- **After every ingestion**, run `scripts/auto_link_source.py` (LLM-based concept extraction) on the new source. Requires `GLM_API_KEY` env var (BigModel/Zhipu) — see the script header for env setup. Without LLM access, manually cross-link the source to its top 3-5 most-related concept/entity pages.

2. Apply naming governance:
- Pick canonical concept names.
- Convert alternates into alias pages.
- Use [canonical-naming.md](references/canonical-naming.md) rules — including the 4 common naming conflict types (style/case/suffix/dot-extension).
- For missing concepts that block resolution, generate placeholder stubs with `scripts/make_alias_stub.py` (single mode) or bulk from a health report (`--from-health`).

3. Add evidence and decision layers:
- For high-impact claims, add source links and `last_verified_at`.
- For architecture/process choices, add ADR entries.
- Use [governance-checklist.md](references/governance-checklist.md).

4. Strengthen learning and navigation:
- Build pathway tours for newcomer, researcher, and builder personas.
- Ensure each critical hub page has bidirectional links.
- For orphan concepts, use a temporary aggregation page in `synthesis/<date>-orphan-backlinks.md` (cross-link all orphans with `[[orphan_name]]`). Promote to formal hub pages (e.g. `concepts/Skill_Cases.md`) when 3+ orphans cluster on a topic.

5. Enforce quality gates:
- Run health check before/after edits.
- Fail maintenance runs when unresolved links rise or evidence coverage drops below threshold.
- After any change, run `wiki_health_v2.py` and diff against the previous baseline — accept the change only if unresolved/orphan counts do not regress.

## Outputs

Produce these artifacts when requested:
- `wiki-evolution-plan.md`: prioritized roadmap (30/60/90 day).
- `canonical-map.csv`: alias -> canonical mapping.
- `evidence-gap-report.md`: claims missing source or stale verification.
- `quality-gates.json`: thresholds and fail conditions.

## Commands

Run health audit (basic — exact match + stem key):
```powershell
python scripts/wiki_health_check.py --root <wiki_root> --out <wiki_root>/.understand-anything/wiki-health.json
```

Run health audit (Obsidian-style fallback, recommended for medium/large wikis):
```powershell
python scripts/wiki_health_v2.py --root <wiki_root> --out <wiki_root>/.understand-anything/wiki-health-v2.json
```

`wiki_health_v2.py` is the **canonical** health semantics; `wiki_health_check.py` (v1)
is a bare-stem variant kept only for generating a `canonical-map.csv` starter.
Diff a change against the previous baseline before accepting it:
```powershell
python scripts/diff_final_vs_baseline.py   # edit line 6 to point at the new report
```

### Querying a wiki at runtime: `wiki-mcp`

`scripts/mcp_server.py` is a zero-dependency stdio MCP server exposing the wiki
as tools. Copy it to `<wiki_root>/.understand-anything/mcp_server.py` and register
it (Minimax Code: `mcp create`). Five tools:

| Tool | Purpose |
| --- | --- |
| `wiki_search` | scored title/body search; 6-hex id and `YYYY-MM-DD` both match file names |
| `wiki_read` | read one page. `--lines "40-60"` for 1-based inclusive LINE ranges; `offset`/`max_chars` for char paging. `meta.cite_as` gives `ref#L40-60` |
| `wiki_evidence` | provenance + outbound URLs + cited pages + 关联/来源/防裸报清单 sections with line numbers — answers "where did this come from" (backlinks give the inbound direction only) |
| `wiki_pack` | assemble several pages under a token budget, priority order, with per-page `cite_as` and a `dropped` list |
| `wiki_backlinks` | pages whose wikilinks resolve to the target |

Run the suite before trusting a change (it spawns its own server, so it always
tests current code):
```powershell
python scripts/test_wiki_mcp.py
```

**Line numbers index the page's markdown body** (frontmatter stripped). The wiki
stores structured summaries rather than full source text, so a line citation
points at the wiki page's own wording — follow `provenance.source_url` for the
original document. See `references/INGEST-SPEC.md` §7.

Auto-link a new source to existing concepts (LLM-based, requires GLM_API_KEY):
```powershell
# Dry-run (prints plan, no file changes)
python scripts/auto_link_source.py --wiki <wiki_root> --source <path/to/new.md>

# Apply (injects backlink section to source + reverse link to each matched concept)
python scripts/auto_link_source.py --wiki <wiki_root> --source <path/to/new.md> --apply
```

Generate canonical mapping starter from unresolved links:
```powershell
python scripts/wiki_health_check.py --root <wiki_root> --emit-canonical-template
```

Generate alias stub for a single missing concept:
```powershell
python scripts/make_alias_stub.py --root <wiki_root> --name <Concept> --target <source_path>
```

Bulk-generate stubs from health report (min broken-ref frequency):
```powershell
python scripts/make_alias_stub.py --root <wiki_root> --from-health <wiki_root>/.understand-anything/wiki-health-v2.json --min-freq 2
```

Ingest a new URL with fixed script entrypoint (run from the skill directory):
```powershell
node scripts/ingest_url_to_wiki.js --url "<url>" --wiki-root "<wiki_root>" --tags "#ingest #source"
```

WeChat extraction note:
- Prefer `msg_title` metadata over `<title>` to avoid `Untitled`.
- Preserve UTF-8 compatibility for Windows toolchains when writing markdown.
- When the source is a public WeChat article, the preferred path is the dedicated `wechat-http-to-llm-wiki` skill rather than this generic script.

## References

- Canonical naming policy: [canonical-naming.md](references/canonical-naming.md)
- Governance and quality checklist: [governance-checklist.md](references/governance-checklist.md)
