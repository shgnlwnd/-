"""설정값. 인증키 같은 비밀값은 코드에 넣지 않고 환경변수로만 받음."""
import os
from urllib.parse import unquote

try:  # 로컬 실행 시 .env 파일을 자동으로 읽음 (Render에서는 없어도 됨)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_URL = "https://apis.data.go.kr/B552845/katSale/trades"

# 공공데이터포털 일반 인증키 (Decoding 키). requests가 알아서 인코딩함.
# Encoding 키를 넣어도 한 번 풀어서 쓰므로 두 번 인코딩되는 문제가 없음.
SERVICE_KEY = unquote(os.environ.get("KAT_SERVICE_KEY", "").strip())

# 상품 분류 코드 (2026-11-14 probe로 확인: 대분류 06, 중분류 04). 환경변수로 바꿀 수 있음.
LCLSF_CD = os.environ.get("KAT_LCLSF_CD", "06")
MCLSF_CD = os.environ.get("KAT_MCLSF_CD", "04")

# 미홍 대체 기준: 복숭아 중 품종명이 아래 셋인 것. 코드 형식과 무관하게 이름으로 거름.
PEACH_MCLSF_NAME = "복숭아"
TARGET_SCLSF_NAMES = {"복숭아(일반)", "백도", "기타"}
TARGET_SCLSF_SUFFIX = {"00", "11", "99"}

# 출하기 달력 범위 (MM-DD)
SEASON_START = "06-10"
SEASON_END = "07-10"

# 앱에서 보여 줄 지난 해 수
YEARS_BACK = 3

# kg당 가격 이상치 기준 (원)
MIN_WON_PER_KG = 200
MAX_WON_PER_KG = 60000

# 전국 공영도매시장 32곳 (도매시장 통합홈페이지 기준, 수산 전용 시장 제외)
MARKETS = {
    "110001": "서울가락", "110008": "서울강서", "310101": "수원", "310401": "안양",
    "310901": "안산", "311201": "구리", "230001": "인천남촌", "230003": "인천삼산",
    "360301": "순천", "240001": "광주각화", "240004": "광주서부", "350402": "정읍",
    "350301": "익산", "350101": "전주", "250001": "대전오정", "250003": "대전노은",
    "330101": "청주", "340101": "천안", "330201": "충주", "370401": "안동",
    "371501": "구미", "220001": "대구북부", "380401": "진주", "380101": "창원팔용",
    "380303": "창원내서", "210001": "부산엄궁", "210009": "부산반여", "380201": "울산",
    "370101": "포항", "320201": "원주", "320101": "춘천", "320301": "강릉",
}

# API 호출 간격(초)과 한 페이지 건수. 개발계정은 하루 10,000건 제한.
REQUEST_INTERVAL = float(os.environ.get("KAT_REQUEST_INTERVAL", "0.2"))
NUM_OF_ROWS = 1000

# Firestore 컬렉션 이름
FIRESTORE_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "peach_daily")
# 예측 모델 검증 결과 같은 계산 결과를 보관하는 컬렉션
META_COLLECTION = os.environ.get("META_COLLECTION", "peach_meta")

# 서버가 메모리에 시세 자료를 보관하는 시간(초)과 예측 모델을 다시 만드는 주기(시간)
CACHE_TTL_SEC = float(os.environ.get("CACHE_TTL_SEC", "3600"))
MODEL_REFRESH_HOURS = float(os.environ.get("MODEL_REFRESH_HOURS", "6"))

# 로그인 토큰을 받아 줄 Firebase 프로젝트 (앱 로그인 프로젝트, 쉼표로 여러 개)
AUTH_PROJECTS = os.environ.get("FIREBASE_AUTH_PROJECTS", "peachscope-a92c1,sfm-work").split(",")

# 앱 요청에 Firebase 로그인 토큰을 요구할지 여부
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "true").lower() == "true"
