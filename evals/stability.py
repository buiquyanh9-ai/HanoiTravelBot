"""Độ ổn định (ý tưởng Day 09): chạy mỗi kịch bản k lần, tính pass@k (đạt ít nhất 1 lần) và pass^k (đạt cả k lần).

python -m evals.stability --k 3 [--only 1,2]
"Đạt" = đúng intent + có đủ chuỗi kỳ vọng + (nếu lập lịch) qua bộ kiểm + ngân sách/đi bộ đúng giới hạn.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.run_evals import SCENARIOS, at  # noqa: E402
from hanoibot.bot import handle  # noqa: E402


def judge(sc: dict, bot: dict) -> list[str]:
    fails = []
    if bot.get("intent") != sc["intent"]:
        fails.append(f"intent={bot.get('intent')}")
    fails += [f"thiếu '{e}'" for e in sc.get("expect", []) if e not in (bot.get("answer") or "")]
    if sc.get("lang") and bot.get("lang") != sc["lang"]:
        fails.append(f"lang={bot.get('lang')}")
    chk = bot.get("check") or {}
    if sc["intent"] == "plan" and not chk.get("ok"):
        fails.append("lịch vi phạm: " + "; ".join(chk.get("violations", [])[:2]))
    tot = chk.get("totals") or {}
    if sc.get("budget") and not tot.get("total_vnd", 10**9) <= sc["budget"]:
        fails.append(f"chi phí {tot.get('total_vnd')}")
    if sc.get("max_walk") and not tot.get("walk_km", 99) <= sc["max_walk"]:
        fails.append(f"đi bộ {tot.get('walk_km')}")
    return fails


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x}
    rows = []
    for sc in SCENARIOS:
        if only and sc["id"] not in only:
            continue
        runs = []
        for _ in range(a.k):
            bot = handle(sc["text"], now=at(sc["now"]))
            runs.append({"fails": judge(sc, bot), "latency_s": bot.get("latency_s")})
        passes = sum(not r["fails"] for r in runs)
        rows.append({"id": sc["id"], "passes": passes, "k": a.k, "runs": runs})
        bad = [f for r in runs for f in r["fails"]]
        print(f"[{sc['id']:2d}] {passes}/{a.k} đạt" + (f"  | lỗi: {bad[:2]}" if bad else ""))
    n = len(rows)
    summary = {"k": a.k, "n": n,
               "pass_at_k": f"{sum(r['passes'] > 0 for r in rows)}/{n}",
               "pass_hat_k": f"{sum(r['passes'] == a.k for r in rows)}/{n}",
               "run_pass_rate": round(sum(r["passes"] for r in rows) / (n * a.k), 3) if n else None}
    print("\nỔN ĐỊNH:", json.dumps(summary, ensure_ascii=False))
    out = ROOT / "runs" / f"stability_{datetime.now():%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Ghi:", out)


if __name__ == "__main__":
    main()
