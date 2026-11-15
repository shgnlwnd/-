// 미홍 복숭아 가격 API 연결 (Render 서버: https://peach-price-api.onrender.com)
// 패키지 이름은 앱에 맞게 바꿀 것.
package com.example.peach.price

import com.google.firebase.auth.FirebaseAuth
import com.google.gson.annotations.SerializedName
import kotlinx.coroutines.tasks.await
import okhttp3.OkHttpClient
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Query
import java.text.NumberFormat
import java.util.Locale
import java.util.concurrent.TimeUnit

// ---------- 응답 모델 ----------

data class PriceView(
    val basis: String,
    @SerializedName("in_season") val inSeason: Boolean,
    @SerializedName("reference_date") val referenceDate: String,
    @SerializedName("size_kg") val sizeKg: String?,
    @SerializedName("market_name") val marketName: String,
    val current: PricePoint?,                                   // 금일(또는 직전 경매일) 시세
    @SerializedName("vs_last_year_pct") val vsLastYearPct: Double?,
    @SerializedName("past_years") val pastYears: List<PastYear>, // 작년·재작년·재재작년
    @SerializedName("avg_3y_won_per_kg") val avg3yWonPerKg: Int?,
    @SerializedName("vs_avg_3y_pct") val vsAvg3yPct: Double?,
    val series: Map<String, List<SeriesPoint>>,                  // 그래프: 연도 -> 날짜별 값
)

data class PricePoint(
    val date: String?,
    @SerializedName("won_per_kg") val wonPerKg: Int?,
    @SerializedName("box_avg") val boxAvg: Int?,
    @SerializedName("box_min") val boxMin: Double?,
    @SerializedName("box_max") val boxMax: Double?,
)

data class PastYear(
    val label: String,      // "작년", "재작년", "재재작년"
    val year: Int,
    val date: String?,
    @SerializedName("won_per_kg") val wonPerKg: Int?,
    @SerializedName("box_avg") val boxAvg: Int?,
)

data class SeriesPoint(
    val md: String,         // "06-25"
    @SerializedName("won_per_kg") val wonPerKg: Int,
)

data class Market(val code: String, val name: String)

// 예상 수확일 앞뒤 3일(총 7일) 가격 예측
data class Forecast(
    val basis: String,
    @SerializedName("harvest_date") val harvestDate: String,
    @SerializedName("size_kg") val sizeKg: String,
    @SerializedName("level_source") val levelSource: String,     // 올해 수준을 무엇으로 잡았는지
    val days: List<ForecastDay>,
    @SerializedName("best_day") val bestDay: String,             // 7일 중 예상가가 가장 높은 날
    @SerializedName("error_by_horizon") val errorByHorizon: List<HorizonError>,
    val note: String,
)

data class ForecastDay(
    val date: String,
    val weekday: String,
    @SerializedName("is_harvest_day") val isHarvestDay: Boolean,
    @SerializedName("market_closed") val marketClosed: Boolean,  // 일요일(경매 거의 없음)
    @SerializedName("won_per_kg") val wonPerKg: Int,
    @SerializedName("box_price") val boxPrice: Int,              // 예상 상자 가격
    @SerializedName("box_low") val boxLow: Int,                  // 예상 범위 하한
    @SerializedName("box_high") val boxHigh: Int,                // 예상 범위 상한
    @SerializedName("expected_error_pct") val expectedErrorPct: Double?, // 과거 평균 오차(%)
)

data class HorizonError(
    val horizon: String,
    @SerializedName("mape_pct") val mapePct: Double,
    val samples: Int,
)

// ---------- Retrofit 인터페이스 ----------

interface PeachPriceService {
    @GET("prices")
    suspend fun prices(
        @Header("Authorization") bearer: String,
        @Query("size") size: String? = "4",      // 상자 kg: 2, 2.5, 3, 4, 4.5, 5, 10
        @Query("market") market: String? = null, // 시장 코드, null이면 전국
        @Query("date") date: String? = null,     // 시험용 기준일 (예: "2026-06-25")
    ): PriceView

