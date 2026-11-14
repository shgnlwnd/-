"""앱 화면용 가격 계산: 금일 시세, 작년 대비, 지난 3년 가격, 추세 그래프."""
from __future__ import annotations

from datetime import date, timedelta

from . import config

# 휴장일이면 며칠 전까지 거슬러 찾을지
LOOKBACK_DAYS = 3


def _won_per_kg(doc: dict | None, size: str | None, market: str | None):
    """문서에서 (상자 크기, 시장) 조건의 kg당 가격과 상자 가격 범위를 꺼냄."""
    if not doc:
        return None
    if market:
        m = (doc.get("markets") or {}).get(market) or {}
        cells = [m[size]] if size and size in m else (list(m.values()) if not size else [])
        kg = sum(c["total_kg"] for c in cells)
        won = sum(c["totprc"] for c in cells)
        if not kg:
            return None
        return {"won_per_kg": round(won / kg), "total_kg": round(kg, 1)}
    s = (doc.get("sizes") or {}).get(size) if size else doc.get("all")
    if not s or not s.get("won_per_kg"):
        return None
    out = {"won_per_kg": s["won_per_kg"], "total_kg": s["total_kg"]}
    if size:
        out.update(box_avg=s.get("box_avg"), box_min=s.get("box_min"), box_max=s.get("box_max"))
    return out


def _with_box(v: dict | None, size: str | None):
    if v and size and "box_avg" not in v:
        v["box_avg"] = round(v["won_per_kg"] * float(size))
    return v


def _find(store, d: date, size, market):
    """d 당일 값, 없으면 직전 경매일 값."""
    for back in range(LOOKBACK_DAYS + 1):
        day = d - timedelta(days=back)
        v = _won_per_kg(store.get_day(day.isoformat()), size, market)
        if v:
            return {"date": day.isoformat(), **_with_box(v, size)}
    return None


def _same_day(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:  # 2월 29일
        return d.replace(year=year, day=28)


def _pct(a, b):
    return round((a - b) / b * 100, 1) if a and b else None


def build_view(store, today: date, size: str | None = "4", market: str | None = None) -> dict:
    in_season = config.SEASON_START <= today.strftime("%m-%d") <= config.SEASON_END
    # 비수기면 가장 최근 시즌의 마지막 날을 기준일로 씀
    if in_season:
        ref = today
    else:
        y = today.year if today.strftime("%m-%d") > config.SEASON_END else today.year - 1
        ref = date.fromisoformat(f"{y}-{config.SEASON_END}")

    current = _find(store, ref, size, market)

    past = []
    for i in range(1, config.YEARS_BACK + 1):
        y = ref.year - i
        v = _find(store, _same_day(ref, y), size, market)
        past.append({"label": ["작년", "재작년", "재재작년"][i - 1] if i <= 3 else f"{i}년 전",
                     "year": y, **(v or {"date": None, "won_per_kg": None})})

    vals = [p["won_per_kg"] for p in past if p["won_per_kg"]]
    avg3 = round(sum(vals) / len(vals)) if vals else None

    # 추세 그래프: 연도별 출하기 날짜별 kg당 가격
    series = {}
    for y in [ref.year] + [ref.year - i for i in range(1, config.YEARS_BACK + 1)]:
        pts = []
        for doc in sorted(store.get_year(y), key=lambda x: x["md"]):
            if not (config.SEASON_START <= doc["md"] <= config.SEASON_END):
                continue
            v = _won_per_kg(doc, size, market)
            if v:
                pts.append({"md": doc["md"], "won_per_kg": v["won_per_kg"]})
        series[str(y)] = pts

    last_year = past[0]["won_per_kg"] if past else None
    cur = current["won_per_kg"] if current else None
    return {
        "basis": "같은 시기 조생 백도계 복숭아 시세 (품종: 복숭아(일반)·백도·기타)",
        "in_season": in_season,
        "reference_date": ref.isoformat(),
        "size_kg": size,
        "market": market,
        "market_name": config.MARKETS.get(market) if market else "전국",
        "current": current,
        "vs_last_year_pct": _pct(cur, last_year),
        "past_years": past,
        "avg_3y_won_per_kg": avg3,
        "vs_avg_3y_pct": _pct(cur, avg3),
        "series": series,
    }
