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
