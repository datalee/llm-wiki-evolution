# Canonical Naming Policy

## Rules

- Keep one canonical page per concept.
- Normalize to one style per repo (recommend: `Title Case` in H1, stable filename slug).
- Keep aliases as thin redirect/placeholder pages only.
- Avoid parallel terms unless they are semantically different.

## Mapping Format

Use `canonical-map.csv` with columns:

```csv
alias,canonical,reason
Harness_Engineering,Harness Engineering,underscore variant
claude-code,Claude Code,slug variant
```

## Enforcement

- New page creation must check for existing canonical equivalents first.
- If a new synonym appears, map it to canonical or split concept with explicit distinction.
