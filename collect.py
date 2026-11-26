"""가격 수집 명령.

  python collect.py probe                 # 응답 형식과 코드 확인 (서울가락, 2026-06-25)
  python collect.py backfill 2024 2026    # 지난 시즌 자료 한꺼번에 받기 (중간에 끊겨도 이어서 받음)
  python collect.py grade-backfill 2020 2026  # 등급(grades) 없는 날짜를 원자료로 다시 집계
  python collect.py report                # 연도별 문서 수, 등급 자료 날짜 범위, 실제 등급명
  python collect.py grade-ratios          # 등급 가격비(특·상·중·하) 실제 값
  python collect.py tree-dump trees.csv   # 예측 모델 나무 200개의 질문 전체 목록
  python collect.py export backup.json    # 컬렉션 전체 백업
  python collect.py day 2026-06-25        # 하루치만 다시 받기
  python collect.py daily                 # 오늘 자료 받기 (Render Cron Job용, 비수기엔 아무것도 안 함)
"""
import math
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from peach_price import config
from peach_price.kat_client import fetch_market_day
from peach_price.normalize import aggregate_day, is_target, to_record

KST = timezone(timedelta(hours=9))


def probe(ymd: str = "2026-06-25", market: str = "110001"):
    print(f"[1] 품목 조건 포함 조회: {config.MARKETS[market]} {ymd}, "
          f"LCLSF={config.LCLSF_CD!r} MCLSF={config.MCLSF_CD!r}")
    rows = fetch_market_day(market, ymd, with_item_filter=True)
    print(f"    {len(rows)}건")
    if not rows:
        print("[2] 0건이라 품목 조건 없이 다시 조회 (코드 형식 확인용)")
        rows = fetch_market_day(market, ymd, with_item_filter=False)
        print(f"    {len(rows)}건")
    peach = [r for r in rows if "복숭아" in (r.get("gds_mclsf_nm") or "")]
    if not peach:
        print("복숭아 행이 없음. 날짜나 시장을 바꿔 다시 해 보세요.")
        return
    codes = Counter((r.get("gds_lclsf_cd"), r.get("gds_mclsf_cd"), r.get("gds_sclsf_cd"),
                     r.get("gds_sclsf_nm")) for r in peach)
    print("\n복숭아 코드 (대분류, 중분류, 소분류, 품종명): 건수")
    for k, n in codes.most_common(15):
        print(f"  {k}: {n}")
    print("\n첫 행 원본:")
    for k, v in peach[0].items():
        print(f"  {k}: {v}")
    target = [r for r in peach if is_target(r)]
    ok = [to_record(r) for r in target]
    print(f"\n대상(일반·백도·기타) {len(target)}건 중 계산 가능 {sum(1 for x in ok if x)}건")
    print("단위 예시:", Counter((r.get('unit_qty'), r.get('unit_nm'), r.get('pkg_nm')) for r in target).most_common(5))
    print("\n→ 위 중분류 코드가 04가 아니면 KAT_MCLSF_CD 환경변수를 그 값으로 바꾸세요.")


def collect_day(store, ymd: str, skip_done: bool = False) -> dict | None:
    if skip_done:
        existing = store.get_day(ymd)
        if existing and len(existing.get("markets_done", [])) == len(config.MARKETS):
            print(f"{ymd} 이미 받음, 건너뜀")
            return existing
    rows, done = [], []
    for code in config.MARKETS:
        rows.extend(fetch_market_day(code, ymd))
        done.append(code)
    agg = aggregate_day(rows)
    if agg["all"] is None:
        print(f"{ymd} 거래 없음 (휴장일 등)")
    else:
        print(f"{ymd} 전국 kg당 {agg['all']['won_per_kg']:,}원, {agg['all']['total_kg']:,}kg")
    store.save_day(ymd, agg, done)
    return agg


def _all_docs(store) -> list[dict]:
    return [d.to_dict() for d in store.col.stream()]


