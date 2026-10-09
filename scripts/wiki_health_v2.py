#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prefix-aware wiki health check (v2 semantics, reconstructed 2026-09-16).

Reconstructed after the original wiki_health_v2.py in this directory was
accidentally overwritten. Semantics pinned by evidence and cross-checked
against the 2026-09-16 08:57 baseline (wiki-health-v2.json) and the fix log
in synthesis/2026-09-05-harness-ingest-and-resolver-hardening:

  - wikilink [[dir/Name]] resolves ONLY against <root>/<dir>/Name.md
    (no cross-directory fallback); bare [[Name]] resolves against any stem
  - link key tries BOTH norm(basename) and norm(basename-without-.md)
    (the .md-suffix tolerance from the 09-05 fix log; handles pages whose
    own stem ends in .md, e.g. aliases/SOUL.md.md)
  - fenced code blocks and inline-backtick spans are skipped (example links
    in docs must not count)
  - unresolved entries: {source, link, line}; source stored without .md
  - orphans: pages with ZERO incoming resolved links (outgoing links do
    not matter); stored relative without .md
  - excluded: .understand-anything/, raw/, ingest-failed-backup-*/, *.bak*.md

Validated 2026-09-16 vs baseline: unresolved sets IDENTICAL (101, 0 added /
0 removed); orphans 208 -> 205 with 3 known residuals (concepts/
Three_World_Models legitimately rescued by a new stub link; aliases/CLAUDE
and sources/2026-07-03-claude-skill-web-clone/SKILL are duplicate-stem
marking-priority differences in the original, direction: 2 pages counted
non-orphan here that the original counted orphan).
"""
import argparse
import json
import re
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]+)?\]\]")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
FENCE_RE = re.compile(r"^\s*```")

EXCLUDED_DIR_PARTS = {".understand-anything", "raw"}
EXCLUDED_DIR_RE = re.compile(r"^ingest-failed-backup")


def norm(s):
    s = s.strip().lower()
    s = re.sub(r"[ _\-]+", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s


def strip_md(name):
    return name[:-3] if name.lower().endswith(".md") else name


def included(p):
    for part in p.parts:
        if part in EXCLUDED_DIR_PARTS or EXCLUDED_DIR_RE.match(part):
            return False
    if ".bak" in p.name:
        return False
    return True


def strip_inline_code(line):
    return INLINE_CODE_RE.sub("", line)


def extract_links(text):
    """Wikilinks outside fenced blocks and inline backtick spans."""
    links = []
    in_fence = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in WIKILINK_RE.finditer(strip_inline_code(raw)):
            links.append((lineno, m.group(1).strip()))
    return links


def category(rel):
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else "(root)"


def count_by_category(rels):
    out = {}
    for r in rels:
        c = category(r)
        out[c] = out.get(c, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def scan(root):
    pages = [p for p in root.rglob("*.md") if included(p)]

    stem_index = {}
    for p in pages:
        stem_index.setdefault(norm(p.stem), []).append(p)
    dir_index = {}
    for p in pages:
        d = p.parent.relative_to(root).as_posix()
        dir_index.setdefault(d, {}).setdefault(norm(p.stem), []).append(p)
    duplicates = {k: v for k, v in stem_index.items() if len(v) > 1}

    unresolved = []
    wikilink_total = 0
    linked_targets = set()

    for p in pages:
        rel = strip_md(p.relative_to(root).as_posix())
        text = p.read_text(encoding="utf-8", errors="replace")
        for lineno, lk in extract_links(text):
            wikilink_total += 1
            segs = lk.split("/")
            basename = segs[-1]
            dir_part = "/".join(segs[:-1]) if len(segs) > 1 else None
            candidates = [norm(basename)]
            stripped = norm(strip_md(basename))
            if stripped != candidates[0]:
                candidates.append(stripped)
            resolved = []
            for key in candidates:
                if dir_part is not None:
                    resolved = dir_index.get(dir_part, {}).get(key, [])
                else:
                    resolved = stem_index.get(key, [])
                if resolved:
                    break
            if resolved:
                for tgt in resolved:
                    linked_targets.add(strip_md(tgt.relative_to(root).as_posix()))
            else:
                unresolved.append({"source": rel, "link": lk, "line": lineno})

    orphans = sorted(
        strip_md(p.relative_to(root).as_posix()) for p in pages
        if strip_md(p.relative_to(root).as_posix()) not in linked_targets
    )

    return {
        "total_pages": len(pages),
        "total_wikilinks": wikilink_total,
        "unresolved_count": len(unresolved),
        "orphans_count": len(orphans),
        "unresolved_by_source_category": count_by_category(
            [u["source"] for u in unresolved]),
        "orphans_by_category": count_by_category(orphans),
        "unresolved": unresolved,
        "orphans": orphans,
        "duplicate_concept_groups": {
            k: [p.relative_to(root).as_posix() for p in v]
            for k, v in duplicates.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    report = scan(root)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("written: %s" % out)
    print("pages=%d links=%d unresolved=%d orphans=%d" % (
        report["total_pages"], report["total_wikilinks"],
        report["unresolved_count"], report["orphans_count"]))


if __name__ == "__main__":
    main()
