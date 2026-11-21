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
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from peach_price import config
from peach_price.prices import build_view

KST = timezone(timedelta(hours=9))
log = logging.getLogger("uvicorn.error")
SIZES = {"2", "2.5", "3", "4", "4.5", "5", "10"}

app = FastAPI(title="미홍 복숭아 가격 API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["Authorization"])

_store = None


def get_store():
    """Firestore 조회 결과를 메모리에 1시간 보관하는 저장소 (자료는 하루 한 번만 바뀜)."""
    global _store
    if _store is None:
        from peach_price.store import CachedStore, FirestoreStore
        _store = CachedStore(FirestoreStore(), ttl=config.CACHE_TTL_SEC)
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
    return {"ok": True, "model_ready": _forecaster["obj"] is not None}


@app.get("/markets")
def markets():
    return [{"code": k, "name": v} for k, v in config.MARKETS.items()]


_forecaster = {"built_at": 0.0, "obj": None, "refreshing": False}
_build_lock = threading.Lock()
BT_META_KEY = "forecast_backtest"


def _build_forecaster(store):
    """예측 모델을 새로 만들어 교체함. 검증 결과는 자료가 그대로면 저장분을 다시 씀."""
    from peach_price.forecast import Forecaster
    with _build_lock:
        today = datetime.now(KST).date()
        docs = []
        for y in range(today.year - 8, today.year + 1):
            docs.extend(store.get_year(y))
        try:
            cached = store.get_meta(BT_META_KEY)
        except Exception as e:  # 저장분을 못 읽어도 예측은 되게 함
            log.warning("검증 결과 저장분 읽기 실패: %s", e)
            cached = None
        t0 = time.time()
        f = Forecaster(docs, bt_cache=cached)
        if not f.bt_reused:
            try:
                store.set_meta(BT_META_KEY, f.bt_doc())
            except Exception as e:
                log.warning("검증 결과 저장 실패: %s", e)
        _forecaster["obj"] = f
        _forecaster["built_at"] = time.time()
        log.info("예측 모델 준비 %.1f초 (검증 결과 %s)", time.time() - t0,
                 "재사용" if f.bt_reused else "새로 계산")
        return f


def _refresh_in_background(store):
    if _forecaster["refreshing"]:
        return
    _forecaster["refreshing"] = True

    def run():
        try:
            _build_forecaster(store)
        except Exception:
            log.exception("예측 모델 갱신 실패 (기존 모델 계속 사용)")
        finally:
            _forecaster["refreshing"] = False
    threading.Thread(target=run, daemon=True).start()


def get_forecaster(store):
    """준비된 모델을 바로 돌려줌. 오래됐으면 지금 요청은 기존 모델로 답하고 뒤에서 새로 만듦."""
    f = _forecaster["obj"]
    if f is None:
        return _build_forecaster(store)  # 서버 켜질 때 미리 만드는 중이면 끝날 때까지 기다림
    if time.time() - _forecaster["built_at"] > config.MODEL_REFRESH_HOURS * 3600:
        _refresh_in_background(store)
    return f


@app.on_event("startup")
def _warm_up():
    """서버가 켜지자마자 자료를 읽고 예측 모델을 만들어 둠. 첫 사용자가 기다리지 않게 함."""
    if os.environ.get("SKIP_WARMUP") == "1":
        return

    def run():
        try:
            _forecaster["refreshing"] = True
            _build_forecaster(get_store())
        except Exception:
            log.exception("시작 시 예측 모델 준비 실패 (첫 요청 때 다시 시도)")
        finally:
            _forecaster["refreshing"] = False
    threading.Thread(target=run, daemon=True).start()


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
