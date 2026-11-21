"""속도 개선(자료 보관, 검증 결과 재사용) 확인."""
from datetime import date, timedelta

from peach_price.forecast import Forecaster
from peach_price.store import CachedStore, MemoryStore


def _docs():
    out = []
    for y in range(2021, 2026):
        d = date(y, 6, 10)
        i = 0
        while d <= date(y, 7, 10):
            wpk = 5000 - 40 * i + (y - 2021) * 100
            out.append({"date": d.isoformat(), "year": y, "md": d.isoformat()[5:],
                        "sizes": {"4": {"total_kg": 8000, "totprc": wpk * 8000, "won_per_kg": wpk}}})
            d += timedelta(days=1)
            i += 1
    return out


def test_backtest_reused_only_when_data_same():
    docs = _docs()
    f = Forecaster(docs)
    assert not f.bt_reused
    again = Forecaster(docs, bt_cache=f.bt_doc())
    assert again.bt_reused and again.bt == f.bt
    extra = docs + [{"date": "2025-07-11", "year": 2025, "md": "07-11",
                     "sizes": {"4": {"total_kg": 8000, "totprc": 3000 * 8000, "won_per_kg": 3000}}}]
    assert not Forecaster(extra, bt_cache=f.bt_doc()).bt_reused


def test_cached_store_reads_once():
    ms = MemoryStore()
    for d in _docs():
        ms.docs[d["date"]] = d
    n = {"c": 0}
    orig = ms.get_year

    def counting(y):
        n["c"] += 1
        return orig(y)
    ms.get_year = counting
    cs = CachedStore(ms, ttl=3600)
    for _ in range(5):
        cs.get_year(2024)
        assert cs.get_day("2024-06-15")["date"] == "2024-06-15"
    assert n["c"] == 1
    cs.save_day("2024-06-16", {"sizes": {}}, [])
    cs.get_year(2024)
    assert n["c"] == 2
