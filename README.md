# LLM Wiki Evolution

A skill that evolves and governs a Karpathy-style LLM wiki into a reliable
decision-grade knowledge system. Use it to audit an existing wiki, fix
structural issues, and produce a concrete evolution plan with measurable
quality gates.

## What it does

- **Audits** wiki health (unresolved wikilinks, orphan pages, duplicate
  concepts, stale evidence).
- **Governs naming** with a canonical-name policy and alias-to-canonical
  mapping.
- **Adds evidence and decision layers** so high-impact claims carry
  source links, verification dates, and ADR-style decision records.
- **Builds learning tours** for newcomer / researcher / builder personas
  with bidirectional hub links.
- **Enforces quality gates** that fail maintenance runs when unresolved
  links rise or evidence coverage drops below threshold.

## Layout

```
SKILL.md                              # main skill entrypoint
agents/openai.yaml                    # Codex/agent interface metadata
references/
  canonical-naming.md                 # naming policy and mapping format
  governance-checklist.md             # quality gates, evidence & ADR rules
scripts/
  ingest_url_to_wiki.js               # single entrypoint URL -> wiki page
  wiki_health_check.py                # wiki health audit + canonical CSV starter
```

## Quick start

```powershell
# Audit a wiki
python scripts/wiki_health_check.py --root <wiki_root> --out .understand-anything/wiki-health.json

# Generate a canonical-map.csv starter from unresolved links
python scripts/wiki_health_check.py --root <wiki_root> --emit-canonical-template

# Ingest a URL into the wiki
node scripts/ingest_url_to_wiki.js --url "<url>" --wiki-root "<wiki_root>" --tags "#ingest #source"
```

## License

MIT (see `LICENSE`).
