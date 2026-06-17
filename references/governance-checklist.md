# Governance Checklist

## Minimum Quality Gates

- Unresolved wikilinks: must not increase.
- Evidence coverage on critical claims: >= 80%.
- Stale claims (`last_verified_at` older than threshold): <= target cap.
- Orphan pages (no inbound and no outbound links): <= target cap.
- Duplicate concepts by normalized name: zero high-confidence duplicates.
- Source ingestion duplicates: zero duplicate pages for the same URL hash.

## Evidence Layer

For each critical claim include:

- source URL or source document path
- extracted assertion
- `last_verified_at` (ISO date)
- confidence or caveat note

## Decision Layer (ADR)

For each major choice include:

- context
- options considered
- decision
- tradeoffs
- review date

## Ingestion Reliability Rules

- Use a single stable ingestion entrypoint script.
- Ban ad-hoc per-URL generated fetch scripts in normal operation.
- For WeChat pages, parse `msg_title` first; fallback to `<title>`.
- After ingestion, run health check and fail if unresolved links increase unexpectedly.
