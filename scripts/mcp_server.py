#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wiki-mcp: zero-dependency stdio MCP server for the local LLM wiki.

Tools:
  wiki_search(query, limit, include_drafts, dir) - title/full-text search
  wiki_read(ref, offset, max_chars)              - read one page
  wiki_backlinks(ref, limit)                     - pages linking to a page

Transport: newline-delimited JSON-RPC 2.0 on stdio (MCP stdio spec).
Log goes to stderr only; stdout carries protocol frames exclusively.

Link/name resolution conventions are copied from wiki_health_v2.py
(WIKILINK_RE, norm(), exclusion rules) so results stay consistent with the
health checker.

Draft policy: ~1/3 of the library is marked `status: draft` in frontmatter.
wiki_search includes drafts by default and flags each hit with draft=true;
set include_drafts=false to hide them (team-edition default later).

All file reads are UTF-8 with errors="replace" so mixed-encoding bytes never
crash the server.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

WIKI_ROOT = Path(os.environ.get(
    "WIKI_MCP_ROOT", r"C:\Users\Administrator\clawd\memory\wiki"
)).resolve()

INDEX_TTL_SECONDS = 60

# ---- conventions shared with wiki_health_v2.py ----------------------------

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]+)?\]\]")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
FENCE_RE = re.compile(r"^\s*```")

EXCLUDED_DIR_PARTS = {".understand-anything", "raw", ".stfolder", ".git"}
EXCLUDED_DIR_RE = re.compile(r"^ingest-failed-backup")
EXCLUDED_STEM_PART = ".bak"          # matches wiki_health_v2: ".bak" in name
EXCLUDED_ROOT_FILES = {"index.md", "log.md"}  # navigation ledgers, not content

DIR_PRIORITY = {"entities": 0, "concepts": 1, "synthesis": 2,
                "aliases": 3, "sources": 4}


def log(msg: str) -> None:
    print(f"[wiki-mcp] {msg}", file=sys.stderr, flush=True)


def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[ _\-]+", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s


def included(p: Path) -> bool:
    try:
        rel_parts = p.relative_to(WIKI_ROOT).parts
    except ValueError:
        return False
    for part in rel_parts[:-1]:
        if part in EXCLUDED_DIR_PARTS or EXCLUDED_DIR_RE.match(part):
            return False
    if EXCLUDED_STEM_PART in p.name:
        return False
    if p.parent == WIKI_ROOT and p.name.lower() in EXCLUDED_ROOT_FILES:
        return False
    return True


def read_text(p: Path) -> str:
    # utf-8 (not utf-8-sig) keeps U+FEFF in the string; some pages carry one
    # or two BOMs, which then breaks frontmatter parsing (`text.startswith`).
    # A leading BOM is an encoding artifact, never content -> strip it here,
    # the single read path every caller goes through.
    return p.read_text(encoding="utf-8", errors="replace").lstrip("﻿")


def strip_md(name: str) -> str:
    return name[:-3] if name.lower().endswith(".md") else name


def rel_ref_of(p: Path) -> str:
    rel = p.resolve().relative_to(WIKI_ROOT).as_posix()
    return strip_md(rel)


# ---- page model -----------------------------------------------------------

FM_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
STUB_MARK = "Alias placeholder generated to resolve wikilinks"


