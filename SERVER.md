# 미홍 복숭아 가격 API

aT 「전국 공영도매시장 정산정보」 API에서 복숭아 경매 정산 자료를 받아 Firebase Firestore에 일자별로 저장하고, 앱에 금일 시세·작년 대비·지난 3년 가격·추세 그래프 자료를 제공함.

- 미홍은 표준코드에 품종이 없어 **같은 시기 조생 백도계 복숭아 시세**(품종명 복숭아(일반)·백도·기타)로 대신함.
- 기간은 매년 6월 10일~7월 10일, 범위는 전국 32개 공영도매시장.
- 가격은 상자 크기별 거래량 가중 평균(kg당)으로 계산하고 상자 가격으로 환산함.

## 구성

| 파일 | 역할 |
|---|---|
| `collect.py` | 수집 명령 (probe / backfill / day / daily) |
| `app.py` | 앱이 부르는 가격 API (FastAPI) |
| `peach_price/kat_client.py` | 정산정보 API 호출, 페이지 처리, 재시도 |
| `peach_price/normalize.py` | 대상 품종 거르기, kg 환산, 하루 집계 |
| `peach_price/prices.py` | 금일·작년 대비·지난 3년·그래프 계산 |
| `peach_price/store.py` | Firestore 저장·조회 |
| `render.yaml` | Render 웹 서비스(무료) |
| `.github/workflows/collect.yml` | 매일 수집 (GitHub Actions) |

## 1. 로컬에서 처음 확인하기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 인증키와 Firebase 키 경로 입력 (코드가 .env를 자동으로 읽음)
python collect.py probe   # 서울가락 2026-06-25 자료로 응답 형식 확인
```

`probe` 출력에서 확인할 것:

1. **건수가 0이 아닌지.** 0이면 인증키가 아직 등록 전일 수 있음(발급 후 1~2시간).
2. **복숭아 코드 형식.** 중분류 코드가 `04`가 아니면 `.env`의 `KAT_MCLSF_CD`를 그 값으로 바꿈. 대분류도 같음.
3. **단위 예시.** `('4', 'kg', '상자')`처럼 kg 단위인지 확인.
4. **API 제공 기간.** `python collect.py probe 2024-06-25`로 2024년 날짜도 나오는지 확인.

결과가 이상하면 출력 전체를 Claude에게 보여 주면 됨. **인증키는 보여 주지 말 것.**

## 2. 지난 시즌 자료 받기 (한 번만)

```bash
python collect.py backfill 2024 2026
```

- 날짜 하나에 시장 32곳을 따로 부르므로 3년치(93일)면 약 3,000번 호출함. 개발계정 하루 한도(10,000건) 안임.
- 중간에 끊기면 같은 명령을 다시 실행하면 됨. 다 받은 날짜는 건너뜀.

## 3. Firebase 준비

1. Firebase 콘솔 → 프로젝트 설정 → 서비스 계정 → **새 비공개 키 생성** → JSON 파일 받기.
2. Firestore 규칙에서 `peach_daily` 컬렉션은 앱이 직접 쓰지 못하게 막음. 서버(서비스 계정)는 규칙과 무관하게 씀.

```
match /peach_daily/{doc} {
  allow read, write: if false;
}
```

## 4. 배포 (완료됨)

- **가격 API:** Render 무료 웹 서비스 `peach-price-api` (`render.yaml`). 주소 `https://peach-price-api.onrender.com`. 환경변수 `FIREBASE_SERVICE_ACCOUNT`는 Render 대시보드에 입력되어 있음.
- **매일 수집:** Render 크론 잡은 유료라 GitHub Actions(`.github/workflows/collect.yml`)로 대신함. 매일 한국 시간 16:10에 `python collect.py daily` 실행, 출하기(6/10~7/10)가 아니면 바로 끝남. 저장소 Secrets에 `KAT_SERVICE_KEY`, `FIREBASE_SERVICE_ACCOUNT` 등록됨.
- Actions 탭에서 "복숭아 가격 수집"을 골라 **Run workflow**로 직접 돌릴 수도 있음.
- 무료 웹 서비스는 15분 동안 요청이 없으면 잠들어 첫 요청이 50초 정도 걸릴 수 있음.

## 5. 앱에서 부르기

```
GET https://<render 주소>/prices?size=4&market=380401
Authorization: Bearer <Firebase 로그인 ID 토큰>
```

- `size`: 상자 kg (2, 2.5, 3, 4, 4.5, 5, 10). 빈 값이면 kg당 가격만.
- `market`: 도매시장 코드(`/markets`로 목록 확인). 빈 값이면 전국.
- `date`: 시험용 기준일. 예: 비수기인 지금 `date=2026-06-25`로 시즌 화면 시험.

응답 예시 (요약):

```json
{
  "basis": "같은 시기 조생 백도계 복숭아 시세 (품종: 복숭아(일반)·백도·기타)",
  "in_season": true,
  "reference_date": "2027-06-25",
  "market_name": "전국",
  "current": {"date": "2027-06-25", "won_per_kg": 3750, "box_avg": 15000, "box_min": 8000, "box_max": 20000},
  "vs_last_year_pct": -6.2,
  "past_years": [
    {"label": "작년", "year": 2026, "won_per_kg": 4000, "box_avg": 16000},
    {"label": "재작년", "year": 2025, "won_per_kg": 3000, "box_avg": 12000},
    {"label": "재재작년", "year": 2024, "won_per_kg": 3500, "box_avg": 14000}
  ],
  "avg_3y_won_per_kg": 3500,
  "series": {"2027": [{"md": "06-25", "won_per_kg": 3750}], "2026": [...]}
}
```

- 금일 자료가 없으면(휴장일 등) 최대 3일 전 경매일 값을 쓰고 `current.date`에 그 날짜가 나옴.
- 비수기에는 `in_season: false`이고 직전 시즌 마지막 날(7/10) 기준 값이 나옴.
- 시장을 고르면 최저·최고가는 빠지고 kg당·상자 평균만 나옴.

## 6. 테스트

```bash
pip install pytest && python -m pytest -q tests
```

## 확인이 필요한 부분

- **단위총물량의 뜻:** 확인 완료. 총 kg임 (예: 3kg 상자 3개 → 9).
- **코드 형식:** 확인 완료. 대분류 `06`, 중분류 `04` (2026-11-14 probe).
- **API 과거 자료 기간:** 2024년 자료가 API로 나오는 것 확인함.