    @GET("markets")
    suspend fun markets(): List<Market>

    @GET("forecast")
    suspend fun forecast(
        @Header("Authorization") bearer: String,
        @Query("harvest_date") harvestDate: String,  // 예상 수확일 "2027-06-22"
        @Query("size") size: String = "4",
        @Query("today") today: String? = null,       // 시험용 기준일
    ): Forecast
}

// ---------- 앱에서 쓰는 저장소 ----------

class PeachPriceRepository(baseUrl: String = "https://peach-price-api.onrender.com/") {

    // 무료 서버는 잠들어 있다가 깨어날 때 50초 정도 걸릴 수 있어 시간 제한을 넉넉히 둠
    private val client = OkHttpClient.Builder()
        .connectTimeout(70, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    private val service: PeachPriceService = Retrofit.Builder()
        .baseUrl(baseUrl)
        .client(client)
        .addConverterFactory(GsonConverterFactory.create())
        .build()
        .create(PeachPriceService::class.java)

    /** 로그인한 사용자의 Firebase ID 토큰을 붙여 가격을 가져옴. */
    suspend fun getPrices(size: String? = "4", market: String? = null, date: String? = null): Result<PriceView> =
        runCatching {
            val user = FirebaseAuth.getInstance().currentUser
                ?: error("로그인이 필요합니다")
            val token = user.getIdToken(false).await().token
                ?: error("로그인 토큰을 가져오지 못했습니다")
            try {
                service.prices("Bearer $token", size, market, date)
            } catch (e: HttpException) {
                if (e.code() == 401) {
                    // 토큰이 만료됐으면 새로 받아 한 번 더 시도
                    val fresh = user.getIdToken(true).await().token ?: throw e
                    service.prices("Bearer $fresh", size, market, date)
                } else throw e
            }
        }

    suspend fun getMarkets(): Result<List<Market>> = runCatching { service.markets() }

    /** 예상 수확일 앞뒤 3일의 일별 예상 가격. 첫 호출은 모델 학습 때문에 수십 초 걸릴 수 있음. */
    suspend fun getForecast(harvestDate: String, size: String = "4", today: String? = null): Result<Forecast> =
        runCatching {
            val user = FirebaseAuth.getInstance().currentUser ?: error("로그인이 필요합니다")
            val token = user.getIdToken(false).await().token ?: error("로그인 토큰을 가져오지 못했습니다")
            service.forecast("Bearer $token", harvestDate, size, today)
        }
}

// ---------- 화면 표시용 문구 ----------

private val won = NumberFormat.getNumberInstance(Locale.KOREA)

fun PriceView.headline(): String {
    val c = current ?: return "시세 정보 없음"
    val perKg = c.wonPerKg ?: return "시세 정보 없음"
    val box = c.boxAvg?.let { "${sizeKg}kg 상자 ${won.format(it)}원" } ?: "kg당 ${won.format(perKg)}원"
    val diff = vsLastYearPct?.let { if (it >= 0) "작년보다 +$it%" else "작년보다 $it%" } ?: ""
    val season = if (inSeason) "" else " (지난 시즌 마지막 값)"
    return "$marketName $box $diff$season".trim()
}

fun PastYear.line(sizeKg: String?): String =
    if (wonPerKg == null) "$label($year): 자료 없음"
    else "$label($year): " + (boxAvg?.let { "${sizeKg}kg 상자 ${won.format(it)}원" } ?: "kg당 ${won.format(wonPerKg)}원")

fun ForecastDay.line(sizeKg: String): String {
    val mark = if (isHarvestDay) " ← 예상 수확일" else ""
    val closed = if (marketClosed) " (일요일, 경매 거의 없음)" else ""
    return "$date($weekday) ${sizeKg}kg 상자 ${won.format(boxPrice)}원 " +
        "(${won.format(boxLow)}~${won.format(boxHigh)}원, 오차 약 ±${expectedErrorPct ?: "-"}%)$mark$closed"
}
