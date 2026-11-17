"""Extract switch-day prices from the original public market snapshot (stdlib only)."""
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parents[1] / "etf-momentum-dashboard" / "data"


def main():
    raw = json.loads((ROOT / "market_data.json").read_text(encoding="utf-8"))
    with (DATA / "dual_history.csv").open(encoding="utf-8") as stream:
        history = list(csv.DictReader(stream))
    needed, old = set(), ""
    for row in history:
        held, day = row["dividend_holding"], row["date"]
        if held != old:
            needed.update((day, symbol) for symbol in (old, held) if symbol)
        old = held
    prices = []
    for symbol, bars in raw["tencent"].items():
        previous = None
        events = raw["events"].get(symbol, {})
        for day, op, close, _, _, volume in bars:
            if float(volume) <= 0:
                continue
            if (day, symbol) in needed:
                assert previous is not None
                last_day, last_close = previous
                split = math.prod(v for ex, v in events.get("splits", {}).items() if last_day < ex <= day)
                cash = sum(v for ex, v in events.get("dividends", {}).items() if last_day < ex <= day)
                reference = last_close / split - cash
                prices.append((day, symbol, float(op), float(op) / reference))
            previous = day, float(close)
    assert len(prices) == len(needed)
    with (DATA / "dividend_trade_prices.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("date", "symbol", "open", "overnight"))
        writer.writerows(sorted(prices))
    print(f"Wrote {len(prices)} verified switch-side prices")


if __name__ == "__main__":
    main()
