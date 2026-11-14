"""aT 전국 공영도매시장 정산정보 API 호출."""
import time

import requests

from . import config


class KatApiError(RuntimeError):
    pass


def _get(params: dict, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(config.API_URL, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                header = data.get("response", data).get("header", {})
                code = str(header.get("resultCode", "00"))
                if code not in ("00", "0", "INFO-000"):
                    raise KatApiError(f"{code} {header.get('resultMsg')}")
                return data
            last = KatApiError(f"HTTP {r.status_code}: {r.text[:200]}")
        except (requests.RequestException, ValueError) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise KatApiError(f"API 호출 실패: {last}")


def _items(data: dict) -> tuple[list[dict], int]:
    body = data.get("response", data).get("body", {})
    total = int(body.get("totalCount") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):  # {"item": [...]} 형태 대응
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return items, total


def fetch_market_day(market_cd: str, ymd: str, with_item_filter: bool = True) -> list[dict]:
    """한 시장, 한 날짜의 정산 자료를 모든 페이지에 걸쳐 받음."""
    if not config.SERVICE_KEY:
        raise KatApiError("환경변수 KAT_SERVICE_KEY가 비어 있음")
    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "serviceKey": config.SERVICE_KEY,
            "returnType": "JSON",
            "pageNo": page,
            "numOfRows": config.NUM_OF_ROWS,
            "cond[whsl_mrkt_cd::EQ]": market_cd,
            "cond[trd_clcln_ymd::EQ]": ymd,
        }
        if with_item_filter:
            if config.LCLSF_CD:
                params["cond[gds_lclsf_cd::EQ]"] = config.LCLSF_CD
            if config.MCLSF_CD:
                params["cond[gds_mclsf_cd::EQ]"] = config.MCLSF_CD
        items, total = _items(_get(params))
        rows.extend(items)
        time.sleep(config.REQUEST_INTERVAL)
        if not items or len(rows) >= total:
            return rows
        page += 1
