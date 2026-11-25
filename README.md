# 복숭아 가격 기능 (시세 조회 + 가격 예측)

앱에서 사진으로 판정한 복숭아에 **도매시장 시세**와 **수확일 전후 예상 가격**을 함께 보여 주기 위한 서버와 안드로이드 연결 코드임.

> **박현에게: 할 일 요약**
> 1. [`android/PeachPriceApi.kt`](android/PeachPriceApi.kt) 파일 하나만 받아서 앱에 넣기
> 2. [`android/README_ANDROID.md`](android/README_ANDROID.md) 보고 build.gradle 의존성 추가
> 3. 로그인 후 `getPrices(...)`(시세)와 `getForecast(...)`(예측) 호출해서 화면에 표시
>
> 서버·자료·모델은 모두 준비 완료 상태라 앱 쪽만 연결하면 됨.

## 1. 현재 상태

| 항목 | 상태 |
|---|---|
| 가격 서버 | `https://peach-price-api.onrender.com` (Render 무료, 배포 완료) |
| 저장된 자료 | 2020~2026년 출하기(6/10~7/10) 전국 공영도매시장 경매 정산 자료, Firestore `peach_daily` |
| 시세 조회 API | `GET /prices` 배포 완료 |
| 가격 예측 API | `GET /forecast` 배포 완료 (Gradient Boosting 모델) |
| 인증 | Firebase 로그인 ID 토큰 필요 (sfm-work 프로젝트) |

```
공공데이터포털 API ─(수집)─▶ Firebase Firestore ─▶ Render 가격 서버(FastAPI) ─▶ 안드로이드 앱
```

## 2. 보여 주는 가격의 뜻

- 도매시장 표준코드에 '미홍' 품종이 없음. 그래서 **같은 시기 전국 조생 백도계 복숭아 시세**(품종명 복숭아(일반)·백도·기타)로 대신함.
- 앱 화면에는 "미홍 가격"이 아니라 응답의 `basis` 문구를 함께 표시해야 함.
- 기준은 판매자(농가)용 **도매 경락가**임. 소매가가 아님.
- 상자 크기별로 kg당 가격이 크게 달라서(2.5 kg 상자 약 6,000원/kg, 5 kg 상자 약 3,000원/kg) 상자 크기를 골라서 보여 줌.

## 3. API 1: 시세 조회 `GET /prices`

금일 시세, 작년 대비, 지난 3년(작년·재작년·재재작년) 같은 날 가격, 추세 그래프 자료.

| 파라미터 | 뜻 | 예 |
|---|---|---|
| `size` | 상자 kg (2, 2.5, 3, 4, 4.5, 5, 10) | `4` |
| `market` | 도매시장 코드, 비우면 전국 (`/markets`로 목록 확인) | `380401`(진주) |
| `date` | 기준일, 비우면 오늘 | `2026-06-25` |

| 응답 필드 | 화면에서 쓰는 곳 |
|---|---|
| `current` | 금일 시세 (휴장일이면 직전 경매일 값, `date`에 실제 날짜) |
| `vs_last_year_pct` | 작년 같은 날 대비 % (가장 크게 표시) |
| `past_years` | 작년·재작년·재재작년 같은 날 가격 |
| `avg_3y_won_per_kg` | 지난 3년 평균선 |
| `series` | 추세 그래프용 연도별 날짜·kg당 가격 |
| `in_season` | false면 비수기. 지난 시즌 마지막 날(7/10) 값이 나옴 |

안드로이드: `PeachPriceRepository().getPrices(size = "4", market = null, date = null)`

## 4. API 2: 가격 예측 `GET /forecast`

예상 수확일 앞뒤 3일(총 7일)의 일별 **예상 가격**, **가격 범위**, **예측 오차**.

| 파라미터 | 뜻 | 예 |
|---|---|---|
| `harvest_date` | 예상 수확일 (앱의 수확 시기 판정 결과) | `2027-06-22` |
| `size` | 상자 kg | `4` |
| `today` | 시험용 기준일, 비우면 오늘. 이 날짜 이후 자료는 쓰지 않음 | `2026-06-20` |

| 응답 필드 | 뜻 |
|---|---|
| `days[]` | 7일 각각의 `date`, `weekday`, `box_price`(예상가), `box_low`~`box_high`(범위), `expected_error_pct`(과거 평균 오차) |
| `days[].is_harvest_day` | 예상 수확일 표시 |
| `days[].market_closed` | 일요일(경매 거의 없음) 표시 |
| `best_day` | 7일 중 예상가가 가장 높은 날 (일요일 제외) |
| `level_source` | 올해 가격 수준을 무엇으로 잡았는지 |
| `error_by_horizon` | 며칠 앞을 예측하느냐에 따른 과거 평균 오차 |

안드로이드: `PeachPriceRepository().getForecast(harvestDate = "2027-06-22", size = "4")`

### 4.1 선택 파라미터: 수확일 분포와 등급 확률 (2026-11-25 배포)

모두 선택이며 없으면 위의 기존 동작과 응답이 그대로임.

| 파라미터 | 설명 |
|---|---|
| `harvest_p10`, `harvest_p90` | 수확일 분포(`harvest_date`가 p50). **둘 다 주거나 둘 다 생략**. `p10 ≤ harvest_date ≤ p90`, 폭 30일 이내, 출하기(6/1~7/15) 밖이면 400 |
| `grade_probs` | 등급 확률 `특:0.5,상:0.3,중:0.2` (특·상·중·하만, 합 1±2%). 형식 오류·합 오류는 400 |

