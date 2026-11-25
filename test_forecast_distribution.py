"""수확일 분포(p10·p50·p90)를 받는 Forecaster.forecast_distribution 시험. 합성 자료로 돌림 (Firestore 불필요)."""
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from peach_price.forecast import Forecaster, _date_weights  # noqa: E402

TODAY = date(2026, 6, 1)


def synthetic_docs():
    docs = []
    for year, level in ((2022, 3000), (2023, 3300), (2024, 3600), (2025, 3400), (2026, 3500)):
        d = date(year, 6, 1)
        while d <= date(year, 7, 15):
            if d.weekday() != 6 and not (year == 2026 and d >= TODAY):     # 일요일 휴장, 올해는 아직 거래 전
                x = (d - date(year, 6, 1)).days
                won = level * (1.25 - 0.012 * x) * (1 + 0.03 * math.sin(d.toordinal()))
                docs.append({"date": d.isoformat(), "year": year, "md": d.isoformat()[5:],
                             "sizes": {"4": {"won_per_kg": won, "total_kg": 8000.0}}})
            d += timedelta(days=1)
    return docs


F = Forecaster(synthetic_docs())


def test_weights_sum_to_one_and_follow_quantiles():
    p10, p50, p90 = date(2026, 6, 10), date(2026, 6, 14), date(2026, 6, 21)
    days = [p10 - timedelta(days=1) + timedelta(days=i) for i in range((p90 - p10).days + 3)]
    w = _date_weights(days, p10, p50, p90)
    assert abs(w.sum() - 1) < 1e-9 and (w >= 0).all()
    cum = w.cumsum()
    # p10 날짜까지 누적이 약 0.1+반일치, p90 날짜까지 약 0.9 이상
    assert 0.08 < cum[days.index(p10)] < 0.2
    assert cum[days.index(p90)] > 0.9
    assert cum[days.index(p50) - 1] < 0.5 <= cum[days.index(p50)]     # 가중 중앙값이 p50 날짜


def test_expected_price_and_range():
    r = F.forecast_distribution(date(2026, 6, 10), date(2026, 6, 14), date(2026, 6, 21), 4, TODAY)
    prices = [d["box_price"] for d in r["days"] if d["weight"] > 0]
    assert min(prices) <= r["expected_box_price"] <= max(prices)
    lo, hi = r["expected_range"]
    assert lo < r["expected_box_price"] < hi or lo < hi
    assert abs(sum(d["weight"] for d in r["days"]) - 1) < 0.01
    assert r["distribution"]["clamped"] is False
    for k in ("date", "weekday", "is_harvest_day", "market_closed", "box_price", "box_low", "box_high", "expected_error_pct"):
        assert k in r["days"][0]                            # 기존 앱 파서가 읽는 필드 유지
    assert sum(1 for d in r["days"] if d["is_harvest_day"]) == 1


def test_sunday_weight_moves_to_next_trading_day():
    r = F.forecast_distribution(date(2026, 6, 10), date(2026, 6, 14), date(2026, 6, 21), 4, TODAY)   # 6/14, 6/21 은 일요일
    closed = [d for d in r["days"] if d["market_closed"]]
    assert closed and all(d["weight"] == 0 for d in closed)
    assert abs(sum(d["weight"] for d in r["days"]) - 1) < 0.01


def test_wider_distribution_gives_wider_range():
    narrow = F.forecast_distribution(date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17), 4, TODAY)
    wide = F.forecast_distribution(date(2026, 6, 10), date(2026, 6, 16), date(2026, 6, 26), 4, TODAY)
    w = lambda r: (r["expected_range"][1] - r["expected_range"][0]) / r["expected_box_price"]  # noqa: E731
    assert w(wide) > w(narrow)


def test_point_distribution_close_to_single_day():
    r = F.forecast_distribution(date(2026, 6, 16), date(2026, 6, 16), date(2026, 6, 16), 4, TODAY)
    day = next(d for d in r["days"] if d["is_harvest_day"])
    assert abs(r["expected_box_price"] - day["box_price"]) / day["box_price"] < 0.05
    base = F.forecast(date(2026, 6, 16), 4, TODAY)
    base_day = next(d for d in base["days"] if d["is_harvest_day"])
    assert day["box_price"] == base_day["box_price"]        # 날짜별 가격은 기존 forecast 와 같은 계산


def test_clamped_at_season_end_and_out_of_season():
    r = F.forecast_distribution(date(2026, 7, 8), date(2026, 7, 11), date(2026, 7, 20), 4, TODAY)
    assert r["distribution"]["clamped"] and r["distribution"]["clamped_mass"] > 0
    assert all(d["date"] <= "2026-07-15" for d in r["days"])
    try:
        F.forecast_distribution(date(2026, 8, 1), date(2026, 8, 3), date(2026, 8, 5), 4, TODAY)
    except ValueError:
        pass
    else:
        raise AssertionError("출하기 밖은 오류여야 함")


def test_bad_input():
    for args in ((date(2026, 6, 20), date(2026, 6, 15), date(2026, 6, 25)),            # 순서 어긋남
                 (date(2026, 6, 1), date(2026, 6, 10), date(2026, 7, 10))):            # 폭 30일 초과
        try:
            F.forecast_distribution(*args, 4, TODAY)
        except ValueError:
            continue
        raise AssertionError(args)


def test_existing_forecast_unchanged():
    r = F.forecast(date(2026, 6, 16), 4, TODAY)
    assert len(r["days"]) == 7 and "expected_box_price" not in r


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} tests passed")
