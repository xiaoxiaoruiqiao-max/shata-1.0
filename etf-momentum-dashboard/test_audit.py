import http.client
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime
from functools import partial
from unittest.mock import patch

import dashboard as d


class IntegrationAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(d, "STATE", root), patch.object(d, "JOURNAL", root / "pilot.json"),
                        patch.object(d, "SHADOW", root / "shadow.json")]
        for p in self.patches:
            p.start()
        self.seed = d.load_history()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_dividend_trade_reconciliation(self):
        rows = d.load_dual_history()
        trades = d.dividend_trades(rows, self.seed, d.load_shadow()["red"])
        self.assertEqual(len(trades), 195)
        self.assertEqual(trades[0]["symbol"], rows[-1][4])
        self.assertEqual(trades[-1]["entry"], rows[0][0])
        self.assertAlmostEqual(sum(t["pnl"] for t in trades), (rows[-1][2]-1)*1e6, places=5)
        self.assertAlmostEqual(math.prod(1+t["ret"] for t in trades), rows[-1][2])
        self.assertEqual(sum(t["sessions"] for t in trades), len(rows))
        for old, new in zip(trades[1:], trades):
            self.assertEqual(old["exit"], new["entry"])
        # Independently reconstruct each purchased day's closing NAV from raw prices.
        raw = json.loads((d.ROOT.parent / "outputs/etf-rotation-audit-20261115/market_data.json").read_text(encoding="utf-8"))
        bars = {s: {r[0]: r for r in data} for s, data in raw["tencent"].items()}
        nav = {r[0]: r[2] for r in rows}
        capital = 1
        for trade in reversed(trades):
            day, symbol = trade["entry"], trade["symbol"]
            price = bars[symbol][day]
            self.assertAlmostEqual(trade["entry_price"], float(price[1]))
            self.assertAlmostEqual(nav[day], capital/(1+d.FEE)*float(price[2])/float(price[1]), places=8)
            capital *= 1+trade["ret"]

    def test_update_idempotence_missing_feed_and_cross_sections(self):
        day, last = "2026-11-16", self.seed[-1]
        now = datetime.fromisoformat(day+"T18:37:00+08:00")
        feeds = {s: {d.SEED: (last[s+"_close"], last[s+"_close"], 1),
                     day: (last[s+"_close"], last[s+"_close"]*1.01, 1)} for s in d.ETF}
        feeds[d.INDEX] = {d.SEED: (4000, 4000, 1), day: (4000, 4040, 1)}
        red_last = d.load_shadow()["red"][-1][1]
        feeds[d.shadow.DIV] = {d.SEED: (red_last, red_last, 1), day: (red_last, red_last*1.01, 1)}
        with patch.object(d, "tencent", side_effect=lambda s,*a: feeds[s]), \
             patch.object(d, "sina", side_effect=lambda s: feeds[s]), \
             patch.object(d, "corporate_actions", return_value=({}, {})), \
             patch.object(d, "premium", return_value=(d.SEED, 2)), \
             patch.object(d, "shadow_actions", return_value={"dividends": [], "splits": {}}):
            state = d.refresh(self.seed, now)
            self.assertEqual(state["status"], "ok")
            self.assertEqual(len(state["extra"]), 1)
            # Original can advance while dividend data must remain at its common cutoff.
            self.assertEqual(d.dual_view(self.seed, now)["as_of"], d.SEED)
            d.update_shadow(self.seed, now)
            dual = d.dual_view(self.seed, now)
            self.assertEqual(dual["as_of"], day)
            self.assertEqual(dual["dividend_report"]["as_of"], day)
            self.assertEqual(d.momentum_view(now)["as_of"], day)
            self.assertAlmostEqual(sum(t["pnl"] for t in dual["dividend_trades"]),
                                   (dual["series"][-1][2]-1)*1e6, places=5)
            self.assertEqual(len(d.refresh(self.seed, now)["extra"]), 1)
            with patch.object(d, "sina", side_effect=ValueError("missing feed")):
                failed = d.refresh(self.seed, now)
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["extra"], state["extra"])
            self.assertTrue(d.dual_view(self.seed, now)["stale"])
            with patch.object(d, "sina", side_effect=lambda s: {k: (v[0], v[1]+.1, v[2]) for k,v in feeds[s].items()}):
                mismatch = d.refresh(self.seed, now)
            self.assertEqual(mismatch["status"], "error")
            self.assertEqual(mismatch["extra"], state["extra"])

    def test_http_security_and_all_page_resources(self):
        server = d.ThreadingHTTPServer(("127.0.0.1", 0), partial(d.Handler, directory=d.WEB))
        server.seed, server.api_key, server.cache_lock = self.seed, None, threading.Lock()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, method="GET", body=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            result = response.status, response.read(), dict(response.getheaders())
            connection.close()
            return result
        try:
            for path in ("/", "/app.js", "/dividend.js", "/dual.js", "/pilot.js", "/shadow.js", "/momentum.js",
                         "/styles.css", "/api/dashboard", "/api/dual-history", "/api/shadow", "/api/momentum"):
                status, body, headers = request(path)
                self.assertEqual(status, 200, path)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                if path.startswith("/api/"):
                    json.loads(body)
                    self.assertEqual(headers["Cache-Control"], "no-store")
            for method in ("GET", "HEAD"):
                for path in ("/../data/pilot.json", "/%2e%2e/data/pilot.json", "/dashboard.py", "/data/", "/unknown"):
                    self.assertEqual(request(path, method)[0], 404)
                self.assertEqual(request("/", method, headers={"Host": "attacker.example"})[0], 403)
            self.assertEqual(request("/api/pilot", "POST", "{}")[0], 403)
            host = "127.0.0.1:" + str(server.server_port)
            headers = {"Origin": "http://"+host, "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
            for body in ("[]", "{", '"text"', '{"revision":0,"kind":"start","date":{}}', "x"*4097):
                self.assertEqual(request("/api/pilot", "POST", body, headers)[0], 400)
            self.assertEqual(request("/api/pilot", "POST", '{"revision":99}', headers)[0], 409)
            attack = {**headers, "Origin": "http://attacker.example"}
            self.assertEqual(request("/api/pilot", "POST", "{}", attack)[0], 403)
            # Verify legitimate journal write/undo only inside this temporary test account.
            self.assertEqual(request("/api/pilot", "POST", '{"revision":0,"kind":"start","date":"2026-11-13"}', headers)[0], 200)
            self.assertEqual(len(d.journal()), 1)
            self.assertEqual(request("/api/pilot", "POST", '{"revision":1,"kind":"undo"}', headers)[0], 200)
            self.assertEqual(d.journal(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_schedulers_run_at_configured_minutes_without_duplicates(self):
        times = [datetime.fromisoformat("2026-11-16T"+t+"+08:00") for t in
                 ("18:14:00", "18:15:00", "18:15:12")]
        with patch.object(d, "datetime") as clock, patch.object(d, "refresh", return_value={"status":"ok"}) as update, \
             patch.object(d.time, "sleep", side_effect=[None, None, RuntimeError("end test")]):
            clock.now.side_effect = times
            with self.assertRaisesRegex(RuntimeError, "end test"):
                d.schedule(self.seed)
            self.assertEqual(update.call_count, 2)

    def test_schedulers_catch_up_after_sleep(self):
        times = [datetime.fromisoformat(t+"+08:00") for t in
                 ("2026-11-16T17:50:00", "2026-11-17T10:51:40", "2026-11-17T10:51:52")]
        for scheduler, update_name in ((d.schedule, "refresh"), (d.schedule_shadow, "update_shadow")):
            with patch.object(d, "datetime") as clock, patch.object(d, update_name, return_value={"status":"ok"}) as update, \
                 patch.object(d, "read_state", return_value={"status":"ok"}), \
                 patch.object(d.time, "sleep", side_effect=[None, None, RuntimeError("end test")]):
                clock.now.side_effect = times
                with self.assertRaisesRegex(RuntimeError, "end test"):
                    scheduler(self.seed)
                self.assertEqual(update.call_count, 2)

    def test_missed_window_is_recorded_without_fabricated_frames(self):
        state = d.load_shadow()
        state["start"] = "2026-11-17"
        d.atomic_json(d.SHADOW, state)
        now = datetime.fromisoformat("2026-11-17T11:00:00+08:00")
        with patch.object(d, "shadow_refresh", side_effect=ValueError("waiting for close")):
            d.update_shadow(self.seed, now)
        pending = d.load_shadow()["pending"]["2026-11-17"]
        self.assertEqual(pending["frames"], {})
        self.assertEqual(pending["targets"], {})
        self.assertIn("09:35", pending["skip_reason"])

    def test_shadow_scheduler_at_configured_minute(self):
        times = [datetime.fromisoformat("2026-11-16T"+t+"+08:00") for t in
                 ("18:16:00", "18:17:00", "18:17:08")]
        with patch.object(d, "datetime") as clock, patch.object(d, "update_shadow") as update, \
             patch.object(d, "read_state", return_value={"status":"ok"}), \
             patch.object(d.time, "sleep", side_effect=[None, None, RuntimeError("end test")]):
            clock.now.side_effect = times
            with self.assertRaisesRegex(RuntimeError, "end test"):
                d.schedule_shadow(self.seed)
            self.assertEqual(update.call_count, 2)


if __name__ == "__main__":
    unittest.main()
