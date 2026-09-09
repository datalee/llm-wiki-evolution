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

## Common Naming Conflict Types (lessons from 2026-09-03 governance pass)

When health check reports duplicate concepts, classify the conflict by type before deciding canonical:

### Type 1: Style mismatch (underscore vs hyphen)

Example: `concepts/loop-engineering.md` (hyphen) vs `concepts/Loop_Engineering.md` (underscore). Same concept, different filename convention.

Resolution: pick one style repo-wide (recommend underscore — matches how Obsidian renders `[[Concept_Name]]`). Rename the outlier and create an `aliases/` redirect at the old path.

### Type 2: Case mismatch (PascalCase vs lower vs UPPER)

Example: `CLAUDE.md` (root, all-caps) vs `claude-code` (lowercase slug) referring to the same idea.

Resolution: lowercase is the safest default for filename slugs; PascalCase for H1 display title. Use frontmatter `title:` for display divergence.

### Type 3: Suffix-mismatch (e.g. `_md` vs `.md`)

Example: `[[SOUL.md]]` (wikilink with literal `.md`) vs `[[SOUL_md]]` (underscore-style). The literal `.md` suffix is a write error — wikilinks should never include `.md`.

Resolution: fix the wikilink to drop `.md`. If the file is named `SOUL.md.md` (double suffix from a stub generator bug), rename the file to drop the suffix.

### Type 4: Path vs bare-stem shadowing

Example: `aliases/Skill.md` (bare-name alias) and `sources/2026-07-03-claude-skill-web-clone/SKILL.md` (real source). Health check reports them as duplicate because normalized stems match.

Resolution: this is usually a name conflict, not a content conflict. The alias is a placeholder; the source is the real file. Either (a) make the alias point explicitly to the source (add `> Redirect: [[sources/.../SKILL]]` note), or (b) rename the alias to a distinct stem like `Skill_placeholder.md`.

## Decision Heuristic

When a duplicate group is detected:

1. **Read both files first** — never assume.
2. If both are concept pages: same content → rename outlier; different content (different perspectives) → keep both, add cross-link note at top of each.
3. If one is an alias placeholder and the other is a real page: alias should redirect to the real page, not block duplicate detection.
4. If one is a concept and the other is an entity (product/project), they are NOT duplicates — rename to make the distinction explicit (e.g. `concepts/GraphRAG` vs `entities/Microsoft_GraphRAG`).

**Time budget**: 2-3 file inspections should classify a conflict. If the conflict is unclear after 5 minutes of inspection, ask the user — never guess and rename.
