"""등급별 가격 비율 (등급 프리미엄).

등급별로 가격 모델을 따로 만들기엔 자료가 적고 대부분(약 73%)이 '특'이라서,
`r(등급, kg) = 등급의 kg당 가격 ÷ 같은 해·같은 상자의 전체 kg당 가격`을 해마다 구해 평균하고,
등급별 예상 가격 = 기존 모델의 예상 가격 × r 로 계산한다.

- 해 전체(출하기 전체)를 합산해 비율을 구함. 날짜별로 구하면 상·중·하는 표본이 너무 얇음.
- 쓰는 등급은 특·상·중·하. 등외, 5~8등, 무등급, "." 등은 등급 판정 대상에서 제외하지만
  전체 평균(분모)에는 그대로 들어 있음 (기존 모델이 예측하는 값이 그 전체 평균이므로).
- 해를 하나씩 빼고 그해 비율을 맞혀 본 오차(`error_pct`)를 같이 낸다.
"""
from __future__ import annotations

GRADES = ("특", "상", "중", "하")
MIN_KG_PER_YEAR = 3000        # 해당 연도·상자·등급의 출하기 합계 물량이 이보다 적으면 그 해는 뺌
MIN_YEARS = 2                 # 비율을 내려면 필요한 최소 연도 수


def compute_ratios(docs: list[dict]) -> dict:
    """{'4': {'특': {'ratio', 'years', 'n_years', 'error_pct', 'share_pct'}, ...}, ...}

    등급 필드(`grades`)가 없는 문서는 건너뜀. 조건을 못 채운 등급은 결과에 없음.
    """
    # year -> kg -> grade -> [totprc, total_kg]
    acc: dict[int, dict[str, dict[str, list[float]]]] = {}
    for d in docs:
        gs = d.get("grades") or {}
        year = d.get("year") or int(str(d.get("date", "0000"))[:4])
        for kg, buckets in gs.items():
            for grade, s in (buckets or {}).items():
                kgs, prc = s.get("total_kg") or 0, s.get("totprc") or 0
                if kgs <= 0 or prc <= 0:
                    continue
                cell = acc.setdefault(year, {}).setdefault(kg, {}).setdefault(grade, [0.0, 0.0])
                cell[0] += prc
                cell[1] += kgs

    per: dict[str, dict[str, dict[int, tuple[float, float]]]] = {}    # kg -> grade -> year -> (ratio, 물량 비중)
    for year, bykg in acc.items():
        for kg, bg in bykg.items():
            all_prc = sum(v[0] for v in bg.values())
            all_kg = sum(v[1] for v in bg.values())
            if all_kg <= 0:
                continue
            avg = all_prc / all_kg
            for g in GRADES:
                if g in bg and bg[g][1] >= MIN_KG_PER_YEAR:
                    per.setdefault(kg, {}).setdefault(g, {})[year] = ((bg[g][0] / bg[g][1]) / avg,
                                                                      bg[g][1] / all_kg)
    out: dict[str, dict[str, dict]] = {}
    for kg, bygrade in per.items():
        for g, byyear in bygrade.items():
            if len(byyear) < MIN_YEARS:
                continue
            ratios = {y: v[0] for y, v in byyear.items()}
            mean = sum(ratios.values()) / len(ratios)
            errs = []
            for y, r in ratios.items():           # 그해를 뺀 나머지 해의 평균으로 맞혀 봄
                rest = [v for yy, v in ratios.items() if yy != y]
                errs.append(abs(r / (sum(rest) / len(rest)) - 1))
            out.setdefault(kg, {})[g] = {
                "ratio": round(mean, 4),
                "years": sorted(ratios),
                "n_years": len(ratios),
                "error_pct": round(100 * sum(errs) / len(errs), 1),
                "share_pct": round(100 * sum(v[1] for v in byyear.values()) / len(byyear), 1),
            }
    return out


def parse_grade_probs(text: str) -> dict[str, float]:
    """'특:0.2,상:0.6,중:0.2' -> {'특':0.2, ...}. 합이 1에서 2% 넘게 벗어나면 오류, 아니면 합을 1로 맞춤."""
    probs: dict[str, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            name, val = part.rsplit(":", 1)
            v = float(val)
        except ValueError:
            raise ValueError("grade_probs 형식은 '특:0.2,상:0.6,중:0.2'")
        name = name.strip()
        if name not in GRADES:
            raise ValueError(f"등급은 {', '.join(GRADES)} 중 하나 (받은 값: {name})")
        if v < 0 or name in probs:
            raise ValueError("등급 확률은 0 이상이고 등급당 한 번만 줄 수 있음")
        probs[name] = v
    total = sum(probs.values())
    if not probs or abs(total - 1) > 0.02:
        raise ValueError("등급 확률의 합이 1이어야 함")
    return {g: v / total for g, v in probs.items() if v > 0}
