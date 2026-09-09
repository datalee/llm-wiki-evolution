#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
      continue;
    }
    args[key] = next;
    i += 1;
  }
  return args;
}

function htmlDecode(s) {
  return s
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"');
}

function stripHtml(raw) {
  return htmlDecode(
    raw
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
  )
    .replace(/\s+/g, " ")
    .trim();
}

function slugify(s) {
  const t = s
    .toLowerCase()
    .replace(/[\u4e00-\u9fff]+/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return t || "article";
}

function today() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function sourceTypeOf(url) {
  if (/mp\.weixin\.qq\.com/.test(url)) return "wechat";
  if (/feishu\.cn|larksuite\.com/.test(url)) return "feishu";
  return "web";
}

function bestTitle(html, fallback = "Untitled") {
  // WeChat metadata first
  const msgTitle = html.match(/var\s+msg_title\s*=\s*'([^']+)'/i);
  if (msgTitle && msgTitle[1]) {
    return htmlDecode(msgTitle[1]).trim() || fallback;
  }
  const m = html.match(/<title[^>]*>([^<]+)<\/title>/i);
  if (!m) return fallback;
  const t = htmlDecode(m[1]).trim();
  if (!t || /微信公众平台|wechat/i.test(t)) return fallback;
  return t;
}

function extractWeChatContent(html) {
  // Balanced-div extraction: js_content contains nested <div>s, so a
  // non-greedy match to the first </div> silently truncates long articles.
  const start = html.search(/id="js_content"/i);
  if (start === -1) return null;
  const openIdx = html.indexOf(">", start) + 1;
  const re = /<div\b[^>]*>|<\/div>/gi;
  re.lastIndex = openIdx;
  let depth = 1;
  let end = -1;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m[0][1] === "/") {
      depth -= 1;
      if (depth === 0) {
        end = m.index;
        break;
      }
    } else {
      depth += 1;
    }
  }
  if (end === -1) return null;
  return html.slice(openIdx, end);
}

function extractMain(html, url) {
  // WeChat preferred content container
  if (/mp\.weixin\.qq\.com/.test(url)) {
    const wechat = extractWeChatContent(html);
    if (wechat) return stripHtml(wechat);
  }
  // Generic fallbacks
  const article = html.match(/<article[^>]*>([\s\S]*?)<\/article>/i);
  if (article) return stripHtml(article[1]);
  const body = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  if (body) return stripHtml(body[1]);
  return stripHtml(html);
}

function appendIfExists(file, line) {
  if (!fs.existsSync(file)) return;
  const content = fs.readFileSync(file, "utf8");
  const out = content.endsWith("\n") ? content + line + "\n" : content + "\n" + line + "\n";
  fs.writeFileSync(file, out, "utf8");
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || !args.url) {
    console.log("Usage:");
    console.log(
      "  node ingest_url_to_wiki.js --url <URL> [--wiki-root <path>] [--tags \"#a #b\"] [--date YYYY-MM-DD]"
    );
    process.exit(args.help ? 0 : 1);
  }

  const url = String(args.url);
  const wikiRoot = path.resolve(
    args["wiki-root"] || "C:\\Users\\Administrator\\clawd\\memory\\wiki"
  );
  const sourceDir = path.join(wikiRoot, "sources");
  const indexFile = path.join(wikiRoot, "index.md");
  const logFile = path.join(wikiRoot, "log.md");
  const date = args.date || today();
  const tags = args.tags || "#ingest";

  if (!fs.existsSync(sourceDir)) {
    throw new Error(`sources directory not found: ${sourceDir}`);
  }

  const res = await fetch(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
  });
  if (!res.ok) {
    throw new Error(`fetch failed: ${res.status} ${res.statusText}`);
  }
  const html = await res.text();
  const title = bestTitle(html, "Untitled");
  const content = extractMain(html, url);
  const summary = content.slice(0, 240).trim();
  const id = `${slugify(title)}-${crypto.createHash("md5").update(url).digest("hex").slice(0, 6)}`;
  const sourceType = sourceTypeOf(url);
  const fileName = `${date}-${id}.md`;
  const filePath = path.join(sourceDir, fileName);

  const lines = [
    `# ${title}`,
    "",
    "---",
    `source_url: ${url}`,
    `source_type: ${sourceType}`,
    `captured_at: ${new Date().toISOString()}`,
    "status: draft",
    `tags: [${tags
      .split(/\s+/)
      .filter(Boolean)
      .map((t) => `"${t.replace(/^#/, "")}"`)
      .join(", ")}]`,
    "---",
    "",
    "## 一句话摘要",
    "",
    summary || "（待补充）",
    "",
    "## 原文摘录",
    "",
    content || "（未提取到正文）",
    "",
  ];

  // Write UTF-8 BOM for better compatibility with some Windows tooling.
  fs.writeFileSync(filePath, "\ufeff" + lines.join("\n"), "utf8");

  const rel = `sources/${fileName}`;
  // Single-bracket markdown link — the wiki's index/log convention. Double brackets
  // ([[title]]) would be parsed as a wikilink and reported unresolved by health scans.
  appendIfExists(indexFile, `- ${date} [${title}](${rel})`);
  appendIfExists(logFile, `- ${date} ingest: [${title}](${rel}) from ${sourceType}`);

  console.log("Saved:", filePath);
  console.log("Title:", title);
  console.log("Type:", sourceType);
}

main().catch((err) => {
  console.error("[ingest_url_to_wiki] ERROR:", err.message);
  process.exit(1);
});
