# -*- coding: utf-8 -*-
"""Diff a wiki-health-v2 report against the current baseline.

Usage:
  python diff_final_vs_baseline.py                # author's default pair
  python diff_final_vs_baseline.py <report.json>  # diff that report instead
  WIKI_HEALTH_DIR=<dir>                           # override the report dir

Defaults point at the author's local wiki layout; pass a path (or env var)
when running elsewhere.
"""
import io, json, os, sys

DIR = os.environ.get(
    'WIKI_HEALTH_DIR',
    r'C:\Users\Administrator\clawd\memory\wiki\.understand-anything')
base = json.load(io.open(os.path.join(DIR, 'wiki-health-v2.json'),
                         encoding='utf-8'))
new_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    DIR, 'wiki-health-v2-2026-09-30-after-rlcd.json')
new = json.load(io.open(new_path, encoding='utf-8'))
bu = {(u['source'], u['link']) for u in base['unresolved']}
nu = {(u['source'], u['link']) for u in new['unresolved']}
added, removed = nu - bu, bu - nu
print('unresolved added=%d removed=%d' % (len(added), len(removed)))
print('=== ADDED (all) ===')
for src, link in sorted(added):
    print('  %s -> %s (line %s)' % (src, link, [u['line'] for u in new['unresolved'] if (u['source'],u['link'])==(src,link)][0]))
print('=== REMOVED (all %d) ===' % len(removed))
for src, link in sorted(removed):
    print('  %s -> %s' % (src, link))
bo, no = set(base['orphans']), set(new['orphans'])
print('orphans: base=%d new=%d | mine-not-base=%d base-not-mine=%d' % (len(bo), len(no), len(no-bo), len(bo-no)))
print('=== base-not-mine sample 10 (baseline orphan, mine linked) ===')
for p in sorted(bo - no)[:10]: print('  -', p)
print('=== mine-not-base sample 10 ===')
for p in sorted(no - bo)[:10]: print('  +', p)
