"""Đối chiếu giờ mở cửa trong data/pois.json với giờ mở cửa BestTime trả về (nguồn thứ 2).

python -m scripts.check_hours   -> in bảng lệch và ghi runs/hours_check.json
Không tự sửa pois.json: chỗ lệch cần người kiểm nguồn chính thức.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hanoibot.pois import WEEKDAYS, load_pois  # noqa: E402
from hanoibot.tools.crowd import get_forecast  # noqa: E402


def table_hours(p, wd: int) -> list[list[int]]:
    """Giờ trong bảng cho thứ wd (0=thứ Hai), làm tròn giờ để so với BestTime (BestTime trả theo giờ)."""
    out = []
    for a, b in p.hours.get(WEEKDAYS[wd], []):
        out.append([int(a[:2]), int(b[:2]) + (1 if b[3:5] not in ("00", "") else 0)])
    return out


def _near(a: list, b: list) -> bool:
    return len(a) == len(b) and len(a) > 0 and all(abs(x[0] - y[0]) <= 1 and abs(x[1] - y[1]) <= 1 for x, y in zip(a, b))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    for p in load_pois().values():
        fc = get_forecast(p)
        if not fc.get("available"):
            rows.append({"poi": p.id, "status": "không có dữ liệu BestTime", "reason": fc.get("reason") or fc.get("_error")})
            continue
        diffs, near = [], 0
        for wd in range(7):
            mine = table_hours(p, wd)
            theirs = fc["bt_hours"].get(str(wd)) or []
            if [list(x) for x in mine] == [list(x) for x in theirs]:
                continue
            if _near(mine, theirs):  # chỉ lệch do làm tròn giờ (BestTime trả theo giờ chẵn)
                near += 1
                continue
            diffs.append({"day": WEEKDAYS[wd], "table": mine, "besttime": theirs})
        status = "khớp" if not diffs and not near else ("gần khớp (±1h)" if not diffs else f"LỆCH {len(diffs)} ngày")
        rows.append({"poi": p.id, "name": p.name, "confidence": p.confidence, "bt_venue": fc.get("venue_name"),
                     "status": status, "diffs": diffs})
    for r in rows:
        print(f"- {r['poi']:15s} {r['status']:28s} {r.get('bt_venue') or ''}")
        for d in r.get("diffs", [])[:7]:
            print(f"    {d['day']}: bảng {d['table']}  vs  BestTime {d['besttime']}")
    out = ROOT / "runs" / "hours_check.json"
    out.write_text(json.dumps({"checked": date.today().isoformat(), "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("Ghi:", out)


if __name__ == "__main__":
    main()
