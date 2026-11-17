"""Two forward-only paper accounts, marked from independently verified closes."""
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import re

from pilot import CAPITAL, MODEL_FEE, PREMIUM_CAP, STOP_DD, TICKERS, cents

DIV = "sh510880"
NASDAQ = "sh513100"
POOLS = {"original": TICKERS, "dividend": (*TICKERS[:-1], DIV)}
SYMBOLS = (*TICKERS, DIV)
OPEN, CLOSE = time(9, 35), time(9, 50)
TICK = Decimal(".001")
FEE = Decimal(str(MODEL_FEE))
SPREAD = Decimal(".003")
SLIPPAGE = Decimal(".002")
PREMIUM = Decimal(str(PREMIUM_CAP))
ORDER_CAP = 100_000


def quote_rows(text, prefix):
    return dict(re.findall(rf'{prefix}((?:sh|sz)\d{{6}})="([^"]*)"', text))


def parse_quotes(tencent, sina, now):
    tx, sn = quote_rows(tencent, "v_"), quote_rows(sina, "hq_str_")
    result = {}
    for s in SYMBOLS:
        try:
            q, a = tx[s].split("~"), sn[s].split(",")
            stamp = datetime.strptime(q[30], "%Y%m%d%H%M%S").replace(tzinfo=now.tzinfo)
            source_time = datetime.fromisoformat(f"{a[30]}T{a[31]}").replace(tzinfo=now.tzinfo)
            if q[2] != s[2:] or a[32] != "00" or not all(OPEN <= t.time() < CLOSE for t in (stamp,source_time)):
                continue
            if any(not timedelta(seconds=-3) <= now-t <= timedelta(seconds=30) for t in (stamp, source_time)):
                continue
            bid, ask, high, low = map(float, (q[9], q[19], q[47], q[48]))
            if not (0 < low < high < 10000 and low <= bid <= ask <= high):
                continue
            if int(q[6]) <= 0 or int(a[8]) <= 0:
                continue
            mirror_bid, mirror_ask = float(a[11]), float(a[21])
            if not (0 < mirror_bid <= mirror_ask < 10000) or max(abs(bid-mirror_bid),abs(ask-mirror_ask)) > .00101:
                continue
            bid_qty, ask_qty = min(int(q[10])*100, int(a[10])), min(int(q[20])*100, int(a[20]))
            if min(bid_qty, ask_qty) <= 0:
                continue
            ref = None
            if s == NASDAQ and len(q) > 78:
                try:
                    ref_price, reported = float(q[78]), float(q[77]) / 100
                    if 0 < ref_price < 10000 and abs((float(q[3])/ref_price-1)-reported) <= .0002:
                        ref = ref_price
                except ValueError:
                    pass
            result[s] = {"stamp": stamp.isoformat(), "bid": bid, "ask": ask, "up": high, "down": low,
                         "bid_qty": bid_qty, "ask_qty": ask_qty, "iopv": ref, "volume": int(q[6])*100}
        except (KeyError, ValueError, IndexError, OverflowError):
            continue
    return result


def tradable(q, side):
    if not q:
        return "盘口/停牌未核对"
    bid, ask = Decimal(str(q["bid"])), Decimal(str(q["ask"]))
    if (ask-bid)*2 > (ask+bid)*SPREAD:
        return "买卖价差超过 0.30%"
    if (side == "buy" and q["ask"] >= q["up"]) or (side == "sell" and q["bid"] <= q["down"]):
        return "触及涨跌停一侧"
    return None


def nasdaq_limit(q):
    if not q["iopv"]:
        return None, "纳指 IOPV 缺失或未核对"
    cap = Decimal(str(q["iopv"]))*(1+PREMIUM)
    if Decimal(str(q["ask"])) >= cap:
        return None, "纳指溢价达到 3.00%"
    return cap.quantize(TICK, rounding=ROUND_CEILING)-TICK, None


def blank():
    return {"cash": CAPITAL, "qty": 0, "holding": "", "pending_buy": False, "receivables": {},
            "peak": CAPITAL, "equity": CAPITAL, "ret": 0, "dd": 0, "mdd": 0, "stopped": None, "order_count": 0}


