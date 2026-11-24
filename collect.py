"""가격 수집 명령.

  python collect.py probe                 # 응답 형식과 코드 확인 (서울가락, 2026-06-25)
  python collect.py backfill 2024 2026    # 지난 시즌 자료 한꺼번에 받기 (중간에 끊겨도 이어서 받음)
  python collect.py grade-backfill 2020 2026  # 등급(grades) 없는 날짜를 원자료로 다시 집계
  python collect.py report                # 연도별 문서 수, 등급 자료 날짜 범위, 실제 등급명
  python collect.py export backup.json    # 컬렉션 전체 백업
  python collect.py day 2026-06-25        # 하루치만 다시 받기
  python collect.py daily                 # 오늘 자료 받기 (Render Cron Job용, 비수기엔 아무것도 안 함)
"""
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
    elif cmd == "report":
        report(store)
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
