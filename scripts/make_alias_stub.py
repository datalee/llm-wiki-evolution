#!/usr/bin/env python3
"""Generate alias stub files for missing wiki concepts.

A stub is a thin placeholder in `aliases/<name>.md` that:
- Provides a target for unresolved wikilinks
- Documents what the concept should be
- Lists source pages that reference it (auto-discovered via health report)
- Marks itself as #stub #missing-concept for future cleanup

Usage:
    # Single stub
    python make_alias_stub.py --root <wiki> --name Long_Context \\
        --target sources/2026-07-28-graph-engineering-vs-loop

    # Bulk from health report (top N most-broken concepts)
    python make_alias_stub.py --root <wiki> --from-health <health.json> \\
        --min-freq 2 --output-dir aliases/

    # No-source stub (concept referenced but no source to point to)
    python make_alias_stub.py --root <wiki> --name Meta_Agent \\
        --reason "no-source" --tags "#agent #harness"
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]+)?\]\]")
TODAY = "2026-09-03"  # update when re-running


def make_stub(name: str, target: str | None, reason: str, sources: list[str], tags: list[str]) -> str:
    src_block = ""
    if sources:
        listed = "\n".join(f"- [[{s}]]" for s in sources[:5])
        extra = f"\n- ...等共 {len(sources)} 处" if len(sources) > 5 else ""
        src_block = f"\n> Referenced by:\n{listed}{extra}\n"

    if target:
        target_line = f"> 转发到: [[{target}]]\n"
    elif reason == "no-source":
        target_line = "> Status: NO SOURCE — 建议找到对应 source 后替换本 stub。\n"
    else:
        target_line = ""

    tag_block = " ".join(tags) if tags else "#stub #missing-concept"
    return f"""# {name}

> Stub created {TODAY} during wiki governance pass.
> Reason: {reason}
{target_line}{src_block}
> 未来: 创建正式概念页（concepts/{name}.md）或合并到相关概念。

## Tags
- {tag_block}
"""


def normalize_source_for_health(s: str) -> str:
    """Health report sources include .md suffix; wikilinks don't."""
    return s[:-3] if s.endswith(".md") else s


def cmd_single(args):
    root = Path(args.root)
    name = args.name
    target = args.target
    reason = args.reason or ("has-source" if target else "no-source")
    sources = []
    tags = args.tags.split() if args.tags else []

    if args.from_health:
        health = json.loads(Path(args.from_health).read_text(encoding="utf-8"))
        sources = sorted({
            normalize_source_for_health(u["source"])
            for u in health["unresolved"]
            if u["link"] == name
        })

    body = make_stub(name, target, reason, sources, tags)
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.md"
    if out_path.exists() and not args.force:
        print(f"SKIP (exists): {out_path.relative_to(root)}")
        return 1
    out_path.write_text(body, encoding="utf-8")
    print(f"CREATE: {out_path.relative_to(root)}  (sources: {len(sources)})")
    return 0


def cmd_bulk(args):
    root = Path(args.root)
    health_path = Path(args.from_health)
    if not health_path.exists():
        print(f"health report not found: {health_path}")
        return 1
    health = json.loads(health_path.read_text(encoding="utf-8"))

    # Group unresolved by target link
    target_sources = defaultdict(set)
    target_freq = Counter()
    for u in health["unresolved"]:
        lk = u["link"]
        if "/" in lk:
            continue  # skip path-style links (already valid)
        target_freq[lk] += 1
        target_sources[lk].add(normalize_source_for_health(u["source"]))

    # Skip names with bad suffixes (e.g. SOUL.md — likely write error)
    to_stub = [(name, n) for name, n in target_freq.items()
               if n >= args.min_freq and not name.endswith(".md")]

    to_stub.sort(key=lambda x: -x[1])
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Building {len(to_stub)} stubs (freq >= {args.min_freq}) ===")
    created = 0
    for name, n in to_stub:
        out_path = out_dir / f"{name}.md"
        if out_path.exists() and not args.force:
            continue
        sources = sorted(target_sources[name])
        body = make_stub(name, target=None, reason="no-source", sources=sources, tags=[])
        out_path.write_text(body, encoding="utf-8")
        created += 1
        print(f"  CREATE: {out_path.relative_to(root)}  ({n} refs)")
    print(f"\ncreated: {created}  (skipped existing: {len(to_stub) - created})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Generate alias stub files for missing wiki concepts")
    ap.add_argument("--root", required=True, help="Wiki root directory")
    ap.add_argument("--name", help="Single stub name (use with --target or --reason)")
    ap.add_argument("--target", help="Target source path (e.g. sources/2026-...)")
    ap.add_argument("--reason", help="Stub reason (default: has-source or no-source)")
    ap.add_argument("--tags", help="Space-separated tags")
    ap.add_argument("--from-health", dest="from_health", help="Path to health report (wiki-health-v2.json)")
    ap.add_argument("--min-freq", type=int, default=2, help="Bulk: minimum broken-ref frequency")
    ap.add_argument("--output-dir", default="aliases", help="Output directory (default: aliases/)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing stubs")
    args = ap.parse_args()

    if args.name:
        return cmd_single(args)
    elif args.from_health:
        return cmd_bulk(args)
    else:
        ap.error("either --name (single) or --from-health (bulk) is required")


if __name__ == "__main__":
    raise SystemExit(main())
