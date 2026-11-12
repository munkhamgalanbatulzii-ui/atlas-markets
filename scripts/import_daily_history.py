#!/usr/bin/env python3
"""Backfill verified daily market OHLCV from a brokerage quote archive.
Source URL per ticker: https://stock.bbe.mn/Home/Stock/{ticker}
Data is NOT attributed as directly scraped from mse.mn.
Never fabricate prices or dates; preserve original Excel history and financials.
"""
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
INDEX=ROOT/'index.html'
PATTERN=re.compile(r'(<script\s+type=["\x27]application/json["\x27]\s+id=["\x27]dataset["\x27]>)(.*?)(</script>)',re.S|re.I)
TODAY=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
REQUESTED=os.getenv('ATLAS_BACKFILL_DATES','').strip()
DATES=set(REQUESTED.split(',')) if REQUESTED else {(TODAY-dt.timedelta(days=n)).isoformat() for n in range(10)}

def number(text):
    value=re.sub(r'[,\s\u00a0₮]','',text.strip())
    return float(value)

def parse_symbol(symbol):
    url='https://stock.bbe.mn/Home/Stock/'+symbol
    response=requests.get(url,timeout=24,headers={'User-Agent':'Mozilla/5.0 (compatible; ATLAS-Markets/1.0)'})
    response.raise_for_status()
    soup=BeautifulSoup(response.text,'html.parser')
    result={}
    for tr in soup.select('tr'):
        cells=[x.get_text(' ',strip=True) for x in tr.find_all('td',recursive=False)]
        if len(cells)<10 or not re.fullmatch(r'20\d\d-\d\d-\d\d',cells[0]):continue
        date=cells[0]
        if date not in DATES or dt.date.fromisoformat(date)>TODAY:continue
        try:
            # Date, Open, High, Low, Previous close, Close, Change MNT,
            # Change %, Volume, Value.
            o,h,l,c=map(number,[cells[1],cells[2],cells[3],cells[5]])
            volume=number(cells[8])
            value=number(cells[9])
            if not all(math.isfinite(x) for x in (o,h,l,c,volume,value)):continue
            if min(o,h,l,c)<=0 or volume<=0 or value<0 or volume!=int(volume):continue
            if l>min(o,c)+1e-6 or h+1e-6<max(o,c):continue
            result[date]=[date,o,h,l,c,int(volume),value]
        except (ValueError,IndexError):
            continue
    return symbol,result,url

def main():
    html=INDEX.read_text(encoding='utf-8')
    match=PATTERN.search(html)
    if not match:raise RuntimeError('Embedded stock dataset missing')
    data=json.loads(match.group(2))
    companies=data['companies']
    if len(companies)!=26:raise RuntimeError('Expected 26 tracked stocks')
    tickers=[c['symbol'] for c in companies]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(parse_symbol,tickers))
    prices={s:rs for s,rs,url in results}
    print('RECEIVED',json.dumps({s:sorted(rs) for s,rs in prices.items()},ensure_ascii=False),flush=True)
    # Verify representative values against the publicly dated 2026-11-11 archive.
    expected={'KHAN':2006.0,'GLMT':1274.0,'XAC':1426.0,'MNDL':85.0}
    if '2026-11-11' in DATES:
        for ticker,price in expected.items():
            row=prices.get(ticker,{}).get('2026-11-11')
            if row is None or abs(row[4]-price)>0.00001:
                raise RuntimeError('Cross-check failed '+ticker+' expected '+str(price)+' got '+str(row))
    additions=[]
    for c in companies:
        ticker=c['symbol']; history=c.get('priceHistory') or []
        existing={r[0]:r for r in history}
        added_for_stock=[]
        for date,entry in sorted(prices[ticker].items()):
            if date in existing:
                if any(abs(float(existing[date][i])-float(entry[i]))>max(0.01,abs(float(entry[i]))*0.001) for i in range(1,7)):
                    print('EXISTING_RECORD_PRESERVED',ticker,date,flush=True)
                continue
            if history and date < history[-1][0]:continue
            if date==history[-1][0] if history else False:continue
            history.append(entry)
            added_for_stock.append(date)
            additions.append([ticker,date,entry[4]])
        if not added_for_stock:continue
        history.sort(key=lambda r:r[0])
        c['priceHistory']=history
        m=c['metrics']
        current=history[-1];prev=history[-2] if len(history)>1 else None
        close=current[4];shares=c.get('shares') or 0;eps=m.get('eps');bvps=m.get('bvps');dps=m.get('dividends12mDPS')
        m['price']=close
        m['priceDate']=current[0]
        m['price1d']=100*(close/prev[4]-1) if prev and prev[4]>0 else None
        m['volume']=current[5]
        m['value']=current[6]
        m['marketcap']=close*shares/1e6 if shares>0 else None
        m['pe']=close/eps if eps and eps>0 else None
        m['pb']=close/bvps if bvps and bvps>0 else None
        m['divYield']=100*dps/close if dps is not None else None
    if not additions:
        print('NO_VERIFIED_NEW_RECORDS',flush=True)
        return
    d=data['summary']
    d['lastPriceAsOf']=max(c['metrics'].get('priceDate','') for c in companies)
    d['priceRecords']=sum(len(c['priceHistory']) for c in companies)
    d['totalMarketcapMillion']=sum(c['metrics'].get('marketcap') or 0 for c in companies)
    d['lastPriceSource']='https://stock.bbe.mn/Home/Stock/{ticker}'
    d['lastPriceRefreshAt']=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')
    d['updatedFrom']='Санхүү: Excel; ханшийн сүүлийн түүх: stock.bbe.mn'
    d['notes']=[n for n in d.get('notes',[]) if not n.startswith('Ханшийн авто шинэчлэлт:')]
    d['notes'].append('Ханшийн авто шинэчлэлт: '+d['lastPriceAsOf']+' хүртэлх арилжааны боломжтой OHLCV-г stock.bbe.mn брокерийн ханшийн бүртгэлээс авсан.')
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    result=html[:match.start(2)]+encoded+html[match.end(2):]
    tmp=INDEX.with_suffix('.tmp')
    tmp.write_text(result,encoding='utf-8')
    tmp.replace(INDEX)
    print('UPDATED_COUNT',len(additions),'LATEST',d['lastPriceAsOf'],flush=True)
    print('NEW_ROWS',json.dumps(additions,ensure_ascii=False),flush=True)
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('IMPORT_FAILED: '+str(error),file=sys.stderr)
        sys.exit(1)
