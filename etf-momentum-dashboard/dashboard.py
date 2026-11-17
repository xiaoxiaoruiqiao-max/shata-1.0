"""Extend the verified backtest and serve its read-only dashboard."""
import argparse
import csv
import json
import math
import os
import tempfile
from contextlib import contextmanager
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as clock_time, timedelta, timezone
from functools import lru_cache, partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import pilot
import shadow

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("ETF_STATE_DIR", ROOT / "data"))
WEB = ROOT / "web"
CHINA = timezone(timedelta(hours=8))
ETF = pilot.TICKERS
INDEX = "sh000300"
FEE = pilot.MODEL_FEE
SEED = "2026-11-13"
CLOSE = "_adj_close"
OPEN = "_adj_open"
SPLIT_RE = re.compile(r"<td>(\d{4}-\d\d-\d\d)</td>\s*<td>份额(?:折算|分拆|拆分|合并)</td>\s*<td>1:([\d.]+)</td>")
DIV_RE = re.compile(r"<td>\d{4}-\d\d-\d\d</td>\s*<td>(\d{4}-\d\d-\d\d)</td>\s*<td>每10份派现金([\d.]+)元</td>")
UPDATE_TIMES = (clock_time(18, 15), clock_time(18, 35), clock_time(19, 5), clock_time(8, 15), clock_time(8, 45))
JOURNAL = STATE / "pilot.json"
SHADOW = STATE / "shadow.json"
SHADOW_TIMES = (clock_time(18, 17), clock_time(18, 37), clock_time(19, 7), clock_time(8, 20), clock_time(8, 47))


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except Exception:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def update_lock(filename="update.lock"):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / filename).open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def load_history():
    with (ROOT / "data/history.csv").open(encoding="utf-8", newline="") as stream:
        history = []
        for row in csv.DictReader(stream):
            for key, value in row.items():
                if key in ("trade", "blocked"):
                    row[key] = value == "True"
                elif key not in ("date", "holding", "old", "target"):
                    row[key] = float(value) if value else None
            history.append(row)
    return history


def read_state():
    path = STATE / "state.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"extra": [], "checked_at": None, "status": "seed", "error": None, "premium": {
        "date": "2026-11-12", "value": 2.218 / 1.8923 - 1,
    }}


