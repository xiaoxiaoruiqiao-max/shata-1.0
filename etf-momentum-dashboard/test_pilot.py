import unittest
from datetime import date, datetime

import dashboard
import pilot


class PilotTrial(unittest.TestCase):
    def setUp(self):
        self.start = self.event("start", "2026-11-11")
        self.buy = self.event("buy", "2026-11-11", symbol="sz159915", qty=170_000, price=2.5, fee=85, reference=2.5)

    def event(self, kind, day, **fields):
        return pilot.event({"kind":kind, "date":day, "time":"09:36"} | fields, date(2026,11,16), dashboard.trading_day)

    def test_partial_sale_cash_and_single_position(self):
        partial = self.event("sell", "2026-11-12", symbol="sz159915", qty=70_000, price=2.6, fee=36.4, reference=2.6)
        book = pilot.replay([self.start, self.buy, partial])
        self.assertEqual(book.holding, "sz159915")
        self.assertEqual(book.qty, 100_000)
        self.assertAlmostEqual(float(book.cash), 256878.60)
        self.assertAlmostEqual(book.tape[-1]["pnl"], 6928.60)
        next_buy = self.event("buy", "2026-11-12", symbol="sh513100", qty=10_000, price=2, fee=4, reference=2, iopv=1.99)
        with self.assertRaisesRegex(ValueError, "剩余"):
            book.post(next_buy)
        rest = self.event("sell", "2026-11-12", symbol="sz159915", qty=100_000, price=2.6, fee=52, reference=2.6)
        book = pilot.replay([self.start, self.buy, partial, rest, next_buy])
        self.assertEqual((book.holding, book.exits, book.qty), ("sh513100", 1, 10_000))

    def test_broker_gates_and_stop_at_close(self):
        same_day = self.event("sell", "2026-11-11", symbol="sz159915", qty=100, price=2.5, fee=0)
        with self.assertRaisesRegex(ValueError, "当日"):
            pilot.replay([self.start, self.buy, same_day])
        self.assertRaises(ValueError, self.event, "buy", "2026-11-11", symbol="sz159915", qty=101, price=2)
        nasdaq = self.event("buy", "2026-11-11", symbol="sh513100", qty=200_000, price=2.06, fee=0, reference=2.06, iopv=2)
        self.assertEqual(pilot.replay([self.start, nasdaq]).premium_issues, 1)
        rows = dashboard.load_history()
        rows = [r.copy() for r in rows]
        rows[-1]["sz159915_close"] = 1.8
        outcome = pilot.evaluate([self.start, self.buy], rows)
        self.assertTrue(outcome["stop"])
        self.assertEqual(outcome["daily"][-1][5], "sz159915")
        self.assertEqual(outcome["daily"][-1][0], "2026-11-13")
        self.assertTrue(all(check[2] is None or isinstance(check[2], str) for check in outcome["checks"]))

    def test_stale_and_pending_market_data(self):
        rows = dashboard.load_history()
        state = {"extra":[], "checked_at":None, "status":"ok", "error":None, "premium":None}
        clock = datetime(2026,11,16,9,35,tzinfo=dashboard.CHINA)
        start = self.event("start", "2026-11-16")
        result = dashboard.dashboard(rows, state, [start], now=clock)
        self.assertTrue(result["signal_ready"])
        self.assertTrue(result["pilot"]["lagging"])
        self.assertEqual(result["next_session"], "2026-11-16")
        self.assertFalse(dashboard.dashboard(rows, state, now=clock.replace(hour=18, minute=15))["signal_ready"])


if __name__ == "__main__":
    unittest.main()
