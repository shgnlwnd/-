"""앱이 부르는 가격 API (Render Web Service).

GET /prices?size=4&market=380401&date=2027-06-25
  - size: 상자 kg (2, 2.5, 3, 4, 4.5, 5, 10). 비우면 크기 구분 없이 kg당 가격만
  - market: 도매시장 코드. 비우면 전국
  - date: 기준일 (기본 오늘, 한국 시간). 시험용
  - 헤더: Authorization: Bearer <Firebase ID 토큰>  (REQUIRE_AUTH=true일 때)
GET /markets   시장 코드 목록
GET /health    상태 확인
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from peach_price import config
from peach_price.prices import build_view

KST = timezone(timedelta(hours=9))
SIZES = {"2", "2.5", "3", "4", "4.5", "5", "10"}

app = FastAPI(title="미홍 복숭아 가격 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["Authorization"])

_store = None


def get_store():
    global _store
    if _store is None:
        from peach_price.store import FirestoreStore
        _store = FirestoreStore()
    return _store


def verify_user(authorization: str | None = Header(default=None)):
    if not config.REQUIRE_AUTH:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "로그인이 필요합니다")
    from firebase_admin import auth
    from peach_price.store import db
    db()  # Firebase 초기화
    try:
        return auth.verify_id_token(authorization.split(" ", 1)[1])
    except Exception:
        raise HTTPException(401, "로그인 정보가 올바르지 않습니다")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/markets")
def markets():
    return [{"code": k, "name": v} for k, v in config.MARKETS.items()]


@app.get("/prices")
def prices(size: str | None = Query("4"), market: str | None = None,
           date_: str | None = Query(None, alias="date"),
           _user=Depends(verify_user), store=Depends(get_store)):
    if size and size not in SIZES:
        raise HTTPException(400, f"size는 {sorted(SIZES, key=float)} 중 하나")
    if market and market not in config.MARKETS:
        raise HTTPException(400, "알 수 없는 시장 코드")
    try:
        today = date.fromisoformat(date_) if date_ else datetime.now(KST).date()
    except ValueError:
        raise HTTPException(400, "date는 YYYY-MM-DD 형식")
    return build_view(store, today, size or None, market)
