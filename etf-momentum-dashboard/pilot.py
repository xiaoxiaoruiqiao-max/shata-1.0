"""Journal broker-confirmed events for one ring-fenced RMB 500,000 trial."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CAPITAL = 500_000
MODEL_FEE = .0002
PREMIUM_CAP = .03
STOP_DD = .15
REVIEW_DAYS = 365
REVIEW_EXITS = 15
REVIEW_SLIPPAGE_BP = 25
TICKERS = ("sh518880", "sh513100", "sz159915", "sh510180")
BLOCKS = ("premium", "halt", "liquidity", "unfilled", "data", "risk")
D = Decimal


def decimal(value, minimum, maximum, places=3):
    try:
        amount = D(str(value))
        if amount.is_finite() and D(str(minimum)) <= amount <= D(str(maximum)) and amount == round(amount, places):
            return amount
    except (InvalidOperation, ValueError, OverflowError):
        pass
    raise ValueError("数值或小数位不符合账本要求")


def event(raw, today, is_session):
    if not isinstance(raw, dict):
        raise ValueError("账本记录格式无效")
    kind = raw.get("kind")
    try:
        day = date.fromisoformat(raw.get("date", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("请填写有效日期") from exc
    if day > today or not is_session(day):
        raise ValueError("请选择已到达且已确认的交易日")
    entry = {"kind": kind, "date": day.isoformat()}
    if kind == "start":
        return entry
    if kind == "skip":
        if raw.get("reason") not in BLOCKS:
            raise ValueError("请填写跳过原因")
        return entry | {"reason": raw["reason"]}
    if kind == "dividend":
        return entry | {"amount": str(decimal(raw.get("amount"), .01, CAPITAL, 2))}
    symbol = raw.get("symbol")
    if symbol not in TICKERS:
        raise ValueError("ETF 不在试验范围内")
    qty = int(decimal(raw.get("qty"), 1, 50_000_000, 0))
    entry |= {"symbol": symbol, "qty": qty}
    if kind == "units":
        return entry
    if kind not in ("buy", "sell"):
        raise ValueError("未知账本操作")
    try:
        stamp = datetime.strptime(f"{day}T{raw.get('time','')}", "%Y-%m-%dT%H:%M")
    except ValueError as exc:
        raise ValueError("请填写券商成交时间（北京时间）") from exc
    if not ("09:15" <= stamp.strftime("%H:%M") <= "15:00"):
        raise ValueError("成交时间不在场内交易时段")
    if kind == "buy" and qty % 100:
        raise ValueError("买入份额须为 100 的整数倍")
    return entry | {"time": stamp.strftime("%H:%M"),
                    "price": str(decimal(raw.get("price"), .001, 10_000)),
                    "fee": str(decimal(raw.get("fee"), 0, 50_000, 2)),
                    "reference": str(decimal(raw["reference"], .001, 10_000)) if raw.get("reference") else None,
                    "iopv": str(decimal(raw["iopv"], .0001, 10_000, 4)) if kind == "buy" and symbol == "sh513100"
                    and raw.get("iopv") else None}


def cents(amount):
    return amount.quantize(D(".01"), rounding=ROUND_HALF_UP)


@dataclass
class Book:
    start: str = ""
    last_date: str = ""
    last_time: str = ""
    cash: Decimal = D(CAPITAL)
    cost: Decimal = D(0)
    qty: int = 0
    holding: str = ""
    exits: int = 0
    slippage_value: Decimal = D(0)
    slippage_base: Decimal = D(0)
    missing_refs: int = 0
    outside: int = 0
    premium_issues: int = 0
    today_buys: int = 0
    tape: list = field(default_factory=list)

    def post(self, e):
        day, kind = e["date"], e["kind"]
        time = e.get("time", "")
        if day < self.last_date or (day == self.last_date and time and time < self.last_time):
            raise ValueError("请按日期和成交时间顺序登记，必要时撤回末条后重录")
        if day != self.last_date:
            self.today_buys = 0
        result = dict(e)
        if kind == "start":
            if self.start:
                raise ValueError("试验已经开始")
            self.start = day
        elif not self.start:
            raise ValueError("请先登记试验起始日")
        elif kind == "dividend":
            self.cash += D(e["amount"])
        elif kind == "units":
            if self.holding != e["symbol"]:
                raise ValueError("只能更正当前持仓的权益变动")
            self.qty = e["qty"]
        elif kind in ("buy", "sell"):
            symbol, qty, price, fee = e["symbol"], e["qty"], D(e["price"]), D(e["fee"])
            gross = cents(qty * price)
            if kind == "buy":
                if self.holding not in ("", symbol):
                    raise ValueError("前一只 ETF 还有剩余持仓")
                debit = gross + fee
                if debit > self.cash:
                    raise ValueError("买入金额超出账本可用余额")
                self.cash -= debit
                self.qty += qty
                self.today_buys += qty
                self.cost += debit
                self.holding = symbol
                if symbol == "sh513100":
                    iopv = D(e["iopv"]) if e.get("iopv") else None
                    if iopv is not None:
                        result["premium"] = float(price/iopv-1)
                    if iopv is None or price >= iopv*(1+D(str(PREMIUM_CAP))):
                        self.premium_issues += 1
            else:
                if self.holding != symbol or qty > self.qty or gross < fee:
                    raise ValueError("卖出数量、标的或费用与账本不符")
                if symbol in ("sz159915", "sh510180") and qty > self.qty - self.today_buys:
                    raise ValueError("境内股票 ETF 当日买入份额不可当日卖出")
                received = gross - fee
                cost = self.cost if qty == self.qty else cents(self.cost * qty / self.qty)
                pnl = received - cost
                self.cash += received
                self.qty -= qty
                self.cost -= cost
                result |= {"pnl": float(pnl), "ret": float(pnl/cost) if cost else None}
                if not self.qty:
                    self.exits += 1
                    self.holding = ""
                    self.today_buys = 0
            if e["reference"]:
                ref = D(e["reference"])
                signed = (price-ref) if kind == "buy" else (ref-price)
                self.slippage_base += ref * qty
                self.slippage_value += max(D(0), signed) * qty
                result["slippage_bp"] = float(signed / ref * 10_000)
            else:
                self.missing_refs += 1
            if not "09:35" <= time <= "09:50":
                self.outside += 1
        self.last_time = time or (self.last_time if self.last_date == day else "")
        self.last_date = day
        self.tape.append(result)


def replay(events):
    book = Book()
    for e in events:
        book.post(e)
    return book


def evaluate(events, history):
    account = replay(events)
    result = {"start": account.start, "capital": CAPITAL, "holding": account.holding, "qty": account.qty,
              "cash": float(account.cash), "events": account.tape[::-1], "daily": [], "stop": False, "checks": []}
    if not account.start:
        return result
    book, benchmark_month, benchmark_units = Book(), None, None
    events = iter(events)
    pending = next(events, None)
    prices, daily = {}, result["daily"]
    peak = nav = 1
    prev_holding = ""
    for row in history:
        day = row["date"]
        for s in TICKERS:
            if row[s+"_close"] is not None:
                prices[s] = row[s+"_close"]
        if day < account.start:
            continue
        switched = skipped = False
        while pending and pending["date"] <= day:
            book.post(pending)
            switched |= pending["kind"] in ("buy", "sell")
            skipped |= pending["kind"] == "skip"
            pending = next(events, None)
        if benchmark_month != day[:7]:
            opens = [row[s+"_adj_open"] for s in TICKERS]
            if all(opens):
                amounts = [q * o for q, o in zip(benchmark_units, opens)] if benchmark_units else [0]*4
                gross = sum(amounts) if benchmark_units else 1
                net = gross
                for _ in range(5):
                    net = gross - MODEL_FEE * sum(abs(net/4 - v) for v in amounts)
                benchmark_month = day[:7]
                benchmark_units = [net/4 / o for o in opens]
        baseline = sum(q*row[s+"_adj_close"] for s, q in zip(TICKERS, benchmark_units)) if benchmark_units else 1
        previous = nav
        nav = float(book.cash + (book.qty * D(str(prices[book.holding])) if book.holding else 0))/CAPITAL
        peak = max(peak, nav)
        dd = nav/peak - 1
        daily.append([day, nav, baseline, nav/previous-1 if previous else None, dd, book.holding, prev_holding,
                      switched, skipped, row["target"], None])
        prev_holding = book.holding
    elapsed = (date.fromisoformat(daily[-1][0]) - date.fromisoformat(account.start)).days if daily else 0
    dd = min((row[4] for row in daily), default=0)
    lagging = bool(pending) or not daily
    slippage = float(account.slippage_value / account.slippage_base * 10000) if account.slippage_base else None
    net, ref = (daily[-1][1]-1, daily[-1][2]-1) if daily else (None, None)
    result.update(stop=dd <= -STOP_DD + 1e-12, lagging=lagging, slippage=slippage, exits=account.exits,
                  checks=[
                      ["至少运行 365 天", elapsed >= REVIEW_DAYS, f"{elapsed} 天"],
                      ["至少完成 15 个完整持仓周期", account.exits >= REVIEW_EXITS, f"{account.exits} 个"],
                      ["扣费累计收益高于同池等权月调仓", net is not None and net > ref, None],
                      ["收盘最大回撤小于 15%", bool(daily) and dd > -STOP_DD + 1e-12, None],
                      ["按金额加权的单边不利滑点不超过 25 bp，成交均有报价记录",
                       slippage is not None and not account.missing_refs and slippage <= REVIEW_SLIPPAGE_BP,
                       f"缺 {account.missing_refs} 笔报价" if account.missing_refs else None],
                      ["成交都在执行窗口，成交后的收盘估值已齐",
                       not account.outside and not lagging, f"{account.outside} 笔窗外成交" if account.outside else None],
                      ["所有纳指买入均有参考净值记录且实际溢价低于 3%",
                       not account.premium_issues, f"{account.premium_issues} 笔待复核" if account.premium_issues else None],
                  ])
    return result
