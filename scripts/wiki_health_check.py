#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
from collections import Counter

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]+)?\]\]")


def norm(s: str) -> str:
    """Normalize a wiki link for matching.

    Preserves '/' as a path separator (each segment normalized independently).
    Within a segment, drops spaces / underscores / dashes to make
    'Agent_Skills' / 'Agent-Skills' / 'agent skills' all equivalent.
    Chinese characters are preserved.
    """
    s = s.strip()
    return "/".join(re.sub(r"[ _\-]+", "", part.lower()) for part in s.split("/"))


def scan(root: Path):
    pages = []
    for p in root.rglob("*.md"):
        if ".understand-anything" in p.parts or "raw" in p.parts:
            continue
        pages.append(p)

    page_norm = {}
    for p in pages:
        # Key 1: full relative path (matches [[concepts/dsh]])
        rel_no_ext = p.relative_to(root).with_suffix("").as_posix()
        rel_key = norm(rel_no_ext)
        page_norm.setdefault(rel_key, []).append(p)
        # Key 2: bare stem (fallback for [[bare_name]] wikilinks).
        # Only add if stem key differs from rel key — otherwise the same file
        # would be stored twice under the same key (root-level files like
        # CLAUDE.md / index.md / log.md have stem == rel_no_ext).
        stem_key = norm(p.stem)
        if stem_key != rel_key:
            page_norm.setdefault(stem_key, []).append(p)

    unresolved = []
    wikilink_total = 0
    duplicates = {k: v for k, v in page_norm.items() if len(v) > 1}
    orphans = set(p.as_posix() for p in pages)

    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        links = [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]
        wikilink_total += len(links)
        rel = p.relative_to(root).as_posix()
        if links:
            orphans.discard(p.as_posix())
        for lk in links:
            n = norm(lk)
            if n not in page_norm:
                unresolved.append({"source": rel, "link": lk})
            else:
                for tgt in page_norm[n]:
                    orphans.discard(tgt.as_posix())

    return {
        "total_pages": len(pages),
        "total_wikilinks": wikilink_total,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "duplicate_concept_groups": {
            k: [p.relative_to(root).as_posix() for p in v] for k, v in duplicates.items()
        },
        "orphan_pages": [Path(p).relative_to(root).as_posix() for p in sorted(orphans)],
    }


def emit_canonical_template(report):
    aliases = Counter(item["link"] for item in report["unresolved"])
    rows = ["alias,canonical,reason"]
    for alias, _ in aliases.most_common():
        rows.append(f"{alias},,unresolved wikilink")
    return "\n".join(rows) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out")
    ap.add_argument("--emit-canonical-template", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    report = scan(root)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.emit_canonical_template:
        print("\n--- canonical-map.csv template ---")
        print(emit_canonical_template(report), end="")


if __name__ == "__main__":
    main()
