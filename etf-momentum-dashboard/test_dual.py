from datetime import datetime
import math
import unittest
from unittest.mock import patch

import dashboard
import shadow


class DualHistory(unittest.TestCase):
    def test_dividend_report_year_and_month_boundaries(self):
        history = [{"date": day, "000300": value} for day, value in
                   [("2024-12-30", 2), ("2024-12-31", 2.2), ("2025-01-02", 2.42),
                    ("2025-01-03", 2.31), ("2025-02-03", 2.64)]]
        series = [(day, 1, nav, "", "sh510880") for day, nav in
                  [("2024-12-31", 1.2), ("2025-01-02", .9),
                   ("2025-01-03", 1.08), ("2025-02-03", 1.32)]]
        report = dashboard.dividend_report(series, history)
        year = report["years"][1]
        self.assertAlmostEqual(year["ret"], .1)
        self.assertAlmostEqual(year["mdd"], -.25)
        self.assertEqual((year["draw_peak"], year["trough"]), ("2024-12-31", "2025-01-02"))
        self.assertAlmostEqual(year["months"]["01"]["ret"], -.1)
        self.assertAlmostEqual(year["months"]["02"]["ret"], 1.32 / 1.08 - 1)
        self.assertAlmostEqual(year["benchmark"], .2)
        self.assertAlmostEqual(report["daily"][0][2], 1.1)
        self.assertAlmostEqual(report["summary"]["mdd"], -.25)

    def test_dividend_report_matches_seed_and_partial_year(self):
        rows = dashboard.load_dual_history()
        report = dashboard.dividend_report(rows, dashboard.load_history())
        self.assertEqual(report["base_date"], "2013-08-30")
        self.assertEqual(set(report["years"][0]["months"]), {"09", "10", "11", "12"})
        self.assertEqual(len(report["daily"]), 3207)
        self.assertEqual(report["summary"]["nav"], rows[-1][2])
        self.assertAlmostEqual(math.prod(1 + y["ret"] for y in report["years"]), rows[-1][2])
        for year in report["years"]:
            self.assertAlmostEqual(math.prod(1 + m["ret"] for m in year["months"].values()), 1 + year["ret"])

    def test_first_day_and_common_cutoff(self):
        rows = dashboard.load_dual_history()
        self.assertEqual((rows[0][0],rows[-1][0],len(rows)),("2013-09-02",dashboard.SEED,3207))
        self.assertTrue(all(a[0]<b[0] for a,b in zip(rows,rows[1:])))
        self.assertTrue(all(row[3] in shadow.POOLS["original"] and row[4] in shadow.POOLS["dividend"] for row in rows))
        self.assertAlmostEqual(rows[-1][5],3.374/3.357-1,places=10)

    def test_roll_forward_needs_two_sources_and_preserves_seed(self):
        seed = dashboard.load_history()
        red = dashboard.load_shadow()["red"][:26]
        day = "2026-11-16"
        now = datetime.fromisoformat(day+"T18:37:00+08:00")
        base_bars = {s:(seed[-1][s+"_close"],seed[-1][s+"_close"],1) for s in dashboard.ETF}
        day_row = dashboard.append_day(seed,day,base_bars,1,{s:({}, {}) for s in dashboard.ETF})
        raw = {"extra":[day_row],"status":"ok"}
        data = {"red":red,"error":None}
        with patch.object(dashboard,"read_state",return_value=raw), patch.object(dashboard,"load_shadow",return_value=data):
            delayed = dashboard.dual_view(seed,now)
            trail_delayed = dashboard.momentum_view(now)
            data["red"].append([day,red[-1][1]*1.02,red[-1][2]*1.02,red[-1][2]])
            fresh = dashboard.dual_view(seed,now)
            trail_fresh = dashboard.momentum_view(now)
        self.assertTrue(delayed["stale"])
        self.assertEqual(delayed["as_of"],dashboard.SEED)
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["as_of"],day)
        self.assertEqual(fresh["series"][:-1],delayed["series"])
        self.assertAlmostEqual(fresh["series"][-1][1]/delayed["series"][-1][1],1)
        self.assertAlmostEqual(fresh["series"][-1][2]/delayed["series"][-1][2],1.02)
        self.assertAlmostEqual(fresh["series"][-1][5],.02)
        self.assertTrue(trail_delayed["stale"])
        self.assertFalse(trail_fresh["stale"])
        self.assertEqual(trail_fresh["symbols"],shadow.SYMBOLS)
        self.assertEqual(trail_fresh["series"][:-1],trail_delayed["series"])
        self.assertEqual(trail_fresh["series"][-1][0],day)
        self.assertAlmostEqual(trail_fresh["series"][-1][-1],dashboard.momentum([r[2] for r in data["red"][-25:]]))

    def test_momentum_seed_has_same_dates_and_latest_scores(self):
        rows = dashboard.load_momentum_history()
        seed = dashboard.load_history()
        self.assertEqual([row[0] for row in rows],[row[0] for row in dashboard.load_dual_history()])
        self.assertTrue(all(len(row)==6 and all(math.isfinite(v) and v>=-1 for v in row[1:]) for row in rows))
        for symbol,value in zip(dashboard.ETF,rows[-1][1:]):
            self.assertAlmostEqual(seed[-1][symbol+"_score"],value,places=10)

    def test_switch_cost_and_missing_open(self):
        a,b = "a","b"
        previous = {a:1,b:2}
        close = {a:.99,b:2.4}
        value,held = dashboard.dual_next(1,a,b,previous,{a:1.05,b:2.2},close)
        self.assertEqual(held,b)
        self.assertAlmostEqual(value,1.05*(1-dashboard.FEE)/(1+dashboard.FEE)*2.4/2.2)
        self.assertEqual(dashboard.dual_next(1,a,b,previous,{a:1.05,b:None},close),(.99,a))


if __name__ == "__main__":
    unittest.main()