응답 추가 필드: 분포 사용 시 `distribution`, `expected_box_price`, `expected_range`, `days[].weight` / 등급 사용 시 `by_grade`, `grade_expected_box_price`, `grade_expected_range`, `grade_ratio_error_pct`, `grade_note`. 해당 상자 kg의 등급 자료가 2개 해 미만이면 `by_grade`는 null이고 `grade_note`에 사유가 있음. 등급 비율 계산은 [`peach_price/grades.py`](peach_price/grades.py).

## 5. 예측 모델 설명

**Gradient Boosting 회귀**(scikit-learn `GradientBoostingRegressor`) + 올해 가격 수준 보정. 코드: [`peach_price/forecast.py`](peach_price/forecast.py)

1. **시즌 곡선 학습:** 입력은 출하기 며칠째·요일·상자 크기, 출력은 그해 평균 대비 상대 가격(로그). 출하 초반 고가 → 물량 증가로 하락하는 흐름, 요일·상자 크기별 차이를 배움. 해마다 가격 수준이 달라 절대 가격 대신 상대 가격을 학습함.
2. **올해 수준 추정:** 시즌 전에는 지난 3년 평균 수준. 시즌 중에는 올해 최근 3거래일 시세를 섞고, 거래일이 쌓일수록 비중을 늘림(n일이면 n/(n+10)).
3. **예상 가격** = 올해 수준 × 시즌 곡선.
4. **오차·범위:** 한 해씩 빼고 학습해 그해를 맞혀 보는 백테스트(leave-one-year-out)로 실제 오차를 잼. 범위는 그 오차의 10~90% 구간.

검증 결과 (2020~2026년 실제 경매 자료):

| 예측 시점 | 평균 오차 |
|---|---|
| 1~3일 앞 | 약 12% |
| 4~10일 앞 | 약 16% |
| 11~30일 앞 | 약 16% |

- '작년 같은 날 가격', '어제 가격 그대로' 같은 단순 방식보다 대부분 오차가 작았음(하루 앞만 '어제 가격 그대로'와 비슷).
- 2025년까지 자료로만 학습해 2026년 6월 22~27일 4 kg 상자 가격을 맞혀 본 결과 실제와 2~8% 차이.
- 자료가 한 해 한 달 × 7년뿐이라 큰 모델은 과적합 위험이 커서 작고 검증 가능한 구조로 만듦.
- 서버는 하루 한 번(그날 첫 요청 때) Firestore 자료로 다시 학습함. 그래서 그날 첫 호출은 수십 초 걸릴 수 있음.

## 6. 시연 방법 (지금은 비수기)

기본값으로 부르면 지난 시즌 마지막 값이 나오므로, 날짜를 지정해 시즌 중처럼 보여 줌.

| 보여 줄 것 | 호출 |
|---|---|
| 시즌 중 시세 화면 | `getPrices(size = "4", date = "2026-06-25")` |
| 시즌 중 예측 화면 | `getForecast(harvestDate = "2026-06-24", size = "4", today = "2026-06-20")` |
| 시즌 전 예측 화면 | `getForecast(harvestDate = "2027-06-22", size = "4")` |

- 서버 모델은 2026년 자료까지 넣고 학습하므로, 2026년 날짜로 시연하면 실제보다 잘 맞아 보임. 발표에서 정확도를 말할 때는 5절의 백테스트 수치를 쓸 것.
- **출시 때는 `date`, `today`를 null로 바꿀 것.**

## 7. 꼭 지킬 것

- **Firebase 프로젝트:** 앱 로그인은 반드시 **sfm-work** 프로젝트여야 함. 다른 프로젝트 토큰은 401로 거절됨.
- **첫 호출 지연:** 무료 서버라 15분 동안 요청이 없으면 잠듦. 깨어나는 첫 호출이 최대 50초, 예측은 학습까지 더해 1분 넘게 걸릴 수 있음. 로딩 표시 필수. 코드는 120초까지 기다리게 되어 있음.
- **비밀값 불필요:** 공공데이터포털 인증키와 Firebase 서비스 계정 키는 앱에 넣지 않음.

## 8. 저장소 구성

| 경로 | 내용 |
|---|---|
| `android/` | 앱에 넣을 Kotlin 코드와 사용법 (**박현이 받을 것**) |
| `app.py` | 가격 API 서버 (FastAPI) |
| `peach_price/forecast.py` | 가격 예측 모델 (수확일 분포 `forecast_distribution`, 등급 반영 포함) |
| `peach_price/grades.py` | 등급 가격비(특·상·중·하) 계산, `grade_probs` 해석 |
| `peach_price/prices.py` | 시세 조회 계산 |
| `peach_price/normalize.py`, `kat_client.py`, `store.py` | 자료 수집·정리·저장 |
| `collect.py` | 자료 수집 명령 |
| `.github/workflows/collect.yml` | 출하기 매일 자동 수집 (GitHub Actions) |
| `tests/` | 테스트 |
| [`SERVER.md`](SERVER.md) | 서버 설치·운영 설명 |

- **자동 수집 다시 켜기:** 공개 저장소는 60일 동안 변경이 없으면 GitHub가 예약 실행을 끔. 앱을 다음 출하기에도 쓸 경우, 6월 초에 Actions 탭 → "복숭아 가격 수집" → **Enable workflow**를 눌러 둘 것.
