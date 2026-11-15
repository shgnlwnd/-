from datetime import date

from peach_price.normalize import aggregate_day, to_record
from peach_price.prices import build_view
from peach_price.store import MemoryStore


def row(market="110001", sname="백도", qty="4", tot_qty="40", totprc="140000",
        avg="14000", lw="8000", hg="20000", unit="kg"):
    return {"whsl_mrkt_cd": market, "gds_mclsf_nm": "복숭아", "gds_sclsf_nm": sname,
            "gds_sclsf_cd": "11", "unit_qty": qty, "unit_nm": unit, "unit_tot_qty": tot_qty,
            "totprc": totprc, "avgprc": avg, "lwprc": lw, "hgprc": hg}


def test_total_qty_as_kg_or_boxes():
    # 4kg 10상자 140,000원: 단위총물량이 40(kg)이든 10(상자)이든 kg당 3,500원
    assert to_record(row(tot_qty="40"))["total_kg"] == 40
    assert to_record(row(tot_qty="10"))["total_kg"] == 40


def test_filter_and_size_split():
    rows = [row(), row(sname="천홍"), row(qty="2.5", tot_qty="25", totprc="150000", avg="15000")]
    agg = aggregate_day(rows)
    assert set(agg["sizes"]) == {"4", "2.5"}       # 천홍은 제외
    assert agg["sizes"]["4"]["won_per_kg"] == 3500
    assert agg["sizes"]["4"]["box_avg"] == 14000
    assert agg["sizes"]["2.5"]["won_per_kg"] == 6000
    assert agg["sizes"]["4"]["box_min"] == 8000


def test_outlier_and_bad_unit_dropped():
    assert to_record(row(totprc="10", avg="1")) is None
    assert to_record(row(unit="개")) is None


def _seed(store):
    for y, price in [(2024, 140000), (2025, 120000), (2026, 160000), (2027, 150000)]:
        store.save_day(f"{y}-06-25", aggregate_day([row(totprc=str(price), avg=str(price // 10))]), [])


def test_view_in_season():
    s = MemoryStore()
    _seed(s)
    v = build_view(s, date(2027, 6, 25), "4")
    assert v["current"]["won_per_kg"] == 3750
    assert [p["label"] for p in v["past_years"]] == ["작년", "재작년", "재재작년"]
    assert [p["won_per_kg"] for p in v["past_years"]] == [4000, 3000, 3500]
    assert v["vs_last_year_pct"] == -6.2
    assert v["avg_3y_won_per_kg"] == 3500


def test_view_holiday_falls_back():
    s = MemoryStore()
    _seed(s)
    v = build_view(s, date(2027, 6, 27), "4")   # 6/27 자료 없음 → 6/25 값
    assert v["current"]["date"] == "2027-06-25"


def test_view_off_season_uses_last_season():
    s = MemoryStore()
    _seed(s)
    s.save_day("2026-07-10", aggregate_day([row()]), [])
    v = build_view(s, date(2026, 11, 14), "4")
    assert v["in_season"] is False
    assert v["reference_date"] == "2026-07-10"
    assert v["current"]["won_per_kg"] == 3500


def test_market_filter():
    s = MemoryStore()
    s.save_day("2027-06-25", aggregate_day([row(market="380401", totprc="100000", avg="10000"),
                                            row(market="110001")]), [])
    v = build_view(s, date(2027, 6, 25), "4", "380401")
    assert v["current"]["won_per_kg"] == 2500
    assert v["market_name"] == "진주"
