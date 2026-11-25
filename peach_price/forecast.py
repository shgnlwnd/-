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
from .grades import GRADES, compute_ratios

SEASON_START_MD = (6, 1)        # 학습에 쓰는 기간 (실제 출하기보다 넓게)
SEASON_END_MD = (7, 15)
MIN_KG_PER_DAY = 2000           # 하루 거래량이 이보다 적으면 값이 튀어 학습에서 뺌
LEVEL_WINDOW = 3                # 올해 수준 추정에 쓰는 최근 거래일 수
LEVEL_SHRINK = 10               # 올해 거래일이 적을 때 지난 해 수준 쪽으로 당기는 강도
STD_SIZES = {2.0, 2.5, 3.0, 4.0, 4.5, 5.0, 10.0}  # 거래가 꾸준한 상자 크기만 씀
HORIZON_BUCKETS = [(0, 3), (4, 10), (11, 30), (31, 365)]  # 며칠 앞 예측인지 구간
WEEKDAY_KO = "월화수목금토일"


MAX_DIST_SPAN_DAYS = 30         # 수확일 분포(p10~p90) 폭 상한
DIST_DRAWS = 4000               # 기대가격 범위를 구할 때 뽑는 횟수
_Z90 = 1.2815515655446004       # 정규분포 90% 분위수


