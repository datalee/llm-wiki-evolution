# -*- coding: utf-8 -*-
import io, json, os
from collections import Counter
DIR = r'C:\Users\Administrator\clawd\memory\wiki\.understand-anything'
base = json.load(io.open(os.path.join(DIR, 'wiki-health-v2.json'), encoding='utf-8'))
new = json.load(io.open(r'C:\Users\Administrator\clawd\memory\wiki\.understand-anything\wiki-health-v2-2026-09-30-after-rlcd.json', encoding='utf-8'))
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

