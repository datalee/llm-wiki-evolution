#!/usr/bin/env python3
"""Full-site wiki health scanner with Obsidian-style wikilink resolution.

Improves on the original skill script by:
- Preserving '/' as a path token in normalization (so [[concepts/dsh]] matches concepts/dsh.md)
- Falling back to bare-stem match (so [[Meta_Agent]] matches concepts/Meta_Agent.md)
- Detecting true orphans (no incoming link from any page)
- Distinguishing alias/concept/entity/source/synthesis by parent directory

Usage:
    python wiki_health_v2.py [--root PATH]

--root defaults to the wiki path hardcoded below (author's machine); override it
to scan any other Karpathy-style wiki.
"""
import re
import json
import argparse
from pathlib import Path
from collections import defaultdict, Counter

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]+)?\]\]")
CODE_CONTEXT_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
DEFAULT_ROOT = Path(r"C:\Users\Administrator\clawd\memory\wiki")
SKIP_DIRS = {".understand-anything", "raw", "inbox_scripts_today", ".git"}


def mask_code_context(text: str) -> str:
    """Blank out inline-code spans and fenced code blocks before wikilink matching.

    Wikilinks inside code contexts are documentation examples (governance reports,
    tutorials quoting ``[[wikilink]]`` syntax), not live links. Replacement is
    length-preserving and newline-preserving so line numbers stay accurate.
    """
    def _blank(m: "re.Match") -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group())
    return CODE_CONTEXT_RE.sub(_blank, text)


def key(s: str) -> str:
    """Per-segment normalization: lower + drop spaces/underscores/hyphens; tolerate .md suffix.

    Hyphen-stripping bridges kebab-case pages (agent-memory-systems) and
    underscore/PascalCase links ([[Agent_Memory_Systems]]) — both exist in this wiki.
    """
    s = s.strip()
    if s.lower().endswith(".md"):
        s = s[:-3]
    if "/" in s:
        return "/".join(
            p.lower().replace(" ", "").replace("_", "").replace("-", "")
            for p in s.split("/")
        )
    return s.lower().replace(" ", "").replace("_", "").replace("-", "")


def category(p: str) -> str:
    return p.split("/", 1)[0] if "/" in p else "(root)"


def main():
    ap = argparse.ArgumentParser(description="Wiki health scanner (v2, Obsidian-style resolution)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="Wiki root directory")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    pages = []
    for p in root.rglob("*.md"):
        if any(part in SKIP_DIRS or "backup" in part.lower() for part in p.parts):
            continue
        # Exclude backup snapshots (index.bak-*, log.bak-*) — dead copies, not live pages
        if ".bak" in p.name:
            continue
        rel = p.relative_to(root).with_suffix("").as_posix()
        pages.append(rel)

    # Build resolution index: bare-stem-key -> [paths], full-path-key -> path
    by_stem = defaultdict(list)
    by_full = {}
    for p in pages:
        by_full[key(p)] = p
        stem = p.rsplit("/", 1)[-1]
        by_stem[key(stem)].append(p)

    unresolved = []  # (source_page, link, line_no)
    incoming = defaultdict(set)  # target_page -> set of source pages
    wikilink_count = 0

    for p in pages:
        full_path = root / (p + ".md")
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        text = mask_code_context(text)
        for m in WIKILINK_RE.finditer(text):
            lk = m.group(1).strip()
            wikilink_count += 1
            # resolve
            tgt = None
            if lk in [pg for pg in pages]:
                tgt = lk
            elif key(lk) in by_full:
                tgt = by_full[key(lk)]
            elif key(lk) in by_stem:
                tgt = by_stem[key(lk)][0]
            if tgt is None:
                line_no = text[:m.start()].count("\n") + 1
                unresolved.append({"source": p, "link": lk, "line": line_no})
            else:
                incoming[tgt].add(p)

    orphans = sorted([p for p in pages if p not in incoming])

    # Categorize orphans
    by_cat = Counter(category(o) for o in orphans)

    # Categorize unresolved by source category
    unresolved_by_source_cat = Counter(category(u["source"]) for u in unresolved)

    print(f"=== FULL-SITE WIKI HEALTH (Obsidian-style resolution) ===")
    print(f"pages:        {len(pages)}")
    print(f"wikilinks:    {wikilink_count}")
    print(f"unresolved:   {len(unresolved)}")
    print(f"orphans:      {len(orphans)}")
    print()
    print(f"=== orphans by category ===")
    for cat, n in by_cat.most_common():
        print(f"  {cat:20s} {n:4d}")
    print()
    print(f"=== unresolved by source category ===")
    for cat, n in unresolved_by_source_cat.most_common():
        print(f"  {cat:20s} {n:4d}")

    # Save full report
    report = {
        "total_pages": len(pages),
        "total_wikilinks": wikilink_count,
        "unresolved_count": len(unresolved),
        "orphans_count": len(orphans),
        "orphans_by_category": dict(by_cat),
        "unresolved_by_source_category": dict(unresolved_by_source_cat),
        "unresolved": unresolved,
        "orphans": orphans,
    }
    out = root / ".understand-anything" / "wiki-health-v2.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport saved: {out.relative_to(root)}")


if __name__ == "__main__":
    main()
