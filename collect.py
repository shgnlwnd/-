"""가격 수집 명령.

  python collect.py probe                 # 응답 형식과 코드 확인 (서울가락, 2026-06-25)
  python collect.py backfill 2024 2026    # 지난 시즌 자료 한꺼번에 받기 (중간에 끊겨도 이어서 받음)
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
