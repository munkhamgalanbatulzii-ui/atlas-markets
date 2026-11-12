#!/usr/bin/env python3
"""Synchronize visible static date labels with verified embedded quote dataset."""
import json
import re
from pathlib import Path

p=Path(__file__).resolve().parents[1]/'index.html'
html=p.read_text(encoding='utf-8')
m=re.search(r'<script\s+type=["\x27]application/json["\x27]\s+id=["\x27]dataset["\x27]>(.*?)</script>',html,re.S|re.I)
if not m:
    raise SystemExit('Cannot locate quotes: no date label changed')
date=json.loads(m.group(1))['summary']['lastPriceAsOf']
if not re.fullmatch(r'20\d\d-\d\d-\d\d',date):
    raise SystemExit('Invalid dataset date')
display=date.replace('-','.')
patterns=[
    (r'(<span class="source-chip"><span class="source-dot"></span>).*?(</span>)',
     lambda match:match.group(1)+'Ханш: брокерийн архив · '+display+match.group(2)),
    (r'(Хамгийн сүүлийн арилжаа )20\d\d\.\d\d\.\d\d',
     lambda match:match.group(1)+display),
    (r'(class="cmp-data-tag"><span class="source-dot"></span>).*?(<small>)',
     lambda match:match.group(1)+' Санхүү: Excel · Ханш: брокерийн архив <b>'+display+'</b>'+match.group(2))
]
for pattern,replacement in patterns:
    html,n=re.subn(pattern,replacement,html,count=1,flags=re.S)
    if n!=1:
        raise SystemExit('Expected visual date label not found: '+pattern)
p.write_text(html,encoding='utf-8')
print('VISIBLE_DATE_LABELS_UPDATED',display)