def replay_day(previous, day, target, frames, closes, actions):
    book = dict(previous)
    cash, qty, symbol = Decimal(str(book["cash"])), book["qty"], book["holding"]
    receivables = {k: Decimal(str(v)) for k, v in book["receivables"].items()}
    if symbol:
        for ex, payout, amount in actions[symbol]["dividends"]:
            if ex == day:
                receivables[payout] = receivables.get(payout, Decimal(0)) + cents(qty*Decimal(amount))
        for ex, ratio in actions[symbol]["splits"].items():
            if ex == day:
                shares = qty*Decimal(ratio)
                if shares != int(shares):
                    raise ValueError("模拟持仓拆并份额尾数待基金公告核对")
                qty = int(shares)
    for payout in sorted(list(receivables)):
        if payout <= day:
            cash += receivables.pop(payout)

    plan = "" if book["stopped"] else target
    sell = symbol if plan is not None and symbol != plan else ""
    buy = plan if plan and (plan != symbol or book["pending_buy"]) else ""
    filled, remaining = [], ""
    if plan is None:
        remaining = "信号或盘前数据未就绪"
    elif (sell or buy) and "09:35" not in frames:
        remaining = "未采到 09:35 盘口，跳过"
    elif sell or buy:
        anchor = frames["09:35"]
        quote_sell, quote_buy = anchor.get(sell), anchor.get(buy)
        remaining = ((tradable(quote_sell, "sell") if sell else None)
                     or (tradable(quote_buy, "buy") if buy else None))
        if not remaining and buy == NASDAQ:
            _, remaining = nasdaq_limit(quote_buy)
        if not remaining:
            sell_floor = (Decimal(str(quote_sell["bid"]))*(1-SLIPPAGE)).quantize(TICK, rounding=ROUND_CEILING) if sell else None
            buy_ceiling = (Decimal(str(quote_buy["ask"]))*(1+SLIPPAGE)).quantize(TICK, rounding=ROUND_FLOOR) if buy else None
            seen = {}
            for minute, snapshot in sorted(frames.items()):
                if not "09:35" <= minute < "09:50":
                    continue
                side, code = ("sell", sell) if sell and qty else ("buy", buy)
                if not code:
                    break
                q = snapshot.get(code)
                if tradable(q, side):
                    continue
                prev = seen.get(code)
                if prev and (q["stamp"] <= prev["stamp"] or q["volume"] <= prev["volume"]):
                    continue
                seen[code] = q
                price = Decimal(str(q["bid"] if side == "sell" else q["ask"]))
                if side == "sell" and price < sell_floor or side == "buy" and price > buy_ceiling:
                    continue
                if side == "buy" and code == NASDAQ:
                    ceiling, invalid = nasdaq_limit(q)
                    if invalid or price > ceiling:
                        continue
                wanted = qty if side == "sell" else int(cash/(price*(1+FEE)))
                depth = q["bid_qty"] if side == "sell" else q["ask_qty"]
                if prev:
                    depth = min(depth, q["volume"]-prev["volume"])
                shares = min(wanted, depth, int(ORDER_CAP/price))
                if side == "buy" or shares != wanted:
                    shares = shares//100*100
                if shares <= 0:
                    continue
                gross = cents(price*shares)
                cost = cents(gross*FEE)
                if side == "sell":
                    qty -= shares
                    cash += gross-cost
                    if not qty:
                        symbol = ""
                elif gross+cost <= cash:
                    qty += shares
                    cash -= gross+cost
                    symbol = code
                else:
                    continue
                filled.append({"minute": minute, "side": side, "symbol": code, "qty": shares, "price": float(price),
                               "fee": float(cost), "iopv": q["iopv"] if code == NASDAQ and side == "buy" else None})
                if side == "buy" and cash < price*100*(1+FEE):
                    break
            if sell and symbol == sell:
                remaining = "09:50 卖出未完成，保留余仓"
            elif buy and (symbol != buy or cash >= buy_ceiling*100*(1+FEE)):
                remaining = "09:50 买入未完成，保留现金"
            else:
                remaining = ""

    pending = previous["pending_buy"] if plan is None else bool(buy and symbol == buy and (remaining or not filled))
    if symbol and not closes.get(symbol):
        raise ValueError("模拟持仓缺少可核对收盘")
    equity = cents(cash + sum(receivables.values(), Decimal(0)) +
                   (qty*Decimal(str(closes[symbol])) if symbol else 0))
    peak = max(float(equity), previous["peak"])
    dd = float(equity)/peak-1
    stop = previous["stopped"] or (day if dd <= -STOP_DD + 1e-12 else None)
    book.update(cash=float(cash), qty=qty, holding=symbol, pending_buy=pending,
                receivables={k:float(v) for k,v in receivables.items()},
                peak=peak, equity=float(equity), ret=float(equity)/CAPITAL-1,
                daily_ret=float(equity)/previous["equity"]-1, dd=dd, mdd=min(previous["mdd"],dd),
                stopped=stop, order_count=previous["order_count"]+len(filled), filled=filled,
                target=target, note=remaining or ("已模拟成交" if filled else "已退出，持有现金" if stop and not symbol else "继续持有"))
    assert cash >= 0 and (qty > 0) == bool(symbol) and math.isfinite(dd)
    return book
