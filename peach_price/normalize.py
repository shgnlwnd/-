"""API 응답을 조생 백도계 기준으로 거르고, 상자 크기별 하루 집계를 만듦."""
from __future__ import annotations

from . import config


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def is_target(row: dict) -> bool:
    """복숭아 중 품종이 일반·백도·기타인 행인지."""
    if (row.get("gds_mclsf_nm") or "").strip() != config.PEACH_MCLSF_NAME:
        return False
    name = (row.get("gds_sclsf_nm") or "").strip()
    code = str(row.get("gds_sclsf_cd") or "").strip()
    return name in config.TARGET_SCLSF_NAMES or (
        bool(code) and code[-2:] in config.TARGET_SCLSF_SUFFIX and name == ""
    )


def box_kg(row: dict) -> float | None:
    """단위물량과 단위명으로 상자 하나의 kg을 구함. kg로 못 바꾸면 None."""
    qty = _num(row.get("unit_qty"))
    unit = (row.get("unit_nm") or "").strip().lower()
    if qty <= 0:
        return None
    if unit == "kg":
        return qty
    if unit == "g":
        return qty / 1000
    return None


def to_record(row: dict) -> dict | None:
    """한 행을 kg 기준 숫자로 바꿈. 쓸 수 없는 행은 None."""
    kg = box_kg(row)
    totprc = _num(row.get("totprc"))
    avgprc = _num(row.get("avgprc"))
    tot_qty = _num(row.get("unit_tot_qty"))
    if not kg or totprc <= 0 or avgprc <= 0 or tot_qty <= 0:
        return None

    # 단위총물량이 '총 kg'인지 '총 상자 수'인지 명세서에 없어 값으로 판별함.
    boxes_by_price = totprc / avgprc
    if abs(tot_qty - boxes_by_price * kg) <= abs(tot_qty - boxes_by_price):
        total_kg = tot_qty
    else:
        total_kg = tot_qty * kg

    won_per_kg = totprc / total_kg
    if not (config.MIN_WON_PER_KG <= won_per_kg <= config.MAX_WON_PER_KG):
        return None

    return {
        "market": str(row.get("whsl_mrkt_cd") or ""),
        "kg": kg,
        "total_kg": total_kg,
        "totprc": totprc,
        "lwprc": _num(row.get("lwprc")),
        "hgprc": _num(row.get("hgprc")),
        "grade": (row.get("grd_nm") or "").strip(),
        "size": (row.get("sz_nm") or "").strip(),
    }


def size_key(kg: float) -> str:
    """상자 크기 키. 4.0 -> '4', 2.5 -> '2.5'"""
    return f"{kg:g}"


def _summ(recs: list[dict]) -> dict:
    total_kg = sum(r["total_kg"] for r in recs)
    totprc = sum(r["totprc"] for r in recs)
    lows = [r["lwprc"] for r in recs if r["lwprc"] > 0]
    highs = [r["hgprc"] for r in recs if r["hgprc"] > 0]
    return {
        "total_kg": round(total_kg, 1),
        "totprc": round(totprc),
        "won_per_kg": round(totprc / total_kg) if total_kg else None,
        "box_min": min(lows) if lows else None,
        "box_max": max(highs) if highs else None,
        "rows": len(recs),
    }


def aggregate_day(rows: list[dict]) -> dict:
    """하루치 원자료 -> 전국/시장별, 상자 크기별 거래량 가중 평균."""
    recs = [r for r in (to_record(x) for x in rows if is_target(x)) if r]
    by_size: dict[str, list[dict]] = {}
    by_grade: dict[str, dict[str, list[dict]]] = {}
    by_market: dict[str, dict[str, list[dict]]] = {}
    for r in recs:
        k = size_key(r["kg"])
        by_size.setdefault(k, []).append(r)
        grade = r["grade"] or "미표기"
        by_grade.setdefault(k, {}).setdefault(grade, []).append(r)
        by_market.setdefault(r["market"], {}).setdefault(k, []).append(r)

    sizes = {}
    for k, rs in by_size.items():
        s = _summ(rs)
        s["box_avg"] = round(s["won_per_kg"] * float(k)) if s["won_per_kg"] else None
        sizes[k] = s

    markets = {
        m: {k: {"total_kg": round(sum(r["total_kg"] for r in rs), 1),
                "totprc": round(sum(r["totprc"] for r in rs))}
            for k, rs in d.items()}
        for m, d in by_market.items()
    }
    # 등급/상자무게별 총 물량과 총 금액을 함께 저장해 재집계 없이 계산 가능하게 함.
    grades = {
        k: {grade: _summ(rs) for grade, rs in buckets.items()}
        for k, buckets in by_grade.items()
    }
    return {"all": _summ(recs) if recs else None, "sizes": sizes,
            "markets": markets, "grades": grades}
