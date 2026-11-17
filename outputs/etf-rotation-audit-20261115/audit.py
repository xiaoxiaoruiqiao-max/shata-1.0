"""Reproduce the post with public daily bars; run predeclared stress tests."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

ROOT = Path(__file__).resolve().parent
RAW = json.loads((ROOT / 'market_data.json').read_text(encoding='utf-8'))
GOLD, NASDAQ, CHINEXT, SSE180, CSI, INDEX, SP500, DIV = RAW['tencent']
POOL = [GOLD, NASDAQ, CHINEXT, SSE180]
START, END, LIVE = '2013-07-31', '2026-10-16', '2026-10-26'
HUB = '2013-10-15'  # Every original ETF has at least 50 post-listing bars by this date.
DAYS = pd.DatetimeIndex([r[0] for r in RAW['tencent'][INDEX]], name='date')
SYMS = list(RAW['tencent'])
CASH = len(SYMS)
HALTS = pd.to_datetime(['2026-06-22', '2026-09-07', '2026-09-17', '2026-09-30', '2026-11-03', '2026-11-09', '2026-11-10', '2026-11-13'])


def bars(source, adjusted=True):
    op, cl, volume, ref = (pd.DataFrame(index=DAYS, columns=SYMS, dtype=float) for _ in range(4))
    for s, rows in RAW[source].items():
        d = pd.DataFrame(rows, columns=['date', 'open', 'close', 'high', 'low', 'volume']).set_index('date').astype(float)
        d.index = pd.to_datetime(d.index)
        d = d[d.volume > 0]
        e = RAW['events'].get(s, {}) if adjusted else {}
        o, c = d.open.copy(), d.close.copy()
        before = d.close.shift()
        for kind in ('splits', 'dividends'):
            for event, value in sorted(e.get(kind, {}).items()):
                i = d.index.searchsorted(event)
                if 0 < i < len(d):
                    before.iloc[i] = before.iloc[i] / value if kind == 'splits' else before.iloc[i] - value
        if adjusted:
            assert (before.dropna() > 0).all()
            assert (abs(c / before - 1).dropna() < .21).all(), (s, (c / before - 1).abs().nlargest(3))
        factor = (d.close.shift() / before).fillna(1).cumprod()
        op[s], cl[s], volume[s], ref[s] = o * factor, c * factor, d.volume, before
    return op, cl, volume, ref


def score(close, n, method='wls'):
    out = pd.DataFrame(index=close.index)
    w = np.linspace(1, 2, n) if method != 'ols' else np.ones(n)
    if method == 'wls_squared':
        w **= 2
    x = np.arange(n) - np.average(np.arange(n), weights=w)
    for s in close:
        c = close[s].ffill()
        y = sliding_window_view(np.log(c.to_numpy()), n)
        y = y - np.average(y, weights=w, axis=1)[:, None]
        slope = y @ (w*x) / np.dot(w, x*x)
        tot = (y*y) @ w
        if method == 'return':
            v = c.pct_change(n-1)
        else:
            r2 = np.divide(slope**2*np.dot(w, x*x), tot, out=np.zeros_like(tot), where=tot > 1e-20)
            v = pd.Series(np.expm1(slope * 252) * np.clip(r2, 0, 1), index=c.index[n-1:])
        out[s] = v.where(close[s].notna().cumsum() >= n)
    return out


def targets(sc, top=1, positive=False):
    ranks = sc.where(sc > 0) if positive else sc
    w = (ranks.rank(axis=1, method='first', ascending=False) <= top).astype(float)
    return w.div(w.sum(axis=1).clip(lower=1), axis=0)


def backtest(mkt, desired, start=START, end=END, fee=2, when='open', guard=True, known_halts=False, lag=1,
             rebalance_monthly=False, stop_score=None, participation=0, capital=1e6):
    op, cl, volume, ref = mkt
    nav = cl.ffill()
    raw_move = op / nav.shift() - 1
    close_move = nav / nav.shift()
    for s in SYMS:
        if cl[s].first_valid_index() is not None:
            close_move.loc[cl[s].first_valid_index(), s] = 1
    execution = op if when == 'open' else cl
    before = (execution / nav.shift()).fillna(1).to_numpy()
    after = (nav / execution).fillna(1).to_numpy()
    cc = close_move.fillna(1).to_numpy()
    ok = execution.notna().to_numpy()
    up = (raw_move if when == 'open' else close_move - 1).to_numpy()
    limit = np.full(ok.shape, .10)
    limit[DAYS >= '2020-08-24', SYMS.index(CHINEXT)] = .20
    # Half a tick on the exchange's rounded price bound, with a small numeric tolerance.
    eps = (.00051 / ref).fillna(0).to_numpy()
    if known_halts:
        ok[DAYS.isin(HALTS), SYMS.index(NASDAQ)] = False
    selection = desired.reindex(columns=SYMS, fill_value=0).shift(lag).fillna(0)
    sel = selection.to_numpy()
    sel = np.column_stack((sel, 1 - sel.sum(axis=1)))
    assert (sel.min() >= -1e-12) and np.allclose(sel.sum(1), 1)
    cash = np.r_[np.zeros(CASH), 1.]
    pos, last = cash.copy(), cash.copy()
    last_month = None
    quarantine = np.zeros(CASH, dtype=bool)
    if stop_score is not None:
        stop_ranks = stop_score.reindex(columns=SYMS).shift().fillna(-np.inf).to_numpy()
        can_reenter = (nav > nav.rolling(4).mean()).shift(fill_value=False).to_numpy()
    if participation:
        # shortcut: daily volume cannot pin down auction depth, use intraday replay for execution estimates.
        dollar_volume = pd.DataFrame({s:{pd.Timestamp(r[0]):float(r[5])*100*float(r[1])
                                           for r in RAW['tencent'][s]} for s in SYMS}).reindex(DAYS)
        prior = dollar_volume.rolling(20, min_periods=5).median().shift()
        caps = np.minimum(dollar_volume, prior).fillna(0).to_numpy() * (participation / capital)
    rows = []
    bp = fee / 10000
    for i in np.flatnonzero((DAYS >= start) & (DAYS <= end)):
        target = sel[i]
        if stop_score is not None:
            quarantine &= ~can_reenter[i]
            quarantine |= (pos[:-1] > 0) & (cc[i-1] <= .96)
            best = np.where(quarantine, -np.inf, stop_ranks[i]).argmax()
            target = cash.copy()
            if not quarantine[best] and np.isfinite(stop_ranks[i, best]):
                target[[best, CASH]] = [1, 0]
        month = DAYS[i].to_period('M') if rebalance_monthly else None
        trade = not np.array_equal(last, target) or month != last_month
        opening = pos * np.r_[before[i], 1.]
        diff = target[:-1] - (opening / opening.sum())[:-1]
        touches = (abs(diff) > 1e-12)
        locked = (~ok[i] | (guard & ((diff > 0) & (up[i] >= limit[i]-eps[i]) | (diff < 0) & (up[i] <= -limit[i]+eps[i]))))
        blocked = bool(trade and (touches & locked).any())
        old_total = pos.sum()
        cost = turnover = 0.
        if participation and trade:
            gross = opening.sum()
            limit_value = np.where(locked, 0, caps[i])
            sales = np.minimum(np.maximum(opening[:-1]-gross*target[:-1],0), limit_value)
            opening[:-1] -= sales
            opening[-1] += sales.sum()*(1-bp)
            best = target[:-1].argmax()
            buy = min(opening[-1]/(1+bp), limit_value[best]) if target[best] == 1 else 0.
            opening[best] += buy
            opening[-1] -= buy*(1+bp)
            cost = bp*(sales.sum()+buy)
            turnover = (sales.sum()+buy)/gross
            blocked = not np.allclose(opening / opening.sum(), target, atol=1e-8, rtol=0)
            if not blocked:
                last = target.copy()
            if blocked and np.array_equal(last, target):
                last = np.full_like(target, -1)
            pos = opening * np.r_[after[i], 1.]
        elif trade and not blocked:
            gross = opening.sum()
            # Solve self-financing rebalance: fees apply to actual ETF units traded.
            total = gross
            for _ in range(10):
                traded = abs(total * target[:-1] - opening[:-1]).sum()
                total = gross - bp * traded
            cost, turnover = gross - total, traded / gross
            pos = (total * target) * np.r_[after[i], 1.]
            last = target.copy()
            last_month = month
        else:
            pos *= np.r_[cc[i], 1.]
        eq = pos.sum()
        weights = pos / eq
        rows.append((DAYS[i], eq, eq / old_total - 1, cost, turnover, turnover > 1e-12, blocked,
                     SYMS[np.argmax(target[:-1])] if target[-1] < .999999 else 'cash', *weights))
    return pd.DataFrame(rows, columns=['date', 'equity', 'ret', 'cost', 'turnover', 'trade', 'blocked', 'target', *SYMS, 'cash']).set_index('date')


def stats(r):
    r = r.dropna()
    eq = (1+r).cumprod()
    years = ((r.index[-1]-r.index[0]).days + 1) / 365.2425
    dd = eq / eq.cummax().clip(lower=1) - 1
    risk = r.std() * np.sqrt(252)
    trough = dd.idxmin()
    old_peak = eq.loc[:trough].idxmax()
    peak_value = max(1, eq.loc[old_peak])
    recovery = eq.loc[trough:].loc[lambda x: x >= peak_value]
    high = eq >= eq.cummax().clip(lower=1)
    longest = (~high).groupby(high.cumsum()).sum().max()
    return dict(total=eq.iloc[-1]-1, cagr=eq.iloc[-1]**(1/years)-1, vol=risk,
                sharpe=r.mean()*252/risk if risk else np.nan, dd=dd.min(), low_day=r.min(),
                es95=r.nsmallest(max(1, int(np.ceil(.05*len(r))))).mean(),
                peak=old_peak.date().isoformat(), trough=trough.date().isoformat(),
                recovery=recovery.index[0].date().isoformat() if len(recovery) else '', underwater=int(longest), n=len(r))


def static(pool):
    return pd.DataFrame(1 / len(pool), index=DAYS, columns=pool)


def monthly(pool, mkt, start=START, end=END, **kw):
    return backtest(mkt, static(pool), start, end, rebalance_monthly=True, **kw)


def bootstrap(r, bench, block):
    d = np.column_stack((np.log1p(r), np.log1p(r)-np.log1p(bench)))
    rng = np.random.default_rng(6152026)
    ids = (rng.integers(len(d), size=(3000, int(np.ceil(len(d)/block)), 1)) + np.arange(block)) % len(d)
    draws = np.expm1(d[ids.reshape(3000, -1)[:, :len(d)]].mean(axis=1) * 242)
    return np.quantile(draws, [.025, .50, .975], axis=0).round(5).tolist()


def self_check(mkt):
    dates = DAYS[50:52]
    op, cl, volume, ref = (pd.DataFrame(1., index=DAYS, columns=SYMS) for _ in range(4))
    op.loc[dates[1], CHINEXT] = .98
    cl.loc[dates[1], CHINEXT] = 1.02
    op.loc[dates[1], SSE180] = 1.01
    desired = pd.DataFrame(0., index=DAYS, columns=[CHINEXT, SSE180])
    desired.loc[dates[0], CHINEXT] = 1
    desired.loc[DAYS[49], SSE180] = 1
    sim = backtest((op, cl, volume, ref), desired, str(dates[0].date()), str(dates[1].date()))
    assert sim.iloc[0].target == SSE180 and sim.iloc[1].target == CHINEXT
    assert np.isclose(sim.equity.iloc[-1], 1.01*1.02/.98*(1-.0002)/(1+.0002)**2)
    assert np.isclose(score(pd.DataFrame({GOLD: np.exp(np.arange(35)*.002)}, index=DAYS[:35]), 25).iloc[-1,0], np.expm1(.002*252))
    for s, d in [(SSE180, '2013-12-23'), (NASDAQ, '2022-01-14')]:
        assert abs(mkt[1][s].ffill().pct_change().loc[d]) < .05


def main():
    mkt = bars('tencent')
    self_check(mkt)
    alt = bars('sina')
    sc = score(mkt[1][POOL], 25)
    wt = targets(sc)
    daily, summary = {}, {}

    def keep(name, result):
        daily[name] = result
        summary[name] = stats(result.ret) | {x: float(result[x].sum()) for x in ['trade', 'blocked', 'turnover']}
        return result

    b = keep('base', backtest(mkt, wt))
    bench = keep('csi300_etf', backtest(mkt, static([CSI])))
    eq = keep('equal_hold', backtest(mkt, static(POOL)))
    em = keep('equal_month', monthly(POOL, mkt))
    idx = keep('csi300_index', backtest(mkt, static([INDEX]), fee=0, guard=False))
    for s in POOL:
        keep('buyhold_'+s, backtest(mkt, static([s])))
    for n in range(15, 51):
        keep('window_'+str(n), backtest(mkt, targets(score(mkt[1][POOL], n)), start=HUB))
    walk = wt.copy()
    window_rets = pd.DataFrame({n: daily['window_'+str(n)].ret for n in range(15, 51)})
    picks = {}
    next_year = pd.Series(DAYS.year, index=DAYS).shift(-1)
    for year in range(2019, 2027):
        training = window_rets.loc[f'{year-4}-01-01':f'{year-1}-12-31']
        best = (training.mean() / training.std()).idxmax()
        picks[year] = int(best)
        walk.loc[next_year == year] = targets(score(mkt[1][POOL], int(best))).loc[next_year == year]
    keep('walk_forward', backtest(mkt, walk, start='2019-01-01'))
    keep('fixed25_from2019', backtest(mkt, wt, start='2019-01-01'))
    for method in ('ols', 'wls_squared', 'return'):
        keep(method, backtest(mkt, targets(score(mkt[1][POOL], 25, method))))
    keep('stop_close_wls', backtest(mkt, wt, stop_score=sc))
    ols = score(mkt[1][POOL], 25, 'ols')
    keep('stop_close_ols', backtest(mkt, targets(ols), stop_score=ols))
    for f in (0, 5, 10, 25, 50):
        keep('bp_'+str(f), backtest(mkt, wt, fee=f))
    keep('next_close', backtest(mkt, wt, when='close'))
    keep('delay_one', backtest(mkt, wt, lag=2))
    keep('delay_two', backtest(mkt, wt, lag=3))
    keep('delay_25bp', backtest(mkt, wt, fee=25, lag=2, known_halts=True))
    for p in (.01, .05, .10):
        for cap in (100_000, 1_000_000):
            keep(f'capacity_{p}_{cap}', backtest(mkt, wt, fee=10, known_halts=True, participation=p, capital=cap))
    keep('known_halts', backtest(mkt, wt, known_halts=True))
    keep('no_limit_filter', backtest(mkt, wt, guard=False))
    keep('sina', backtest(alt, targets(score(alt[1][POOL], 25))))
    keep('top2', backtest(mkt, targets(sc, top=2)))
    keep('positive_or_cash', backtest(mkt, targets(sc, positive=True)))
    for s in POOL:
        keep('omit_'+s, backtest(mkt, targets(sc.drop(columns=s))))
    for s, replacement in [(NASDAQ, SP500), (SSE180, DIV), (SSE180, CSI)]:
        universe = [replacement if x == s else x for x in POOL]
        for name, basket in [('original', POOL), ('swap_'+replacement, universe)]:
            keep(name+'_from2014', backtest(mkt, targets(score(mkt[1][basket], 25)), start='2014-04-01'))

    nav_arr = pd.DataFrame(RAW['nav_513100'])
    nav_arr.index = pd.to_datetime(nav_arr.x, unit='ms', utc=True).dt.tz_convert('Asia/Shanghai').dt.tz_localize(None).dt.normalize()
    nav = nav_arr.y.reindex(DAYS)
    actual_cl = pd.Series({pd.Timestamp(x[0]):float(x[2]) for x in RAW['tencent'][NASDAQ]}).reindex(DAYS)
    premium = (actual_cl / nav - 1).ffill()
    for p in (.03, .05, .10):
        limited = sc.copy()
        limited[NASDAQ] = limited[NASDAQ].mask(premium.shift(2) > p)
        keep('premium_gate_'+str(p), backtest(mkt, targets(limited)))
    raw = bars('tencent', adjusted=False)
    keep('no_corporate_actions', backtest(raw, targets(score(raw[1][POOL], 25)), guard=False))
    tail = keep('extended', backtest(mkt, wt, end='2026-11-13', known_halts=True))
    equal_tail = monthly(POOL, mkt, end='2026-11-13')
    csi_tail = backtest(mkt, static([CSI]), end='2026-11-13')
    periods = {}
    for label, start, end in [('early', START, '2018-12-31'), ('mid', '2019-01-01', '2022-12-31'),
                               ('recent', '2023-01-01', END), ('from2019', '2019-01-01', END),
                               ('unseen_since_post', LIVE, '2026-11-13'), ('2015_crash', '2015-06-15', '2015-08-26'),
                               ('pandemic_crash', '2020-02-20', '2020-03-23'), ('rapid_reversal', '2024-09-24', '2024-10-18')]:
        periods[label] = {key: stats(data.ret.loc[start:end]) for key, data in [('base', tail), ('equal', equal_tail), ('csi', csi_tail)]}

    yearly = pd.DataFrame({k: (1+x.ret).groupby(x.index.year).prod()-1 for k,x in
                           [('strategy', b), ('equal', em), ('csi_etf',bench), ('csi_index',idx)]})
    rolling = {}
    for n in (242, 726):
        rot = np.expm1(np.log1p(b.ret).rolling(n).sum()).dropna()
        e = np.expm1(np.log1p(em.ret).rolling(n).sum()).dropna()
        rolling[n] = dict(n=len(rot), positive=float((rot>0).mean()), beats_equal=float((rot>e).mean()),
                          worst=float(rot.min()), worst_ending=rot.idxmin().date().isoformat(), median=float(rot.median()))
    date_range = sc.shift(1).loc[START:END]
    neg = date_range.max(axis=1) <= 0
    overweight = b[POOL].mean()
    largest_drop_date = b.ret.idxmin()
    positions = b[POOL].idxmax(axis=1)
    old = positions.shift()
    new = positions
    legs = []
    raw_by_day = {s:{pd.Timestamp(r[0]):tuple(map(float,r[1:6])) for r in RAW['tencent'][s]} for s in POOL}
    for day in b.index[b.trade]:
        previous = b.index[b.index.get_loc(day)-1] if day != b.index[0] else None
        opening_nav = 1e6 if previous is None else 1e6*b.equity.loc[previous] * mkt[0].loc[day,old.loc[day]] / mkt[1][old.loc[day]].ffill().loc[previous]
        for symbol in ([old.loc[day]] if previous is not None else []) + [new.loc[day]]:
            price, _, high, _, lots = raw_by_day[symbol][day]
            fill = opening_nav if symbol == old.loc[day] else opening_nav*(1-.0002*(previous is not None))/(1+.0002)
            legs.append((day.date().isoformat(), symbol, fill, lots*100*high, fill / (lots*100*price), 1e6 / (lots*100*price),
                         fill / (lots*100*high), 1e6 / (lots*100*high)))
    legs = pd.DataFrame(legs, columns=['date','symbol','planned_yuan','all_day_max_yuan','compounded_fraction','fixed_1m_fraction','compounded_lower','fixed_1m_lower'])
    legs.to_csv(ROOT / 'liquidity_proxies.csv', index=False)
    price_gap = {s: (mkt[0].loc[largest_drop_date,s]/mkt[1][s].ffill().shift().loc[largest_drop_date]-1,
                     mkt[1].loc[largest_drop_date,s]/mkt[0].loc[largest_drop_date,s]-1)
                 for s in {old.loc[largest_drop_date],new.loc[largest_drop_date]}}
    for k in [20, 25, 34]:
        assert len(daily['window_'+str(k)]) == len(daily['window_25'])
    outcome = {'metrics': summary, 'periods': periods, 'yearly': yearly.to_dict(), 'rolling': rolling, 'walk_picks': picks,
               'bootstrap': {n:bootstrap(b.ret,em.ret,n) for n in (20, 60, 120)},
               'all_nonpositive_days': int(neg.sum()), 'all_nonpositive_geom_daily': np.expm1(np.log1p(b.ret[neg]).mean()),
               'weights': overweight.to_dict(),
               'worst_day': dict(date=largest_drop_date.date().isoformat(), old=old.loc[largest_drop_date],
                                 new=new.loc[largest_drop_date], gap_and_intraday=price_gap),
               'daily_dollar_volume_fraction': legs[['compounded_fraction','fixed_1m_fraction']].quantile([.5,.9,.99,1]).to_dict(),
               'over_entire_daily_volume': {c:int((legs[c]>1).sum()) for c in ['compounded_lower','fixed_1m_lower']},
               'blocked_dates': list(b.index[b.blocked].strftime('%Y-%m-%d')),
               'excess_without_best_10_relative_days': float(np.expm1((np.log1p(b.ret)-np.log1p(em.ret)).sort_values().iloc[:-10].sum())),
               'no_best_10_days_cagr': stats(b.ret.mask(b.ret.isin(b.ret.nlargest(10)),0))['cagr'],
               'latest_nav': {str(i.date()): [float(nav.loc[i]),float(actual_cl.loc[i]),float(premium.loc[i])] for i in premium.loc['2026-11-02':].dropna().index if pd.notna(nav.loc[i])},
               'live_switches': tail.loc[LIVE:].query('trade or blocked')[['target','ret','blocked']].reset_index().assign(date=lambda x:x.date.dt.strftime('%Y-%m-%d')).to_dict('records')}
    (ROOT / 'results.json').write_text(json.dumps(outcome, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    pd.DataFrame({k:v for k,v in summary.items()}).T.to_csv(ROOT / 'scenario_metrics.csv')
    pd.concat({'strategy': b, 'csi_etf':bench['equity'], 'equal_month':em['equity'], 'nasdaq_hold':daily['buyhold_'+NASDAQ]['equity']}, axis=1).to_csv(ROOT / 'daily_results.csv')
    pd.DataFrame({k: daily[k].equity for k in ['base','equal_month','buyhold_'+NASDAQ,'csi300_etf','capacity_0.01_1000000','delay_25bp']}).to_csv(ROOT / 'curves.csv')
    yearly.to_csv(ROOT / 'annual_returns.csv')
    print(pd.DataFrame(summary).T[['cagr','dd','sharpe','trade','blocked']].round(4).to_string())


if __name__ == '__main__':
    main()
