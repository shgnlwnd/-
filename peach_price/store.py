"""Firestore 저장/조회. 문서 하나 = 하루치 집계 (peach_daily/{YYYY-MM-DD})."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import config

_db = None


def _init_firebase():
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")
    if raw:
        # Render 환경변수에 서비스 계정 JSON 전체를 한 줄로 넣는 방식
        cred = credentials.Certificate(json.loads(raw))
    else:
        # 로컬에서는 GOOGLE_APPLICATION_CREDENTIALS 파일 경로 사용
        cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)


def db():
    global _db
    if _db is None:
        _init_firebase()
        from firebase_admin import firestore
        # 이름을 붙인 Firestore 데이터베이스를 쓰면 FIRESTORE_DATABASE에 그 이름을 넣음
        name = os.environ.get("FIRESTORE_DATABASE") or None
        _db = firestore.client(database_id=name) if name else firestore.client()
    return _db


class FirestoreStore:
    def __init__(self, collection: str = config.FIRESTORE_COLLECTION):
        self.col = db().collection(collection)

    def save_day(self, ymd: str, agg: dict, markets_done: list[str]) -> None:
        self.col.document(ymd).set({
            "date": ymd,
            "year": int(ymd[:4]),
            "md": ymd[5:],
            **agg,
            "markets_done": sorted(markets_done),
            "updated_at": datetime.now(timezone.utc),
        })

    def get_day(self, ymd: str) -> dict | None:
        snap = self.col.document(ymd).get()
        return snap.to_dict() if snap.exists else None

    def get_year(self, year: int) -> list[dict]:
        docs = self.col.where("year", "==", year).stream()
        return [d.to_dict() for d in docs]

    # 계산 결과 보관용 (예: 예측 모델 검증 결과). 시세 자료와 섞이지 않게 다른 컬렉션에 둠.
    def get_meta(self, key: str) -> dict | None:
        snap = db().collection(config.META_COLLECTION).document(key).get()
        return snap.to_dict() if snap.exists else None

    def set_meta(self, key: str, value: dict) -> None:
        db().collection(config.META_COLLECTION).document(key).set(value)


class CachedStore:
    """Firestore 조회 결과를 메모리에 잠시 보관함.

    시세 자료는 하루 한 번(GitHub Actions 수집)만 바뀌므로, 요청마다 Firestore를
    다시 읽지 않고 ttl초 동안 같은 결과를 씀. 저장(save_day)은 그대로 넘기고 해당 해 보관분을 비움.
    """

    def __init__(self, store, ttl: float = 3600):
        import threading
        self.store = store
        self.ttl = ttl
        self._years: dict[int, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def get_year(self, year: int) -> list[dict]:
        import time
        hit = self._years.get(year)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        docs = self.store.get_year(year)
        with self._lock:
            self._years[year] = (time.time(), docs)
        return docs

    def get_day(self, ymd: str) -> dict | None:
        for d in self.get_year(int(ymd[:4])):
            if d.get("date") == ymd:
                return d
        return None

    def save_day(self, ymd, agg, markets_done):
        self.store.save_day(ymd, agg, markets_done)
        self._years.pop(int(ymd[:4]), None)

    def get_meta(self, key):
        return self.store.get_meta(key)

    def set_meta(self, key, value):
        self.store.set_meta(key, value)


class MemoryStore:
    """테스트와 로컬 확인용."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    def save_day(self, ymd, agg, markets_done):
        self.docs[ymd] = {"date": ymd, "year": int(ymd[:4]), "md": ymd[5:], **agg,
                          "markets_done": sorted(markets_done)}

    def get_day(self, ymd):
        return self.docs.get(ymd)

    def get_year(self, year):
        return [d for d in self.docs.values() if d["year"] == year]

    def get_meta(self, key):
        return getattr(self, "meta", {}).get(key)

    def set_meta(self, key, value):
        if not hasattr(self, "meta"):
            self.meta = {}
        self.meta[key] = value