def parse_frontmatter(text: str):
    """Return (fm_dict, fm_start_line, fm_end_line) or ({}, None, None).

    Frontmatter may sit after the H1 title line (ingest style), so callers
    must skip the [start, end] line span when hunting for the H1.
    """
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines[:60]):
        if line.strip() == "---":
            start = i
            break
    if start is None:
        return {}, None, None
    for j in range(start + 1, min(len(lines), start + 120)):
        if lines[j].strip() == "---":
            end = j
            break
    if end is None:
        return {}, None, None
    fm = {}
    for line in lines[start + 1:end]:
        m = FM_KEY_RE.match(line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm, start, end


class Page:
    __slots__ = ("path", "ref", "dir", "stem", "nstem", "text", "text_lower",
                 "body", "title", "fm", "is_draft", "is_stub")

    def __init__(self, path: Path, text: str):
        rel = path.relative_to(WIKI_ROOT).as_posix()
        self.path = path
        self.ref = strip_md(rel)
        self.dir = rel.split("/")[0] if "/" in rel else "(root)"
        self.stem = path.stem
        self.nstem = norm(self.stem)
        self.text = text
        self.text_lower = text.lower()
        self.fm, fm_start, fm_end = parse_frontmatter(text)
        lines = text.splitlines()
        self.body = "\n".join(lines[fm_end + 1:]) if fm_end is not None else text
        title = self.fm.get("title", "")
        if not title:
            # first H1 outside the frontmatter block (H1 often precedes it)
            for i, line in enumerate(lines):
                if fm_start is not None and fm_start <= i <= fm_end:
                    continue
                m = H1_RE.match(line)
                if m:
                    title = m.group(1).strip()
                    break
        self.title = title or self.stem
        self.is_draft = self.fm.get("status", "").strip().lower() == "draft"
        self.is_stub = STUB_MARK in text


# ---- index ----------------------------------------------------------------

_cache = {"at": 0.0, "pages": [], "by_ref": {}, "alias": {}, "alias_norm": {},
          "nstem_index": {}}


def load_alias_map() -> dict:
    amap = {}
    p = WIKI_ROOT / "canonical-map.csv"
    if p.is_file():
        for i, row in enumerate(csv.reader(read_text(p).splitlines())):
            if not row or len(row) < 2:
                continue
            if i == 0 and row[0].strip().lower() == "alias":
                continue
            alias, canonical = row[0].strip(), row[1].strip()
            if alias and canonical:
                amap[alias] = canonical
    return amap


def get_index(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["pages"] and now - _cache["at"] < INDEX_TTL_SECONDS:
        return _cache
    pages = []
    for p in WIKI_ROOT.rglob("*.md"):
        if not included(p):
            continue
        try:
            pages.append(Page(p, read_text(p)))
        except OSError as e:
            log(f"skip unreadable {p}: {e}")
    _cache["pages"] = pages
    _cache["by_ref"] = {pg.ref: pg for pg in pages}
    _cache["alias"] = amap = load_alias_map()
    _cache["alias_norm"] = {norm(k): v for k, v in amap.items()}
    nstem_index: dict = {}
    for pg in pages:
        nstem_index.setdefault(pg.nstem, []).append(pg.ref)
    _cache["nstem_index"] = nstem_index
    _cache["at"] = now
    log(f"index built: {len(pages)} pages, {len(amap)} aliases")
    return _cache


# ---- ref resolution -------------------------------------------------------

class RefError(ValueError):
    pass


def _page_from_disk_path(p: Path, idx: dict):
    try:
        ref = rel_ref_of(p)
    except ValueError:
        return None  # outside wiki root
    pg = idx["by_ref"].get(ref)
    if pg:
        return pg
    try:  # exists on disk but excluded from index (e.g. index.md) - read ad hoc
        return Page(p, read_text(p))
    except OSError:
        return None


def resolve_ref(ref: str, visited=None, idx=None) -> Page:
    idx = idx or get_index()
    visited = visited if visited is not None else set()
    r = str(ref).strip().strip("/")
    if not r:
        raise RefError("empty ref")

    # 0) absolute path, must stay inside the wiki root
    p_abs = Path(r)
    if p_abs.is_absolute():
        if p_abs.is_file():
            pg = _page_from_disk_path(p_abs, idx)
            if pg:
                return pg
        raise RefError(f"file not found inside wiki root: {ref}")

    # 1) exact relative path (with or without .md)
    for cand in (r, r + ".md"):
        p = WIKI_ROOT / cand
        if p.is_file():
            pg = _page_from_disk_path(p, idx)
            if pg:
                return pg

    # 2) alias map (exact, then normalized), cycle-guarded
    target = idx["alias"].get(r) or idx["alias_norm"].get(norm(r))
    if target and target != r and target not in visited:
        visited.add(target)
        try:
            return resolve_ref(target, visited=visited, idx=idx)
        except RefError:
            pass  # stale alias row: fall through to structural resolution

    # 3) 6-hex page id suffix (ingest naming convention)
    if re.fullmatch(r"[0-9a-fA-F]{6}", r):
        hits = [pg for pg in idx["pages"]
                if pg.stem.endswith("-" + r.lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise RefError(
                f"ambiguous id6 {r}: " + ", ".join(pg.ref for pg in hits))

    # 4) bare name == normalized stem (health-check rule: matches any dir)
    nr = norm(r)
    hits = [pg for pg in idx["pages"] if pg.nstem == nr]
    if hits:
        hits.sort(key=lambda pg: (DIR_PRIORITY.get(pg.dir, 9), pg.ref))
        return hits[0]

    # 5) unique substring fallback
    sub = [pg for pg in idx["pages"] if nr and nr in pg.nstem]
    if len(sub) == 1:
        return sub[0]
    if len(sub) > 1:
        sub.sort(key=lambda pg: (len(pg.nstem), pg.ref))
        raise RefError("ambiguous ref %r, candidates: %s"
                       % (ref, ", ".join(pg.ref for pg in sub[:8])))
    raise RefError(f"ref not found: {ref}")


# ---- links ----------------------------------------------------------------

def extract_links(text: str):
    """Wikilinks outside fenced blocks and inline backtick spans
    (same semantics as wiki_health_v2.extract_links)."""
    links = []
    in_fence = False
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in WIKILINK_RE.finditer(INLINE_CODE_RE.sub("", raw)):
            links.append((lineno, m.group(1).strip()))
    return links


def resolve_link_to_refs(link: str, idx: dict) -> set:
    """All page refs a wikilink could point to (deliberately wide:
    bare names matching several dirs all count, same as the health checker)."""
    refs: set = set()
    l = link.strip()
    if not l:
        return refs
    names = [l]
    canon = idx["alias"].get(l) or idx["alias_norm"].get(norm(l))
    if canon and canon != l:
        names.append(canon)
    for name in names:
        n = name.strip().strip("/")
        if not n:
            continue
        for cand in (n, n + ".md"):
            p = WIKI_ROOT / cand
            if p.is_file():
                try:
                    refs.add(rel_ref_of(p))
                except ValueError:
                    pass
                break
        for ref in idx["nstem_index"].get(norm(n), ()):
            refs.add(ref)
    return refs


# ---- tools ----------------------------------------------------------------

def _snippet(line: str, width: int = 160) -> str:
    if not line:
        return ""
    line = line.strip()
    return line if len(line) <= width else line[:width] + "..."


def _line_at(text: str, pos: int) -> str:
    nl = text.rfind("\n", 0, pos)
    end = text.find("\n", pos)
    return text[nl + 1:end if end >= 0 else len(text)]


_LATIN_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or",
    "is", "are", "with", "at", "by", "vs", "as", "be",
}


def _latin_subphrases(terms, min_len=2, max_count=12):
    """Contiguous Latin sub-phrases (len >= min_len) of the query, longest
    first. Only maximal runs of adjacent Latin terms are considered, so a
    query like ["openai", "发的那篇", "context", "graph", "文章"] yields
    ["context", "graph"] but never mixes CJK terms into a phrase."""
    run, runs = [], []
    for t in terms:
        nt = norm(t)
        if nt and re.fullmatch(r"[a-z0-9]+", nt) and nt not in _LATIN_STOPWORDS:
            run.append(nt)
        else:
            if len(run) >= min_len:
                runs.append(run)
            run = []
    if len(run) >= min_len:
        runs.append(run)
    subs = []
    for r in runs:
        n = len(r)
        for i in range(n):
            for j in range(i + min_len, n + 1):
                subs.append(r[i:j])
    subs.sort(key=len, reverse=True)
    return subs[:max_count]


def tool_search(query, limit=10, include_drafts=True, dir_filter="all") -> str:
    idx = get_index()
    q = str(query).strip()
    if not q:
        raise RefError("empty query")
    limit = max(1, min(int(limit), 50))
    nq = norm(q)
    terms = [t for t in re.split(r"[\s,，、;；/]+", q) if t]
    date_hits = re.findall(r"\d{4}-\d{2}-\d{2}", q)
    id6 = re.search(r"\b[0-9a-fA-F]{6}\b", q)
    subphrases = _latin_subphrases(terms)

    scored = []
    drafts_hidden = 0
    for pg in idx["pages"]:
        if dir_filter != "all" and pg.dir != dir_filter:
            continue
        if not include_drafts and pg.is_draft:
            drafts_hidden += 1
            continue
        s = 0
        title_n = norm(pg.title)
        first_line = None
        if nq:
            if nq == pg.nstem or (title_n and nq == title_n):
                s += 120
            elif nq in pg.nstem or (title_n and nq in title_n):
                s += 70
        hit_text = 0
        for t in terms:
            nt = norm(t)
            if not nt:
                continue
            if nt in pg.nstem or nt in title_n:
                s += 25
            pos = pg.text_lower.find(nt)
            if pos >= 0:
                hit_text += 1
                if first_line is None:
                    first_line = _line_at(pg.text, pos)
        if terms:
            s += int(40 * hit_text / len(terms))
        # multi-term coverage bonus: every term present in name/title beats
        # pages that only match one term each ("context graph" intent)
        if len(terms) > 1:
            covered = sum(1 for t in terms
                          if (norm(t) in pg.nstem) or (norm(t) and norm(t) in title_n))
            if covered == len(terms):
                s += 30
        # phrase bonus: strongest intent signal. Either the whole normalized
        # query appears verbatim (covers CamelCase/concatenated forms like
        # ContextGraph) or the multi-word query appears as-is in the text
        # (covers "context graph" written with a space). When the query adds
        # words around the key phrase ("Context Graph openai", Chinese
        # qualifiers around a Latin phrase), the whole-query checks miss —
        # fall back to contiguous Latin sub-phrases of the query.
        q_flat = re.sub(r"\s+", " ", q).lower()
        phrase_hit = bool(nq) and nq in pg.text_lower
        if not phrase_hit and len(terms) > 1 and q_flat in pg.text_lower:
            phrase_hit = True
        if not phrase_hit:
            for sub in subphrases:
                flat = " ".join(sub)
                fused = "".join(sub)
                if (flat in pg.text_lower or fused in pg.text_lower
                        or fused in pg.nstem
                        or (title_n and fused in title_n)):
                    phrase_hit = True
                    break
        if phrase_hit:
            s += 50
        for d in date_hits:
            if d in pg.stem:
                s += 60
                if first_line is None:
                    first_line = pg.body.splitlines()[0] if pg.body else ""
        if id6 and pg.stem.endswith("-" + id6.group(1).lower()):
            s += 80
            if first_line is None:
                first_line = pg.body.splitlines()[0] if pg.body else ""
        if s <= 0:
            continue
        if pg.is_stub:
            s = int(s * 0.2)  # alias stubs are placeholders, push them down
        scored.append((s, pg, first_line))

    scored.sort(key=lambda e: (-e[0], e[1].ref))
    results = [{
        "score": s,
        "ref": pg.ref,
        "title": pg.title,
        "dir": pg.dir,
        "draft": pg.is_draft,
        "snippet": _snippet(first_line),
    } for s, pg, first_line in scored[:limit]]
    return json.dumps({
        "query": q,
        "total_hits": len(scored),
        "drafts_hidden": drafts_hidden,
        "results": results,
    }, ensure_ascii=False, indent=2)


_LINE_SPEC = None  # placeholder to keep diffs small


def _parse_line_spec(spec):
    """'12' | '12-40' | '12-' | '5-10,20-30' -> [(start,end|None), ...]
    Returns [] for empty/None. 1-based inclusive, end None = to EOF."""
    if not spec:
        return []
    out = []
    for chunk in re.split(r"[,\s]+", str(spec).strip()):
        if not chunk:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d*)", chunk)
        if not m:
            raise ValueError("bad line spec: %r" % chunk)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else None
        if start < 1:
            raise ValueError("line numbers are 1-based")
        if end is not None and end < start:
            raise ValueError("line range end < start: %r" % chunk)
        out.append((start, end))
    return out


def _slice_lines(body, ranges):
    """Slice body text by 1-based inclusive line ranges.

    Line numbers index the page BODY (frontmatter already stripped) -- i.e.
    line 1 is the first markdown line of the file's content. The wiki keeps
    structured summaries rather than full source text, so a line reference
    points at the wiki page's own wording, not at the original document.
    """
    lines = body.splitlines()
    n = len(lines)
    picked, kept = [], set()
    for start, end in ranges:
        last = n if end is None else min(end, n)
        for ln in range(start, last + 1):
            if ln <= n and ln not in kept:
                kept.add(ln)
                picked.append(ln)
    picked.sort()
    text = "\n".join(lines[ln - 1] for ln in picked)
    return text, (picked[0] if picked else None), (picked[-1] if picked else None), n


def _line_of_offset(body, char_offset):
    """1-based line number containing a char offset (for citing char pages)."""
    if char_offset <= 0:
        return 1
    return body.count("\n", 0, char_offset) + 1


def _est_tokens(text):
    """Rough token estimate: CJK/fullwidth ~1 token per char, ASCII ~1 per 4."""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F
                or 0xFF00 <= o <= 0xFFEF or 0x3040 <= o <= 0x30FF):
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


_URL_RE = re.compile(r"https?://[^\s\)\]\|<>\"']+")
_EVIDENCE_HEADS = ("## 关联", "## 相关来源", "## 防裸报清单", "## 来源")


def tool_read(ref, offset=0, max_chars=8000, lines=None) -> str:
    idx = get_index()
    pg = resolve_ref(ref, idx=idx)
    body = pg.body
    total = len(body)
    ranges = _parse_line_spec(lines)
    if ranges:
        part, ls, le, total_lines = _slice_lines(body, ranges)
        meta = {
            "ref": pg.ref,
            "title": pg.title,
            "dir": pg.dir,
            "draft": pg.is_draft,
            "line_mode": True,
            "body_line_start": ls,
            "body_line_end": le,
            "body_total_lines": total_lines,
            "body_total_chars": total,
            "truncated": False,
            "cite_as": "%s#L%s-%s" % (pg.ref, ls, le) if ls else pg.ref,
        }
        for k in ("status", "source_url", "source_type", "captured_at"):
            if k in pg.fm:
                meta[k] = pg.fm[k]
        return json.dumps({"meta": meta, "body": part},
                          ensure_ascii=False, indent=2)

    offset = max(0, int(offset))
    max_chars = max(200, min(int(max_chars), 50000))
    part = body[offset:offset + max_chars]
    truncated = offset + max_chars < total
    meta = {
        "ref": pg.ref,
        "title": pg.title,
        "dir": pg.dir,
        "draft": pg.is_draft,
        "line_mode": False,
        "body_offset": offset,
        "body_line_start": _line_of_offset(body, offset),
        "body_total_chars": total,
        "body_total_lines": body.count("\n") + 1,
        "truncated": truncated,
        "cite_as": "%s#L%d" % (pg.ref, _line_of_offset(body, offset)),
    }
    if truncated:
        meta["next_offset"] = offset + max_chars
    for k in ("status", "source_url", "source_type", "captured_at"):
        if k in pg.fm:
            meta[k] = pg.fm[k]
    return json.dumps({"meta": meta, "body": part},
                      ensure_ascii=False, indent=2)


def tool_backlinks(ref, limit=50) -> str:
    idx = get_index()
    limit = max(1, min(int(limit), 200))
    target = resolve_ref(ref, idx=idx)
    pages_with = []
    total_links = 0
    for pg in idx["pages"]:
        if pg.ref == target.ref:
            continue
        hits = [{"line": lineno, "link": f"[[{link}]]"}
                for lineno, link in extract_links(pg.text)
                if target.ref in resolve_link_to_refs(link, idx)]
        if hits:
            pages_with.append({"ref": pg.ref, "title": pg.title,
                               "draft": pg.is_draft, "links": hits})
            total_links += len(hits)
    pages_with.sort(key=lambda e: (-len(e["links"]), e["ref"]))
    return json.dumps({
        "target": {"ref": target.ref, "title": target.title},
        "backlink_pages": len(pages_with),
        "total_links": total_links,
        "results": pages_with[:limit],
    }, ensure_ascii=False, indent=2)


# ---- evidence + pack (ported 2026-10-08 from oomol-lab/wiki-graph concepts) ----

def tool_evidence(ref, max_items=40) -> str:
    """Provenance + where a page's claims come from.

    Returns the page's own external source metadata, plus the concrete lines
    that carry its evidence: outbound URLs, evidence-bearing sections
    (关联/来源/防裸报清单), and wikilinks to other pages it leans on. Answers
    'where did this conclusion come from' -- which backlinks alone cannot.
    """
    idx = get_index()
    pg = resolve_ref(ref, idx=idx)
    max_items = max(1, min(int(max_items), 200))
    lines = pg.body.splitlines()

    urls, cites = [], []
    for i, ln in enumerate(lines, start=1):
        for u in _URL_RE.findall(ln):
            u = u.rstrip(".,;")
            if u not in [x["url"] for x in urls]:
                urls.append({"url": u, "line": i})
        for m in re.finditer(r"\[\[[^\]]+\]\]", ln):
            t = m.group(0)
            if t not in [x["link"] for x in cites]:
                cites.append({"line": i, "link": t[:200]})

    sections = []
    for h in _EVIDENCE_HEADS:
        for i, ln in enumerate(lines, start=1):
            if ln.strip() == h:
                j = i
                chunk = []
                while j < len(lines) and not (
                        lines[j].startswith("## ") and j + 1 > i):
                    if lines[j].strip():
                        chunk.append({"line": j + 1, "text": lines[j][:300]})
                    j += 1
                sections.append({"heading": h, "line": i,
                                 "items": chunk[:max_items]})
                break

    prov = {k: pg.fm[k] for k in
            ("source_url", "source_type", "captured_at", "status")
            if k in pg.fm}

    return json.dumps({
        "ref": pg.ref, "title": pg.title, "dir": pg.dir, "draft": pg.is_draft,
        "provenance": prov,
        "has_provenance": bool(prov),
        "external_urls": urls[:max_items],
        "external_url_count": len(urls),
        "wikilink_cites": cites[:max_items],
        "wikilink_cite_count": len(cites),
        "evidence_sections": sections,
        "note": ("Line numbers index this page's markdown body. The wiki keeps "
                 "structured summaries, not full source text, so a line "
                 "citation points at the wiki page's own wording; follow "
                 "provenance.source_url for the original document."),
    }, ensure_ascii=False, indent=2)


def tool_pack(refs, budget=4000, char_budget=None) -> str:
    """Assemble context from several pages under a token budget.

    Token estimate is rough (CJK ~1 token/char, ASCII ~1 per 4). Pages are
    consumed in the order given (highest priority first); once the budget is
    spent the rest are listed as dropped so the caller knows what it missed.
    """
    if isinstance(refs, str):
        refs = [r for r in re.split(r"[,\s]+", refs) if r]
    refs = [r for r in (refs or []) if r]
    if not refs:
        raise ValueError("refs must be a non-empty list or comma-separated str")
    idx = get_index()
    budget = max(100, min(int(budget), 100000))
    exact_chars = int(char_budget) if char_budget else None
    if exact_chars:
        exact_chars = max(300, min(exact_chars, 400000))
    limit_tokens = budget if not exact_chars else None

    packed, dropped = [], []
    spent_tokens, spent_chars = 0, 0
    for r in refs:
        try:
            pg = resolve_ref(r, idx=idx)
        except Exception as e:
            dropped.append({"ref": r, "reason": "unresolved: %s" % e})
            continue
        body = pg.body
        if exact_chars is not None:
            room = exact_chars - spent_chars
            if room <= 0:
                dropped.append({"ref": pg.ref, "reason": "char budget exhausted"})
                continue
            snippet = body[:room]
            t = _est_tokens(snippet)
        else:
            # token budget is authoritative: take a guess, then shrink to fit
            left = limit_tokens - spent_tokens
            if left < 50:
                dropped.append({"ref": pg.ref, "reason": "token budget exhausted"})
                continue
            snippet = body[:min(len(body), max(400, left * 4))]
            t = _est_tokens(snippet)
            while t > left and len(snippet) > 200:
                snippet = snippet[:max(200, int(len(snippet) * 0.85))]
                t = _est_tokens(snippet)

        cut = len(snippet) < len(body)
        if cut:  # trim to last newline for readable text
            nl = snippet.rfind("\n")
            if nl > len(snippet) * 0.5:
                snippet = snippet[:nl]
                t = _est_tokens(snippet)
        packed.append({
            "ref": pg.ref, "title": pg.title, "dir": pg.dir,
            "draft": pg.is_draft,
            "chars": len(snippet),
            "tokens_est": t,
            "body_total_chars": len(body),
            "body_total_lines": body.count("\n") + 1,
            "partial": cut,
            "cite_as": "%s#L1-%s" % (pg.ref, snippet.count("\n") + 1),
            "text": snippet,
        })
        spent_tokens += t
        spent_chars += len(snippet)
        if cut:
            dropped.append({"ref": pg.ref, "reason": "truncated at budget"})

    return json.dumps({
        "budget_mode": "chars" if exact_chars else "tokens_est",
        "budget_tokens_est": None if exact_chars else budget,
        "budget_chars": exact_chars,
        "spent_tokens_est": spent_tokens,
        "spent_chars": spent_chars,
        "within_budget": (spent_chars <= exact_chars if exact_chars
                           else spent_tokens <= budget),
        "packed_count": len(packed),
        "dropped": dropped,
        "token_estimator": "CJK/fullwidth ~1 token/char, ASCII ~1 per 4 chars",
        "context": packed,
    }, ensure_ascii=False, indent=2)


# ---- MCP plumbing ---------------------------------------------------------

TOOLS = [
    {
        "name": "wiki_search",
        "description": (
            "Search the local LLM wiki (~1400 pages under sources/entities/"
            "concepts/synthesis/aliases). Title/name matches outrank body "
            "matches; a date (2026-09-26) or 6-hex page id in the query also "
            "matches file names. Returns JSON with ref/title/dir/draft/"
            "snippet. Draft pages (frontmatter status: draft) are included by "
            "default and flagged draft=true; pass include_drafts=false to "
            "hide them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "keywords, a title fragment, a date "
                                         "(2026-09-26), or a 6-hex page id"},
                "limit": {"type": "integer", "default": 10, "maximum": 50},
                "include_drafts": {"type": "boolean", "default": True},
                "dir": {"type": "string",
                        "enum": ["all", "sources", "entities", "concepts",
                                 "synthesis", "aliases"],
                        "default": "all"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wiki_read",
        "description": (
            "Read one wiki page. Accepts: relative path with/without .md "
            "(entities/OpenViking), canonical-map alias (Claude Code), "
            "6-hex page id (2f3acb), or bare page name (OpenViking; "
            "entities > concepts > synthesis > aliases > sources priority). "
            "Returns JSON {meta, body}. Two addressing modes: pass lines="
            "\"12-40\" (or \"12-\", \"12\", \"5-10,20-30\") for 1-based "
            "inclusive LINE ranges, or use offset/max_chars for CHAR "
            "paging (meta.next_offset). Either way meta carries "
            "body_line_start and cite_as=\"ref#L12-L40\" so you can cite the "
            "exact lines. Line numbers index the page body (frontmatter "
            "stripped)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "offset": {"type": "integer", "default": 0},
                "max_chars": {"type": "integer", "default": 8000,
                              "maximum": 50000},
                "lines": {"type": "string",
                          "description": "1-based inclusive line ranges: "
                                         "'12-40', '12-', '12', or "
                                         "'5-10,20-30'. Overrides offset/"
                                         "max_chars when present."},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "wiki_evidence",
        "description": (
            "Provenance + evidence for one wiki page. Returns the page's "
            "external source metadata (source_url/source_type/captured_at) "
            "plus the concrete lines carrying its evidence: outbound URLs, "
            "wikilinks it cites, and the 关联/来源/防裸报清单 sections. Use "
            "this to answer 'where did this conclusion come from' -- "
            "wiki_backlinks gives the inbound direction only. Line numbers "
            "index this page's own markdown body; the wiki stores structured "
            "summaries, not full source text, so follow provenance.source_url "
            "for the original."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "same ref forms as wiki_read"},
                "max_items": {"type": "integer", "default": 40,
                              "maximum": 200},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "wiki_pack",
        "description": (
            "Assemble context from several pages under a token budget, in "
            "the priority order you pass them. Returns JSON with per-page "
            "text, tokens_est, partial flag and cite_as, plus a dropped list "
            "so you know what the budget did not cover. Token estimate is "
            "rough (CJK ~1/char, ASCII ~1 per 4) -- pass char_budget for "
            "exact control. Prefer this over concatenating several "
            "wiki_read calls when you want a fixed context size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array", "items": {"type": "string"},
                    "description": "page refs, highest priority first"},
                "budget": {"type": "integer", "default": 4000,
                           "maximum": 100000,
                           "description": "approx token budget"},
                "char_budget": {"type": "integer",
                                "description": "exact char cap; overrides "
                                               "the token->char conversion"},
            },
            "required": ["refs"],
        },
    },
    {
        "name": "wiki_backlinks",
        "description": (
            "List wiki pages whose wikilinks ([[target]] or "
            "[[target|desc]]) resolve to the given page. Same wide "
            "resolution as the wiki health checker: bare names matching "
            "same-named pages in several dirs all count. Use for 'what "
            "references X' and for impact checks before edits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string",
                        "description": "same ref forms as wiki_read"},
                "limit": {"type": "integer", "default": 50, "maximum": 200},
            },
            "required": ["ref"],
        },
    },
]


