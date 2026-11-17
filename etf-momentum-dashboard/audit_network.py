"""Probe real feeds in an isolated state directory; never update the user's ledger."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import dashboard as d


def main():
    d.STATE = d.ROOT / "audit-state"
    d.STATE.mkdir(exist_ok=True)
    d.SHADOW = d.STATE / "shadow.json"
    d.JOURNAL = d.STATE / "pilot.json"
    seed = d.load_history()
    now = datetime.now(d.CHINA)
    def probe(name, fn):
        try:
            value = fn()
            return name, {"ok": True, "count": len(value),
                          "latest": max(value) if name.startswith(("tencent", "sina")) else None}
        except Exception as error:
            return name, {"ok": False, "error": str(error)}
    jobs = []
    for symbol in (*d.ETF, d.INDEX, d.shadow.DIV):
        jobs.append(("tencent:" + symbol, lambda s=symbol: d.tencent(s, "2026-11-06", "2026-11-17")))
        jobs.append(("sina:" + symbol, lambda s=symbol: d.sina(s)))
    with ThreadPoolExecutor(max_workers=12) as pool:
        probes = dict(pool.map(lambda job: probe(*job), jobs))
    state = d.refresh(seed, now)
    d.update_shadow(seed, now)
    red = d.load_shadow()
    result = {"checked_at": now.isoformat(), "feeds": probes,
              "refresh": {k: v for k, v in state.items() if k != "extra"},
              "extra_rows": len(state["extra"]),
              "shadow": {"error": red["error"], "red_latest": red["red"][-1][0], "start": red["start"]}}
    (d.STATE / "network-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
