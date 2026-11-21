"""앱이 부르는 가격 API (Render Web Service).

GET /prices?size=4&market=380401&date=2027-06-25
  - size: 상자 kg (2, 2.5, 3, 4, 4.5, 5, 10). 비우면 크기 구분 없이 kg당 가격만
  - market: 도매시장 코드. 비우면 전국
  - date: 기준일 (기본 오늘, 한국 시간). 시험용
  - 헤더: Authorization: Bearer <Firebase ID 토큰>  (REQUIRE_AUTH=true일 때)
GET /forecast?harvest_date=2027-06-22&size=4
  - 예상 수확일 앞뒤 3일(총 7일) 일별 예상 가격·범위·예측 오차
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
    token = authorization.split(" ", 1)[1]
    for app_ in _auth_apps():
        try:
            return auth.verify_id_token(token, app=app_)
        except Exception:
            continue
    raise HTTPException(401, "로그인 정보가 올바르지 않습니다")


_auth_cache: list = []


def _auth_apps():
    """앱 로그인(Firebase Auth) 프로젝트별로 토큰 검증용 Firebase 앱을 만듦.

    자료 저장(Firestore)은 sfm-work, 앱 로그인은 peachscope-a92c1처럼 프로젝트가 다를 수 있어
    FIREBASE_AUTH_PROJECTS(쉼표 구분)에 적힌 프로젝트의 토큰을 모두 받아 줌.
    """
    if _auth_cache:
        return _auth_cache
    import firebase_admin
    from peach_price.store import db
    db()  # 기본 Firebase 앱 초기화 (서비스 계정 자격 증명)
    default = firebase_admin.get_app()
    for pid in [p.strip() for p in config.AUTH_PROJECTS if p.strip()]:
        name = f"auth-{pid}"
        try:
            a = firebase_admin.get_app(name)
        except ValueError:
            a = firebase_admin.initialize_app(default.credential, {"projectId": pid}, name=name)
        _auth_cache.append(a)
    return _auth_cache


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/markets")
def markets():
    return [{"code": k, "name": v} for k, v in config.MARKETS.items()]


_forecaster = {"day": None, "obj": None}


def get_forecaster(store):
    """하루에 한 번만 다시 학습함 (자료가 하루 단위로만 바뀜)."""
    from peach_price.forecast import Forecaster
    today = datetime.now(KST).date()
    if _forecaster["day"] != today or _forecaster["obj"] is None:
        docs = []
        for y in range(today.year - 8, today.year + 1):
            docs.extend(store.get_year(y))
        _forecaster["obj"] = Forecaster(docs)
        _forecaster["day"] = today
    return _forecaster["obj"]


@app.get("/forecast")
def forecast(harvest_date: str = Query(..., description="예상 수확일 YYYY-MM-DD"),
             size: str = Query("4"),
             today_: str | None = Query(None, alias="today"),
             _user=Depends(verify_user), store=Depends(get_store)):
    """예상 수확일 앞뒤 3일(총 7일)의 일별 예상 가격, 범위, 예측 오차."""
    if size not in SIZES:
        raise HTTPException(400, f"size는 {sorted(SIZES, key=float)} 중 하나")
    try:
        harvest = date.fromisoformat(harvest_date)
        today = date.fromisoformat(today_) if today_ else datetime.now(KST).date()
    except ValueError:
        raise HTTPException(400, "날짜는 YYYY-MM-DD 형식")
    try:
        return get_forecaster(store).forecast(harvest, float(size), today)
    except ValueError as e:
        raise HTTPException(400, str(e))


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


@app.get('/history/week')
def historical_week(year: int = Query(..., ge=2020, le=2100),
                    month: int = Query(..., ge=6, le=7),
                    week: int = Query(..., ge=1, le=5),
                    size: str = Query('4'),
                    _user=Depends(verify_user), store=Depends(get_store)):
    """선택 연도·월·월내 주차(1~7일 단위)의 실제 경매 가격."""
    from history_prices import history_week
    if size not in SIZES:
        raise HTTPException(400, f'size는 {sorted(SIZES, key=float)} 중 하나')
    if year > datetime.now(KST).year:
        raise HTTPException(400, '미래 연도는 조회할 수 없습니다')
    try:
        return history_week(store, year, month, week, size)
    except ValueError as e:
        raise HTTPException(400, str(e))
