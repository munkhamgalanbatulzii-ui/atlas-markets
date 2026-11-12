#!/usr/bin/env python3
"""Update ATLAS 26-company site from a verified daily OHLCV source.
Optional ATLAS_PRICE_FEED_URL: HTTPS JSON {"date":"YYYY-MM-DD","prices":[{"ticker":"KHAN","open":1,"high":2,"low":1,"close":2,"volume":200,"value":300}]}
If unset, read the official MSE daily-report webpage with Playwright.
Never guess a trading date or replace yesterday's price with an unverified quote.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'index.html'
DATASET = re.compile(r'(<script\s+type=["\']application/json["\']\s+id=["\']dataset["\']>)(.*?)(</script>)', re.I | re.S)
TZ = ZoneInfo('Asia/Ulaanbaatar')
SOURCE = 'https://www.mse.mn/trade-daily-report'
ALIASES = {
    'symbol':['симбол','тикер','symbol','ticker'],
    'open':['нээлт','нээлтийн ханш','open'],
    'high':['дээд','хамгийн өндөр','high'],
    'low':['доод','хамгийн бага','low'],
    'close':['хаалт','хаалтын ханш','close'],
    'volume':['тоо ширхэг','ширхэг','арилжсан ширхэг','volume'],
    'value':['үнийн дүн','ү/дүн','value','turnover'],
}

def norm(v):
    return re.sub(r'\s+',' ',str(v or '').lower().replace('\xa0',' ')).strip()

def num(v):
    s = str(v or '').replace('\xa0','').replace(' ','').replace(',','').replace('₮','').strip()
    if s in ('','-','—','–','N/A','null'): return None
    try: n=float(s)
    except ValueError: return None
    return n if math.isfinite(n) else None

def day(v):
    if not v:return None
    try:return dt.date.fromisoformat(str(v).strip().replace('.','-').replace('/','-')).isoformat()
    except ValueError:return None

def table_rows(tables):
    out=[]
    for table in tables:
        heads=[norm(h) for h in table.get('headers',[])]
        found={}
        for k,aliases in ALIASES.items():
            for a in aliases:
                if a in heads:
                    found[k]=heads.index(a)
                    break
        if len(found)!=7:continue
        for cells in table.get('rows',[]):
            if len(cells)>max(found.values()):
                out.append({k:cells[i] for k,i in found.items()})
    return out

def fetch_authorized_feed(url):
    if not url.startswith('https://'):raise RuntimeError('Only HTTPS feeds supported')
    headers={'User-Agent':'ATLAS-Markets/1.0','Accept':'application/json'}
    token=os.getenv('ATLAS_PRICE_FEED_TOKEN','').strip()
    if token: headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req,timeout=45) as response:
        value=json.load(response)
    if not isinstance(value,dict):raise RuntimeError('Feed needs a top-level date and prices array')
    return value

def extract_report_date(page):
    meta=page.evaluate("""() => {
        const fields=[];
        for(const e of document.querySelectorAll('input[type=date],input[aria-label*=огноо],input[placeholder*=огноо],time')){
           const val=e.value||e.dateTime||e.getAttribute('datetime')||e.textContent;
           if(val)fields.push(val.trim());
        }
        const text=(document.querySelector('main')||document.body).innerText||'';
        const key=text.indexOf('Өдрийн арилжааны тайлан');
        return {fields,text:key>=0?text.slice(Math.max(0,key-200),key+450):text.slice(0,450)};
    }""")
    today=dt.datetime.now(TZ).date()
    def recent(v):
        d=day(v)
        if d and today-dt.timedelta(days=14)<=dt.date.fromisoformat(d)<=today:return d
        return None
    for v in meta['fields']:
        d=recent(v)
        if d:return d
    for v in re.findall(r'\b20\d\d[./-](?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01])\b',meta['text']):
        d=recent(v)
        if d:return d
    raise RuntimeError('Could not verify the official trading DATE; no changes made')

def fetch_mse_report():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page(locale='mn-MN',timezone_id='Asia/Ulaanbaatar')
            page.goto(SOURCE,wait_until='domcontentloaded',timeout=60000)
            page.get_by_text('Өдрийн арилжааны тайлан',exact=False).first.wait_for(timeout=25000)
            try: page.locator('table tbody tr').first.wait_for(timeout=25000)
            except Exception:pass
            page.wait_for_timeout(2500)
            report_day=extract_report_date(page)
            tables=page.evaluate("""() => [...document.querySelectorAll('table')].map(t=>({
                headers:[...t.querySelectorAll('thead th')].map(x=>x.innerText.trim()),
                rows:[...t.querySelectorAll('tbody tr')].map(tr=>[...tr.querySelectorAll('td')].map(x=>x.innerText.trim()))
            }))""")
            values=table_rows(tables)
            if not values:raise RuntimeError('Official daily table unavailable or its structure changed')
            return {'date':report_day,'prices':values,'source':SOURCE}
        finally:browser.close()

def normalized_price(raw,report_day):
    symbol=norm(raw.get('ticker',raw.get('symbol',''))).upper()
    if not re.fullmatch('[A-Z0-9]{2,12}',symbol):return None
    vals=[num(raw.get(k)) for k in ('open','high','low','close','volume','value')]
    if any(v is None for v in vals):return None
    o,h,l,c,volume,amount=vals
    if min(o,h,l,c)<=0 or volume<=0 or amount<0:return None
    if l>min(o,c) or h<max(o,c) or l>h:return None
    if abs(volume-round(volume))>0.00001:return None
    return symbol,[report_day,o,h,l,c,int(round(volume)),amount]

def merge(html,payload):
    found=DATASET.search(html)
    if not found:raise RuntimeError('index.html embedded dataset missing')
    data=json.loads(found.group(2))
    companies=data.get('companies',[])
    if len(companies)!=26:raise RuntimeError('Expected 26 stocks; refusing to modify other data')
    report_day=day(payload.get('date'))
    today=dt.datetime.now(TZ).date()
    if not report_day:raise RuntimeError('No verified YYYY-MM-DD report date')
    report_date=dt.date.fromisoformat(report_day)
    if report_date>today or today-report_date>dt.timedelta(days=14):
        raise RuntimeError('Reported date not valid or too old')
    by_ticker={c['symbol']:c for c in companies}
    prices={}
    for row in payload.get('prices',[]):
        p=normalized_price(row,report_day)
        if not p:continue
        symbol,rec=p
        if symbol not in by_ticker:continue
        if symbol in prices and prices[symbol]!=rec:
            raise RuntimeError('Conflicting rows for '+symbol)
        prices[symbol]=rec
    if not prices:raise RuntimeError('No valid trades for the monitored 26 stocks')
    changed=[]
    for symbol,record in prices.items():
        c=by_ticker[symbol]
        history=c.get('priceHistory') or []
        same=next((p for p in history if p[0]==report_day),None)
        if same is not None:
            if same!=record:raise RuntimeError(symbol+' existing date differs; manual review needed')
            continue
        if history and report_day<=history[-1][0]:continue
        history.append(record)
        c['priceHistory']=history
        m=c['metrics']; close=record[4]
        prev=history[-2] if len(history)>1 else None
        shares=c.get('shares');eps=m.get('eps');bvps=m.get('bvps');dps=m.get('dividends12mDPS')
        m.update({
            'price':close,'priceDate':report_day,'firstTradeDate':history[0][0],
            'price1d':100*(close/prev[4]-1) if prev and prev[4]>0 else None,
            'volume':record[5],'value':record[6],
            'marketcap':close*shares/1e6 if shares and shares>0 else None,
            'pe':close/eps if eps and eps>0 else None,
            'pb':close/bvps if bvps and bvps>0 else None,
            'divYield':100*dps/close if dps is not None else None
        })
        changed.append(symbol)
    if not changed:
        print('No new verified closes. Report day:',report_day)
        return None
    summary=data['summary']
    summary['priceRecords']=sum(len(c['priceHistory']) for c in companies)
    summary['totalMarketcapMillion']=sum(c['metrics'].get('marketcap') or 0 for c in companies)
    summary['lastPriceAsOf']=max(c['metrics'].get('priceDate') or '' for c in companies)
    summary['lastPriceSource']=payload.get('source',SOURCE)
    summary['lastPriceRefreshAt']=dt.datetime.now(TZ).isoformat(timespec='seconds')
    summary['updatedFrom']='Санхүү: Excel · Ханш: МХБ'
    # The financial statement timestamp remains unchanged.
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    print('Updated:',', '.join(sorted(changed)), 'Trading date:',report_day)
    updated=html[:found.start(2)]+encoded+html[found.end(2):]
    # Replace the static Excel date badge only after verified updates are written.
    updated=re.sub(r'(<span class="source-chip"><span class="source-dot"></span>)([^<]*)(</span>)',
                   lambda match:match.group(1)+'МХБ · '+summary['lastPriceAsOf']+match.group(3),
                   updated,count=1)
    return updated

def main():
    url=os.getenv('ATLAS_PRICE_FEED_URL','').strip()
    payload=fetch_authorized_feed(url) if url else fetch_mse_report()
    payload.setdefault('source',url or SOURCE)
    old=INDEX.read_text(encoding='utf-8')
    new=merge(old,payload)
    if new is not None:
        tmp=INDEX.with_suffix('.html.tmp')
        tmp.write_text(new,encoding='utf-8')
        tmp.replace(INDEX)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('UPDATE FAILED — original site kept: '+str(e),file=sys.stderr)
        sys.exit(1)
