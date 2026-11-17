"""Fetch unadjusted public daily bars and keep the inputs used by the audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import json
import re
import subprocess
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SYMBOLS = ('sh518880', 'sh513100', 'sz159915', 'sh510180', 'sh510300', 'sh000300', 'sh513500', 'sh510880')
END = '2026-11-13'
TENCENT = 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param='
SINA = 'https://finance.sina.com.cn/realstock/company/{}/hisdata/klc_kl.js'
EVENTS = 'https://fundf10.eastmoney.com/fhsp_{}.html'
NAV = 'https://fund.eastmoney.com/pingzhongdata/513100.js'
DECODER = 'https://raw.githubusercontent.com/akfamily/akshare/main/akshare/stock/cons.py'


def get(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=14) as response:
                return response.read().decode('utf-8-sig')
        except OSError:
            if attempt == 2:
                raise


def tx(job):
    symbol, start, end = job
    raw = json.loads(get(f'{TENCENT}{symbol},day,{start},{end},500,'))
    assert raw['code'] == 0, (job, raw['code'])
    rows = raw['data'].get(symbol, {}).get('day', [])
    return symbol, [r[:6] for r in rows if str(start) <= r[0] <= str(end)]


def events(symbol):
    html = get(EVENTS.format(symbol[2:]))
    assert '分红送配' in html
    dividends = {d: float(a) / 10 for d, a in re.findall(r'<td>\d{4}-\d\d-\d\d</td>\s*<td>(\d{4}-\d\d-\d\d)</td>\s*<td>每10份派现金([\d.]+)元</td>', html)}
    splits = {d: float(a) for d, a in re.findall(r'<td>(\d{4}-\d\d-\d\d)</td>\s*<td>份额(?:折算|分拆|拆分|合并)</td>\s*<td>1:([\d.]+)</td>', html)}
    return symbol, {'dividends': dividends, 'splits': splits}


def main():
    jobs = [(s, date(y, 1, 1), min(date(y + 2, 1, 1) - timedelta(days=1), date.fromisoformat(END)))
            for s in SYMBOLS for y in range(2012, 2027, 2)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        tx_results = pool.map(tx, jobs)
        event_results = pool.map(events, (s for s in SYMBOLS if s != 'sh000300'))
        sina_results = pool.map(get, (SINA.format(s) for s in SYMBOLS))
        decoder = pool.submit(get, DECODER)
        nav = pool.submit(get, NAV)
        tencent = {s: [] for s in SYMBOLS}
        for symbol, rows in tx_results:
            tencent[symbol].extend(rows)
        coded = [re.search(r'"([\w+/]+)"', x)[1] for x in sina_results]
        code = decoder.result().split('hk_js_decode = """', 1)[1].split('"""', 1)[0]
        parsed = subprocess.run(['node', '-e', code + '\nprocess.stdout.write(JSON.stringify(JSON.parse(require("fs").readFileSync(0,"utf8")).map(d)));'],
                                input=json.dumps(coded), capture_output=True, text=True, check=True, encoding='utf-8')
        sina = {s: [[r['date'][:10], r['open'], r['close'], r['high'], r['low'], r['volume']] for r in rows
                    if '2012-01-01' <= r['date'][:10] <= END]
                for s, rows in zip(SYMBOLS, json.loads(parsed.stdout))}
        points = json.loads(re.search(r'Data_netWorthTrend\s*=\s*(\[.*?\]);', nav.result(), re.S)[1])
        data = {'tencent': tencent, 'sina': sina, 'events': dict(event_results), 'nav_513100': points}
    for symbol in SYMBOLS:
        a, b = tencent[symbol], sina[symbol]
        assert a and b and len({r[0] for r in a}) == len(a), symbol
        print(symbol, len(a), a[0][0], a[-1][0], len(b), b[0][0], b[-1][0])
    (ROOT / 'market_data.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


if __name__ == '__main__':
    main()
