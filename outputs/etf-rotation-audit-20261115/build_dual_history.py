"""Build the common-cutoff history seed from the saved, cross-checked audit data."""
from pathlib import Path
import numpy as np
import pandas as pd
from audit import DIV, POOL, bars, score, targets, backtest


def main():
    market = bars("tencent")
    symbols = [*POOL, DIV]
    scores = score(market[1][symbols], 25)
    eligible = scores.notna().all(axis=1).shift(1, fill_value=False)
    start = eligible[eligible].index[0].date().isoformat()
    results = {}
    for key, pool in (("original", POOL), ("dividend", [*POOL[:-1], DIV])):
        sim = backtest(market, targets(scores[pool]), start=start, end="2026-11-13", guard=False)
        np.testing.assert_allclose(1+sim.ret, sim.equity/sim.equity.shift(fill_value=1))
        results[key+"_nav"] = sim.equity
        results[key+"_holding"] = sim[[*pool,"cash"]].idxmax(axis=1).replace("cash","")
    frame = pd.DataFrame(results)
    asset_returns = market[1].ffill().pct_change()
    frame["dividend_asset_ret"] = [asset_returns.at[day,s] if s else None for day,s in frame.dividend_holding.items()]
    assert frame.index.is_unique and frame.index[-1] == pd.Timestamp("2026-11-13")
    out = Path(__file__).resolve().parents[2] / "etf-momentum-dashboard" / "data" / "dual_history.csv"
    frame.to_csv(out, float_format="%.12f")
    trail = scores.loc[frame.index, symbols]
    assert np.isfinite(trail.to_numpy()).all()
    trail.to_csv(out.with_name("momentum_history.csv"), float_format="%.12f")
    print(frame.iloc[[0,-1]].to_string())
    print(len(frame), "shared sessions")


if __name__ == "__main__":
    main()
