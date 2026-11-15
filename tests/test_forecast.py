import math
from datetime import date, timedelta

import pytest

from peach_price.forecast import Forecaster


def synth_docs(years=(2022, 2023, 2024, 2025), level=None):
    """출하기 초반 비싸고 점점 내려가는 가상 시세. 해마다 수준만 다름."""
    level = level or {2022: 4200, 2023: 5000, 2024: 4400, 2025: 4100, 2026: 4600}
    docs = []
    for y in years:
        d = date(y, 6, 1)
        while d <= date(y, 7, 15):
            if d.weekday() != 6:
                t = (d - date(y, 6, 1)).days
                base = level[y] * (1.4 - 0.012 * t) * (1.05 if d.weekday() == 0 else 1.0)
                docs.append({"date": d.isoformat(), "sizes": {
                    "4": {"won_per_kg": round(base), "total_kg": 50000.0},
                    "2.5": {"won_per_kg": round(base * 1.7), "total_kg": 80000.0}}})
            d += timedelta(days=1)
    return docs


def test_forecast_shape_and_order():
    f = Forecaster(synth_docs())
    r = f.forecast(date(2026, 6, 22), 4, today=date(2026, 5, 1))
    assert len(r["days"]) == 7
    assert r["days"][3]["is_harvest_day"] and r["days"][3]["date"] == "2026-06-22"
    for x in r["days"]:
        assert x["box_low"] <= x["box_price"] <= x["box_high"]
    # 시즌 전이므로 지난 해 수준 사용
    assert "지난" in r["level_source"]
    # 가격이 시즌 동안 내려가는 패턴을 배웠는지 (월요일 효과 제외하고 처음 > 끝)
    assert r["days"][0]["won_per_kg"] > r["days"][-1]["won_per_kg"] * 0.97


def test_uses_current_season_level():
    docs = synth_docs(years=(2022, 2023, 2024, 2025, 2026))
    f = Forecaster(docs)
    r = f.forecast(date(2026, 6, 25), 4, today=date(2026, 6, 20))
    assert "올해" in r["level_source"]
    # 실제 2026 값과 가까워야 함
    actual = {d["date"]: d["sizes"]["4"]["won_per_kg"] for d in docs}
    got = [x for x in r["days"] if x["date"] in actual]
    err = [abs(x["won_per_kg"] / actual[x["date"]] - 1) for x in got]
    assert max(err) < 0.08


def test_errors_reported():
    f = Forecaster(synth_docs())
    r = f.forecast(date(2026, 6, 22), 4, today=date(2026, 6, 21))
    assert r["error_by_horizon"]
    assert all(x["expected_error_pct"] is not None for x in r["days"])


def test_unknown_size():
    f = Forecaster(synth_docs())
    with pytest.raises(ValueError):
        f.forecast(date(2026, 6, 22), 10, today=date(2026, 6, 1))