def dispatch(name: str, args: dict) -> str:
    if name == "wiki_search":
        return tool_search(args.get("query", ""),
                           limit=args.get("limit", 10),
                           include_drafts=bool(args.get("include_drafts", True)),
                           dir_filter=args.get("dir", "all"))
    if name == "wiki_read":
        return tool_read(args.get("ref", ""),
                         offset=args.get("offset", 0),
                         max_chars=args.get("max_chars", 8000),
                         lines=args.get("lines"))
    if name == "wiki_evidence":
        return tool_evidence(args.get("ref", ""),
                             max_items=args.get("max_items", 40))
    if name == "wiki_pack":
        return tool_pack(args.get("refs") or [],
                         budget=args.get("budget", 4000),
                         char_budget=args.get("char_budget"))
    if name == "wiki_backlinks":
        return tool_backlinks(args.get("ref", ""),
                              limit=args.get("limit", 50))
    raise LookupError(f"unknown tool: {name}")


def handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if method == "initialize":
        ver = params.get("protocolVersion") or "2024-11-05"
        return {"protocolVersion": ver,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "wiki-mcp", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            text = dispatch(name, args)
            return {"content": [{"type": "text", "text": text}],
                    "isError": False}
        except Exception as e:
            return {"content": [{"type": "text",
                                 "text": f"error: {type(e).__name__}: {e}"}],
                    "isError": True}
    raise LookupError(f"method not found: {method}")


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    log(f"starting, root={WIKI_ROOT}")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or "id" not in msg:
            continue  # notification: nothing to answer
        resp = {"jsonrpc": "2.0", "id": msg["id"]}
        try:
            resp["result"] = handle_request(msg)
        except Exception as e:
            resp["error"] = {"code": -32603,
                             "message": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
