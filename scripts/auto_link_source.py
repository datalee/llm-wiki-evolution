"""Auto-link a new wiki source to existing concepts/entities/aliases.

Pipeline (v1 dry-run):
1. Read source content (first 4000 chars)
2. Prompt LLM to extract 5-10 key concepts mentioned in source
3. For each concept, search wiki for matching page (concepts/entities/aliases)
4. Output proposed bidirectional link plan as JSON (no file modification)

Usage:
    python auto_link_source.py --wiki <wiki_root> --source <source_path> [--apply]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

# On Windows, fall back to reading the user environment from the registry
# when os.environ is missing keys (PowerShell may not propagate them to children).
if sys.platform == "win32" and not os.environ.get("GLM_API_KEY"):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            try:
                val, _ = winreg.QueryValueEx(k, "GLM_API_KEY")
                os.environ["GLM_API_KEY"] = val
            except FileNotFoundError:
                pass
    except ImportError:
        pass


def norm(s: str) -> str:
    """Normalize for matching: drop _ - space, lower."""
    return re.sub(r"[ _\-]+", "", s.strip().lower())


# === Step 1: Read source ===

def read_source(path: Path, max_chars: int = 4000) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    # Skip the frontmatter/metadata block
    lines = text.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("#!") and len(line) > 5:
            body_start = i
            break
    body = "\n".join(lines[body_start:body_start + 200])
    return body[:max_chars]


# === Step 2: LLM extraction ===

EXTRACT_PROMPT = """You are integrating a new wiki source page into an existing knowledge graph. Read the source and identify 5-10 key concepts that are LIKELY to have existing pages elsewhere in the wiki.

Source title: {title}
Source path: {path}
Content (first 4000 chars):
---
{content}
---

Return ONLY a valid JSON array (no markdown, no commentary, no explanation). Each object describes one concept:

[
  {{"name": "Concept Name", "category_guess": "concepts|entities|aliases", "search_terms": ["alt1", "alt2", "chinese_name", "snake_case"]}}
]

CRITICAL OUTPUT RULES:
- Output MUST be parseable as JSON — escape all quotes inside strings
- Close every bracket and brace
- Do not truncate — finish every string with a closing quote
- No trailing commas
- No comments