def request_text(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urlopen(request, timeout=18) as response:
        return response.read().decode("utf-8-sig")


def parse_bar(op, close, volume):
    op, close, volume = float(op), float(close), float(volume)
    if not all(map(math.isfinite, (op, close, volume))) or min(op, close) <= 0 or volume < 0:
        raise ValueError("行情数值无效")
    return op, close, volume


def tencent(symbol, start, end):
    for host in ("ifzq.gtimg.cn", "web.ifzq.gtimg.cn"):
        try:
            data = json.loads(request_text(
                f"https://{host}/appstock/app/fqkline/get?param={symbol},day,{start},{end},500,"
            ))
            return {r[0]: parse_bar(r[1], r[2], r[5]) for r in data["data"][symbol]["day"]}
        except (OSError, ValueError, KeyError):
            continue
    raise ValueError(f"{symbol} 腾讯行情不可用")


def sina(symbol):
    body = request_text(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20k=/CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen=240"
    )
    match = re.search(r"var k=\((\[.*\])\);", body, re.S)
    if not match:
        raise ValueError(f"{symbol} 新浪行情不可用")
    return {r["day"]: parse_bar(r["open"], r["close"], r["volume"]) for r in json.loads(match[1])}


def corporate_actions(symbol):
    url = f"https://fundf10.eastmoney.com/fhsp_{symbol[2:]}.html"
    body = request_text(url)
    if "分红送配" not in body or symbol[2:] not in body:
        raise ValueError(f"{symbol} 基金权益事件不可用")
    dividends = {day: float(amount) / 10 for day, amount in DIV_RE.findall(body)}
    splits = {day: float(ratio) for day, ratio in SPLIT_RE.findall(body)}
    return dividends, splits


def premium():
    body = request_text("https://fund.eastmoney.com/pingzhongdata/513100.js")
    points = json.loads(re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", body, re.S)[1])
    for row in reversed(points[-10:]):
        if row["y"] > 0:
            return datetime.fromtimestamp(row["x"] / 1000, CHINA).date().isoformat(), row["y"]
    raise ValueError("纳指单位净值不可用")


def momentum(values):
    if len(values) != 25 or any(value is None or value <= 0 for value in values):
        raise ValueError("连续价格不足")
    xs = range(25)
    ws = [1 + x / 24 for x in xs]
    ys = [math.log(value) for value in values]
    xm = sum(w * x for w, x in zip(ws, xs)) / sum(ws)
    ym = sum(w * y for w, y in zip(ws, ys)) / sum(ws)
    xx = sum(w * (x-xm)**2 for w, x in zip(ws, xs))
    xy = sum(w * (x-xm) * (y-ym) for w, x, y in zip(ws, xs, ys))
    slope = xy / xx
    total = sum(w * (y-ym)**2 for w, y in zip(ws, ys))
    residual = sum(w * (y-ym-slope*(x-xm))**2 for w, x, y in zip(ws, xs, ys))
    return math.expm1(252 * slope) * max(0, min(1, 1-residual/total)) if total else 0.0


def closures(year):
    path = STATE / "closures.json"
    if not path.exists():
        path = ROOT / "data/closures.json"
    schedules = json.loads(path.read_text(encoding="utf-8"))
    return set(schedules[str(year)]) if str(year) in schedules else None


def update_calendar():
    body = request_text("https://www.sse.com.cn/disclosure/dealinstruc/closed/")
    match = re.search(r"<strong>\s*(\d{4})年休市安排\s*</strong>(.*?)</table>", body, re.S)
    if not match:
        raise ValueError("上交所年度休市表格式已变化")
    year = int(match[1])
    block = re.sub(r"<[^>]+>", "", match[2])
    spans = re.findall(r"(\d{1,2})月(\d{1,2})日[^至\d]{0,12}至(\d{1,2})月(\d{1,2})日[^\d]{0,12}休市", block)
    if len(spans) != 7:
        raise ValueError("上交所休市表条目不足")
    days = set()
    for sm, sd, em, ed in spans:
        start = date(year,int(sm),int(sd))
        end = date(year + (int(em) < int(sm)),int(em),int(ed))
        while start <= end:
            days.add(start.isoformat())
            start += timedelta(days=1)
    path = STATE / "closures.json"
    existing = json.loads((path if path.exists() else ROOT / "data/closures.json").read_text(encoding="utf-8"))
    for yr in {day[:4] for day in days}:
        old = set(existing.get(yr, []))
        updated = {day for day in days if day.startswith(yr)}
        existing[yr] = sorted(updated if int(yr) == year else old | updated)
    atomic_json(path, existing)


def trading_day(day):
    if day.weekday() >= 5:
        return False
    holidays = closures(day.year)
    return None if holidays is None else day.isoformat() not in holidays


def last_due_day(now):
    current = now.date()
    if now.time() < UPDATE_TIMES[0]:
        current -= timedelta(days=1)
    while trading_day(current) is False:
        current -= timedelta(days=1)
    return current


def next_session(day):
    current = date.fromisoformat(day) + timedelta(days=1)
    while trading_day(current) is False:
        current += timedelta(days=1)
    return current.isoformat() if trading_day(current) else None


def append_day(history, day, bars, index_return, actions):
    previous = history[-1]
    equity = previous["rotation"]
    current = {"date": day, "old": previous["holding"],
               "target": max(ETF, key=lambda symbol: previous[symbol+"_score"]),
               "000300": previous["000300"] * index_return}
    tradable = set()
    for symbol in ETF:
        current.update({symbol+"_open": None, symbol+"_close": None,
                        symbol+OPEN: None, symbol+CLOSE: previous[symbol+CLOSE]})
        if symbol not in bars:
            continue
        op, close, volume = bars[symbol]
        last = next(row for row in reversed(history) if row[symbol+"_close"] is not None)
        dividends, splits = actions[symbol]
        factor = math.prod(value for ex, value in splits.items() if last["date"] < ex <= day)
        cash = sum(value for ex, value in dividends.items() if last["date"] < ex <= day)
        reference = last[symbol+"_close"] / factor - cash
        if reference <= 0 or abs(close / reference - 1) > 0.205:
            raise ValueError(f"{symbol} {day} 价格跳变，需核对除权或行情")
        if volume <= 0:
            continue
        tradable.add(symbol)
        current.update({symbol+"_open": op, symbol+"_close": close,
                        symbol+OPEN: previous[symbol+CLOSE] * op / reference,
                        symbol+CLOSE: previous[symbol+CLOSE] * close / reference})
    old, target = current["old"], current["target"]
    trade = old != target and old in tradable and target in tradable
    cost = 0.0
    if trade:
        before_sale = equity * current[old+OPEN] / previous[old+CLOSE]
        after_sale = before_sale * (1-FEE)
        after_purchase = after_sale / (1+FEE)
        equity = after_purchase * current[target+CLOSE] / current[target+OPEN]
        cost = before_sale-after_purchase
    else:
        equity *= current[old+CLOSE] / previous[old+CLOSE]
    peak = max(equity, previous["rotation"] / (1+previous["dd"]))
    current.update({"rotation": equity, "ret": equity / previous["rotation"] - 1,
                    "dd": equity / peak - 1, "holding": target if trade else old,
                    "trade": trade, "blocked": old != target and not trade, "fee": cost})
    for symbol in ETF:
        prices = [row[symbol+CLOSE] for row in history[-24:]]
        current[symbol+"_score"] = momentum(prices + [current[symbol+CLOSE]])
    return current


def refresh(seed, now=None):
    with update_lock():
        return refresh_locked(seed, now or datetime.now(CHINA))


def refresh_locked(seed, now):
    state = read_state()
    history = seed + state["extra"]
    last = history[-1]["date"]
    if closures(now.year) is None or (now.month == 12 and closures(now.year+1) is None):
        try:
            update_calendar()
        except (OSError, ValueError) as error:
            print(f"休市日历更新待重试：{error}", flush=True)
    expected = last_due_day(now)
    start = (date.fromisoformat(last) - timedelta(days=7)).isoformat()
    finish = expected.isoformat()
    try:
        if now.date().isoformat() < last:
            raise ValueError("系统日期早于已发布行情")
        if trading_day(expected) is None:
            raise ValueError(f"{expected.year} 年交易日历待确认")
        with ThreadPoolExecutor(max_workers=15) as pool:
            tx = {s: pool.submit(tencent, s, start, (expected+timedelta(days=1)).isoformat()) for s in (INDEX,*ETF)}
            sn = {s: pool.submit(sina,s) for s in (INDEX,*ETF)}
            ca = {s: pool.submit(corporate_actions,s) for s in ETF}
            prem = pool.submit(premium)
            tx = {s: f.result() for s,f in tx.items()}
            sn = {s: f.result() for s,f in sn.items()}
            ca = {s: f.result() for s,f in ca.items()}
            try:
                nav_day, nav_value = prem.result()
            except Exception:
                nav_day = nav_value = None
        dates = sorted(day for day in tx[INDEX] if last < day <= finish)
        if finish > last and (not dates or dates[-1] != finish):
            raise ValueError(f"等待 {finish} 两路收盘行情")
        if dates != sorted(day for day in sn[INDEX] if last < day <= finish):
            raise ValueError("腾讯与新浪交易日覆盖不一致")
        gap = date.fromisoformat(last) + timedelta(days=1)
        date_set = set(dates)
        while gap <= expected:
            if trading_day(gap) is True and gap.isoformat() not in date_set:
                raise ValueError(f"缺少 {gap} 交易日，已保留上一版")
            gap += timedelta(days=1)
        if last not in tx[INDEX] or last not in sn[INDEX]:
            raise ValueError("更新基准缺少与已发布行情的重叠交易日")
        if abs(tx[INDEX][last][1]-sn[INDEX][last][1]) > .03:
            raise ValueError("两路基准重叠收盘价不一致")
        for s in ETF:
            overlap = next(row for row in reversed(history) if row[s+"_close"] is not None)
            key = overlap["date"]
            if key not in tx[s] or key not in sn[s]:
                raise ValueError(f"{s} 缺少已发布行情的重叠交易日")
            if max(abs(tx[s][key][1]-overlap[s+"_close"]),abs(sn[s][key][1]-overlap[s+"_close"])) > .0011:
                raise ValueError(f"{s} 已发布收盘与行情源不一致")
        last_index = sn[INDEX][last][1]
        halt_path = STATE / "confirmed_halts.json"
        halts = json.loads(halt_path.read_text(encoding="utf-8")) if halt_path.exists() else {}
        for day in dates:
            if abs(tx[INDEX][day][1]-sn[INDEX][day][1]) > .03:
                raise ValueError(f"{day} 基准收盘价不一致")
            bars = {}
            for symbol in ETF:
                a, b = sn[symbol].get(day), tx[symbol].get(day)
                if a is None and b is None:
                    if symbol in halts.get(day, {}):
                        continue
                    # A missing row has no reliable suspension marker in these feeds.
                    raise ValueError(f"{symbol} {day} 双源缺少行情，需核对停牌")
                if a is None or b is None or max(abs(a[0]-b[0]),abs(a[1]-b[1])) > .0011:
                    raise ValueError(f"{symbol} {day} 双源开收盘不一致")
                bars[symbol] = (a[0], a[1], max(a[2], b[2]))
            index_close = sn[INDEX][day][1]
            history.append(append_day(history, day, bars, index_close / last_index, ca))
            last_index = index_close
        if nav_day:
            same_day = next((row for row in reversed(history) if row["date"] == nav_day), None)
            if same_day and same_day["sh513100_close"] and nav_day >= (state["premium"] or {}).get("date", ""):
                state["premium"] = {"date": nav_day, "value": same_day["sh513100_close"] / nav_value - 1}
        state.update(extra=history[len(seed):], checked_at=now.isoformat(timespec="seconds"), status="ok", error=None)
    except Exception as error:
        state.update(checked_at=now.isoformat(timespec="seconds"), status="error", error=str(error))
    atomic_json(STATE / "state.json", state)
    return state


def trades(history):
    result = []
    active, entry = None, 1
    for index, row in enumerate(history):
        if not row["trade"]:
            continue
        before = history[index-1]["rotation"] if index else 1
        if active:
            old = active["symbol"]
            sale = before * row[old+OPEN] / history[index-1][old+CLOSE] * (1-FEE)
            result.append({**active, "exit": row["date"], "exit_price": row[old+"_open"],
                           "ret": sale / entry - 1, "pnl": (sale-entry) * 1_000_000,
                           "sessions": index-active["index"], "open": False})
            entry = sale
        symbol = row["holding"]
        active = {"symbol": symbol, "entry": row["date"], "entry_price": row[symbol+"_open"],
                  "index": index, "proxy": row[symbol+"_open"] is None}
    last = history[-1]
    result.append({**active, "exit": None, "exit_price": None,
                   "ret": last["rotation"]/entry-1, "pnl": (last["rotation"]-entry)*1_000_000,
                   "sessions": len(history)-active["index"], "open": True})
    assert math.isclose(sum(t["pnl"] for t in result), (last["rotation"]-1)*1_000_000, abs_tol=.001)
    return result[::-1]


def calendar_summary(rows, base_date):
    summary = {}
    anchor, benchmark = 1, 1
    for index, row in enumerate(rows):
        year = row["date"][:4]
        month = row["date"][5:7]
        y = summary.setdefault(year, {"year": year, "start_value": anchor, "benchmark_start": benchmark,
                                      "mdd": 0, "peak": anchor, "peak_date": rows[index-1]["date"] if index else base_date,
                                      "draw_peak": None, "trough": None, "months": {}})
        value = row["rotation"]
        if value > y["peak"]:
            y.update(peak=value, peak_date=row["date"])
        dd = value/y["peak"]-1
        if dd < y["mdd"]:
            y.update(mdd=dd, draw_peak=y["peak_date"], trough=row["date"])
        m = y["months"].setdefault(month, {"start":anchor})
        m["ret"] = value/m["start"]-1
        m["end"] = row["date"]
        y["ret"] = value/y["start_value"]-1
        y["benchmark"] = row["000300"]/y["benchmark_start"]-1
        anchor, benchmark = value, row["000300"]
    return list(summary.values())


def journal():
    return json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else []


def dashboard(seed, state, events=(), now=None):
    rows = seed + state["extra"]
    latest = rows[-1]
    days = (date.fromisoformat(latest["date"])-date(2012,12,31)).days
    target = max(ETF, key=lambda s: latest[s+"_score"])
    now = now or datetime.now(CHINA)
    due = last_due_day(now)
    session = next_session(latest["date"])
    ready = bool(session and session >= now.date().isoformat() and trading_day(due) and due.isoformat() <= latest["date"]
                 and state["status"] == "ok" and all(latest[s+"_close"] is not None for s in ETF))
    live = pilot.evaluate(events, rows)
    if live["daily"]:
        live_rows = [{"date":r[0], "rotation":r[1], "000300":r[2]} for r in live["daily"]]
        elapsed = (date.fromisoformat(live_rows[-1]["date"])-date.fromisoformat(live["start"])).days
        nav = live_rows[-1]["rotation"]
        positions = {r[0]:r for r in live["daily"]}
        for i, row in enumerate(rows):
            actual = positions.get(row["date"])
            if actual and actual[5] and i:
                s = actual[5]
                actual[10] = row[s+CLOSE]/rows[i-1][s+CLOSE]-1
        live.update(as_of=live_rows[-1]["date"], years=calendar_summary(live_rows, live["start"]),
                    summary={"ret": nav-1, "cagr": nav**(365.2425/elapsed)-1 if elapsed >= 365 else None,
                             "mdd": min(r[4] for r in live["daily"]), "dd": live["daily"][-1][4], "nav": nav})
    else:
        live.update(as_of=None, years=[], summary=None)
    return {"as_of": latest["date"], "start": rows[0]["date"], "history_end": SEED,
            "due_date": due.isoformat(), "next_session": session, "signal_ready": ready,
            "session_today": trading_day(now.date()) is True,
            "now": now.isoformat(timespec="seconds"), "pilot": live, "premium_cap": pilot.PREMIUM_CAP,
            "checked_at": state["checked_at"], "status": state["status"], "error": state["error"],
            "premium": state["premium"], "holding": latest["holding"], "target":target,
            "summary": {"ret":latest["rotation"]-1, "cagr":latest["rotation"]**(365.2425/days)-1,
                        "mdd":min(row["dd"] for row in rows), "dd":latest["dd"], "nav":latest["rotation"]},
            "assets":[{"symbol":s, "score":latest[s+"_score"], "close":latest[s+"_close"],
                       "ret25":latest[s+CLOSE]/rows[-26][s+CLOSE]-1} for s in ETF],
            "years":calendar_summary(rows, "2012-12-31"), "trades":trades(rows),
            "daily":[[r["date"],r["rotation"],r["000300"],r["ret"],r["dd"],r["holding"],r["old"],
                      r["trade"],r["blocked"],r["target"],
                      r[r["holding"]+CLOSE]/rows[i-1][r["holding"]+CLOSE]-1 if i else None] for i,r in enumerate(rows)]}


def latest_schedule_slot(now, times):
    slots = [now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0) for t in times]
    return max(slot if slot <= now else slot-timedelta(days=1) for slot in slots)


def schedule(seed):
    observed = None
    while True:
        now = datetime.now(CHINA)
        minute = now.replace(second=0,microsecond=0)
        slot = latest_schedule_slot(now, UPDATE_TIMES)
        if observed != slot:
            observed = slot
            try:
                state = refresh(seed, now)
                print(now.isoformat(timespec="minutes"), state["status"], state.get("error") or "行情已核对", flush=True)
            except Exception as error:
                print(f"更新未完成：{error}", flush=True)
        time.sleep(12)


def load_shadow():
    if SHADOW.exists():
        return json.loads(SHADOW.read_text(encoding="utf-8"))
    with (ROOT / "data/shadow_red_seed.csv").open(encoding="utf-8", newline="") as file:
        red = [[r["date"], float(r["close"]), float(r["adjusted"])] for r in csv.DictReader(file)]
    if len(red) != 26 or red[-1][0] != SEED:
        raise ValueError("红利动量预热数据不完整")
    return {"start":None, "red":red, "days":[], "pending":{}, "error":None, "checked_at":None, "actions":None}


def shadow_actions(symbol):
    body = request_text(f"https://fundf10.eastmoney.com/fhsp_{symbol[2:]}.html")
    if "分红送配" not in body or symbol[2:] not in body:
        raise ValueError(f"{symbol} 权益事件不可用")
    full = re.findall(DIV_RE.pattern + r"\s*<td>(\d{4}-\d\d-\d\d)</td>", body)
    if len(full) != len(DIV_RE.findall(body)):
        raise ValueError(f"{symbol} 缺少分红发放日")
    return {"dividends": [[ex,pay,str(pilot.D(amount)/10)] for ex,amount,pay in full],
            "splits": dict(SPLIT_RE.findall(body))}


def shadow_signals(history, state):
    red = {row[0]:row[1:] for row in state["red"]}
    common = [r for r in history if r["date"] <= state["red"][-1][0]][-26:]
    day = common[-1]["date"]
    scores = {s:common[-1][s+"_score"] for s in ETF}
    if len(common) > 25 and all(r["date"] in red for r in common[-25:]):
        scores[shadow.DIV] = momentum([red[r["date"]][1] for r in common[-25:]])
    ranks = {name:[{"symbol":s, "score":scores[s]} for s in sorted(pool,key=lambda s:scores.get(s,-math.inf),reverse=True)
                   if s in scores] for name,pool in shadow.POOLS.items()}
    ready = (len(scores) == 5 and red.get(day, [None])[0] is not None
             and all(common[-1][s+"_close"] is not None for s in ETF))
    return day, ranks, ready


def shadow_refresh(seed, now, state):
    base = read_state()
    history = seed + base["extra"]
    due = last_due_day(now).isoformat()
    if not state["start"]:
        today = now.date()
        state["start"] = (today.isoformat() if trading_day(today) is True and now.time() < shadow.OPEN
                          else next_session(today.isoformat()))
    if due != history[-1]["date"] or base["status"] != "ok":
        raise ValueError("等待原组合收盘双源数据")
    known = next(r for r in reversed(state["red"]) if r[1] is not None)
    repair_from = min((r[0] for r in state["red"] if r[0]>SEED and r[1] is not None and len(r)<4), default=known[0])
    start = (date.fromisoformat(repair_from)-timedelta(days=7)).isoformat()
    with ThreadPoolExecutor(max_workers=7) as pool:
        tx = pool.submit(tencent,shadow.DIV,start,(date.fromisoformat(due)+timedelta(days=1)).isoformat())
        sn = pool.submit(sina,shadow.DIV)
        actions = dict(zip(shadow.SYMBOLS,pool.map(shadow_actions,shadow.SYMBOLS)))
        tx, sn = tx.result(), sn.result()
    if any(known[0] not in feed or abs(feed[known[0]][1]-known[1]) > .0011 for feed in (tx,sn)):
        raise ValueError("510880 重叠收盘价未通过双源核对")
    final_red = state["red"][-1][0]
    red = []
    for row in state["red"]:
        if row[0]>SEED and len(row)<4:
            if row[1] is None:
                row = [*row,None]
            else:
                a,b = tx.get(row[0]),sn.get(row[0])
                if a and b and max(abs(a[0]-b[0]),abs(a[1]-row[1]),abs(b[1]-row[1])) <= .0011:
                    row = [*row,row[2]*a[0]/row[1]]
        red.append(row)
    halts_path = STATE / "confirmed_halts.json"
    halts = json.loads(halts_path.read_text(encoding="utf-8")) if halts_path.exists() else {}
    for row in history:
        day = row["date"]
        if not final_red < day <= due:
            continue
        a,b = tx.get(day),sn.get(day)
        if a is None and b is None and shadow.DIV in halts.get(day, {}):
            red.append([day, None, red[-1][2], None])
            continue
        if a is None or b is None or max(abs(a[0]-b[0]),abs(a[1]-b[1])) > .0011 or min(a[2],b[2]) <= 0:
            raise ValueError(f"{day} 510880 收盘缺失或双源不一致")
        last = next(r for r in reversed(red) if r[1] is not None)
        event = actions[shadow.DIV]
        split = math.prod(float(ratio) for ex,ratio in event["splits"].items() if last[0] < ex <= day)
        income = sum(float(amount) for ex,_,amount in event["dividends"] if last[0] < ex <= day)
        reference = last[1]/split-income
        if reference <= 0 or abs(a[1]/reference-1) > .105:
            raise ValueError(f"{day} 510880 除权或行情跳变待核对")
        red.append([day, a[1], last[2]*a[1]/reference, last[2]*a[0]/reference])

    pending, days = state["pending"].copy(), list(state["days"])
    last_mark = days[-1]["date"] if days else ""
    red_closes = {r[0]:r[1] for r in red}
    closes = {}
    for row in history:
        day = row["date"]
        for s in shadow.SYMBOLS:
            value = red_closes.get(day) if s == shadow.DIV else row[s+"_close"]
            if value is not None:
                closes[s] = value
        if not state["start"] or day < state["start"] or day <= last_mark or day > due:
            continue
        session = pending.pop(day, {})
        morning = session.get("actions")
        if morning and any(([e for e in actions[s]["dividends"] if e[0] == day or e[1] == day],
                             actions[s]["splits"].get(day)) !=
                            ([e for e in morning[s]["dividends"] if e[0] == day or e[1] == day],
                             morning[s]["splits"].get(day)) for s in shadow.SYMBOLS):
            session = {}
        books = {name:shadow.replay_day(days[-1][name] if days else shadow.blank(),day,
                                       session.get("targets",{}).get(name), session.get("frames",{}),
                                       closes,actions) for name in shadow.POOLS}
        if session.get("skip_reason"):
            for book in books.values():
                if not book["filled"]:
                    book["note"] = session["skip_reason"]
        days.append({"date":day, **books})
    state.update(red=red, days=days, pending=pending, actions=actions, error=None)


def shadow_quote(url, referer):
    request = Request(url,headers={"User-Agent":"Mozilla/5.0","Referer":referer,"Cache-Control":"no-cache"})
    with urlopen(request,timeout=6) as response:
        return response.read().decode("gb18030")


def shadow_capture(seed, now, state):
    day, minute = now.date().isoformat(), now.strftime("%H:%M")
    if not state["start"] or day < state["start"]:
        return False
    if day not in state["pending"]:
        base = read_state()
        history = seed+base["extra"]
        asof, ranks, ready = shadow_signals(history,state)
        on_time = (minute == "09:35" and ready and base["status"] == "ok" and not state["error"]
                   and history[-1]["date"] == asof and next_session(asof) == day
                   and (day == state["start"] or (state["days"] and state["days"][-1]["date"] == asof))
                   and state["actions"] and (state["checked_at"] or "")[:10] == day)
        state["pending"][day] = {"targets": {k:v[0]["symbol"] for k,v in ranks.items()} if on_time else {},
                                 "actions":state["actions"] if on_time else None, "frames":{}}
    session = state["pending"][day]
    if not session["targets"]:
        return minute == "09:35"
    if minute in session["frames"]:
        return False
    codes = ",".join(shadow.SYMBOLS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tx = pool.submit(shadow_quote, f"https://qt.gtimg.cn/q={codes}", "https://gu.qq.com/")
        sn = pool.submit(shadow_quote, f"https://hq.sinajs.cn/list={codes}", "https://finance.sina.com.cn/")
        frames = shadow.parse_quotes(tx.result(),sn.result(),datetime.now(CHINA))
    if datetime.now(CHINA).strftime("%H:%M") != minute:
        return False
    if not frames:
        return False
    session["frames"][minute] = frames
    return True


def update_shadow(seed, now, capture=False):
    with update_lock("shadow.lock"):
        state = load_shadow()
        if capture:
            day = now.date().isoformat()
            existing = day in state["pending"]
            changed = False
            try:
                changed = shadow_capture(seed,now,state)
            finally:
                if changed or (not existing and day in state["pending"]):
                    atomic_json(SHADOW,state)
        else:
            day = now.date().isoformat()
            if (state["start"] and state["start"] <= day and now.time() >= shadow.CLOSE
                    and trading_day(now.date()) is True and day not in state["pending"]
                    and not any(row["date"] == day for row in state["days"])):
                state["pending"][day] = {"targets": {}, "actions": None, "frames": {},
                    "skip_reason": "错过 09:35 采集窗口，未采到当时盘口；今日跳过，不补造成交"}
            try:
                shadow_refresh(seed,now,state)
            except (OSError,ValueError,KeyError) as error:
                state["error"] = str(error)
                print("并行模拟待核对",error,flush=True)
            state["checked_at"] = now.isoformat(timespec="seconds")
            atomic_json(SHADOW,state)


def schedule_shadow(seed):
    last_refresh = None
    startup_retries = 0
    while True:
        now = datetime.now(CHINA)
        minute = now.replace(second=0,microsecond=0)
        slot = latest_schedule_slot(now, SHADOW_TIMES)
        try:
            if shadow.OPEN <= now.time() < shadow.CLOSE and now.second >= 10 and trading_day(now.date()) is True:
                update_shadow(seed,now,capture=True)
            elif last_refresh != slot:
                if last_refresh is None and startup_retries < 3 and read_state()["status"] == "seed":
                    startup_retries += 1
                else:
                    last_refresh = slot
                    update_shadow(seed,now)
        except (OSError,ValueError,KeyError) as error:
            print("并行模拟采集待重试",error,flush=True)
        time.sleep(8)


def shadow_view(seed):
    state, base, now = load_shadow(), read_state(), datetime.now(CHINA)
    day, ranks, ready = shadow_signals(seed+base["extra"],state)
    latest = state["days"][-1] if state["days"] else {}
    pending = state["pending"].get(now.date().isoformat(),{})
    fresh = ready and not state["error"] and base["status"] == "ok" and last_due_day(now).isoformat() == day
    return {"start":state["start"], "as_of":day, "next_session":next_session(day) if fresh else None,
            "checked_at":state["checked_at"], "error":state["error"], "ranks":ranks, "pending_frames":len(pending.get("frames",{})),
            "session_note":pending.get("skip_reason"), "capture_open":shadow.OPEN <= now.time() < shadow.CLOSE,
            "latest":{name:latest.get(name) for name in shadow.POOLS}, "latest_day":latest.get("date"),
            "series":[[r["date"],*[r[k][f] for k in shadow.POOLS for f in ("ret","dd")]] for r in state["days"]],
            "recent":state["days"][-10:][::-1]}


@lru_cache(maxsize=1)
def load_dual_history():
    with (ROOT/"data/dual_history.csv").open(encoding="utf-8", newline="") as stream:
        rows = tuple((r["date"],float(r["original_nav"]),float(r["dividend_nav"]),
                      r["original_holding"],r["dividend_holding"],
                      float(r["dividend_asset_ret"]) if r["dividend_asset_ret"] else None) for r in csv.DictReader(stream))
    if not rows or rows[-1][0] != SEED:
        raise ValueError("两版历史回测底稿不完整")
    return rows


def dual_next(equity, holding, target, prev_close, op, close):
    if target != holding and op[target] and (not holding or op[holding]):
        if holding:
            equity *= op[holding]/prev_close[holding]*(1-FEE)
        return equity/(1+FEE)*close[target]/op[target],target
    if holding:
        equity *= close[holding]/prev_close[holding]
    return equity,holding


def dividend_report(series, history):
    first = series[0][0]
    anchor = next(row for row in reversed(history) if row["date"] < first)
    index = {row["date"]: row["000300"] / anchor["000300"] for row in history}
    daily, rows = [], []
    previous = peak = 1
    for day, _, nav, _, holding, *_ in series:
        peak = max(peak, nav)
        daily.append([day, nav, index[day], nav / previous - 1, nav / peak - 1])
        rows.append({"date": day, "rotation": nav, "000300": index[day]})
        previous = nav
    elapsed = (date.fromisoformat(series[-1][0]) - date.fromisoformat(anchor["date"])).days
    return {"start": first, "base_date": anchor["date"], "as_of": series[-1][0], "daily": daily,
            "years": calendar_summary(rows, anchor["date"]),
            "summary": {"ret": previous - 1, "nav": previous,
                        "cagr": previous ** (365.2425 / elapsed) - 1,
                        "mdd": min(row[4] for row in daily)}}


@lru_cache(maxsize=1)
def load_dividend_trade_prices():
    with (ROOT / "data/dividend_trade_prices.csv").open(encoding="utf-8", newline="") as stream:
        return {(r["date"], r["symbol"]): (float(r["open"]), float(r["overnight"]))
                for r in csv.DictReader(stream)}


def dividend_trades(series, history, red):
    prices = dict(load_dividend_trade_prices())
    red_index = {r[0]: r for r in red}
    for previous, row in zip(history, history[1:]):
        day = row["date"]
        if day <= SEED:
            continue
        for symbol in ETF:
            if row[symbol+OPEN] is not None:
                prices[day, symbol] = row[symbol+"_open"], row[symbol+OPEN] / previous[symbol+CLOSE]
        current_red, old_red = red_index.get(day), red_index.get(previous["date"])
        if current_red and old_red and len(current_red) >= 4 and current_red[3] is not None:
            prices[day, shadow.DIV] = (current_red[3] * current_red[1] / current_red[2],
                                       current_red[3] / old_red[2])
    result, active, entry, old = [], None, 1, ""
    for index, row in enumerate(series):
        day, _, nav, _, held, *_ = row
        if held == old:
            continue
        if active:
            sale_price, overnight = prices[day, old]
            sale = series[index-1][2] * overnight * (1-FEE)
            result.append({**active, "exit": day, "exit_price": sale_price,
                           "ret": sale / entry - 1, "pnl": (sale-entry) * 1_000_000,
                           "sessions": index-active["index"], "open": False})
            entry = sale
        active = {"symbol": held, "entry": day, "entry_price": prices[day, held][0],
                  "index": index, "proxy": False}
        old = held
    result.append({**active, "exit": None, "exit_price": None,
                   "ret": series[-1][2]/entry-1, "pnl": (series[-1][2]-entry)*1_000_000,
                   "sessions": len(series)-active["index"], "open": True})
    return result[::-1]


def dual_view(seed, now=None):
    base, state = read_state(), load_shadow()
    full = seed[-26:]+base["extra"]
    series = list(load_dual_history())
    red = state["red"]
    red_index = {r[0]:i for i,r in enumerate(red)}
    for old, new in zip(full,full[1:]):
        day = new["date"]
        if day <= SEED:
            continue
        old_red, curr_red = red_index.get(old["date"]), red_index.get(day)
        if old_red is None or old_red < 24 or curr_red is None or len(red[curr_red]) < 4:
            break
        scores = {s:old[s+"_score"] for s in ETF}
        scores[shadow.DIV] = momentum([r[2] for r in red[old_red-24:old_red+1]])
        prev_close = {s:old[s+CLOSE] for s in ETF} | {shadow.DIV:red[old_red][2]}
        op = {s:new[s+OPEN] for s in ETF} | {shadow.DIV:red[curr_red][3]}
        close = {s:new[s+CLOSE] for s in ETF} | {shadow.DIV:red[curr_red][2]}
        last = series[-1]
        a, held_a = dual_next(last[1],last[3],max(ETF,key=scores.get),prev_close,op,close)
        b, held_b = dual_next(last[2],last[4],max(shadow.POOLS["dividend"],key=scores.get),prev_close,op,close)
        asset_ret = close[held_b]/prev_close[held_b]-1 if held_b else None
        series.append((day,a,b,held_a,held_b,asset_ret))
    asof, ranks, ready = shadow_signals(full,state)
    latest = series[-1][0]
    now = now or datetime.now(CHINA)
    stale = (not ready or bool(state["error"]) or base["status"] != "ok" or
             latest != asof or last_due_day(now).isoformat() > latest)
    return {"as_of":latest,"rank_as_of":asof,"stale":stale,"ranks":ranks,"series":series,
            "dividend_report": dividend_report(series, seed + base["extra"]),
            "dividend_trades": dividend_trades(series, seed + base["extra"], red)}


@lru_cache(maxsize=1)
def load_momentum_history():
    with (ROOT/"data/momentum_history.csv").open(encoding="utf-8", newline="") as stream:
        rows = tuple((r["date"],*(float(r[s]) for s in shadow.SYMBOLS)) for r in csv.DictReader(stream))
    if not rows or rows[-1][0] != SEED:
        raise ValueError("动量轨迹底稿不完整")
    return rows


def momentum_view(now=None):
    base, state = read_state(), load_shadow()
    series = list(load_momentum_history())
    red = state["red"]
    by_date = {r[0]:i for i,r in enumerate(red)}
    for r in base["extra"]:
        day = r["date"]
        if day <= SEED:
            continue
        i = by_date.get(day,-1)
        if i < 24:
            break
        red_score = momentum([x[2] for x in red[i-24:i+1]])
        series.append((day,*(r[s+"_score"] for s in ETF),red_score))
    latest = series[-1][0]
    now = now or datetime.now(CHINA)
    stale = bool(state["error"]) or base["status"] != "ok" or last_due_day(now).isoformat() > latest
    return {"as_of":latest,"stale":stale,"symbols":shadow.SYMBOLS,"series":series}


class Handler(SimpleHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def parse_request(self):
        if not super().parse_request():
            return False
        hosts = self.headers.get_all("Host", [])
        allowed = {"localhost", "127.0.0.1", "::1"} | {
            h.strip().lower() for h in os.environ.get("ETF_ALLOWED_HOSTS", "").split(",") if h.strip()}
        try:
            parsed = urlsplit("//" + (hosts[0] if len(hosts) == 1 else ""))
            valid = (parsed.hostname in allowed and not parsed.username and not parsed.password
                     and not parsed.path and not parsed.query and not parsed.fragment)
            parsed.port  # Reject malformed ports before comparing Origin and Host.
        except ValueError:
            valid = False
        if not valid:
            self.send_error(403, "Host not allowed")
            return False
        return True

    def end_headers(self):
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                         "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def do_HEAD(self):
        if not self.static_path():
            self.send_error(404)
            return
        super().do_HEAD()

    def static_path(self):
        return self.path.split("?")[0] in ("/", "/index.html", "/styles.css", "/app.js", "/pilot.js",
                    "/shadow.js", "/dual.js", "/dividend.js", "/momentum.js", "/mark.svg")

    def send_json(self, value, status=200):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        if self.path != "/api/pilot":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if (not host or origin not in (f"http://{host}", f"https://{host}") or
                self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or
                self.headers.get("Content-Type") != "application/json"):
            self.send_json({"error":"账本只能在本站页面修改"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4096:
                raise ValueError("账本请求长度无效")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("账本请求格式无效")
            now = datetime.now(CHINA)
            with update_lock():
                entries = journal()
                if body.get("revision") != len(entries):
                    self.send_json({"error":"账本已变化，请刷新后重试"}, 409)
                    return
                if body.get("kind") == "undo":
                    entries = entries[:-1]
                else:
                    item = pilot.event(body, now.date(), trading_day)
                    if item.get("date") == now.date().isoformat() and item.get("time", "") > now.strftime("%H:%M"):
                        raise ValueError("成交时间尚未到达")
                    pilot.replay(entries + [item])
                    entries.append(item)
                atomic_json(JOURNAL, entries)
        except ValueError as error:
            self.send_json({"error":str(error)}, 400)
            return
        except OSError:
            self.send_json({"error":"正在处理行情或账本，请稍后重试"}, 503)
            return
        self.send_json({"ok":True})

    def do_GET(self):
        if self.path.split("?")[0] == "/api/momentum":
            try:
                self.send_json(momentum_view())
            except (ValueError,OSError,KeyError) as error:
                self.log_error("读取动量轨迹失败 %s",error)
                self.send_json({"error":"动量轨迹暂不可读"},503)
            return
        if self.path.split("?")[0] == "/api/dual-history":
            try:
                self.send_json(dual_view(self.server.seed))
            except (ValueError,OSError,KeyError) as error:
                self.log_error("读取双版历史失败 %s",error)
                self.send_json({"error":"双版历史暂不可读"},503)
            return
        if self.path.split("?")[0] == "/api/shadow":
            try:
                result = shadow_view(self.server.seed)
            except (ValueError, OSError, KeyError) as error:
                self.log_error("读取并行模拟失败 %s", error)
                self.send_json({"error":"并行模拟数据暂不可读"},503)
                return
            self.send_json(result)
            return
        if self.path.split("?")[0] == "/api/dashboard":
            state_file = STATE / "state.json"
            try:
                versions = tuple(p.stat().st_mtime_ns if p.exists() else None for p in (state_file, JOURNAL))
                # Refresh the status clock around the scheduled publication even if the dataset is unchanged.
                key = (versions, datetime.now(CHINA).isoformat(timespec="minutes"))
                with self.server.cache_lock:
                    if self.server.api_key != key:
                        self.server.api_body = json.dumps(dashboard(self.server.seed, read_state(), journal()), ensure_ascii=False,
                                                          allow_nan=False, separators=(",", ":")).encode()
                        self.server.api_key = key
                    body = self.server.api_body
            except (ValueError, OSError, KeyError) as error:
                self.log_error("读行情失败 %s", error)
                self.send_error(503,"Dashboard temporarily unavailable")
                return
            self.send_json(body)
            return
        if not self.static_path():
            self.send_error(404)
            return
        super().do_GET()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve","refresh","check"])
    parser.add_argument("--bind", default=os.environ.get("ETF_BIND","127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ETF_PORT","8080")))
    parser.add_argument("--no-schedule", action="store_true")
    args = parser.parse_args()
    seed = load_history()
    assert len(seed)==3365 and seed[-1]["date"]==SEED
    if args.command=="refresh":
        state=refresh(seed)
        print(json.dumps({k:v for k,v in state.items() if k!="extra"}, ensure_ascii=False))
        raise SystemExit(0 if state["status"]=="ok" else 1)
    if args.command=="check":
        d = dashboard(seed,read_state())
        print(json.dumps({"as_of":d["as_of"],"trades":len(d["trades"]),"summary":d["summary"]}, ensure_ascii=False))
        return
    if not args.no_schedule:
        threading.Thread(target=schedule,args=(seed,),daemon=True).start()
        threading.Thread(target=schedule_shadow,args=(seed,),daemon=True).start()
    server = ThreadingHTTPServer((args.bind,args.port),partial(Handler,directory=WEB))
    server.seed, server.api_key = seed, None
    server.cache_lock = threading.Lock()
    print(f"Dashboard http://{args.bind}:{args.port}",flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
