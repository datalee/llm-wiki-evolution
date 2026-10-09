#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke tests for wiki-mcp server: protocol layer + the three tools."""
import json
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable
SERVER = Path(__file__).with_name("mcp_server.py")

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  | {detail}" if detail and not cond else ""))


def rpc(proc, id_, method, params=None):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("server closed stream (crashed?)")
    return json.loads(line.decode("utf-8"))


def call(proc, id_, name, args):
    r = rpc(proc, id_, "tools/call",
            {"name": name, "arguments": args})
    result = r.get("result", {})
    text = result["content"][0]["text"]
    return result, (json.loads(text) if not result.get("isError") else text)


proc = subprocess.Popen([PY, "-X", "utf8", str(SERVER)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
try:
    # ---- protocol layer ----
    r = rpc(proc, 1, "initialize", {"protocolVersion": "2025-06-18",
                                    "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "0"}})
    check("initialize echoes protocolVersion",
          r["result"]["protocolVersion"] == "2025-06-18")

    r = rpc(proc, 2, "tools/list")
    names = [t["name"] for t in r["result"]["tools"]]
    check("tools/list has 5 tools (3 original + evidence/pack added 2026-10-08)",
          names == ["wiki_search", "wiki_read", "wiki_evidence",
                    "wiki_pack", "wiki_backlinks"], str(names))

    r = rpc(proc, 3, "nonexistent/method")
    check("unknown method -> error", "error" in r and r["error"]["code"] == -32603)

    # notification must not produce a response line: hard to assert directly,
    # skip; next request would hang if notifications were echoed.

    # ---- wiki_search ----
    t0 = time.time()
    result, data = call(proc, 10, "wiki_search", {"query": "OpenViking"})
    dt = time.time() - t0
    check("search OpenViking ok", not result.get("isError"), str(data)[:200])
    check("first call under 15s (index build)", dt < 15, f"{dt:.1f}s")
    refs = [x["ref"] for x in data["results"]]
    check("entity page ranked first", refs and refs[0] == "entities/OpenViking", str(refs[:3]))
    src = [x for x in data["results"] if x["ref"].endswith("2f3acb")]
    check("source page found & flagged draft", src and src[0]["draft"] is True, str(src))

    t0 = time.time()
    result, data = call(proc, 11, "wiki_search", {"query": "OpenViking", "include_drafts": False})
    dt = time.time() - t0
    check("repeat call fast (cache)", dt < 3, f"{dt:.1f}s")
    check("drafts hidden when asked",
          data["drafts_hidden"] >= 1 and not any(x["draft"] for x in data["results"]))

    result, data = call(proc, 12, "wiki_search", {"query": "赔付", "limit": 5})
    hits = [x["ref"] for x in data.get("results", [])]
    check("chinese body search hits source page", bool(hits)
          and any(h.endswith("2f3acb") for h in hits), str(hits))

    result, data = call(proc, 13, "wiki_search", {"query": "2026-09-26 openviking"})
    check("date+word query works", bool(data.get("results")))

    # ---- ranking regression: latin sub-phrase bonus (2026-09-27 fix) ----
    # The +50 phrase bonus used to fire only when the WHOLE normalized query
    # appeared verbatim, so adding any qualifier ("Context Graph openai",
    # Chinese qualifiers around a Latin phrase) dropped the target page out
    # of the top 10. HERB page = sources/2026-09-26-...-8c8b94.
    herb = "sources/2026-09-26-herb-enterprise-deep-search-benchmark-8c8b94"

    def rank_of(query):
        _, d = call(proc, 90, "wiki_search", {"query": query, "limit": 10})
        refs = [x["ref"] for x in d.get("results", [])]
        return refs.index(herb) + 1 if herb in refs else 99

    check("bare phrase baseline intact", rank_of("Context Graph") <= 6)
    check("latin qualifier keeps target in top 3",
          rank_of("Context Graph openai") <= 3)
    check("cjk qualifier keeps target in top 6",
          rank_of("openai 发的那篇 Context Graph 文章") <= 6)
    check("compact qualifier still ranks target first",
          rank_of("OpenAI V7 客户案例") <= 3)

    # ---- wiki_read ----
    result, data = call(proc, 20, "wiki_read", {"ref": "entities/OpenViking", "max_chars": 3000})
    check("read by dir/name", not result.get("isError")
          and data["meta"]["ref"] == "entities/OpenViking")
    check("body contains AGPLv3", "AGPL" in data["body"])

    result, data = call(proc, 21, "wiki_read", {"ref": "2f3acb", "max_chars": 2000})
    check("read by id6", not result.get("isError")
          and data["meta"]["ref"].endswith("2f3acb")
          and data["meta"]["draft"] is True, str(data.get("meta")))

    result, data = call(proc, 22, "wiki_read", {"ref": "Claude Code", "max_chars": 1500})
    check("read via canonical-map alias", not result.get("isError")
          and data["meta"]["ref"] == "entities/Claude_Code", str(data.get("meta")))

    result, data = call(proc, 23, "wiki_read", {"ref": "MCP_Server", "max_chars": 1500})
    check("read via alias to concept (MCP_Server->MCP)", not result.get("isError"),
          str(data)[:200])

    # pagination
    result, data = call(proc, 24, "wiki_read", {"ref": "entities/OpenViking", "max_chars": 500})
    check("read truncation gives next_offset",
          data["meta"]["truncated"] and data["meta"]["next_offset"] == 500)
    result, data2 = call(proc, 25, "wiki_read",
                         {"ref": "entities/OpenViking", "offset": 500, "max_chars": 500})
    check("read resumes from offset", data2["meta"]["body_offset"] == 500
          and data2["body"] != data["body"])

    # ---- wiki_backlinks ----
    result, data = call(proc, 30, "wiki_backlinks", {"ref": "entities/MCP"})
    check("backlinks hub page MCP works", not result.get("isError")
          and data["backlink_pages"] > 0, str(data)[:300])

    result, data = call(proc, 31, "wiki_backlinks", {"ref": "entities/OpenViking"})
    bl = [x["ref"] for x in data.get("results", [])]
    check("backlinks OpenViking has results", data.get("backlink_pages", 0) >= 1, str(bl))

    # the source page links the entity page? check explicitly
    result, data = call(proc, 32, "wiki_backlinks", {"ref": "entities/Claude_Code"})
    check("backlinks via alias ref ok", not result.get("isError"))

    # ---- error paths ----
    result, _ = call(proc, 40, "wiki_read", {"ref": "no/such/page-xyz"})
    check("missing ref -> isError", result.get("isError") is True)

    result, _ = call(proc, 41, "wiki_search", {"query": ""})
    check("empty query -> isError", result.get("isError") is True)

    result, _ = call(proc, 42, "wiki_nope", {})
    check("unknown tool -> isError", result.get("isError") is True)

    r = rpc(proc, 50, "ping")
    check("ping -> {}", r.get("result") == {})

    # ---- 2026-10-08: four improvements ported from oomol-lab/wiki-graph ----
    # ① line addressing  ② wiki_evidence  ③ wiki_pack  (+ BOM frontmatter fix)
    result, tools = rpc(proc, 60, "tools/list"), None
    names = [t["name"] for t in result["result"]["tools"]]
    check("tools/list now has 5 tools", len(names) == 5)
    check("wiki_evidence registered", "wiki_evidence" in names)
    check("wiki_pack registered", "wiki_pack" in names)

    result, d = call(proc, 61, "wiki_read",
                     {"ref": "concepts/RLCD", "lines": "1-10"})
    m = d["meta"]
    check("lines mode on", m.get("line_mode") is True)
    check("line range honors 1-10", m.get("body_line_start") == 1
          and m.get("body_line_end") == 10)
    check("line slice returns 10 lines", len(d["body"].splitlines()) == 10)
    check("cite_as carries line range",
          m.get("cite_as", "").startswith("concepts/RLCD#L1-10"))
    check("frontmatter stripped (line 1 is not ---)",
          not d["body"].splitlines()[0].strip().startswith("---"))

    result, d2 = call(proc, 62, "wiki_read",
                      {"ref": "concepts/RLCD", "lines": "3-5,8-9"})
    # assert on reported range, not splitlines() -- a trailing empty selected
    # line is legitimately dropped by splitlines
    check("multi-range start/end correct",
          d2["meta"].get("body_line_start") == 3
          and d2["meta"].get("body_line_end") == 9)
    check("multi-range picked 5 lines",
          d2["body"].count("\n") == 4)

    result, d3 = call(proc, 63, "wiki_read",
                      {"ref": "concepts/RLCD", "lines": "999999-1000000"})
    check("out-of-range lines -> empty body, no error", d3["body"] == "")

    result, d4 = call(proc, 64, "wiki_read",
                      {"ref": "concepts/RLCD", "offset": 3000, "max_chars": 400})
    check("char mode still works", d4["meta"].get("line_mode") is False)
    check("char mode reports containing line",
          d4["meta"].get("body_line_start", 0) > 1)

    result, e = call(proc, 65, "wiki_evidence", {"ref": "concepts/RLCD"})
    check("evidence: RLCD cites other pages",
          e.get("wikilink_cite_count", 0) > 3)
    check("evidence: has 关联 section",
          any(s["heading"] == "## 关联"
              for s in e.get("evidence_sections", [])))

    result, e2 = call(proc, 66, "wiki_evidence",
                      {"ref": "sources/2026-10-08-oomol-lab-wiki-graph-长文本知识库CLI-f3ff5a"})
    check("evidence: surfaces external URL",
          e2.get("external_url_count", 0) >= 1)
    check("evidence: surfaces 防裸报清单",
          any(s["heading"] == "## 防裸报清单"
              for s in e2.get("evidence_sections", [])))

    refs = ["concepts/RLCD", "concepts/Recursive_Self_Improvement",
            "concepts/Ontology"]
    result, p = call(proc, 67, "wiki_pack", {"refs": refs, "budget": 800})
    check("pack: stays within token budget",
          p["meta"] if False else p.get("spent_tokens_est", 1e9) <= 800)
    check("pack: reports within_budget", p.get("within_budget") is True)
    check("pack: every entry citable",
          all("cite_as" in c for c in p.get("context", [])))
    check("pack: reports what it dropped", isinstance(p.get("dropped"), list))

    result, p2 = call(proc, 68, "wiki_pack",
                      {"refs": refs, "char_budget": 5000})
    check("pack: char budget respected",
          sum(c["chars"] for c in p2["context"]) <= 5000)
    check("pack: char mode flag", p2.get("budget_mode") == "chars")

    result, p3 = call(proc, 69, "wiki_pack",
                      {"refs": "concepts/RLCD,concepts/Ontology", "budget": 300})
    check("pack: accepts comma string", p3.get("packed_count") == 2)
finally:
    proc.kill()
    err = proc.stderr.read().decode("utf-8", "replace")

print("\n---- stderr (server log, first 400 chars) ----")
print(err[:400])
print(f"\n{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
