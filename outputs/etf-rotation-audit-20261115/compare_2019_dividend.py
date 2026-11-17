"""One-ETF substitution from 2019, without touching the operating dashboard."""
import json
import numpy as np
import pandas as pd

from audit import (ROOT, POOL, RAW, SSE180, DIV, GOLD, NASDAQ, CHINEXT, bars,
                   score, targets, backtest, stats, monthly, static, bootstrap, self_check)

START, END = '2019-01-01', '2026-11-13'
OUT = ROOT / 'dividend-comparison-2019'
POOLS = {'sse180': POOL, 'dividend': [*POOL[:-1], DIV]}
CASES = {
    'open_2bp': {},
    'open_10bp': {'fee': 10},
    'close_10bp': {'fee': 10, 'when': 'close'},
    'one_session_late_25bp': {'fee': 25, 'lag': 2},
    '500k_1pct_volume_10bp': {'fee': 10, 'participation': .01, 'capital': 500_000},
    '500k_5pct_volume_10bp': {'fee': 10, 'participation': .05, 'capital': 500_000},
}


def simulation(market, desired, **options):
    return backtest(market, desired, start=START, end=END, known_halts=True, **options)


def drawdowns(value):
    return value / value.cummax().clip(lower=1) - 1


def measure(result):
    return stats(result.ret) | {
        'trade_days': int(result.trade.sum()),
        'blocked_days': int(result.blocked.sum()),
        'fees_yuan_500k': float(result.cost.sum() * 500_000),
    }


def main():
    OUT.mkdir(exist_ok=True)
    market = bars('tencent')
    self_check(market)
    check_market = bars('sina')
    scores = score(market[1][[*POOL, DIV]], 25)
    results, metrics, stopped = {}, {}, {}

    def keep(case, name, run):
        results[case, name] = run
        metrics.setdefault(case, {})[name] = measure(run)
        return run

    for name, pool in POOLS.items():
        goal = targets(scores[pool])
        for case, options in CASES.items():
            keep(case, name, simulation(market, goal, **options))
        keep('sina_open_2bp', name, simulation(check_market, targets(score(check_market[1][pool], 25))))
        keep('equal_monthly', name, monthly(pool, market, start=START, end=END, known_halts=True))
        keep('single_etf', name, simulation(market, static([pool[-1]])))
        for window in (20, 24, 26, 28, 34):
            keep(f'window_{window}', name, simulation(market, targets(score(market[1][pool], window))))
        stress = results['500k_1pct_volume_10bp', name]
        hit = drawdowns(stress.equity).loc[lambda s: s <= -.15]
        if len(hit):
            limited = goal.copy()
            limited.loc[hit.index[0]:] = 0
            liquidate = keep('once_stop_15pct_500k', name, simulation(market, limited, **CASES['500k_1pct_volume_10bp']))
            assert np.allclose(liquidate.loc[:hit.index[0], 'equity'], stress.loc[:hit.index[0], 'equity'])
            cash = liquidate.loc[hit.index[0]:].query('cash >= .999999')
            stopped[name] = {'hit': hit.index[0].date().isoformat(), 'trigger_dd': float(hit.iloc[0]),
                             'exit': str(cash.index[0].date()) if len(cash) else None,
                             'final_return': float(liquidate.equity.iloc[-1] - 1)}

    base = {name: results['open_2bp', name] for name in POOLS}
    ret = pd.DataFrame({name: run.ret for name, run in base.items()})
    daily = pd.DataFrame({name: run.equity for name, run in base.items()})
    assert ret.index.is_unique and len(ret) == 1907
    assert (ret.index[0].date().isoformat(), ret.index[-1].date().isoformat()) == ('2019-01-02', END)
    assert np.allclose(daily, (1 + ret).cumprod())
    assert np.allclose(base['dividend'][SSE180], 0) and np.allclose(base['sse180'][DIV], 0)
    for name in POOLS:
        assert abs(metrics['open_2bp'][name]['cagr'] - metrics['sina_open_2bp'][name]['cagr']) < .005
    annual = (1 + ret).groupby(ret.index.year).prod() - 1
    annual['spread_pp'] = (annual.dividend - annual.sse180) * 100
    annual['relative_log'] = np.log1p(annual.dividend) - np.log1p(annual.sse180)
    annual['sse180_held_pct'] = base['sse180'][SSE180].groupby(ret.index.year).mean() * 100
    annual['dividend_held_pct'] = base['dividend'][DIV].groupby(ret.index.year).mean() * 100
    rel = np.log1p(ret.dividend) - np.log1p(ret.sse180)
    periods = {}
    for label, start, end in [('2019–2021', START, '2021-12-31'), ('2022–2024', '2022-01-01', '2024-12-31'),
                              ('2025–latest', '2025-01-01', END), ('post_new_180_methodology', '2024-12-16', END)]:
        periods[label] = {name: stats(ret[name].loc[start:end]) for name in POOLS}
    held = pd.DataFrame({name: run[[*pool, 'cash']].idxmax(axis=1)
                         for (name, pool), run in zip(POOLS.items(), base.values())})
    sleeves = {}
    for name, pool in POOLS.items():
        own = held[name] == pool[-1]
        other = 'dividend' if name == 'sse180' else 'sse180'
        mask = own & held[other].isin([GOLD, NASDAQ, CHINEXT])
        sleeves[name] = {'held_days': int(own.sum()), 'held_fraction': float(own.mean()),
                         'fourth_vs_shared_days': int(mask.sum()),
                         'fourth_vs_shared_log_difference_div_minus_old': float(rel[mask].sum())}
    price_rets = market[1].ffill().pct_change().loc[ret.index, [*POOL, DIV]]
    traded_liquidity = {}
    for symbol in (SSE180, DIV):
        market_liq = pd.DataFrame(RAW['tencent'][symbol], columns=['date','op','cl','hi','low','lot']).set_index('date').astype(float)
        traded_liquidity[symbol] = float((market_liq.op*market_liq.lot*100).loc['2025-01-01':END].median())
    pair_windows = {n: float((rel.rolling(n).sum().dropna() > 0).mean()) for n in (242, 726)}
    outcome = {'metrics': metrics, 'periods': periods, 'stop_trial': stopped,
               'same_close_holding_fraction': float((held.sse180 == held.dividend).mean()),
               'sleeves': sleeves, 'correlation': price_rets.corr().to_dict(),
               'recent_median_day_value_approx': traded_liquidity,
               'rolling_relative_win_fraction': pair_windows,
               'relative_block_bootstrap_annualized': {n: [r[1] for r in bootstrap(ret.dividend, ret.sse180, n)] for n in (20,60,120)}}
    (OUT / 'summary.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    annual.to_csv(OUT / 'annual.csv', encoding='utf-8-sig')
    pd.concat({'equity': daily, 'daily_return': ret, 'drawdown': daily.apply(drawdowns), 'held_at_close': held}, axis=1).to_csv(
        OUT / 'daily.csv', encoding='utf-8-sig')
    pd.DataFrame({(case,name):values for case, series in metrics.items() for name,values in series.items()}).T.to_csv(
        OUT / 'scenarios.csv', encoding='utf-8-sig', index_label=['case','pool'])
    print(pd.DataFrame({(case,name):values for case, series in metrics.items() for name,values in series.items()}).T[
        ['total','cagr','dd','vol','sharpe','trade_days']].round(4).to_string())
    print(annual.round(4).to_string())
    print(json.dumps({k:v for k,v in outcome.items() if k not in ('metrics','periods')}, indent=2))


if __name__ == '__main__':
    main()