def report(store):
    """Firestore 자료 현황: 연도별 문서 수, 등급 자료가 있는 날짜 범위, 실제 등급명(grd_nm)."""
    docs = _all_docs(store)
    by_year: dict[int, list[dict]] = {}
    for d in docs:
        by_year.setdefault(int(d.get("year") or d["date"][:4]), []).append(d)
    print(f"# Firestore {config.FIRESTORE_COLLECTION} 현황 (문서 {len(docs)}개)\n")
    print("| 연도 | 문서 수 | 거래 있는 날 | 등급 필드 있는 문서 | 등급 거래 있는 날짜 범위 |")
    print("|---|---|---|---|---|")
    for y in sorted(by_year):
        ds = sorted(by_year[y], key=lambda x: x["date"])
        trade = [d for d in ds if d.get("all")]
        graded = [d for d in ds if "grades" in d]
        gdays = [d["date"] for d in graded if any(d["grades"].get(k) for k in d["grades"])]
        rng = f"{gdays[0]} ~ {gdays[-1]} ({len(gdays)}일)" if gdays else "없음"
        print(f"| {y} | {len(ds)} | {len(trade)} | {len(graded)} | {rng} |")
    names: Counter = Counter()
    days: dict[str, set] = {}
    kg: Counter = Counter()
    sizes: dict[str, set] = {}
    for d in docs:
        for size, gs in (d.get("grades") or {}).items():
            for g, s in gs.items():
                names[g] += s.get("rows") or 0
                days.setdefault(g, set()).add(d["date"])
                kg[g] += s.get("total_kg") or 0
                sizes.setdefault(g, set()).add(size)
    print("\n## 실제 등급명 (grd_nm, 빈 값은 '미표기')\n")
    if not names:
        print("등급 자료 없음 (grade-backfill 전)")
        return
    print("| 등급명 | 경매 건수 | 거래일 수 | 총 물량(kg) | 상자 크기(kg) |")
    print("|---|---|---|---|---|")
    for g, n in names.most_common():
        sz = ", ".join(sorted(sizes[g], key=float))
        print(f"| {g!r} | {n:,} | {len(days[g])} | {round(kg[g]):,} | {sz} |")


def grade_ratios(store):
    """등급 가격비(특·상·중·하 ÷ 전체) 실제 값. /forecast의 grade_probs 계산에 쓰는 값과 같음."""
    from peach_price.forecast import STD_SIZES
    from peach_price.grades import GRADES, compute_ratios
    out = compute_ratios(_all_docs(store))
    print("\n## 등급 가격비 (출하기 합산, 해 평균)\n")
    print("| 상자 | 등급 | 가격비 | 연도 수 | 해별 오차(%) | 물량 비중(%) |")
    print("|---|---|---|---|---|---|")
    for kg in sorted(out, key=float):
        if float(kg) not in STD_SIZES:
            continue
        for g in GRADES:
            v = out[kg].get(g)
            if v:
                print(f"| {kg}kg | {g} | {v['ratio']} | {v['n_years']} | {v['error_pct']} | {v['share_pct']} |")
            else:
                print(f"| {kg}kg | {g} | 자료 부족 | - | - | - |")


def _question(feature: int, thr: float) -> tuple[str, str]:
    """나무 갈림길 질문을 사람이 읽는 말로. (입력 이름, 질문)"""
    from datetime import date, timedelta
    if feature == 0:
        day = int(thr)                       # season_day <= thr 이면 왼쪽
        d = date(2026, 6, 1) + timedelta(days=day)
        return "출하기 며칠째", f"{d.month}월 {d.day}일(출하기 {day}일째)까지인가? (≤ {thr:g})"
    if feature == 1:
        names = "월화수목금토일"
        upto = names[: int(thr) + 1]
        return "요일", f"{upto[0]}~{upto[-1]}요일인가? (요일 ≤ {thr:g})" if len(upto) > 1 else f"월요일인가? (요일 ≤ {thr:g})"
    return "상자 크기", f"{thr:g}kg 이하 상자인가?"


