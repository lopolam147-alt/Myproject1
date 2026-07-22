import requests
from urllib.parse import urlparse
import time

# 快取匯率，避免每次搜尋都打 API（每 4 小時更新一次）
RATES_CACHE = {"data": None, "timestamp": 0}

# 預設備用匯率（當 API 失效時使用）
FALLBACK_RATES = {
    # ========== 美洲 ==========
    "USD": 1.0,        # 美元 (基準)
    "CAD": 1.36,       # 加拿大元
    "MXN": 17.0,       # 墨西哥比索
    "BRL": 5.0,        # 巴西雷亞爾
    # ========== 歐洲 ==========
    "EUR": 0.92,       # 歐元
    "GBP": 0.78,       # 英鎊
    "CHF": 0.89,       # 瑞士法郎
    "SEK": 10.5,       # 瑞典克朗
    "NOK": 10.8,       # 挪威克朗
    "DKK": 6.9,        # 丹麥克朗
    "PLN": 4.0,        # 波蘭茲羅提
    "CZK": 23.5,       # 捷克克朗
    "HUF": 365.0,      # 匈牙利福林
    # ========== 亞洲 ==========
    "CNY": 7.2,        # 人民幣
    "JPY": 150.0,      # 日圓
    "KRW": 1350.0,     # 韓元
    "TWD": 32.5,       # 新台幣
    "HKD": 7.8,        # 港幣
    "SGD": 1.35,       # 新加坡元
    "MYR": 4.65,       # 馬來西亞令吉
    "PHP": 56.0,       # 菲律賓披索
    "IDR": 15800.0,    # 印尼盾
    "THB": 36.0,       # 泰銖
    "VND": 25400.0,    # 越南盾
    "INR": 83.0,       # 印度盧比
    # ========== 大洋洲 ==========
    "AUD": 1.52,       # 澳幣
    "NZD": 1.65,       # 紐西蘭幣
    # ========== 中東 / 非洲 ==========
    "ILS": 3.7,        # 以色列新謝克爾
    "SAR": 3.75,       # 沙烏地里亞爾
    "AED": 3.67,       # 阿聯酋迪拉姆
    "ZAR": 18.5,       # 南非蘭特
}

def get_exchange_rates():
    """回傳 {'USD': 1.0, 'GBP': 0.78, 'EUR': 0.92, ...}"""
    now = time.time()
    # 如果快取超過 4 小時，重新抓取
    if not RATES_CACHE["data"] or (now - RATES_CACHE["timestamp"] > 14400):
        try:
            # 建議改用較穩定的免費 API
            resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                RATES_CACHE["data"] = data.get("rates", {})
                RATES_CACHE["timestamp"] = now
            else:
                print(f"⚠️ 匯率 API 請求失敗（狀態碼 {resp.status_code}），使用備用匯率")
                RATES_CACHE["data"] = FALLBACK_RATES.copy()
                RATES_CACHE["timestamp"] = now
        except Exception as e:
            print(f"⚠️ 匯率 API 請求異常: {e}，使用備用匯率")
            RATES_CACHE["data"] = FALLBACK_RATES.copy()
            RATES_CACHE["timestamp"] = now
    return RATES_CACHE["data"]

def detect_currency_from_url(url: str) -> str:
    """從網址判斷商品頁面的幣別"""
    domain = urlparse(url).netloc.lower()
    if "co.uk" in domain or "uk" in domain:
        return "GBP"
    if "de" in domain or "fr" in domain or "it" in domain or "es" in domain:
        return "EUR"
    if "com.tw" in domain or "tw" in domain:
        return "TWD"
    if "cn" in domain or "com.cn" in domain:
        return "CNY"
    if "co.jp" in domain or "jp" in domain:
        return "JPY"
    if "com.hk" in domain or "hk" in domain:
        return "HKD"
    # 預設為美金
    return "USD"

def get_target_currency_from_country(country: str) -> str:
    country_map = {
        # 美洲
        "us": "USD", "usa": "USD", "united states": "USD",
        "ca": "CAD", "canada": "CAD",
        "mx": "MXN", "mexico": "MXN",
        "br": "BRL", "brazil": "BRL",
        # 歐洲
        "uk": "GBP", "united kingdom": "GBP", "england": "GBP",
        "de": "EUR", "germany": "EUR",
        "fr": "EUR", "france": "EUR",
        "it": "EUR", "italy": "EUR",
        "es": "EUR", "spain": "EUR",
        "nl": "EUR", "netherlands": "EUR",
        "se": "SEK", "sweden": "SEK",
        "no": "NOK", "norway": "NOK",
        "dk": "DKK", "denmark": "DKK",
        "ch": "CHF", "switzerland": "CHF",
        "pl": "PLN", "poland": "PLN",
        "cz": "CZK", "czech": "CZK",
        "hu": "HUF", "hungary": "HUF",
        # 亞洲
        "cn": "CNY", "china": "CNY",
        "jp": "JPY", "japan": "JPY",
        "kr": "KRW", "korea": "KRW", "south korea": "KRW",
        "tw": "TWD", "taiwan": "TWD",
        "hk": "HKD", "hong kong": "HKD",
        "sg": "SGD", "singapore": "SGD",
        "my": "MYR", "malaysia": "MYR",
        "ph": "PHP", "philippines": "PHP",
        "id": "IDR", "indonesia": "IDR",
        "th": "THB", "thailand": "THB",
        "vn": "VND", "vietnam": "VND",
        "in": "INR", "india": "INR",
        # 大洋洲
        "au": "AUD", "australia": "AUD",
        "nz": "NZD", "new zealand": "NZD",
        # 中東 / 非洲
        "il": "ILS", "israel": "ILS",
        "sa": "SAR", "saudi": "SAR",
        "ae": "AED", "uae": "AED",
        "za": "ZAR", "south africa": "ZAR",
    }
    return country_map.get(country.lower().strip(), "USD")

def convert_price(price: float, from_currency: str, to_currency: str) -> float:
    """將價格從 from_currency 轉換成 to_currency"""
    if price is None or price <= 0:
        return None
    if from_currency == to_currency:
        return price
    rates = get_exchange_rates()
    # 如果抓不到匯率，直接回傳原價（當作沒轉換）
    if from_currency not in rates or to_currency not in rates:
        return price
    # 先轉成 USD，再轉成目標幣別
    usd_value = price / rates[from_currency]
    return usd_value * rates[to_currency]