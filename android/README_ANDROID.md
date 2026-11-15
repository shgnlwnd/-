# 안드로이드 앱에 가격 연결하기 (Kotlin)

## 1. 파일 넣기

`PeachPriceApi.kt`를 앱의 `app/src/main/java/<패키지 경로>/price/` 폴더에 넣고, 맨 위 `package` 줄을 앱 패키지에 맞게 바꿈.

## 2. build.gradle(.kts) (app 모듈) 의존성 추가

```kotlin
dependencies {
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.8.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.6")
    // Firebase Auth는 이미 쓰고 있으면 추가할 필요 없음
}
```

추가 후 Android Studio 위쪽의 **Sync Now** 클릭.

## 3. 인터넷 권한 (AndroidManifest.xml)

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

이미 Firebase를 쓰고 있으면 들어 있을 가능성이 큼.

## 4. 시세 불러오기 (ViewModel 예시)

```kotlin
class PriceViewModel : ViewModel() {
    private val repo = PeachPriceRepository()
    val text = MutableLiveData<String>()

    // boxKg: 사용자가 고른 상자 크기, marketCode: null이면 전국
    fun load(boxKg: String = "4", marketCode: String? = null) {
        viewModelScope.launch {
            text.value = "시세 불러오는 중…"
            // 지금은 비수기라 date를 넣어 시즌 화면을 시험함. 출시 때는 date = null
            repo.getPrices(size = boxKg, market = marketCode, date = "2026-06-25")
                .onSuccess { v ->
                    text.value = buildString {
                        appendLine(v.headline())
                        v.pastYears.forEach { appendLine(it.line(v.sizeKg)) }
                        append("※ ${v.basis}")
                    }
                }
                .onFailure { text.value = "시세를 불러오지 못했습니다: ${it.message}" }
        }
    }
}
```

결과 예시 (date=2026-06-25, 4kg 상자):

```
전국 4kg 상자 ○○,○○○원 작년보다 +○.○%
작년(2025): 4kg 상자 ○○,○○○원
재작년(2024): 4kg 상자 ○○,○○○원
재재작년(2023): 4kg 상자 ○○,○○○원
※ 같은 시기 조생 백도계 복숭아 시세 (품종: 복숭아(일반)·백도·기타)
```

- 그래프는 `v.series["2026"]`, `v.series["2025"]`처럼 연도별 날짜·kg당 가격 목록으로 그리면 됨.
- 시장 고르기 목록은 `repo.getMarkets()`로 받음.

## 5. 가격 예측 불러오기 (ViewModel 예시)

```kotlin
fun loadForecast(harvestDate: String, boxKg: String = "4") {
    viewModelScope.launch {
        text.value = "예상 가격 계산 중… (처음엔 1분 정도 걸릴 수 있음)"
        // harvestDate: 앱이 판정한 예상 수확일 "yyyy-MM-dd"
        // 시험할 때는 today = "2026-06-20" 처럼 기준일을 넣을 수 있음. 출시 때는 null
        repo.getForecast(harvestDate = harvestDate, size = boxKg)
            .onSuccess { f ->
                text.value = buildString {
                    f.days.forEach { appendLine(it.line(f.sizeKg)) }
                    appendLine("가장 비쌀 것으로 보이는 날: ${f.bestDay}")
                    append("※ ${f.basis}")
                }
            }
            .onFailure { text.value = "예측을 불러오지 못했습니다: ${it.message}" }
    }
}
```

결과 예시:

```
2027-06-19(토) 4kg 상자 ○○,○○○원 (○○,○○○~○○,○○○원, 오차 약 ±16.3%)
…
2027-06-22(화) 4kg 상자 ○○,○○○원 (○○,○○○~○○,○○○원, 오차 약 ±16.3%) ← 예상 수확일
…
가장 비쌀 것으로 보이는 날: 2027-06-2○
```

- `box_low`~`box_high`를 막대나 음영으로, `box_price`를 선으로 그리면 범위가 잘 보임.
- `market_closed`가 true인 날(일요일)은 흐리게 표시하는 것을 권함.

## 6. 확인할 점

- 로그인하지 않은 상태로 부르면 "로그인이 필요합니다"가 나옴. Firebase(sfm-work) 로그인 뒤에 부를 것.
- 무료 서버가 잠에서 깨는 시간과 예측 모델 학습 때문에 첫 호출이 1분 넘게 걸릴 수 있음. 로딩 표시를 꼭 둘 것. 코드는 120초까지 기다림.