def tree_dump(store, path: str):
    """예측 모델(시즌 곡선 Gradient Boosting)의 나무 200개에 들어간 질문을 모두 꺼냄.

    - CSV(path): 나무·노드마다 한 줄. 갈림길은 질문, 잎은 그 칸의 가격 영향(%)
    - 화면(요약): 입력별 질문 수, 서로 다른 질문 목록과 쓰인 횟수, 1번 나무 구조
    """
    import csv
    import math
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    from peach_price.forecast import Forecaster
    today = datetime.now(timezone(timedelta(hours=9))).date()
    docs = []
    for y in range(today.year - 8, today.year + 1):
        docs.extend(store.get_year(y))
    m = Forecaster(docs).model
    gbm = m.gbm
    lr = gbm.learning_rate
    rows, uniq, feat_count = [], Counter(), Counter()
    n_leaves = 0
    for t, est in enumerate(gbm.estimators_[:, 0], start=1):
        tr = est.tree_
        depth = {0: 0}
        for node in range(tr.node_count):
            left, right = tr.children_left[node], tr.children_right[node]
            if left != -1:
                depth[left] = depth[right] = depth[node] + 1
                name, q = _question(int(tr.feature[node]), float(tr.threshold[node]))
                feat_count[name] += 1
                uniq[(name, q)] += 1
                rows.append([t, node, depth[node], "질문", name, round(float(tr.threshold[node]), 3), q,
                             int(tr.n_node_samples[node]), ""])
            else:
                n_leaves += 1
                eff = (math.exp(lr * float(tr.value[node][0][0])) - 1) * 100
                rows.append([t, node, depth[node], "잎", "", "", "", int(tr.n_node_samples[node]), round(eff, 3)])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["나무", "노드", "깊이", "구분", "입력", "기준값", "질문(예=왼쪽)", "자료 수(표본)", "잎 가격 영향(%)"])
        w.writerows(rows)

    # 잎마다 뿌리에서 잎까지 거친 질문과 답(예/아니오), 그리고 그 답을 합친 조건
    leaf_rows, cond_count, depth_count = [], Counter(), Counter()
    for t, est in enumerate(gbm.estimators_[:, 0], start=1):
        tr = est.tree_
        stack = [(0, [])]
        while stack:
            node, path_q = stack.pop()
            left, right = tr.children_left[node], tr.children_right[node]
            if left != -1:
                f_, thr = int(tr.feature[node]), float(tr.threshold[node])
                q = _question(f_, thr)[1]
                stack.append((right, path_q + [(f_, thr, q, False)]))
                stack.append((left, path_q + [(f_, thr, q, True)]))
                continue
            eff = (math.exp(lr * float(tr.value[node][0][0])) - 1) * 100
            cond = _leaf_condition(path_q)
            cond_count[cond] += 1
            depth_count[len(path_q)] += 1
            qs = [f"{q} → {'예' if yes else '아니오'}" for _, _, q, yes in path_q]
            leaf_rows.append([t, node, len(path_q)] + qs + [""] * (3 - len(qs))
                             + [cond, int(tr.n_node_samples[node]), round(eff, 3)])
    leaf_path = path.replace(".csv", "") + "_leaves.csv"
    with open(leaf_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["나무", "잎 노드", "거친 질문 수", "질문1 → 답", "질문2 → 답", "질문3 → 답",
                    "합친 조건", "자료 수(표본)", "잎 가격 영향(%)"])
        w.writerows(leaf_rows)
    n_q = sum(feat_count.values())
    print(f"## 예측 모델 나무 질문 (학습 {len(m.obs)}건, 연도 {sorted({o.d.year for o in m.obs})})\n")
    print(f"- 나무 {len(gbm.estimators_)}개, 질문(갈림길) {n_q}개, 잎 {n_leaves}개, 서로 다른 질문 {len(uniq)}개")
    print(f"- 잎 가격 영향 = 학습률({lr}) × 잎 값을 가격 배수로 바꾼 것. 나무 200개 영향을 모두 곱한 것이 시즌 곡선임")
    print(f"- 전체 목록: {path}\n")
    print("### 입력별 질문 수\n")
    print("| 입력 | 질문 수 | 비율 |")
    print("|---|---|---|")
    for name, c in feat_count.most_common():
        print(f"| {name} | {c} | {100 * c / n_q:.1f}% |")
    print("\n### 서로 다른 질문 전체 (많이 쓰인 순)\n")
    print("| 순위 | 입력 | 질문 | 쓰인 횟수 |")
    print("|---|---|---|---|")
    for i, ((name, q), c) in enumerate(uniq.most_common(), start=1):
        print(f"| {i} | {name} | {q} | {c} |")
    print("\n### 1번 나무 구조\n")
    print("| 노드 | 깊이 | 구분 | 질문 / 잎 가격 영향 | 자료 수 |")
    print("|---|---|---|---|---|")
    for r in rows:
        if r[0] != 1:
            break
        body = r[6] if r[3] == "질문" else f"{r[8]:+.3f}%"
        print(f"| {r[1]} | {r[2]} | {r[3]} | {body} | {r[7]} |")

    n_leaf_q = sum(k * v for k, v in depth_count.items())
    print("\n## 잎에 들어간 질문 (뿌리 → 잎 경로)\n")
    print(f"- 잎 {len(leaf_rows)}개, 잎까지 거친 질문 총 {n_leaf_q}개, 서로 다른 잎 조건 {len(cond_count)}가지")
    print("- 거친 질문 수별 잎 수: " + ", ".join(f"{k}개 {v}잎" for k, v in sorted(depth_count.items())))
    print(f"- 잎마다 전체 경로: {leaf_path}\n")
    print("### 1번 나무 잎 8개의 경로\n")
    print("| 잎 | 질문1 → 답 | 질문2 → 답 | 질문3 → 답 | 합친 조건 | 자료 수 | 가격 영향 |")
    print("|---|---|---|---|---|---|---|")
    for r in leaf_rows:
        if r[0] != 1:
            break
        print(f"| {r[1]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]:+.3f}% |")
    eff_by_cond = {}
    for r in leaf_rows:
        eff_by_cond.setdefault(r[6], []).append(r[8])
    print("\n### 자주 나온 잎 조건 상위 40\n")
    print("| 순위 | 합친 조건 | 잎 수 | 평균 가격 영향 |")
    print("|---|---|---|---|")
    for i, (c, n) in enumerate(cond_count.most_common(40), start=1):
        e = eff_by_cond[c]
        print(f"| {i} | {c} | {n} | {sum(e) / len(e):+.3f}% |")


