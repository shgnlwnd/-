"""등급 확률(grade_probs)로 등급별 가격을 내는 기능 시험. 합성 자료 사용 (Firestore 불필요)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from peach_price.forecast import Forecaster  # noqa: E402
from peach_price.grades import compute_ratios, parse_grade_probs  # noqa: E402
from test_forecast_distribution import synthetic_docs, TODAY  # noqa: E402

SHARE = {"특": (0.60, 1.10), "상": (0.20, 0.90), "중": (0.08, 0.70), "하": (0.04, 0.50), "등외": (0.05, 0.40),
         ".": (0.03, 1.00)}


def docs_with_grades(skip_years=()):
    out = []
    for d in synthetic_docs():
        s = d["sizes"]["4"]
        if d["year"] not in skip_years:
            tot_kg, won = s["total_kg"], s["won_per_kg"]
            norm = sum(sh * r for sh, r in SHARE.values())
            d = dict(d, grades={"4": {g: {"total_kg": tot_kg * sh, "totprc": tot_kg * sh * won * r / norm}
                                      for g, (sh, r) in SHARE.items()}})
        out.append(d)
    return out


def test_ratios_from_grades():
    t = compute_ratios(docs_with_grades())["4"]
    assert set(t) == {"특", "상", "중", "하"}                    # 등외·"." 은 제외
    assert t["특"]["ratio"] > t["상"]["ratio"] > t["중"]["ratio"] > t["하"]["ratio"]
    assert abs(t["특"]["ratio"] - 1.10 / sum(sh * r for sh, r in SHARE.values())) < 0.01   # 전체 평균 대비 and t["특"]["error_pct"] < 1
    assert t["특"]["n_years"] >= 4


def test_ratio_needs_two_years():
    assert compute_ratios([d for d in docs_with_grades(skip_years=(2022, 2023, 2024, 2025)) if "grades" in d]) == {}


def test_parse_grade_probs():
    assert parse_grade_probs("특:0.2, 상:0.6,중:0.2") == {"특": 0.2, "상": 0.6, "중": 0.2}
    assert abs(sum(parse_grade_probs("특:0.5,상:0.51").values()) - 1) < 1e-9      # 2% 안쪽은 합을 맞춤
    for bad in ("특:0.5", "특:1,별:0", "특:abc", "특:0.5,특:0.5", "특:-1,상:2"):
        try:
            parse_grade_probs(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


F = Forecaster(docs_with_grades())
P = {"특": 0.2, "상": 0.6, "중": 0.2}


def test_distribution_with_grades():
    r = F.forecast_distribution(date(2026, 6, 10), date(2026, 6, 14), date(2026, 6, 21), 4, TODAY, grade_probs=P)
    b = r["by_grade"]
    assert b["특"]["expected_box_price"] > b["상"]["expected_box_price"] > b["중"]["expected_box_price"]
    lo, hi = b["상"]["range"]
    assert lo < b["상"]["expected_box_price"] < hi
    t = F.grade_ratios["4"]
    want = r["expected_box_price"] * sum(P[g] * t[g]["ratio"] for g in P)
    assert abs(r["grade_expected_box_price"] - want) <= 1
    assert r["grade_expected_range"][0] < r["grade_expected_box_price"] < r["grade_expected_range"][1]
    assert r["grade_ratio_error_pct"] is not None
    # 등급을 안 주면 기존 응답과 같음
    plain = F.forecast_distribution(date(2026, 6, 10), date(2026, 6, 14), date(2026, 6, 21), 4, TODAY)
    assert "by_grade" not in plain and plain["expected_box_price"] == r["expected_box_price"]


def test_single_day_with_grades():
    r = F.forecast(date(2026, 6, 16), 4, TODAY, grade_probs={"특": 1.0})
    day = next(d for d in r["days"] if d["is_harvest_day"])
    assert abs(r["by_grade"]["특"]["expected_box_price"] - day["box_price"] * F.grade_ratios["4"]["특"]["ratio"]) <= 1


def test_missing_grade_data_is_not_faked():
    f = Forecaster(docs_with_grades(skip_years=(2022, 2023, 2024, 2025)))        # 등급 자료 1개 해뿐
    r = f.forecast(date(2026, 6, 16), 4, TODAY, grade_probs={"특": 1.0})
    assert r["by_grade"] is None and "부족" in r["grade_note"]
    assert "grade_expected_box_price" not in r


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"{len(tests)} tests passed")