def _date_weights(days: list[date], p10: date, p50: date, p90: date) -> np.ndarray:
    """날짜별 가중치. p10·p50·p90을 지나는 누적분포(구간별 직선)를 날짜 단위로 쪼갬.

    분포의 양 끝 10%는 p10 하루 전~p10, p90~p90 하루 뒤에 퍼뜨림. days 는 그 하루 전부터 하루 뒤까지 전부 줘야 합이 1.
    """
    xs = np.array([(p10 - timedelta(days=1)).toordinal(), p10.toordinal(), p50.toordinal(), p90.toordinal(),
                   (p90 + timedelta(days=1)).toordinal()], float) + np.arange(5) * 1e-6   # 날짜가 겹쳐도 증가하게
    ps = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    edges = np.array([d.toordinal() - 0.5 for d in days] + [days[-1].toordinal() + 0.5])
    return np.diff(np.interp(edges, xs, ps))


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
    parts = [f"{y}:{len(v)}:{max(o.d for o in v).isoformat()}:{round(sum(o.won_per_kg for o in v))}"
             for y, v in sorted(by_year.items())]
    return f"v2|{sorted(STD_SIZES)}|{MIN_KG_PER_DAY}|" + ",".join(parts)


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
        self.grade_ratios = compute_ratios(docs)
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

    def _grade_block(self, size: float, probs: dict[str, float], expected: float,
                     draws: np.ndarray) -> dict:
        """등급 확률 probs 가 주어졌을 때 등급별·등급 반영 기대 가격.

        등급별 가격 = 기본 예측 × 등급 비율 r. 범위는 기본 예측의 draws 에 r 과 그 오차(해를 하나씩 빼고 맞힌
        평균 절대 오차 ≈ 0.8σ 로 보고 로그정규 잡음)를 곱한 10~90%. 자료가 모자란 등급이 있으면 내지 않음.
        """
        table = self.grade_ratios.get(f"{size:g}") or {}
        missing = [g for g in probs if g not in table]
        if missing:
            return {"by_grade": None,
                    "grade_note": f"{size:g}kg 상자의 {', '.join(missing)} 등급 자료(2개 해 이상)가 부족해 등급별 가격을 내지 않음"}
        rng = np.random.default_rng(1)
        n = len(draws)
        sig = {g: table[g]["error_pct"] / 100 / 0.8 for g in probs}
        by = {}
        for g, p in probs.items():
            r = table[g]["ratio"]
            d = draws * r * np.exp(sig[g] * rng.standard_normal(n))
            lo, hi = (float(x) for x in np.quantile(d, [0.10, 0.90]))
            by[g] = {"prob": round(p, 3), "ratio": r, "expected_box_price": round(expected * r),
                     "range": [round(lo), round(hi)], "ratio_error_pct": table[g]["error_pct"],
                     "ratio_years": table[g]["years"]}
        names = list(probs)
        pv = np.array([probs[g] for g in names])
        pick = rng.choice(len(names), size=n, p=pv / pv.sum())
        rr = np.array([table[g]["ratio"] for g in names])[pick]
        ss = np.array([sig[g] for g in names])[pick]
        mixed = draws * rr * np.exp(ss * rng.standard_normal(n))
        lo, hi = (float(x) for x in np.quantile(mixed, [0.10, 0.90]))
        return {
            "by_grade": by,
            "grade_expected_box_price": round(expected * float(np.sum(pv * np.array([table[g]['ratio'] for g in names])))),
            "grade_expected_range": [round(lo), round(hi)],
            "grade_ratio_error_pct": round(float(np.sum(pv * np.array([table[g]['error_pct'] for g in names]))), 1),
            "grade_note": "등급별 가격 = 기본 예측 × 과거 해의 등급 가격비(해 전체 합산 평균). 특·상·중·하만 대상이고 "
                          "등외·5~8등·무등급은 비율 계산 대상에서 제외했습니다. 등급 확률은 입력값을 그대로 썼습니다.",
        }

    def forecast(self, harvest: date, size: float, today: date,
                 grade_probs: dict[str, float] | None = None) -> dict:
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
        result = {
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
        if grade_probs:
            hd = next(x for x in out_days if x["is_harvest_day"])
            e = self._err_for(max((harvest - today).days, 0))
            mu, sg = (e["q10"] + e["q90"]) / 2, max((e["q90"] - e["q10"]) / (2 * _Z90), 1e-6)
            draws = hd["box_price"] * np.exp(mu + sg * np.random.default_rng(0).standard_normal(DIST_DRAWS))
            result.update(self._grade_block(size, grade_probs, hd["box_price"], draws))
        return result

    def forecast_distribution(self, p10: date, p50: date, p90: date, size: float, today: date,
                              n_draws: int = DIST_DRAWS,
                              grade_probs: dict[str, float] | None = None) -> dict:
        """수확일이 p10~p90 분포(수확 모델의 days_range_90)일 때의 기대 가격과 범위.

        1. p10·p50·p90을 지나는 누적분포로 날짜별 가중치를 만듦 (양 끝 10%는 하루씩 퍼뜨림).
        2. 날짜별 가격은 forecast()와 같은 방식(올해 수준 × 시즌 곡선). 일요일 가중치는 다음 경매일로 넘김.
        3. 기대가격 = 가중평균. 범위 = 날짜를 가중치대로 뽑고 그 날짜의 가격 오차(백테스트 10~90%를 로그
           정규로 근사)를 얹어 4000번 뽑은 값의 10~90%. 수확일 불확실성과 가격 수준 오차가 함께 들어감.
        응답의 days 는 forecast()와 같은 필드에 weight 를 더한 것이라 기존 앱 파서가 그대로 읽는다.
        """
        size = float(size)
        if size not in self.model.sizes:
            raise ValueError(f"{size:g}kg 상자 자료가 없음. 가능한 크기: {[f'{s:g}' for s in self.model.sizes]}")
        if not (p10 <= p50 <= p90):
            raise ValueError("수확일은 harvest_p10 ≤ harvest_date ≤ harvest_p90 순서여야 함")
        if (p90 - p10).days > MAX_DIST_SPAN_DAYS:
            raise ValueError(f"수확일 구간이 {MAX_DIST_SPAN_DAYS}일을 넘음")
        all_days = [p10 - timedelta(days=1) + timedelta(days=i) for i in range((p90 - p10).days + 3)]
        w_all = _date_weights(all_days, p10, p50, p90)
        keep = [i for i, d in enumerate(all_days) if in_window(d)]
        if not keep or w_all[keep].sum() <= 0:
            raise ValueError("예상 수확일 구간이 출하기 범위(6월 1일~7월 15일) 밖이라 가격을 예측할 수 없음")
        clamped_mass = float(1.0 - w_all[keep].sum())
        days = [all_days[i] for i in keep]
        w = w_all[keep] / w_all[keep].sum()

        closed = [d.weekday() == 6 for d in days]
        for i, c in enumerate(closed):          # 일요일에 수확하면 다음 경매일에 팔린다고 봄
            if c and w[i] > 0:
                j = i + 1 if i + 1 < len(days) else i - 1
                if j >= 0:
                    w[j] += w[i]
                    w[i] = 0.0

        as_of = min(today, days[0] - timedelta(days=1))
        lv, level_src = self.model.year_level(days[0].year, size, as_of)
        if not math.isfinite(lv):
            raise ValueError("올해 가격 수준을 정할 자료가 없음")
        curve = self.model.curve(days, size)
        mids = lv * curve
        errs = [self._err_for(max((d - today).days, 0)) for d in days]
        mu = np.array([(e["q10"] + e["q90"]) / 2 for e in errs])
        sigma = np.maximum(np.array([(e["q90"] - e["q10"]) / (2 * _Z90) for e in errs]), 1e-6)

        rng = np.random.default_rng(0)
        idx = rng.choice(len(days), size=n_draws, p=w / w.sum())
        draws = mids[idx] * size * np.exp(mu[idx] + sigma[idx] * rng.standard_normal(n_draws))
        low, high = (float(x) for x in np.quantile(draws, [0.10, 0.90]))
        expected = float(np.sum(w * mids)) * size

        out_days = []
        for d, m, e, wi, cl in zip(days, mids, errs, w, closed):
            out_days.append({
                "date": d.isoformat(), "weekday": WEEKDAY_KO[d.weekday()], "is_harvest_day": d == p50,
                "market_closed": cl, "weight": round(float(wi), 3),
                "won_per_kg": round(float(m)), "box_price": round(float(m) * size),
                "box_low": round(float(m) * math.exp(e["q10"]) * size),
                "box_high": round(float(m) * math.exp(e["q90"]) * size),
                "expected_error_pct": e["mape_pct"],
            })
        best = max((x for x in out_days if not x["market_closed"]), key=lambda x: x["box_price"])
        result = {
            "basis": "같은 시기 전국 조생 백도계 복숭아 도매 시세 예측 (품종: 복숭아(일반)·백도·기타)",
            "harvest_date": p50.isoformat(),
            "size_kg": f"{size:g}",
            "level_source": level_src,
            "trained_years": self.years,
            "distribution": {"p10": p10.isoformat(), "p50": p50.isoformat(), "p90": p90.isoformat(),
                             "clamped": clamped_mass > 1e-9, "clamped_mass": round(clamped_mass, 3)},
            "expected_box_price": round(expected),
            "expected_range": [round(low), round(high)],
            "days": out_days,
            "best_day": best["date"],
            "error_by_horizon": [
                {"horizon": v["horizon"], "mape_pct": v["mape_pct"], "samples": v["n"]}
                for v in self.bt.values()
            ],
            "note": "수확일이 p10~p90 분포를 따른다고 보고 날짜별 가격을 가중평균한 기대가격입니다. 범위는 수확일 불확실성과 "
                    "가격 수준 오차(과거 해를 하나씩 빼고 맞혀 본 오차의 10~90%를 로그정규로 근사)를 함께 넣은 10~90% 구간이며, "
                    "수확일 분포 자체는 수확 모델의 가정값입니다. 일요일 가중치는 다음 경매일로 넘겼습니다.",
        }
        if grade_probs:
            result.update(self._grade_block(size, grade_probs, expected, draws))
        return result