def _leaf_condition(path_q) -> str:
    """경로의 예/아니오 답을 입력별 범위 하나로 합침. 예) 출하 6/15~6/22 · 2.75kg 초과 3.5kg 이하"""
    from datetime import date, timedelta
    lo = {0: None, 1: None, 2: None}
    hi = {0: None, 1: None, 2: None}
    for f_, thr, _, yes in path_q:
        if f_ in (0, 1):                      # 정수 입력: ≤thr 이면 floor(thr) 이하, 아니면 floor(thr)+1 이상
            v = math.floor(thr)
            if yes:
                hi[f_] = v if hi[f_] is None else min(hi[f_], v)
            else:
                lo[f_] = v + 1 if lo[f_] is None else max(lo[f_], v + 1)
        else:
            if yes:
                hi[2] = thr if hi[2] is None else min(hi[2], thr)
            else:
                lo[2] = thr if lo[2] is None else max(lo[2], thr)
    parts = []
    if lo[0] is not None or hi[0] is not None:
        d = lambda n: (lambda x: f"{x.month}/{x.day}")(date(2026, 6, 1) + timedelta(days=n))
        a = d(lo[0]) if lo[0] is not None else ""
        b = d(hi[0]) if hi[0] is not None else ""
        parts.append(f"출하 {a}" if a == b else f"출하 {a}~{b}")
    if lo[1] is not None or hi[1] is not None:
        names = "월화수목금토일"
        a = lo[1] or 0
        if hi[1] is None:
            parts.append(f"{names[a]}요일 이후")
        else:
            parts.append(f"{names[a]}요일" if a == hi[1] else f"{names[a]}~{names[hi[1]]}요일")
    if lo[2] is not None or hi[2] is not None:
        if lo[2] is not None and hi[2] is not None:
            parts.append(f"{lo[2]:g}kg 초과 {hi[2]:g}kg 이하")
        elif hi[2] is not None:
            parts.append(f"{hi[2]:g}kg 이하")
        else:
            parts.append(f"{lo[2]:g}kg 초과")
    return " · ".join(parts)


def export(store, path: str):
    """재집계 전 백업용. 컬렉션 전체를 JSON 파일로 저장함."""
    import json
    docs = _all_docs(store)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, default=str)
    print(f"{len(docs)}개 문서를 {path}에 저장함")


def season_dates(year: int):
    d = date.fromisoformat(f"{year}-{config.SEASON_START}")
    end = date.fromisoformat(f"{year}-{config.SEASON_END}")
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "daily"
    if cmd == "probe":
        probe(*argv[2:4])
        return
    from peach_price.store import FirestoreStore
    store = FirestoreStore()
    if cmd == "backfill":
        y1, y2 = int(argv[2]), int(argv[3])
        for y in range(y1, y2 + 1):
            for ymd in season_dates(y):
                collect_day(store, ymd, skip_done=True)
    elif cmd == "grade-backfill":
        # 등급(grades)이 없는 날짜만 원자료를 다시 받아 재집계함. 끊기면 같은 명령으로 이어서 받음.
        y1, y2 = int(argv[2]), int(argv[3])
        for y in range(y1, y2 + 1):
            for ymd in season_dates(y):
                existing = store.get_day(ymd)
                if existing is not None and "grades" in existing:
                    continue
                collect_day(store, ymd, skip_done=False)
    elif cmd == "tree-dump":
        tree_dump(store, argv[2] if len(argv) > 2 else "tree_questions.csv")
    elif cmd == "report":
        report(store)
    elif cmd == "grade-ratios":
        grade_ratios(store)
    elif cmd == "export":
        export(store, argv[2] if len(argv) > 2 else "peach_daily_backup.json")
    elif cmd == "day":
        collect_day(store, argv[2])
    elif cmd == "daily":
        today = datetime.now(KST).date()
        if not (config.SEASON_START <= today.strftime("%m-%d") <= config.SEASON_END):
            print(f"{today} 출하기 아님, 수집 안 함")
            return
        # 늦게 올라오는 정산분이 있어 어제 것도 다시 받음
        for d in (today - timedelta(days=1), today):
            collect_day(store, d.isoformat())
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
