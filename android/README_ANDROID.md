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

## 4. 화면에서 부르기 (ViewModel 예시)

```kotlin
class PriceViewModel : ViewModel() {
    private val repo = PeachPriceRepository()
    val text = MutableLiveData<String>()

    // boxKg: 사용자가 고른 상자 크기, marketCode: null이면 전국
    fun load(boxKg: String = "4", marketCode: String? = null) {
        viewModelScope.launch {
            text.value = "시세 불러오는 중…"
            // 지금은 비수기라 date를 넣어 시즌 화면을 시험함. 실제 배포 때는 date = null
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
재재작년(2023): 자료 없음
※ 같은 시기 조생 백도계 복숭아 시세 (품종: 복숭아(일반)·백도·기타)
```

- 2026-06-25를 기준일로 넣으면 "작년"이 2025년이 되고, 2023년은 받아 두지 않아 "자료 없음"으로 나옴. 2027년 시즌에는 2024~2026이 모두 채워짐.
- 그래프는 `v.series["2026"]`, `v.series["2025"]`처럼 연도별 날짜·kg당 가격 목록으로 그리면 됨.
- 시장 고르기 목록은 `repo.getMarkets()`로 받음.

## 5. 확인할 점

- 로그인하지 않은 상태로 부르면 "로그인이 필요합니다"가 나옴. Firebase 로그인 뒤에 부를 것.
- 첫 호출이 50초까지 걸릴 수 있음(무료 서버가 잠에서 깨는 시간). 로딩 표시를 꼭 둘 것.
