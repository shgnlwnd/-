"""수확 예정일 앞뒤 3일(총 7일)의 일별 예상 가격.

모델 구성 (자료가 출하기 한 달 × 몇 해뿐이라 단순하고 검증 가능한 구조로 둠)
1. 시즌 곡선: 해마다 가격 수준이 달라서, 각 해·상자 크기의 출하기 평균으로 나눈 '상대 가격'을 만듦.
   이 상대 가격을 (출하기 며칠째, 요일, 상자 크기)로 설명하는 Gradient Boosting 회귀 모델을 학습함.
   → 출하기 안에서 날짜에 따라 가격이 어떻게 오르내리는지(초반 고가 → 물량 증가로 하락)를 배움.
2. 올해 수준: 시즌 전에는 지난 3년 평균 수준을 씀. 시즌 중에는 최근 거래일의 실제값 ÷ 곡선 예측값으로
   잡은 올해 수준을 지난 3년 평균과 섞고, 올해 거래일이 쌓일수록 올해 쪽 비중을 늘림.
3. 예상 가격 = 올해 수준 × 시즌 곡선.
4. 예측 오차와 범위: 해마다 하나씩 빼고 학습해 그해를 맞혀 보는 백테스트(leave-one-year-out)를
   예측 시점(며칠 앞을 맞히는지)별로 돌려 실제 오차를 잼. 범위는 그 오차의 10~90% 분위수.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

from . import config

SEASON_START_MD = (6, 1)        # 학습에 쓰는 기간 (실제 출하기보다 넓게)
SEASON_END_MD = (7, 15)
MIN_KG_PER_DAY = 2000           # 하루 거래량이 이보다 적으면 값이 튀어 학습에서 뺌
LEVEL_WINDOW = 3                # 올해 수준 추정에 쓰는 최근 거래일 수
LEVEL_SHRINK = 10               # 올해 거래일이 적을 때 지난 해 수준 쪽으로 당기는 강도
STD_SIZES = {2.0, 2.5, 3.0, 4.0, 4.5, 5.0, 10.0}  # 거래가 꾸준한 상자 크기만 씀
HORIZON_BUCKETS = [(0, 3), (4, 10), (11, 30), (31, 365)]  # 며칠 앞 예측인지 구간
WEEKDAY_KO = "월화수목금토일"


def season_day(d: date) -> int:
    return (d - date(d.year, *SEASON_START_MD)).days


def in_window(d: date) -> bool:
    return date(d.year, *SEASON_START_MD) <= d <= date(d.year, *SEASON_END_MD)


@dataclass
class Obs:
    d: date
    size: float
    won_per_kg: float
    kg: float


def rows_from_docs(docs: list[dict]) -> list[Obs]:
    """Firestore 일자 문서 → 상자 크기별 관측치."""
    out = []
    for doc in docs:
        d = date.fromisoformat(doc["date"])
        if not in_window(d):
            continue
        for k, s in (doc.get("sizes") or {}).items():
            if float(k) in STD_SIZES and s.get("won_per_kg") and (s.get("total_kg") or 0) >= MIN_KG_PER_DAY:
                out.append(Obs(d, float(k), float(s["won_per_kg"]), float(s["total_kg"])))
    return out


def _features(sday: np.ndarray, wday: np.ndarray, size: np.ndarray) -> np.ndarray:
    return np.column_stack([sday, wday, size])


class SeasonModel:
    """시즌 곡선 + 올해 수준."""

    def __init__(self, obs: list[Obs]):
        self.obs = obs
        self.sizes = sorted({o.size for o in obs})
        # 해·크기별 수준 = 거래량 가중 평균 kg당 가격
        self.level: dict[tuple[int, float], float] = {}
        acc: dict[tuple[int, float], list[float]] = {}
        for o in obs:
            a = acc.setdefault((o.d.year, o.size), [0.0, 0.0])
            a[0] += o.won_per_kg * o.kg
            a[1] += o.kg
        for k, (w, kg) in acc.items():
            self.level[k] = w / kg
        X, y, w = [], [], []
        for o in obs:
            lv = self.level[(o.d.year, o.size)]
            X.append((season_day(o.d), o.d.weekday(), o.size))
            y.append(math.log(o.won_per_kg / lv))
            w.append(math.sqrt(o.kg))
        X = np.array(X, dtype=float)
        self.gbm = GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
            min_samples_leaf=8, random_state=0)
        self.gbm.fit(_features(X[:, 0], X[:, 1], X[:, 2]), np.array(y), sample_weight=np.array(w))

    def curve(self, days: list[date], size: float) -> np.ndarray:
        X = _features(np.array([season_day(d) for d in days], float),
                      np.array([d.weekday() for d in days], float),
                      np.full(len(days), size))
        return np.exp(self.gbm.predict(X))

    def year_level(self, year: int, size: float, as_of: date | None) -> tuple[float, str]:
        """올해(year) 수준 추정. as_of 이전 자료만 씀.

        올해 최근 거래일 시세로 잡은 수준과 지난 3년 평균 수준을 섞음.
        올해 거래일이 쌓일수록 올해 쪽 비중이 커짐 (거래일 n일이면 n/(n+10)).
        2020~2026년 백테스트에서 이 방식이 '직전 가격 그대로', '작년 같은 날' 방식보다 오차가 작았음.
        """
        cur = sorted([o for o in self.obs if o.d.year == year and o.size == size
                      and (as_of is None or o.d <= as_of)], key=lambda o: o.d)
        past = sorted((y, v) for (y, s), v in self.level.items() if s == size and y < year)
        prior = float(np.mean([v for _, v in past[-3:]])) if past else None
        if not cur:
            if prior is None:
                return float("nan"), "자료 없음"
            return prior, f"지난 {min(3, len(past))}년 평균 수준 (올해 거래 전)"
        rec = cur[-LEVEL_WINDOW:]
        ratios = np.array([o.won_per_kg for o in rec]) / self.curve([o.d for o in rec], size)
        recent = float(np.exp(np.average(np.log(ratios), weights=[o.kg for o in rec])))
        if prior is None:
            return recent, f"올해 최근 {len(rec)}거래일 시세"
        w = len(cur) / (len(cur) + LEVEL_SHRINK)
        lv = math.exp(w * math.log(recent) + (1 - w) * math.log(prior))
        return lv, f"올해 시세 {round(w * 100)}% + 지난 해 평균 {round((1 - w) * 100)}%"


def _bucket(h: int) -> int:
    for i, (a, b) in enumerate(HORIZON_BUCKETS):
        if a <= h <= b:
            return i
    return len(HORIZON_BUCKETS) - 1


def backtest(obs: list[Obs]) -> dict:
    """해마다 하나씩 빼고 학습해 그해를 예측. 예측 시점(며칠 앞)별 오차 분포를 냄."""
    years = sorted({o.d.year for o in obs})
    errs: dict[int, list[float]] = {i: [] for i in range(len(HORIZON_BUCKETS))}
    for ty in years:
        train = [o for o in obs if o.d.year != ty]
        if len({o.d.year for o in train}) < 2:
            continue
        test = [o for o in obs if o.d.year == ty]
        m = SeasonModel(train)
        for size in {o.size for o in test}:
            ts = sorted([o for o in test if o.size == size], key=lambda o: o.d)
            if len(ts) < 6:
                continue
            m.obs = train + test  # 수준 추정에 '그 시점까지의' 테스트 연도 자료를 허용
            for o in ts[::2]:
                for h in (1, 3, 7, 14, 30):
                    as_of = o.d - timedelta(days=h)
                    lv, _ = m.year_level(ty, size, as_of)
                    if not math.isfinite(lv):
                        continue
                    pred = lv * m.curve([o.d], size)[0]
                    errs[_bucket(h)].append(math.log(o.won_per_kg / pred))
            m.obs = train
    out = {}
    for i, (a, b) in enumerate(HORIZON_BUCKETS):
        e = np.array(errs[i])
        if len(e) < 10:
            continue
        out[i] = {
            "horizon": f"{a}~{b}일 전 예측",
            "n": int(len(e)),
            "mape_pct": round(float(np.mean(np.abs(np.expm1(e)))) * 100, 1),
            "q10": float(np.quantile(e, 0.10)),
            "q90": float(np.quantile(e, 0.90)),
        }
    return out


def data_key(obs: list[Obs]) -> str:
    """학습 자료가 바뀌었는지 판단하는 열쇠. 해마다 관측치 수와 마지막 날짜로 만듦.

    지난 해 자료는 바뀌지 않으므로 비수기에는 이 값이 그대로이고, 출하기에는 매일 바뀜.
    """
    by_year: dict[int, list[Obs]] = {}
    for o in obs:
        by_year.setdefault(o.d.year, []).append(o)
    parts = [f"{y}:{len(v)}:{max(o.d for o in v).isoformat()}" for y, v in sorted(by_year.items())]
    return f"v1|{sorted(STD_SIZES)}|{MIN_KG_PER_DAY}|" + ",".join(parts)


def bt_to_doc(bt: dict, key: str) -> dict:
    """Firestore에 넣을 수 있게 (숫자 열쇠 → 목록) 바꿈."""
    return {"key": key, "items": [{"bucket": i, **v} for i, v in bt.items()]}


def bt_from_doc(doc: dict | None, key: str) -> dict | None:
    if not doc or doc.get("key") != key:
        return None
    return {int(x["bucket"]): {k: v for k, v in x.items() if k != "bucket"} for x in doc["items"]}


class Forecaster:
    def __init__(self, docs: list[dict], bt_cache: dict | None = None):
        """bt_cache: 저장해 둔 검증 결과 문서. 자료가 그대로면 무거운 백테스트를 다시 돌리지 않음."""
        self.obs = rows_from_docs(docs)
        self.years = sorted({o.d.year for o in self.obs})
        if len(self.years) < 2:
            raise ValueError("학습에 쓸 출하기 자료가 2년 이상 필요함")
        self.model = SeasonModel(self.obs)
        self.data_key = data_key(self.obs)
        cached = bt_from_doc(bt_cache, self.data_key)
        self.bt_reused = cached is not None
        self.bt = cached if cached is not None else backtest(self.obs)

    def bt_doc(self) -> dict:
        return bt_to_doc(self.bt, self.data_key)

    def _err_for(self, h: int) -> dict:
        b = _bucket(h)
        while b not in self.bt and b > 0:
            b -= 1
        return self.bt.get(b) or {"horizon": "", "n": 0, "mape_pct": None, "q10": -0.2, "q90": 0.2}

    def forecast(self, harvest: date, size: float, today: date) -> dict:
        size = float(size)
        if size not in self.model.sizes:
            raise ValueError(f"{size:g}kg 상자 자료가 없음. 가능한 크기: {[f'{s:g}' for s in self.model.sizes]}")
        days = [harvest + timedelta(days=i) for i in range(-3, 4)]
        if not (in_window(days[0]) and in_window(days[-1])):
            raise ValueError("예상 수확일이 출하기 범위(6월 4일~7월 12일) 밖이라 가격을 예측할 수 없음")
        as_of = min(today, days[0] - timedelta(days=1))
        lv, level_src = self.model.year_level(harvest.year, size, as_of)
        curve = self.model.curve(days, size)
        out_days = []
        for d, c in zip(days, curve):
            h = max((d - today).days, 0)
            e = self._err_for(h)
            mid = lv * c
            closed = d.weekday() == 6
            out_days.append({
                "date": d.isoformat(),
                "weekday": WEEKDAY_KO[d.weekday()],
                "is_harvest_day": d == harvest,
                "market_closed": closed,   # 일요일은 대부분 경매가 없음
                "won_per_kg": round(mid),
                "box_price": round(mid * size),
                "box_low": round(mid * math.exp(e["q10"]) * size),
                "box_high": round(mid * math.exp(e["q90"]) * size),
                "expected_error_pct": e["mape_pct"],
            })
        best = max((x for x in out_days if not x["market_closed"]), key=lambda x: x["box_price"])
        return {
            "basis": "같은 시기 전국 조생 백도계 복숭아 도매 시세 예측 (품종: 복숭아(일반)·백도·기타)",
            "harvest_date": harvest.isoformat(),
            "size_kg": f"{size:g}",
            "level_source": level_src,
            "trained_years": self.years,
            "days": out_days,
            "best_day": best["date"],
            "error_by_horizon": [
                {"horizon": v["horizon"], "mape_pct": v["mape_pct"], "samples": v["n"]}
                for v in self.bt.values()
            ],
            "note": "예측 범위는 과거 해를 하나씩 빼고 맞혀 본 오차의 10~90% 구간임. "
                    "일요일은 경매가 거의 없어 참고값임.",
        }
