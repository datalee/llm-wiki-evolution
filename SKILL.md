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

0. Standardize ingestion entrypoints:
- Use one stable ingestion script entrypoint from `scripts/ingest_url_to_wiki.js` inside this skill package.
- Do not generate one-off `fetch_xxx.js` scripts per URL.
- Require idempotent naming or URL-hash based dedup to avoid duplicate source pages.
- For `mp.weixin.qq.com` article URLs, prefer the dedicated `wechat-http-to-llm-wiki` skill instead of the generic ingest script. That flow uses direct HTTP extraction tuned for `activity-name` and `js_content`.

2. Apply naming governance:
- Pick canonical concept names.
- Convert alternates into alias pages.
- Use [canonical-naming.md](references/canonical-naming.md) rules.

3. Add evidence and decision layers:
- For high-impact claims, add source links and `last_verified_at`.
- For architecture/process choices, add ADR entries.
- Use [governance-checklist.md](references/governance-checklist.md).

4. Strengthen learning and navigation:
- Build pathway tours for newcomer, researcher, and builder personas.
- Ensure each critical hub page has bidirectional links.

5. Enforce quality gates:
- Run health check before/after edits.
- Fail maintenance runs when unresolved links rise or evidence coverage drops below threshold.

## Outputs

Produce these artifacts when requested:
- `wiki-evolution-plan.md`: prioritized roadmap (30/60/90 day).
- `canonical-map.csv`: alias -> canonical mapping.
- `evidence-gap-report.md`: claims missing source or stale verification.
- `quality-gates.json`: thresholds and fail conditions.

## Commands

Run health audit:
```powershell
python scripts/wiki_health_check.py --root <wiki_root> --out <wiki_root>/.understand-anything/wiki-health.json
```

Generate canonical mapping starter from unresolved links:
```powershell
python scripts/wiki_health_check.py --root <wiki_root> --emit-canonical-template
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
