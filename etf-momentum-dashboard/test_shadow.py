import copy
from datetime import datetime
import unittest
from unittest.mock import patch

import dashboard
import shadow

DAY = "2026-11-16"
NEXT = "2026-11-17"
GOLD, NAS, GROWTH, SSE = shadow.POOLS["original"]
RED = shadow.DIV
EMPTY_ACTIONS = {s:{"dividends":[], "splits":{}} for s in shadow.SYMBOLS}


def quote(minute=35, price=2.0, depth=100000):
    return {"stamp": f"{DAY}T09:{minute}:15+08:00", "bid": price-.001, "ask": price,
            "up":2.3, "down":1.8, "bid_qty":depth, "ask_qty":depth, "iopv":None,
            "volume":100000*(minute-34)}


class ForwardComparison(unittest.TestCase):
    def test_premium_and_missing_anchor_keep_the_old_holding(self):
        holding = shadow.blank() | {"holding":SSE, "qty":200_000, "cash":100_000}
        nasdaq = quote(price=2.06) | {"iopv":2}
        results = shadow.replay_day(holding, DAY, NAS, {"09:35":{SSE:quote(),NAS:nasdaq}},
                                    {SSE:2}, EMPTY_ACTIONS)
        self.assertEqual(results["holding"], SSE)
        self.assertIn("3.00%", results["note"])
        self.assertEqual(results["filled"], [])
        without_iopv = shadow.replay_day(holding, DAY, NAS, {"09:35":{SSE:quote(),NAS:quote()}},
                                         {SSE:2}, EMPTY_ACTIONS)
        self.assertIn("IOPV", without_iopv["note"])
        missed = shadow.replay_day(holding, DAY, RED, {"09:36":{SSE:quote(36),RED:quote(36)}},
                                   {SSE:2}, EMPTY_ACTIONS)
        self.assertIn("09:35", missed["note"])

    def test_partial_exit_never_funds_the_new_etf_until_flat(self):
        prior = shadow.blank() | {"holding":SSE, "qty":100_000, "cash":300_000}
        frames = {"09:35":{SSE:quote(), RED:quote()}}
        partial = shadow.replay_day(prior, DAY, RED, frames, {SSE:2, RED:2}, EMPTY_ACTIONS)
        self.assertEqual((partial["holding"],partial["qty"]), (SSE,50_000))
        frames["09:36"] = {SSE:quote(36), RED:quote(36)}
        frames["09:37"] = {SSE:quote(37), RED:quote(37)}
        full = shadow.replay_day(prior, DAY, RED, frames, {SSE:2, RED:2}, EMPTY_ACTIONS)
        self.assertEqual(full["holding"], RED)
        self.assertEqual([r["side"] for r in full["filled"]], ["sell","sell","buy"])
        self.assertTrue(all(r["qty"]*r["price"] <= 100000 for r in full["filled"]))
        self.assertTrue(full["pending_buy"])

    def test_stop_is_independent_and_permanent(self):
        frames = {f"09:{m}":{RED:quote(m),GOLD:quote(m)} for m in range(35,42)}
        falling = shadow.replay_day(shadow.blank(),DAY,RED,frames,{RED:1.65},EMPTY_ACTIONS)
        flat = shadow.replay_day(shadow.blank(),DAY,GOLD,frames,{GOLD:2},EMPTY_ACTIONS)
        self.assertEqual(falling["stopped"],DAY)
        self.assertIsNone(flat["stopped"])
        exited = shadow.replay_day(falling,NEXT,GOLD,frames,{RED:1.70,GOLD:2},EMPTY_ACTIONS)
        self.assertEqual(exited["holding"],"")
        self.assertEqual(exited["stopped"],DAY)
        self.assertEqual(exited["order_count"],falling["order_count"]+len(exited["filled"]))
        self.assertTrue(all(f["side"]=="sell" for f in exited["filled"]))

    def test_dividend_is_valued_before_cash_is_available(self):
        actions=copy.deepcopy(EMPTY_ACTIONS)
        actions[RED]["dividends"]=[[DAY,NEXT,"0.100"]]
        prior=shadow.blank() | {"holding":RED,"qty":100_000,"cash":300_000}
        ex=shadow.replay_day(prior,DAY,RED,{}, {RED:1.9},actions)
        self.assertEqual((ex["cash"],ex["equity"],ex["receivables"][NEXT]),(300_000,500_000,10_000))
        pay=shadow.replay_day(ex,NEXT,RED,{}, {RED:1.9},actions)
        self.assertEqual((pay["cash"],pay["equity"],pay["receivables"]),(310_000,500_000,{}))

    def test_quote_validation_is_point_in_time(self):
        q=[""]*82
        for i,v in {2:"513100",3:"2.059",6:"20000",9:"2.058",10:"120",
                    19:"2.059",20:"85",30:"20261116093512",47:"2.200",48:"1.800",77:"2.95",78:"2.0000"}.items():
            q[i]=v
        a=[""]*33
        for i,v in {8:"2000000",10:"13100",11:"2.058",20:"8000",21:"2.059",
                    30:DAY,31:"09:35:13",32:"00"}.items():
            a[i]=v
        tx='v_sh513100="'+ "~".join(q)+'";'
        sn='var hq_str_sh513100="'+ ",".join(a)+'";'
        now=datetime.fromisoformat(DAY+"T09:35:18+08:00")
        active=shadow.parse_quotes(tx,sn,now)[NAS]
        self.assertEqual((active["iopv"],active["ask_qty"]),(2,8000))
        self.assertFalse(shadow.parse_quotes(tx,sn,now.replace(minute=37)))
        self.assertFalse(shadow.parse_quotes(tx,sn.replace('00";','03";'),now))

    def test_settlement_and_two_distinct_signal_pools(self):
        history = dashboard.load_history()
        data = dashboard.load_shadow()
        when = datetime.fromisoformat(DAY+"T18:17:00+08:00")
        closes = {s:(history[-1][s+"_close"],history[-1][s+"_close"],1) for s in dashboard.ETF}
        last = dashboard.append_day(history,DAY,closes,1,{s:({}, {}) for s in dashboard.ETF})
        red_data = {dashboard.SEED:(3.374,3.374,100),DAY:(3.38,3.38,100)}
        clean = {"start":DAY, "red":data["red"][:26], "days":[], "pending":{},
                 "actions":None, "checked_at":None, "error":None}
        with patch.object(dashboard,"read_state",return_value={"status":"ok","extra":[last]}), \
             patch.object(dashboard,"tencent",return_value=red_data), patch.object(dashboard,"sina",return_value=red_data), \
             patch.object(dashboard,"shadow_actions",side_effect=EMPTY_ACTIONS.__getitem__):
            dashboard.shadow_refresh(history,when,clean)
            dashboard.shadow_refresh(history,when,clean)
        self.assertEqual(len(clean["days"]),1)
        self.assertEqual(clean["days"][0]["dividend"]["equity"],shadow.CAPITAL)
        self.assertIn("盘前",clean["days"][0]["original"]["note"])
        asof,ranks,ready = dashboard.shadow_signals(history+[last],clean)
        self.assertTrue(ready)
        self.assertEqual(asof,DAY)
        self.assertEqual({v["symbol"] for v in ranks["dividend"]}-{v["symbol"] for v in ranks["original"]},{RED})


if __name__ == "__main__":
    unittest.main()