Constraints:
- 5-10 GENERAL concepts only (Harness, Agent, Memory, Skill, SDD, Vibe Coding)
- search_terms should include natural form + underscore variant + Chinese name (if applicable)
- If source is thin, return fewer items but still valid JSON"""


def call_llm(title: str, path: str, content: str) -> list:
    """Call GLM-5.3-Flash via BigModel OpenAI-compatible API. Retries on JSON parse failure."""
    prompt = EXTRACT_PROMPT.format(title=title, path=path, content=content)
    api_key = os.environ.get("GLM_API_KEY")
    if not api_key:
        print("GLM_API_KEY not set; cannot call LLM")
        return []

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    for attempt in range(3):
        data = {
            "model": "glm-5.3-flash",
            "messages": [
                {"role": "system", "content": "You output ONLY valid, complete, parseable JSON. No markdown, no commentary, no truncation. Every bracket and brace is closed."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 3000,  # larger to avoid truncation
            "temperature": 0.1,
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8")
                result = json.loads(body)
                raw = result["choices"][0]["message"]["content"]
                if attempt == 0:
                    print(f"  [model: glm-5.3-flash, attempt {attempt+1}]")
                break
        except Exception as e:
            print(f"  [LLM call failed attempt {attempt+1}: {e}]")
            continue
    else:
        print("  [all LLM attempts failed]")
        return []

    # Robust JSON parsing
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < 0:
        print(f"  [no JSON array in output: {raw[:200]}]")
        return []
    json_str = raw[start:end+1]

    # Try parse, with retry via LLM on failure
    for retry in range(2):
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            if retry == 0:
                print(f"  [JSON parse failed: {e}; retrying with LLM...]")
                # Re-prompt with error context
                fix_prompt = f"The following output is invalid JSON. Fix it and return ONLY the corrected JSON array:\n\n{json_str}\n\nError: {e}\n\nReturn only valid JSON, no commentary."
                try:
                    req2 = urllib.request.Request(
                        url,
                        data=json.dumps({
                            "model": "glm-5.3-flash",
                            "messages": [
                                {"role": "system", "content": "Fix the JSON. Output only valid JSON."},
                                {"role": "user", "content": fix_prompt},
                            ],
                            "max_tokens": 3000,
                            "temperature": 0.0,
                        }).encode("utf-8"),
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req2, timeout=60) as resp2:
                        body2 = resp2.read().decode("utf-8")
                        result2 = json.loads(body2)
                        raw2 = result2["choices"][0]["message"]["content"]
                        raw2 = re.sub(r"^```(?:json)?\s*", "", raw2)
                        raw2 = re.sub(r"\s*```$", "", raw2)
                        start2 = raw2.find("[")
                        end2 = raw2.rfind("]")
                        if start2 >= 0 and end2 >= 0:
                            json_str = raw2[start2:end2+1]
                            continue
                except Exception as e2:
                    print(f"  [JSON fix retry failed: {e2}]")
            print(f"  [JSON parse failed permanently: {e}]")
            return []
    return []


# === Step 3: Match against existing wiki pages ===

def build_page_index(wiki_root: Path):
    """Build index: normalized_name -> [paths]."""
    by_norm = {}
    for p in wiki_root.rglob("*.md"):
        if any(s in p.parts for s in {".understand-anything", "raw", ".git", "test-stubs"}):
            continue
        rel = p.relative_to(wiki_root).with_suffix("").as_posix()
        # Index by full rel path
        by_norm.setdefault(norm(rel), []).append(rel)
        # Index by bare stem
        stem = rel.rsplit("/", 1)[-1]
        by_norm.setdefault(norm(stem), []).append(rel)
    return by_norm


def find_match(concept: dict, by_norm: dict) -> str | None:
    """Try to find a wiki page for the given concept."""
    search_terms = [concept.get("name", "")] + concept.get("search_terms", [])
    candidates = []
    for term in search_terms:
        if not term:
            continue
        n = norm(term)
        if n in by_norm:
            # Prefer pages whose category matches the guess
            guess = concept.get("category_guess", "")
            paths = by_norm[n]
            for p in paths:
                if guess and p.startswith(guess + "/"):
                    return p
                candidates.append(p)
    # If no category-matched, return first candidate
    if candidates:
        # Prefer concepts/ > entities/ > aliases/ > sources/
        for prefix in ("concepts/", "entities/", "aliases/", "sources/"):
            for p in candidates:
                if p.startswith(prefix):
                    return p
        return candidates[0]
    return None


# === Step 4: Output proposed plan ===

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--apply", action="store_true", help="Actually modify files (default: dry-run)")
    args = ap.parse_args()

    wiki_root = Path(args.wiki)
    source_path = Path(args.source)
    # Suffixless rel path for wikilinks ([[sources/xxx]] not [[sources/xxx.md]])
    rel_source = source_path.relative_to(wiki_root).with_suffix("").as_posix()

    print(f"=== Auto-link: {rel_source} ===\n")

    # 1. Read source
    title = source_path.stem
    content = read_source(source_path)
    print(f"source: {len(content)} chars read")

    # 2. LLM extract
    concepts = call_llm(title, rel_source, content)
    print(f"LLM extracted: {len(concepts)} concept candidates\n")

    # 3. Build page index
    by_norm = build_page_index(wiki_root)
    print(f"wiki page index: {len(by_norm)} normalized keys\n")

    # 4. Match + propose plan
    plan = []
    for c in concepts:
        match = find_match(c, by_norm)
        plan.append({
            "concept": c.get("name", ""),
            "category_guess": c.get("category_guess", ""),
            "matched_page": match,
            "search_terms": c.get("search_terms", []),
        })

    # Print plan (write JSON FIRST so we have it even if print crashes)
    if not args.apply:
        out = wiki_root / ".understand-anything" / f"auto-link-plan-{source_path.stem}.json"
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"plan saved: {out.relative_to(wiki_root)}")

    matched = [p for p in plan if p["matched_page"]]
    unmatched = [p for p in plan if not p["matched_page"]]
    print(f"=== plan: {len(matched)} matched, {len(unmatched)} unmatched ===\n")
    for p in plan:
        try:
            status = "✓" if p.get("matched_page") else "✗"
            target = p.get("matched_page") or "(no match)"
            concept = p.get("concept", "")
            if not isinstance(concept, str):
                concept = str(concept)[:30]
            print(f"  {status} {concept:30s} -> {target}")
        except Exception as e:
            print(f"  [print error: {e}]  plan entry: {p}")

    if not args.apply:
        print("\n=== dry-run (no files modified). Use --apply to execute. ===")
        return 0

    # Apply: inject backlinks
    if not matched:
        print("nothing to apply (no matches)")
        return 0
    print("\n=== APPLY ===")

    # 1. Append "## 相关来源" section to source
    source_text = source_path.read_text(encoding="utf-8")
    related_lines = ["- [[{0}]]".format(p["matched_page"]) for p in matched if p.get("matched_page")]
    missing_lines = ["- `{0}` (LLM 抽取但 wiki 无对应页面 — 可后续建 stub)".format(p["concept"]) for p in unmatched]
    addition_lines = [
        "",
        "---",
        "",
        "## 相关来源 (auto-linked by GLM-5.3-Flash)",
        "",
        "> Added {0} via `auto_link_source_v1.py` (LLM concept extraction + wiki match).".format(datetime.date.today().isoformat()),
        "",
    ] + related_lines + ([">", "> **未匹配的 LLM 抽取概念**（wiki 无对应页面）:"] + missing_lines if missing_lines else []) + [">"]

    if "## 相关来源 (auto-linked" in source_text:
        print(f"  source already has auto-linked section, skipping source append")
    else:
        source_path.write_bytes(source_path.read_bytes() + "\n".join(addition_lines).encode("utf-8"))
        print(f"  source appended: +{sum(len(l) for l in addition_lines)} chars")

    # 2. Append backward-link line to each matched page
    for p in matched:
        if not p.get("matched_page"):
            continue
        target_path = wiki_root / (p["matched_page"] + ".md")
        if not target_path.exists():
            print(f"  WARN: matched page missing: {target_path}")
            continue
        target_bytes = target_path.read_bytes()
        # Skip if this exact source link already exists (date-agnostic dedup)
        if rel_source.encode("utf-8") in target_bytes:
            print(f"  skip (already links this source): {p['matched_page']}")
            continue
        target_text = target_bytes.decode("utf-8")
        # Heuristic: append at the end of the file with a tag line
        today = datetime.date.today().isoformat()
        addition = f"\n> {today} 补充 [[{rel_source}|{p['concept']}]]（auto-linked by GLM-5.3-Flash）\n"
        target_path.write_bytes((target_text + addition).encode("utf-8"))
        print(f"  backlink appended: {p['matched_page']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
