from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import deque
import dataclasses
import email as email_pkg
import base64
import ctypes
from contextlib import ExitStack
import hashlib
import json
import queue
import random
import re
import secrets
import shutil
import select
import socket
import ssl
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, BooleanVar, IntVar, StringVar, Tk, Toplevel, Label, Menu, PanedWindow, filedialog, messagebox, simpledialog
from tkinter import font as tkfont
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, unquote, urljoin, urlparse, urlsplit, urlunsplit

import imaplib
import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    from curl_cffi.requests import Session as CurlCffiSession  # type: ignore
except ImportError:
    CurlCffiSession = None  # type: ignore


APP_TITLE = "OpenAI 注册 + Session 获取"
APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "state.json"
STATE_SCHEMA_VERSION = 2
STATE_DATA_DIR = APP_DIR / "state_data"
STATE_SESSION_DIR = STATE_DATA_DIR / "sessions"
STATE_SAVE_DEBOUNCE_SECONDS = 1.5
MAX_LOG_RECORDS_PER_VIEW = 2000
MAX_TOTAL_LOG_RECORDS = 10000
UI_FONT_FAMILY = "Microsoft YaHei"
UI_FONT_SIZE = 9
UI_TEXT_FONT_SIZE = 10
TOOLTIP_DELAY_MS = 450
CHATGPT_BASE_URL = "https://chatgpt.com"
AUTH_BASE_URL = "https://auth.openai.com"
DEFAULT_PAYPAL_EXTENSION_DIR = r"D:\downloads\googledownloads\palpay扩展\palpay"
AUDIO_DEFAULT_DEVICE_LABEL = "系统默认"
AUTH_AUTHORIZE_CONTINUE_URL = f"{AUTH_BASE_URL}/api/accounts/authorize/continue"
AUTH_EMAIL_OTP_SEND_URL = f"{AUTH_BASE_URL}/api/accounts/email-otp/send"
AUTH_EMAIL_OTP_VALIDATE_URL = f"{AUTH_BASE_URL}/api/accounts/email-otp/validate"
AUTH_WORKSPACE_SELECT_URL = f"{AUTH_BASE_URL}/api/accounts/workspace/select"
AUTH_PHONE_SEND_URL = f"{AUTH_BASE_URL}/api/accounts/add-phone/send"
AUTH_PHONE_OTP_VALIDATE_URL = f"{AUTH_BASE_URL}/api/accounts/phone-otp/validate"
AUTH_OAUTH_TOKEN_URLS = [
    f"{AUTH_BASE_URL}/api/oauth/oauth2/token",
    f"{AUTH_BASE_URL}/oauth/token",
]
DEFAULT_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_STRIPE_PK = "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n"
STRIPE_VERSION_FULL = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
DEFAULT_STRIPE_RUNTIME_VERSION = "6f8494a281"
PAY_LONG_LINK_TIMEOUT = 30
DEFAULT_AUTH_CONCURRENCY = 10
MAX_AUTH_CONCURRENCY = 30
DEFAULT_LINK_PROXY_PRECHECK_LIMIT = 500
DEFAULT_LINK_PROXY_PRECHECK_CONCURRENCY = 100
MAX_LINK_PROXY_PRECHECK_CONCURRENCY = 300
PROVIDER_PROXY_TARGET_STOCK = 500
PROVIDER_PROXY_LOW_WATER = 200
PROVIDER_PROXY_MAX_WORKERS = 30
PROVIDER_PROXY_TAKE_TIMEOUT = 60
PROVIDER_PROXY_BACKOFF_SECONDS = (1, 2, 5, 10, 30)
PROVIDER_PROXY_ROLES = ("create", "followup", "approve")
PROVIDER_PROXY_ROLE_LABELS = {"create": "第一步", "followup": "后续", "approve": "Approve"}
ACCOUNT_ALL_GROUP = "全部"
ACCOUNT_DEFAULT_GROUP = "未分组"
ACCOUNT_SORT_CUSTOM = "custom"
ACCOUNT_SORT_ASC = "asc"
ACCOUNT_SORT_DESC = "desc"
ACCOUNT_SORT_DIRECTIONS = {ACCOUNT_SORT_CUSTOM, ACCOUNT_SORT_ASC, ACCOUNT_SORT_DESC}
ACCOUNT_SORT_COLUMNS = ("email", "type", "status", "attempts")
ACCOUNT_SORT_LABELS = {
    "email": "邮箱",
    "type": "类型",
    "status": "状态",
    "attempts": "撞链次数",
}

IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
TOKEN_ENDPOINTS = [
    {"name": "LIVE", "url": "https://login.live.com/oauth20_token.srf", "scope": ""},
    {"name": "LIVE+scope", "url": "https://login.live.com/oauth20_token.srf", "scope": IMAP_SCOPE},
    {"name": "V1-COMMON", "url": "https://login.microsoftonline.com/common/oauth2/token", "scope": "", "resource": "https://outlook.office.com/"},
    {"name": "V1-CONSUMERS", "url": "https://login.microsoftonline.com/consumers/oauth2/token", "scope": "", "resource": "https://outlook.office.com/"},
    {"name": "CONSUMERS", "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "scope": IMAP_SCOPE},
    {"name": "CONSUMERS-noscope", "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "scope": ""},
    {"name": "COMMON", "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": IMAP_SCOPE},
    {"name": "COMMON-noscope", "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": ""},
]

FIRST_NAMES = [
    "Ethan", "Noah", "Liam", "Mason", "Lucas", "Logan", "Owen", "Ryan", "Leo", "Adam",
    "Ella", "Ava", "Mia", "Luna", "Chloe", "Grace", "Ruby", "Nora", "Ivy", "Sofia",
]
LAST_NAMES = [
    "Smith", "Brown", "Taylor", "Walker", "Wilson", "Clark", "Hall", "Young", "Allen", "King",
    "Scott", "Green", "Baker", "Adams", "Turner",
]

PAYMENT_MODES = {
    "无卡长链接 US/USD": {"country": "US", "currency": "USD"},
    "无卡长链接 BR/BRL": {"country": "BR", "currency": "BRL"},
    "无卡长链接 DE/EUR": {"country": "DE", "currency": "EUR"},
    "无卡长链接 FR/EUR": {"country": "FR", "currency": "EUR"},
    "无卡长链接 GB/GBP": {"country": "GB", "currency": "GBP"},
    "无卡长链接 CA/CAD": {"country": "CA", "currency": "CAD"},
    "无卡长链接 AU/AUD": {"country": "AU", "currency": "AUD"},
    "无卡长链接 JP/JPY": {"country": "JP", "currency": "JPY"},
    "GoPay 长链接 ID/IDR": {"country": "ID", "currency": "IDR", "payment_provider": "gopay"},
    "PayPal 长链接 US/USD": {"country": "US", "currency": "USD"},
    "试用短链 PayPal US/USD": {"country": "US", "currency": "USD", "trial_short_link": True},
    "PayPal 长链接 FR/EUR": {"country": "FR", "currency": "EUR"},
    "Apple Pay 支付页 US/USD": {"country": "US", "currency": "USD", "apple_pay_hosted": True},
    "Apple Pay 支付页 JP/JPY": {"country": "JP", "currency": "JPY", "apple_pay_hosted": True},
}
PAYMENT_MODE_ALIASES = {name.replace("长链接", "短链"): name for name in PAYMENT_MODES}
KEPT_REGISTER_BROWSER_SESSIONS = {}
TEAM_EMAIL_DOMAIN = "wishtoapp.edu.kg"
COUNTRY_CURRENCY = {
    "AT": "EUR", "AU": "AUD", "BE": "EUR", "BR": "BRL", "CA": "CAD", "CH": "CHF", "CZ": "CZK",
    "DE": "EUR", "DK": "DKK", "ES": "EUR", "FI": "EUR", "FR": "EUR", "GB": "GBP", "HK": "HKD",
    "ID": "IDR", "IE": "EUR", "IN": "INR", "IT": "EUR", "JP": "JPY", "KR": "KRW", "MX": "MXN",
    "MY": "MYR", "NL": "EUR", "NO": "NOK", "NZ": "NZD", "PH": "PHP", "PL": "PLN", "PT": "EUR",
    "SE": "SEK", "SG": "SGD", "TH": "THB", "TW": "TWD", "US": "USD", "VN": "VND",
}
OPENAI_SUPPORTED_COUNTRY_CODES = {
    "AX", "AL", "DZ", "AS", "AD", "AO", "AI", "AQ", "AG", "AR",
    "AM", "AW", "AU", "AT", "AZ", "BS", "BH", "BD", "BB", "BE",
    "BZ", "BJ", "BM", "BT", "BO", "BQ", "BA", "BW", "BV", "BR",
    "IO", "BN", "BG", "BF", "BI", "CV", "KH", "CM", "CA", "KY",
    "CF", "TD", "CL", "CX", "CC", "CO", "KM", "CG", "CK", "CR",
    "CI", "HR", "CW", "CY", "CZ", "DK", "DJ", "DM", "DO", "EC",
    "SV", "GQ", "ER", "EE", "SZ", "FK", "FO", "FJ", "FI", "FR",
    "GF", "PF", "TF", "GA", "GM", "GE", "DE", "GH", "GI", "GR",
    "GL", "GD", "GP", "GU", "GT", "GG", "GN", "GW", "GY", "HT",
    "HM", "VA", "HN", "HU", "IS", "IN", "ID", "IQ", "IE", "IM",
    "IL", "IT", "JM", "JP", "JE", "JO", "KZ", "KE", "KI", "KW",
    "KG", "LA", "LV", "LB", "LS", "LR", "LI", "LT", "LU", "MG",
    "MW", "MY", "MV", "ML", "MT", "MH", "MQ", "MR", "MU", "YT",
    "MX", "FM", "MD", "MC", "MN", "ME", "MS", "MA", "MZ", "MM",
    "NA", "NR", "NP", "NL", "NC", "NZ", "NI", "NE", "NG", "NU",
    "NF", "MK", "MP", "NO", "OM", "PK", "PW", "PS", "PA", "PG",
    "PE", "PH", "PN", "PL", "PT", "PR", "QA", "RE", "RO", "RW",
    "BL", "SH", "KN", "LC", "MF", "PM", "VC", "WS", "SM", "ST",
    "SN", "RS", "SC", "SL", "SG", "SX", "SK", "SI", "SB", "SO",
    "ZA", "GS", "KR", "SS", "ES", "LK", "SR", "SJ", "SE", "CH",
    "TW", "TZ", "TH", "TL", "TG", "TK", "TO", "TT", "TN", "TR",
    "TM", "TC", "TV", "UG", "UA", "AE", "GB", "UM", "US", "UY",
    "UZ", "VU", "WF", "EH", "ZM",
}
EUR_COUNTRIES = {
    "AD", "AT", "BE", "CY", "EE", "FI", "FR", "DE", "GR", "HR",
    "IE", "IT", "LV", "LT", "LU", "MT", "MC", "ME", "NL", "PT",
    "SM", "SK", "SI", "ES",
}
COUNTRY_CURRENCY.update({country: "EUR" for country in EUR_COUNTRIES if country not in COUNTRY_CURRENCY})
COUNTRY_CURRENCY.update({
    "AE": "AED", "AR": "ARS", "BH": "BHD", "BM": "BMD", "BO": "BOB", "BQ": "USD",
    "CL": "CLP", "CO": "COP", "GU": "USD", "IL": "ILS", "PR": "USD", "TR": "TRY",
    "UA": "UAH", "UM": "USD", "ZA": "ZAR",
})
COUNTRY_PHONE_PREFIX = {
    "AU": "+61", "CA": "+1", "DE": "+49", "GB": "+44", "IE": "+353", "JP": "+81",
    "NZ": "+64", "SG": "+65", "TH": "+66", "US": "+1",
    "AD": "+376", "AE": "+971", "AL": "+355", "AR": "+54", "AT": "+43", "BE": "+32",
    "BG": "+359", "BH": "+973", "BM": "+1", "BO": "+591", "BR": "+55", "CH": "+41",
    "CL": "+56", "CO": "+57", "CR": "+506", "CY": "+357", "CZ": "+420", "DK": "+45",
    "EE": "+372", "ES": "+34", "FI": "+358", "FR": "+33", "GI": "+350", "GR": "+30",
    "HK": "+852", "HU": "+36", "ID": "+62", "IL": "+972", "IN": "+91", "IS": "+354",
    "IT": "+39", "KR": "+82", "KZ": "+7", "LI": "+423", "LT": "+370", "LU": "+352",
    "LV": "+371", "MC": "+377", "MD": "+373", "ME": "+382", "MK": "+389", "MT": "+356",
    "MX": "+52", "MY": "+60", "NL": "+31", "NO": "+47", "PH": "+63", "PL": "+48",
    "PT": "+351", "QA": "+974", "RO": "+40", "RS": "+381", "SA": "+966", "SE": "+46",
    "SI": "+386", "SK": "+421", "SM": "+378", "TR": "+90", "TW": "+886", "UA": "+380",
    "UY": "+598", "ZA": "+27",
}
US_BILLING_NAMES = [("James", "Smith"), ("John", "Brown"), ("Michael", "Johnson"), ("Robert", "Miller"), ("David", "Davis"), ("William", "Wilson")]
US_BILLING_STREETS = [
    ("3110 Sunset Boulevard", "Los Angeles", "CA", "90026"),
    ("1200 Market Street", "San Francisco", "CA", "94102"),
    ("500 Main Street", "Austin", "TX", "78701"),
    ("88 Broadway", "New York", "NY", "10007"),
    ("1200 Peachtree St", "Atlanta", "GA", "30309"),
]
DE_BILLING_NAMES = [("Lukas", "Schneider"), ("Felix", "Muller"), ("Jonas", "Weber"), ("Leon", "Fischer"), ("Marie", "Wagner"), ("Laura", "Becker"), ("Maximilian", "Hoffmann"), ("Paul", "Schulz"), ("Emma", "Koch"), ("Hannah", "Bauer"), ("Sophie", "Richter"), ("Noah", "Klein")]
DE_BILLING_STREETS = [
    ("Friedrichstrasse 123", "Berlin", "BE", "10117"),
    ("Leopoldstrasse 50", "Munich", "BY", "80802"),
    ("Zeil 85", "Frankfurt am Main", "HE", "60313"),
    ("Konigsallee 60", "Dusseldorf", "NW", "40212"),
    ("Moenckebergstrasse 7", "Hamburg", "HH", "20095"),
    ("Hohenzollernring 72", "Cologne", "NW", "50672"),
    ("Kaiserstrasse 44", "Stuttgart", "BW", "70173"),
    ("Kaufingerstrasse 15", "Munich", "BY", "80331"),
    ("Georgstrasse 24", "Hanover", "NI", "30159"),
    ("Prager Strasse 9", "Dresden", "SN", "01069"),
    ("Schadowstrasse 36", "Dusseldorf", "NW", "40212"),
    ("Breite Strasse 18", "Bonn", "NW", "53111"),
]
GB_BILLING_NAMES = [("Oliver", "Smith"), ("George", "Taylor"), ("Harry", "Brown"), ("Noah", "Wilson"), ("Jack", "Davies"), ("Arthur", "Evans"), ("Olivia", "Johnson"), ("Amelia", "Roberts"), ("Isla", "Walker"), ("Ava", "Thompson"), ("Mia", "White"), ("Grace", "Hughes")]
GB_BILLING_STREETS = [
    ("221B Baker Street", "London", "England", "NW1 6XE"),
    ("10 Downing Street", "London", "England", "SW1A 2AA"),
    ("45 Deansgate", "Manchester", "England", "M3 2AY"),
    ("18 Park Row", "Leeds", "England", "LS1 5JA"),
    ("77 Queen Street", "Cardiff", "Wales", "CF10 2GR"),
    ("9 Princes Street", "Edinburgh", "Scotland", "EH2 2ER"),
    ("33 Broad Street", "Birmingham", "England", "B1 2HF"),
    ("14 Castle Street", "Liverpool", "England", "L2 0NE"),
    ("52 College Green", "Bristol", "England", "BS1 5SH"),
    ("6 Royal Avenue", "Belfast", "Northern Ireland", "BT1 1DA"),
]
AU_BILLING_NAMES = [("Jack", "Wilson"), ("Oliver", "Taylor"), ("Noah", "Brown"), ("Charlotte", "Smith"), ("Amelia", "Jones"), ("Isla", "Williams")]
AU_BILLING_STREETS = [
    ("120 Collins Street", "Melbourne", "Victoria", "3000"),
    ("88 George Street", "Sydney", "New South Wales", "2000"),
    ("45 Queen Street", "Brisbane", "Queensland", "4000"),
    ("22 King William Street", "Adelaide", "South Australia", "5000"),
    ("60 St Georges Terrace", "Perth", "Western Australia", "6000"),
    ("18 Elizabeth Street", "Hobart", "Tasmania", "7000"),
]
EXTRA_BILLING_NAMES = [("Alex", "Tan"), ("Daniel", "Lee"), ("Emma", "Wong"), ("Mia", "Chen"), ("Noah", "Martin"), ("Olivia", "Nguyen")]
EXTRA_BILLING_STREETS = {
    "TH": [("999 Rama I Road", "Bangkok", "Bangkok", "10330"), ("88 Sukhumvit Road", "Bangkok", "Bangkok", "10110"), ("45 Nimman Road", "Chiang Mai", "Chiang Mai", "50200")],
    "JP": [("1-1 Marunouchi", "Chiyoda-ku", "Tokyo", "100-0005"), ("2-2-1 Yaesu", "Chuo-ku", "Tokyo", "104-0028"), ("3-1 Umeda", "Osaka", "Osaka", "530-0001")],
    "SG": [("10 Anson Road", "Singapore", "Singapore", "079903"), ("1 Raffles Place", "Singapore", "Singapore", "048616"), ("80 Robinson Road", "Singapore", "Singapore", "068898")],
    "NZ": [("22 Queen Street", "Auckland", "Auckland", "1010"), ("50 Lambton Quay", "Wellington", "Wellington", "6011"), ("120 Hereford Street", "Christchurch", "Canterbury", "8011")],
    "CA": [("100 King Street West", "Toronto", "ON", "M5X 1A9"), ("555 West Hastings Street", "Vancouver", "BC", "V6B 4N6"), ("1250 Rene-Levesque Blvd", "Montreal", "QC", "H3B 4W8")],
    "IE": [("1 Grand Canal Square", "Dublin", "Dublin", "D02 P820"), ("10 South Mall", "Cork", "Cork", "T12 RD43"), ("5 Eyre Square", "Galway", "Galway", "H91 FPK2")],
}
BILLING_PROFILE_CITY_BY_COUNTRY = {
    "AT": ["Vienna", "Graz", "Linz"], "BE": ["Brussels", "Antwerp", "Ghent"], "BR": ["Sao Paulo", "Rio de Janeiro", "Brasilia"],
    "CH": ["Zurich", "Geneva", "Basel"], "DK": ["Copenhagen", "Aarhus", "Odense"], "ES": ["Madrid", "Barcelona", "Valencia"],
    "FI": ["Helsinki", "Espoo", "Tampere"], "FR": ["Paris", "Lyon", "Marseille"], "ID": ["Jakarta", "Surabaya", "Bandung"],
    "IT": ["Rome", "Milan", "Turin"], "KR": ["Seoul", "Busan", "Incheon"], "MX": ["Mexico City", "Guadalajara", "Monterrey"],
    "NL": ["Amsterdam", "Rotterdam", "Utrecht"], "NO": ["Oslo", "Bergen", "Trondheim"], "PL": ["Warsaw", "Krakow", "Gdansk"],
    "PT": ["Lisbon", "Porto", "Coimbra"], "SE": ["Stockholm", "Gothenburg", "Malmo"], "TW": ["Taipei", "Taichung", "Kaohsiung"],
}
POSTAL_PATTERN_BY_COUNTRY = {
    "AD": "AD###", "AR": "C####", "AU": "####", "AT": "####", "BE": "####", "BR": "#####-###",
    "CA": "A#A #A#", "CH": "####", "CL": "#######", "CZ": "### ##", "DE": "#####", "DK": "####",
    "ES": "#####", "FI": "#####", "FR": "#####", "GB": "AA# #AA", "IE": "A## A###", "ID": "#####",
    "IN": "######", "IT": "#####", "JP": "###-####", "KR": "#####", "MX": "#####", "NL": "#### AA",
    "NO": "####", "NZ": "####", "PL": "##-###", "PT": "####-###", "SE": "### ##", "SG": "######",
    "TH": "#####", "US": "#####",
}
BILLING_STREET_POOL = ["Market Street", "Central Avenue", "Station Road", "Main Street", "High Street", "King Street"]
BILLING_PROFILE_BY_COUNTRY = {
    country: {
        "currency": COUNTRY_CURRENCY.get(country, "USD"),
        "phone_prefix": COUNTRY_PHONE_PREFIX.get(country, "+1"),
        "city_pool": BILLING_PROFILE_CITY_BY_COUNTRY.get(country, ["Capital City", "Central District", "Market Town"]),
        "postal_pattern": POSTAL_PATTERN_BY_COUNTRY.get(country, "#####"),
        "street_pool": BILLING_STREET_POOL,
    }
    for country in OPENAI_SUPPORTED_COUNTRY_CODES
}
LOCALE_MAP = {
    "de": ("de-DE", "de"), "en": ("en-US", "en"), "en-US": ("en-US", "en"), "es": ("es-ES", "es"),
    "fr": ("fr-FR", "fr"), "id": ("id-ID", "id"), "it": ("it-IT", "it"), "ja": ("ja-JP", "ja"),
    "ko": ("ko-KR", "ko"), "pt-BR": ("pt-BR", "pt-BR"), "zh-CN": ("zh-CN", "zh-CN"), "zh-TW": ("zh-TW", "zh-TW"),
}

DEVICE_PROFILES = [
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/New_York"},
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/Chicago"},
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/Los_Angeles"},
    {"locale": "en-GB", "languages": ["en-GB", "en"], "timezone": "Europe/London"},
]
REGISTER_DEVICE_PROFILES = [
    {"locale": "ja-JP", "languages": ["ja-JP", "ja"], "timezone": "Asia/Tokyo"},
]
TEAM_DEVICE_PROFILES = [
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/New_York"},
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/Chicago"},
    {"locale": "en-US", "languages": ["en-US", "en"], "timezone": "America/Los_Angeles"},
]
PAYMENT_DEVICE_PROFILES = [
    {"locale": "ja-JP", "languages": ["ja-JP", "ja"], "timezone": "Asia/Tokyo"},
]
COUNTRY_BROWSER_LOCALE = {
    "AU": "en-AU", "BR": "pt-BR", "CA": "en-CA", "DE": "de-DE", "ES": "es-ES",
    "FR": "fr-FR", "GB": "en-GB", "ID": "id-ID", "IN": "en-IN", "IT": "it-IT",
    "JP": "ja-JP", "KR": "ko-KR", "MX": "es-MX", "NL": "nl-NL", "NZ": "en-NZ",
    "PT": "pt-PT", "SG": "en-SG", "TH": "th-TH", "TW": "zh-TW", "US": "en-US",
    "VN": "vi-VN",
}


@dataclasses.dataclass
class MailAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str
    raw: str
    account_type: str = "free"
    status: str = ""
    openai_rt: str = ""
    auth_phone_number: str = ""
    auth_phone_sms_url: str = ""
    group: str = ACCOUNT_DEFAULT_GROUP


@dataclasses.dataclass
class PhoneEntry:
    number: str
    sms_url: str
    status: str = "可用"
    last_code: str = ""
    last_error: str = ""
    receive_count: int = 0


@dataclasses.dataclass
class PaymentCard:
    card: str
    month: str
    year: str
    cvv: str
    status: str = "未用"


@dataclasses.dataclass
class ProxyConfig:
    local_proxy: str = ""
    dynamic_proxy: str = ""
    chain_url: str = ""

    @property
    def label(self) -> str:
        return format_proxy_chain_label(self.local_proxy, self.dynamic_proxy)


@dataclasses.dataclass(frozen=True)
class ProxyHealthResult:
    success: bool
    ip: str = ""
    country: str = ""
    region: str = ""
    city: str = ""
    timezone: str = ""
    org: str = ""
    chatgpt_status: int = 0
    stripe_status: int = 0
    failed_stage: str = ""
    error: str = ""

    @property
    def location(self) -> str:
        return "/".join(part for part in (self.country, self.region, self.city) if part)

    @property
    def summary(self) -> str:
        if not self.success:
            detail = f": {self.error}" if self.error else ""
            return f"检测失败[{self.failed_stage or 'unknown'}]{detail}"
        return " ".join(
            part
            for part in (
                self.ip,
                self.location,
                self.timezone,
                self.org,
                f"ChatGPT={self.chatgpt_status}",
                f"Stripe={self.stripe_status}",
            )
            if part
        )


def detect_proxy_health(proxy_url: str, timeout: int = 15, session=None) -> ProxyHealthResult:
    client = session or requests.Session()
    normalized = normalize_proxy_url(proxy_url)
    if normalized:
        client.proxies.update({"http": normalized, "https": normalized})
    try:
        response = client.get("https://ipinfo.io/json", timeout=timeout)
        if response.status_code != 200:
            return ProxyHealthResult(False, failed_stage="出口", error=f"HTTP {response.status_code}")
        payload = response.json() or {}
        ip = str(payload.get("ip") or "").strip()
        country = str(payload.get("country") or "").strip().upper()
        if not ip or not re.fullmatch(r"[A-Z]{2}", country):
            return ProxyHealthResult(False, failed_stage="出口", error="IPInfo 缺少 IP 或国家代码")
        base = {
            "ip": ip,
            "country": country,
            "region": str(payload.get("region") or "").strip(),
            "city": str(payload.get("city") or "").strip(),
            "timezone": str(payload.get("timezone") or "").strip(),
            "org": str(payload.get("org") or "").strip(),
        }
    except Exception as exc:
        return ProxyHealthResult(False, failed_stage="出口", error=str(exc))

    try:
        response = client.get(f"{CHATGPT_BASE_URL}/api/auth/csrf", timeout=timeout)
        chatgpt_status = int(response.status_code)
        if chatgpt_status not in (200, 403):
            return ProxyHealthResult(False, **base, chatgpt_status=chatgpt_status, failed_stage="ChatGPT", error=f"HTTP {chatgpt_status}")
    except Exception as exc:
        return ProxyHealthResult(False, **base, failed_stage="ChatGPT", error=str(exc))

    try:
        response = client.get("https://api.stripe.com/v1/payment_pages/__connectivity_check__", timeout=timeout)
        stripe_status = int(response.status_code)
        if stripe_status == 407 or stripe_status == 429 or stripe_status >= 500:
            return ProxyHealthResult(
                False,
                **base,
                chatgpt_status=chatgpt_status,
                stripe_status=stripe_status,
                failed_stage="Stripe",
                error=f"HTTP {stripe_status}",
            )
    except Exception as exc:
        return ProxyHealthResult(False, **base, chatgpt_status=chatgpt_status, failed_stage="Stripe", error=str(exc))

    return ProxyHealthResult(True, **base, chatgpt_status=chatgpt_status, stripe_status=stripe_status)


def parse_provider_regions(value: str) -> tuple[str, ...]:
    regions = []
    seen = set()
    for raw in re.split(r"[\s,，;；]+", str(value or "").strip()):
        if not raw:
            continue
        region = raw.upper()
        if not re.fullmatch(r"[A-Z]{2}", region):
            raise ValueError(f"国家代码必须是两位字母: {raw}")
        if region not in seen:
            seen.add(region)
            regions.append(region)
    if not regions:
        raise ValueError("至少填写一个 region 国家代码")
    return tuple(regions)


@dataclasses.dataclass(frozen=True)
class ProxyProviderConfig:
    enabled: bool = False
    username: str = ""
    password: str = ""
    endpoint: str = ""
    duration: int = 5
    regions_text: str = "JP"

    @property
    def regions(self) -> tuple[str, ...]:
        return parse_provider_regions(self.regions_text)

    def validated(self) -> "ProxyProviderConfig":
        if not self.enabled:
            return self
        username = str(self.username or "").strip()
        password = str(self.password or "")
        endpoint = str(self.endpoint or "").strip()
        if not username:
            raise ValueError("用户名不能为空")
        if not password:
            raise ValueError("密码不能为空")
        parsed = urlsplit(endpoint if "://" in endpoint else f"http://{endpoint}")
        if not parsed.hostname or parsed.port is None:
            raise ValueError("主机端口格式应为 hostname:port")
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("主机端口不能包含凭据、路径或参数")
        duration = int(self.duration)
        if duration < 1 or duration > 120:
            raise ValueError("t 必须在 1–120 之间")
        self.regions
        return self

    def build_proxy_url(self, region: str, sid: str | None = None) -> str:
        self.validated()
        region = str(region or "").strip().upper()
        if region not in self.regions:
            raise ValueError(f"region 未配置: {region}")
        sid = str(sid or random_provider_sid())
        if not re.fullmatch(r"[A-Za-z0-9]{8}", sid):
            raise ValueError("sid 必须是 8 位字母或数字")
        parsed = urlsplit(self.endpoint if "://" in self.endpoint else f"http://{self.endpoint}")
        host = parsed.hostname or ""
        host_text = f"[{host}]" if ":" in host and not host.startswith("[") else host
        username = quote(str(self.username).strip(), safe="")
        password = quote(str(self.password), safe="")
        return f"http://{username}-region-{region}-sid-{sid}-t-{int(self.duration)}:{password}@{host_text}:{parsed.port}"

    def state_dict(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "username": self.username,
            "password": self.password,
            "endpoint": self.endpoint,
            "duration": int(self.duration),
            "regions": self.regions_text,
        }

    @classmethod
    def from_state(cls, value) -> "ProxyProviderConfig":
        data = value if isinstance(value, dict) else {}
        try:
            duration = int(data.get("duration") or 5)
        except Exception:
            duration = 5
        return cls(
            enabled=bool(data.get("enabled")),
            username=str(data.get("username") or ""),
            password=str(data.get("password") or ""),
            endpoint=str(data.get("endpoint") or ""),
            duration=min(120, max(1, duration)),
            regions_text=str(data.get("regions") or "JP"),
        )


def random_provider_sid() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def proxy_exit_country(proxy_exit: str) -> str:
    match = re.search(r"(?:^|\s)([A-Z]{2})(?:/|\s|$)", str(proxy_exit or "").upper())
    return match.group(1) if match else ""


@dataclasses.dataclass(frozen=True)
class ProviderProxyCandidate:
    role: str
    url: str
    region: str
    proxy_exit: str = ""


class ProviderProxyPoolManager:
    def __init__(
        self,
        detector,
        status_callback=None,
        validated_callback=None,
        target_stock: int = PROVIDER_PROXY_TARGET_STOCK,
        low_water: int = PROVIDER_PROXY_LOW_WATER,
        max_workers: int = PROVIDER_PROXY_MAX_WORKERS,
    ):
        self.detector = detector
        self.status_callback = status_callback or (lambda *_args: None)
        self.validated_callback = validated_callback or (lambda *_args: None)
        self.target_stock = max(1, int(target_stock))
        self.low_water = min(self.target_stock, max(0, int(low_water)))
        self.max_workers = max(1, int(max_workers))
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._configs = {role: ProxyProviderConfig() for role in PROVIDER_PROXY_ROLES}
        self._local_proxy = ""
        self._ready = {role: deque() for role in PROVIDER_PROXY_ROLES}
        self._inflight = {role: 0 for role in PROVIDER_PROXY_ROLES}
        self._refilling = {role: False for role in PROVIDER_PROXY_ROLES}
        self._region_indexes = {role: 0 for role in PROVIDER_PROXY_ROLES}
        self._failure_counts = {role: 0 for role in PROVIDER_PROXY_ROLES}
        self._next_allowed = {role: 0.0 for role in PROVIDER_PROXY_ROLES}
        self._generation = {role: 0 for role in PROVIDER_PROXY_ROLES}
        self._thread = None
        self._executor = None
        self._round_robin_index = 0

    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="provider-proxy-check")
            self._thread = threading.Thread(target=self._run, name="provider-proxy-pool", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        executor = self._executor
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        self._thread = None
        self._executor = None

    def update_max_workers(self, max_workers: int) -> None:
        max_workers = max(1, int(max_workers))
        old_executor = None
        with self._condition:
            if max_workers == self.max_workers:
                return
            self.max_workers = max_workers
            for role in PROVIDER_PROXY_ROLES:
                self._generation[role] += 1
                self._inflight[role] = 0
            if self._thread and self._thread.is_alive() and self._executor:
                old_executor = self._executor
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="provider-proxy-check")
            self._condition.notify_all()
        if old_executor:
            old_executor.shutdown(wait=False, cancel_futures=True)

    def configure(self, configs: dict[str, ProxyProviderConfig], local_proxy: str = "") -> None:
        with self._condition:
            local_proxy = normalize_proxy_url(local_proxy)
            local_changed = local_proxy != self._local_proxy
            self._local_proxy = local_proxy
            for role in PROVIDER_PROXY_ROLES:
                config = configs.get(role, ProxyProviderConfig())
                config.validated()
                if local_changed or config != self._configs[role]:
                    self._generation[role] += 1
                    self._ready[role].clear()
                    self._region_indexes[role] = 0
                    self._failure_counts[role] = 0
                    self._next_allowed[role] = 0.0
                self._configs[role] = config
                self._refilling[role] = bool(config.enabled)
            self._condition.notify_all()
        self.start()
        self._publish_all_status()

    def enabled_roles(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(role for role in PROVIDER_PROXY_ROLES if self._configs[role].enabled)

    def ready_count(self, role: str) -> int:
        with self._condition:
            return len(self._ready[role])

    def snapshot(self, role: str) -> dict:
        with self._condition:
            return {
                "enabled": self._configs[role].enabled,
                "ready": len(self._ready[role]),
                "inflight": self._inflight[role],
                "target": self.target_stock,
                "low_water": self.low_water,
                "failures": self._failure_counts[role],
            }

    def wait_until_ready(self, minimum: int, stop_event: threading.Event | None = None, roles=None) -> bool:
        minimum = max(0, int(minimum))
        with self._condition:
            while True:
                wanted_roles = set(roles or PROVIDER_PROXY_ROLES)
                enabled = [role for role in PROVIDER_PROXY_ROLES if role in wanted_roles and self._configs[role].enabled]
                if all(len(self._ready[role]) >= minimum for role in enabled):
                    return True
                if self._stop_event.is_set() or (stop_event and stop_event.is_set()):
                    return False
                self._condition.wait(timeout=0.5)

    def take(self, role: str, timeout: float, stop_event: threading.Event | None = None) -> ProviderProxyCandidate | None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            if not self._configs[role].enabled:
                return None
            while not self._ready[role]:
                if self._stop_event.is_set() or (stop_event and stop_event.is_set()):
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(0.5, remaining))
            candidate = self._ready[role].popleft()
            if len(self._ready[role]) <= self.low_water:
                self._refilling[role] = True
            self._condition.notify_all()
        self._publish_status(role)
        return candidate

    def _publish_status(self, role: str) -> None:
        try:
            self.status_callback(role, self.snapshot(role))
        except Exception:
            pass

    def _publish_all_status(self) -> None:
        for role in PROVIDER_PROXY_ROLES:
            self._publish_status(role)

    def _next_candidate_locked(self, role: str) -> tuple[ProviderProxyCandidate, str, int]:
        config = self._configs[role]
        regions = config.regions
        index = self._region_indexes[role]
        region = regions[index % len(regions)]
        self._region_indexes[role] = index + 1
        return ProviderProxyCandidate(role, config.build_proxy_url(region), region), self._local_proxy, self._generation[role]

    def _run(self) -> None:
        while not self._stop_event.is_set():
            scheduled = False
            with self._condition:
                executor = self._executor
                if not executor:
                    return
                total_inflight = sum(self._inflight.values())
                for offset in range(len(PROVIDER_PROXY_ROLES)):
                    if total_inflight >= self.max_workers:
                        break
                    role_index = (self._round_robin_index + offset) % len(PROVIDER_PROXY_ROLES)
                    role = PROVIDER_PROXY_ROLES[role_index]
                    config = self._configs[role]
                    ready_count = len(self._ready[role])
                    if not config.enabled:
                        continue
                    if ready_count <= self.low_water:
                        self._refilling[role] = True
                    if not self._refilling[role]:
                        continue
                    if ready_count + self._inflight[role] >= self.target_stock:
                        if ready_count >= self.target_stock:
                            self._refilling[role] = False
                        continue
                    if time.monotonic() < self._next_allowed[role]:
                        continue
                    candidate, local_proxy, generation = self._next_candidate_locked(role)
                    self._inflight[role] += 1
                    total_inflight += 1
                    scheduled = True
                    future = executor.submit(self.detector, candidate, local_proxy)
                    future.add_done_callback(
                        lambda completed, item=candidate, gen=generation: self._complete_check(item, gen, completed)
                    )
                self._round_robin_index = (self._round_robin_index + 1) % len(PROVIDER_PROXY_ROLES)
                if not scheduled:
                    self._condition.wait(timeout=0.2)

    def _complete_check(self, candidate: ProviderProxyCandidate, generation: int, future) -> None:
        try:
            proxy_exit = str(future.result() or "").strip()
        except Exception as exc:
            proxy_exit = f"检测失败: {exc}"
        actual_country = proxy_exit_country(proxy_exit)
        passed = not _proxy_exit_failed_text(proxy_exit) and actual_country == candidate.region
        accepted = dataclasses.replace(candidate, proxy_exit=proxy_exit)
        role = candidate.role
        with self._condition:
            self._inflight[role] = max(0, self._inflight[role] - 1)
            if generation == self._generation[role] and self._configs[role].enabled:
                if passed and len(self._ready[role]) < self.target_stock:
                    self._ready[role].append(accepted)
                    self._failure_counts[role] = 0
                    self._next_allowed[role] = 0.0
                    try:
                        self.validated_callback(accepted)
                    except Exception:
                        pass
                elif not passed:
                    failures = self._failure_counts[role] + 1
                    self._failure_counts[role] = failures
                    backoff = PROVIDER_PROXY_BACKOFF_SECONDS[min(failures - 1, len(PROVIDER_PROXY_BACKOFF_SECONDS) - 1)]
                    self._next_allowed[role] = time.monotonic() + backoff
                if len(self._ready[role]) >= self.target_stock:
                    self._refilling[role] = False
            self._condition.notify_all()
        self._publish_status(role)


class ToolTip:
    def __init__(self, widget, text: str, delay_ms: int = TOOLTIP_DELAY_MS):
        self.widget = widget
        self.text = str(text or "").strip()
        self.delay_ms = delay_ms
        self._after_id = None
        self._window = None
        if self.text:
            widget.bind("<Enter>", self._schedule, add="+")
            widget.bind("<Leave>", self._hide, add="+")
            widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._window or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except Exception:
            return
        window = Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = Label(
            window,
            text=self.text,
            justify="left",
            background="#fff7d6",
            foreground="#111827",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=5,
            wraplength=420,
            font=(UI_FONT_FAMILY, UI_FONT_SIZE),
        )
        label.pack()
        self._window = window

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None


@dataclasses.dataclass
class LogRecord:
    seq: int
    time_text: str
    message: str
    email: str = ""
    scope: str = "global"


@dataclasses.dataclass
class DeviceFingerprint:
    user_agent: str
    locale: str
    languages: list[str]
    timezone: str
    viewport_width: int
    viewport_height: int
    screen_width: int
    screen_height: int
    outer_width: int
    outer_height: int
    device_scale_factor: float
    hardware_concurrency: int
    device_memory: int
    platform: str
    vendor: str = "Google Inc."
    max_touch_points: int = 0

    @property
    def accept_language(self) -> str:
        if not self.languages:
            return self.locale
        return ",".join([self.languages[0], *[f"{lang};q={max(0.5, 0.9 - i * 0.1):.1f}" for i, lang in enumerate(self.languages[1:], start=0)]])

    @property
    def chrome_major(self) -> str:
        match = re.search(r"Chrome/(\d+)", self.user_agent)
        return match.group(1) if match else "146"

    @property
    def chrome_full(self) -> str:
        match = re.search(r"Chrome/([\d.]+)", self.user_agent)
        return match.group(1) if match else "146.0.0.0"


def generate_fingerprint(profiles: list[dict] | None = None) -> DeviceFingerprint:
    profile = random.choice(profiles or DEVICE_PROFILES)
    viewport = random.choice([
        (1280, 720, 1280, 720, 1),
        (1365, 768, 1366, 768, 1),
        (1440, 900, 1440, 900, 1),
        (1536, 864, 1536, 864, 1.25),
        (1600, 900, 1600, 900, 1),
        (1920, 1080, 1920, 1080, 1),
    ])
    major = random.randint(134, 146)
    build = random.randint(6000, 9999)
    patch = random.randint(50, 220)
    user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.{build}.{patch} Safari/537.36"
    return DeviceFingerprint(
        user_agent=user_agent,
        locale=profile["locale"],
        languages=list(profile["languages"]),
        timezone=profile["timezone"],
        viewport_width=viewport[0],
        viewport_height=viewport[1],
        screen_width=viewport[2],
        screen_height=viewport[3],
        outer_width=viewport[0] + random.randint(8, 16),
        outer_height=viewport[1] + random.randint(72, 96),
        device_scale_factor=viewport[4],
        hardware_concurrency=random.choice([4, 6, 8, 8, 12, 16]),
        device_memory=random.choice([4, 8, 8, 16]),
        platform="Win32",
    )


def generate_register_fingerprint() -> DeviceFingerprint:
    return generate_fingerprint(REGISTER_DEVICE_PROFILES)


def generate_team_fingerprint() -> DeviceFingerprint:
    return generate_fingerprint(TEAM_DEVICE_PROFILES)


def generate_payment_fingerprint() -> DeviceFingerprint:
    return generate_fingerprint(PAYMENT_DEVICE_PROFILES)


def generate_fingerprint_for_exit(exit_info: ProxyHealthResult) -> DeviceFingerprint:
    if not exit_info.success or not exit_info.country:
        raise ValueError("代理出口信息不可用，无法生成匹配指纹")
    locale = COUNTRY_BROWSER_LOCALE.get(exit_info.country, "en-US")
    primary_language = locale.split("-", 1)[0]
    profile = {
        "locale": locale,
        "languages": [locale, primary_language] if locale != primary_language else [locale],
        "timezone": exit_info.timezone or "UTC",
    }
    return generate_fingerprint([profile])


def generate_team_email() -> str:
    return f"{secrets.token_hex(6)}@{TEAM_EMAIL_DOMAIN}"


def parse_account_line(line: str) -> MailAccount:
    parts = [part.strip() for part in str(line or "").strip().split("----")]
    if len(parts) < 4:
        raise ValueError("格式错误，应为 email----password----client_id----refresh_token")
    email_addr, password, client_id, refresh_token = parts[:4]
    if not email_addr or not client_id or not refresh_token:
        raise ValueError("email / client_id / refresh_token 不能为空")
    extras = extract_account_extras(parts[4:])
    openai_rt = extras["openai_rt"]
    return MailAccount(
        email=email_addr,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
        raw="----".join([email_addr, password, client_id, refresh_token]),
        account_type=str(extras.get("account_type") or ("plus" if openai_rt else "free")),
        status="已绑定手机号" if openai_rt else "待获取RT" if extras["auth_phone_number"] and extras["auth_phone_sms_url"] else "",
        openai_rt=openai_rt,
        auth_phone_number=extras["auth_phone_number"],
        auth_phone_sms_url=extras["auth_phone_sms_url"],
    )


def extract_account_extras(extra_parts: list[str]) -> dict:
    result = {"openai_rt": "", "auth_phone_number": "", "auth_phone_sms_url": "", "account_type": ""}
    for raw_part in extra_parts:
        part = str(raw_part or "").strip()
        if not part:
            continue
        lower = part.lower()
        if lower.startswith(("rt_token=", "openai_rt=")):
            result["openai_rt"] = part.split("=", 1)[1].strip()
            continue
        if lower.startswith(("auth_phone=", "auth_phone_number=", "phone=")):
            result["auth_phone_number"] = part.split("=", 1)[1].strip()
            continue
        if lower.startswith(("auth_phone_sms_url=", "auth_sms_url=", "phone_sms_url=", "sms_url=")):
            result["auth_phone_sms_url"] = part.split("=", 1)[1].strip()
            continue
        if lower.startswith(("account_type=", "type=")):
            account_type = part.split("=", 1)[1].strip().lower()
            if account_type in {"free", "plus", "team"}:
                result["account_type"] = account_type
            continue
        inline_phone = re.match(r"^([+\d][\d\s().-]*)(https?://\S+)$", part)
        if inline_phone:
            result["auth_phone_number"] = result["auth_phone_number"] or inline_phone.group(1).strip()
            result["auth_phone_sms_url"] = result["auth_phone_sms_url"] or inline_phone.group(2).strip()
            continue
        if not result["auth_phone_number"] and re.fullmatch(r"[+\d][\d\s().-]{5,}", part):
            result["auth_phone_number"] = part
            continue
        if not result["auth_phone_sms_url"] and re.match(r"https?://\S+$", part):
            result["auth_phone_sms_url"] = part
            continue
    return result


def extract_rt_token(extra_parts: list[str]) -> str:
    return str(extract_account_extras(extra_parts).get("openai_rt") or "")


def account_to_dict(account: MailAccount) -> dict:
    raw = account.raw
    if not raw and account.client_id and account.refresh_token:
        raw = "----".join([account.email, account.password, account.client_id, account.refresh_token])
    return {
        "email": account.email,
        "password": account.password,
        "client_id": account.client_id,
        "refresh_token": account.refresh_token,
        "raw": raw,
        "account_type": account.account_type,
        "status": account.status,
        "openai_rt": account.openai_rt,
        "auth_phone_number": account.auth_phone_number,
        "auth_phone_sms_url": account.auth_phone_sms_url,
        "group": account.group or ACCOUNT_DEFAULT_GROUP,
    }


def account_from_dict(value: dict) -> MailAccount:
    raw_value = str(value.get("raw") or "")
    if raw_value:
        try:
            account = parse_account_line(raw_value)
            account.account_type = str(value.get("account_type", account.account_type) or "free")
            account.status = str(value.get("status", account.status) or "")
            account.openai_rt = str(value.get("openai_rt", account.openai_rt) or account.openai_rt)
            account.auth_phone_number = str(value.get("auth_phone_number", account.auth_phone_number) or account.auth_phone_number)
            account.auth_phone_sms_url = str(value.get("auth_phone_sms_url", account.auth_phone_sms_url) or account.auth_phone_sms_url)
            account.group = str(value.get("group") or ACCOUNT_DEFAULT_GROUP)
            return account
        except Exception:
            pass
    email_addr = str(value.get("email", "")).strip()
    password = str(value.get("password", ""))
    client_id = str(value.get("client_id", "")).strip()
    refresh_token = str(value.get("refresh_token", "")).strip()
    raw = raw_value if client_id and refresh_token else ""
    if not raw and email_addr and client_id and refresh_token:
        raw = "----".join([email_addr, password, client_id, refresh_token])
    account = MailAccount(
        email=email_addr,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
        raw=raw,
        account_type=str(value.get("account_type", "free") or "free"),
        status=str(value.get("status", "") or ""),
        openai_rt=str(value.get("openai_rt", "") or ""),
        auth_phone_number=str(value.get("auth_phone_number", "") or ""),
        auth_phone_sms_url=str(value.get("auth_phone_sms_url", "") or ""),
        group=str(value.get("group") or ACCOUNT_DEFAULT_GROUP),
    )
    return account


def account_export_line(account: MailAccount, name_prefix: str = "") -> str:
    line = account.raw or "----".join([account.email, account.password, account.client_id, account.refresh_token]).rstrip("-")
    if not line:
        line = account.email
    prefix = str(name_prefix or "").strip()
    if prefix:
        parts = line.split("----", 1)
        if parts:
            parts[0] = f"({prefix}){parts[0]}"
            line = "----".join(parts)
    if account.openai_rt and "----rt_token=" not in line:
        line = f"{line}----rt_token={account.openai_rt}"
    if account.auth_phone_number and "----auth_phone=" not in line:
        line = f"{line}----auth_phone={account.auth_phone_number}"
    if account.auth_phone_sms_url and "----auth_phone_sms_url=" not in line:
        line = f"{line}----auth_phone_sms_url={account.auth_phone_sms_url}"
    return line


def phone_to_dict(phone: PhoneEntry) -> dict:
    return dataclasses.asdict(phone)


def phone_from_dict(value: dict) -> PhoneEntry:
    return PhoneEntry(
        number=str(value.get("number", "")).strip(),
        sms_url=str(value.get("sms_url", "")).strip(),
        status=str(value.get("status", "可用") or "可用"),
        last_code=str(value.get("last_code", "") or ""),
        last_error=str(value.get("last_error", "") or ""),
        receive_count=max(0, int(value.get("receive_count", 0) or 0)),
    )


def parse_phone_line(line: str) -> PhoneEntry:
    text = str(line or "").strip()
    if "----" in text:
        parts = [part.strip() for part in text.split("----")]
        if len(parts) >= 2 and re.fullmatch(r"\+\d+", parts[0]) and re.match(r"https?://\S+$", parts[1]):
            return PhoneEntry(number=parts[0], sms_url=parts[1])
    match = re.match(r"^(\+\d+)\s*(https?://\S+)\s*$", text)
    if not match:
        raise ValueError("格式错误，应为 +手机号https://短信链接 或 +手机号----https://短信链接")
    return PhoneEntry(number=match.group(1), sms_url=match.group(2))


def parse_paypal_phone_line(line: str) -> PhoneEntry:
    text = str(line or "").strip()
    if "----" in text:
        number, sms_url = [part.strip() for part in text.split("----", 1)]
        if number and re.match(r"https?://\S+$", sms_url):
            return PhoneEntry(number=number, sms_url=sms_url)
    match = re.match(r"^([+\d][\d\s().-]*)\s*(https?://\S+)\s*$", text)
    if not match:
        raise ValueError("格式错误，应为 手机号----https://接码链接")
    return PhoneEntry(number=match.group(1).strip(), sms_url=match.group(2).strip())


def normalize_us_phone_for_form(phone_number: str) -> str:
    digits = re.sub(r"\D+", "", str(phone_number or ""))
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits


def payment_card_to_dict(card: PaymentCard) -> dict:
    return dataclasses.asdict(card)


def payment_card_from_dict(value: dict) -> PaymentCard:
    return PaymentCard(
        card=str(value.get("card", "")).strip(),
        month=str(value.get("month", "")).strip(),
        year=str(value.get("year", "")).strip(),
        cvv=str(value.get("cvv", "")).strip(),
        status=str(value.get("status", "未用") or "未用"),
    )


class StateStore:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.data_dir = STATE_DATA_DIR
        self.session_dir = STATE_SESSION_DIR
        self.loaded_legacy = False
        self.missing_session_files = False
        self.warnings: list[str] = []
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: tuple[int, dict, set[str] | None] | None = None
        self._save_thread: threading.Thread | None = None
        self._version = 0
        self._latest_written_version = 0
        self._legacy_backup_done = False

    def load(self) -> dict:
        self.loaded_legacy = False
        self.missing_session_files = False
        self.warnings = []
        if not self.state_file.exists():
            return {}
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if int(data.get("schema_version") or 1) >= STATE_SCHEMA_VERSION:
            data["session_results"] = self._load_split_sessions(data.get("session_results", {}))
            return data
        self.loaded_legacy = True
        if not isinstance(data.get("session_results"), dict):
            data["session_results"] = {}
        return data

    def save(self, snapshot: dict, dirty_session_emails: set[str] | None, flush: bool = False) -> None:
        with self._pending_lock:
            self._version += 1
            version = self._version
            if flush:
                self._pending = None
            else:
                pending_dirty = dirty_session_emails
                if self._pending:
                    _old_version, _old_snapshot, old_dirty = self._pending
                    if old_dirty is None or dirty_session_emails is None:
                        pending_dirty = None
                    else:
                        pending_dirty = set(old_dirty) | set(dirty_session_emails)
                self._pending = (version, snapshot, pending_dirty)
                if not self._save_thread or not self._save_thread.is_alive():
                    self._save_thread = threading.Thread(target=self._save_worker, daemon=True)
                    self._save_thread.start()
                return
        self._write_if_current(version, snapshot, dirty_session_emails)

    def _save_worker(self) -> None:
        while True:
            time.sleep(STATE_SAVE_DEBOUNCE_SECONDS)
            with self._pending_lock:
                pending = self._pending
                self._pending = None
            if not pending:
                with self._pending_lock:
                    if self._pending is None:
                        self._save_thread = None
                        return
                    continue
            version, snapshot, dirty_session_emails = pending
            self._write_if_current(version, snapshot, dirty_session_emails)
            with self._pending_lock:
                if self._pending is None:
                    self._save_thread = None
                    return

    def _write_if_current(self, version: int, snapshot: dict, dirty_session_emails: set[str] | None) -> None:
        with self._write_lock:
            if version < self._latest_written_version:
                return
            self._write_snapshot(snapshot, dirty_session_emails)
            self._latest_written_version = version

    def _load_split_sessions(self, raw_index) -> dict:
        if not isinstance(raw_index, dict):
            return {}
        sessions = {}
        for email_addr, item in raw_index.items():
            email_key = str(email_addr or "").strip()
            if not email_key:
                continue
            rel_path = ""
            if isinstance(item, dict):
                rel_path = str(item.get("session_file") or "").strip()
            if not rel_path:
                rel_path = self._session_rel_path(email_key)
            session_path = (self.data_dir / rel_path).resolve()
            try:
                session_path.relative_to(self.data_dir.resolve())
            except Exception:
                self.warnings.append(f"Session 文件路径越界，已跳过: {email_key}")
                continue
            if not session_path.exists():
                self.missing_session_files = True
                self.warnings.append(f"Session 文件缺失，已跳过: {email_key}")
                continue
            try:
                payload = json.loads(session_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
                    payload = payload["payload"]
                if isinstance(payload, dict):
                    sessions[email_key] = payload
            except Exception as exc:
                self.warnings.append(f"Session 文件读取失败，已跳过 {email_key}: {exc}")
        return sessions

    def _write_snapshot(self, snapshot: dict, dirty_session_emails: set[str] | None) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        if self.loaded_legacy and not self._legacy_backup_done and self.state_file.exists():
            backup = self.state_file.with_name(
                f"state.backup-before-state-split-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            )
            try:
                shutil.copy2(self.state_file, backup)
                self._legacy_backup_done = True
            except Exception:
                pass

        data = dict(snapshot)
        sessions = data.pop("session_results", {})
        if not isinstance(sessions, dict):
            sessions = {}
        dirty_all = dirty_session_emails is None
        dirty_keys = {str(email or "").strip().lower() for email in (dirty_session_emails or set()) if str(email or "").strip()}
        session_index = {}
        for email_addr, payload in sessions.items():
            email_key = str(email_addr or "").strip()
            if not email_key or not isinstance(payload, dict):
                continue
            rel_path = self._session_rel_path(email_key)
            session_path = self.data_dir / rel_path
            session_index[email_key] = {
                "session_file": rel_path,
                "has_session_json": bool(str(payload.get("session_json") or "")),
                "has_storage_state_json": bool(str(payload.get("storage_state_json") or "")),
                "payment_link_type": str(payload.get("payment_link_type") or ""),
                "updated_at": data.get("updated_at", ""),
            }
            if dirty_all or email_key.lower() in dirty_keys or not session_path.exists():
                session_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = session_path.with_suffix(".json.tmp")
                session_payload = {"email": email_key, "updated_at": data.get("updated_at", ""), "payload": payload}
                tmp.write_text(json.dumps(session_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                tmp.replace(session_path)

        data["schema_version"] = STATE_SCHEMA_VERSION
        data["session_results"] = session_index
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_file)

    def _session_rel_path(self, email_addr: str) -> str:
        digest = hashlib.sha256(str(email_addr or "").strip().lower().encode("utf-8")).hexdigest()[:24]
        return f"sessions/{digest}.json"


def parse_payment_card_line(line: str) -> PaymentCard:
    parts = [part.strip() for part in str(line or "").strip().split("|")]
    if len(parts) != 4:
        raise ValueError("格式错误，应为 卡号|月|年|CVV")
    card, month, year, cvv = parts
    if not re.fullmatch(r"\d{12,19}", card) or not re.fullmatch(r"\d{1,2}", month) or not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"\d{3,4}", cvv):
        raise ValueError("卡号/月/年/CVV 格式不正确")
    return PaymentCard(card=card, month=str(int(month)), year=year, cvv=cvv)


def replace_paypal_card_head(paypal_card: str, payment_card: PaymentCard) -> str:
    parts = str(paypal_card or "").split("----")
    if len(parts) < 7:
        raise ValueError("PayPal 卡信息格式错误，需要至少 7 段 ---- 分隔")
    parts[0] = payment_card.card
    parts[1] = f"{payment_card.year}/{payment_card.month}"
    parts[2] = payment_card.cvv
    return "----".join(parts)


def normalize_proxy_url(value: str, default_scheme: str = "http") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"{default_scheme}://{text}"
    return text


def random_proxy_sid(length: int = 10) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def randomize_proxy_sid(proxy_url: str) -> str:
    text = str(proxy_url or "").strip()
    if not text:
        return ""
    sid = random_proxy_sid()
    parsed = urlsplit(text)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.lower() == "sid" for key, _value in query_pairs):
        query = urlencode([(key, sid if key.lower() == "sid" else value) for key, value in query_pairs])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        new_userinfo = re.sub(r"(?i)(sid[-_=])([^-:@;&/?]+)", lambda m: f"{m.group(1)}{sid}", userinfo, count=1)
        if new_userinfo != userinfo:
            return urlunsplit((parsed.scheme, f"{new_userinfo}@{host}", parsed.path, parsed.query, parsed.fragment))

    new_text = re.sub(r"(?i)(sid[-_=])([^-:@;&/?]+)", lambda m: f"{m.group(1)}{sid}", text, count=1)
    return new_text


def mask_proxy_url(proxy_url: str) -> str:
    text = str(proxy_url or "").strip()
    if not text:
        return "直连"
    return text


def format_proxy_chain_label(local_proxy: str = "", dynamic_proxy: str = "") -> str:
    local_text = str(local_proxy or "").strip() or "直连"
    dynamic_text = str(dynamic_proxy or "").strip() or "直连"
    return f"本地={local_text:<30} -> 动态={dynamic_text}"


def display_width(value: str) -> int:
    width = 0
    for char in str(value or ""):
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def pad_display(value: str, target_width: int) -> str:
    text = str(value or "")
    return text + " " * max(0, target_width - display_width(text))


def format_named_proxy_log(label: str, proxy: ProxyConfig | str, source: str = "") -> str:
    name = str(label or "").strip()
    if source:
        name = f"{name}({source})"
    proxy_text = proxy.label if hasattr(proxy, "label") else str(proxy or "")
    return f"[代理] {pad_display(name, 32)}: {proxy_text}"


def find_access_token(value) -> str:
    if isinstance(value, dict):
        for key in ("accessToken", "access_token", "token"):
            token = str(value.get(key) or "").strip()
            if token:
                return token
        for item in value.values():
            token = find_access_token(item)
            if token:
                return token
    if isinstance(value, list):
        for item in value:
            token = find_access_token(item)
            if token:
                return token
    return ""


def extract_access_token_from_session_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("Bearer "):
        return raw.split(None, 1)[1].strip()
    try:
        return find_access_token(json.loads(raw))
    except Exception:
        pass
    match = re.search(r'"(?:accessToken|access_token|token)"\s*:\s*"([^"]+)"', raw)
    if match:
        return match.group(1).strip()
    return raw if raw.count(".") >= 2 and len(raw) > 80 else ""


def random_urlsafe_string(length: int) -> str:
    token = secrets.token_urlsafe(max(1, length))
    return token[:length]


def pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def decode_jwt_payload(token: str) -> dict:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1].replace("-", "+").replace("_", "/")
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.b64decode(payload).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_nested_record(payload: dict, key: str) -> dict:
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}


def first_non_empty(*values) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def currency_for_country(country: str) -> str:
    return COUNTRY_CURRENCY.get(str(country or "").upper(), "USD")


def normalize_opll_country(country: str) -> str:
    country = str(country or "").strip().upper()
    return country if country in OPENAI_SUPPORTED_COUNTRY_CODES else "US"


def locale_parts(locale: str = "en") -> tuple[str, str]:
    return LOCALE_MAP.get(str(locale or "").strip(), LOCALE_MAP["en"])


def opll_extract_processor_entity(data) -> str:
    if not isinstance(data, dict):
        return ""
    direct = data.get("processor_entity") or data.get("processorEntity")
    if direct:
        return str(direct).strip()
    for key in ("checkout_session", "session", "checkout", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = opll_extract_processor_entity(nested)
            if found:
                return found
    return ""


def opll_extract_stripe_publishable_key(data) -> str:
    if isinstance(data, str):
        match = re.search(r"pk_live_[A-Za-z0-9]+", data)
        return match.group(0) if match else ""
    if isinstance(data, dict):
        for key in ("stripe_publishable_key", "publishable_key", "publishableKey", "stripePublishableKey", "key"):
            found = opll_extract_stripe_publishable_key(data.get(key))
            if found:
                return found
        for item in data.values():
            found = opll_extract_stripe_publishable_key(item)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = opll_extract_stripe_publishable_key(item)
            if found:
                return found
    return ""


def opll_processor_entity_for_country(country: str, processor_entity: str = "") -> str:
    entity = str(processor_entity or "").strip()
    if entity:
        return entity
    return "openai_llc" if str(country or "").upper() == "US" else "openai_ie"


def opll_chatgpt_success_return_url(cs_id: str, country: str, processor_entity: str = "") -> str:
    entity = opll_processor_entity_for_country(country, processor_entity)
    return f"https://chatgpt.com/checkout/verify?stripe_session_id={cs_id}&processor_entity={entity}&plan_type=plus"


def opll_to_openai_pay_url(stripe_hosted_url: str) -> str:
    url = str(stripe_hosted_url or "").strip()
    if not url:
        return ""
    if url.startswith("https://checkout.stripe.com"):
        return "https://pay.openai.com" + url[len("https://checkout.stripe.com"):]
    parsed = urlsplit(url)
    if parsed.netloc.lower() == "checkout.stripe.com":
        return urlunsplit((parsed.scheme or "https", "pay.openai.com", parsed.path, parsed.query, parsed.fragment))
    return url


def opll_stripe_checkout_long_url(cs_id: str, country: str, processor_entity: str = "") -> str:
    return (
        f"https://checkout.stripe.com/c/pay/{cs_id}"
        f"?returned_from_redirect=true&ui_mode=custom&return_url="
        f"{quote(opll_chatgpt_success_return_url(cs_id, country, processor_entity), safe='')}"
    )


def opll_stripe_confirm_return_url(cs_id: str, checkout: dict, stripe_hosted_url: str) -> str:
    hosted_url = opll_to_openai_pay_url(stripe_hosted_url) or opll_stripe_checkout_long_url(
        cs_id,
        checkout["billing_country"],
        checkout.get("processor_entity", ""),
    )
    if "pay.openai.com/" in hosted_url or "checkout.stripe.com/" in hosted_url:
        parsed = urlsplit(hosted_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault(
            "success_return_url",
            opll_chatgpt_success_return_url(
                cs_id,
                checkout["billing_country"],
                checkout.get("processor_entity", ""),
            ),
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return hosted_url


def opll_new_http_session() -> requests.Session:
    if CurlCffiSession is not None:
        session = CurlCffiSession(impersonate="chrome136")  # type: ignore[assignment]
    else:
        session = requests.Session()
    if hasattr(session, "trust_env"):
        session.trust_env = False
    return session


def opll_build_chatgpt_session(access_token: str, proxy_url: str = "") -> requests.Session:
    token = extract_access_token_from_session_text(access_token) or str(access_token or "").strip()
    if not token:
        raise RuntimeError("当前账号没有 Access Token，请先注册并获取 Session 信息")
    device_id = str(uuid.uuid4())
    session = opll_new_http_session()
    session.headers.update({
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {token}",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
        "oai-language": "en-US",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "Cookie": f"oai-did={device_id}",
    })
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return session


def opll_is_non_retryable_link_error(exc: Exception | str) -> bool:
    text = str(exc or "").lower()
    non_retryable_markers = (
        "billing country must match request country",
        "confirm_error_reason=payment_method_types_mismatch",
        "token_invalidated",
        "authentication token has been invalidated",
    )
    return any(marker in text for marker in non_retryable_markers)


def opll_create_checkout(access_token: str, country: str, currency: str, proxy_url: str = "") -> dict:
    country = normalize_opll_country(country)
    currency = currency_for_country(country)
    session = opll_build_chatgpt_session(access_token, proxy_url)
    response = session.post(
        "https://chatgpt.com/backend-api/payments/checkout",
        json={
            "entry_point": "all_plans_pricing_modal",
            "plan_name": "chatgptplusplan",
            "billing_details": {"country": country, "currency": currency},
            "promo_campaign": {"promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False},
            "checkout_ui_mode": "custom",
        },
        headers={
            "Referer": "https://chatgpt.com/",
            "x-openai-target-path": "/backend-api/payments/checkout",
            "x-openai-target-route": "/backend-api/payments/checkout",
        },
        timeout=PAY_LONG_LINK_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"checkout create failed: HTTP {response.status_code} {response.text[:500]}")
    data = response.json() or {}
    cs_id = data.get("checkout_session_id") or data.get("session_id") or data.get("id")
    if not cs_id or not str(cs_id).startswith("cs_"):
        raise RuntimeError(f"checkout response missing cs_id: {str(data)[:500]}")
    return {
        "cs_id": str(cs_id),
        "processor_entity": opll_extract_processor_entity(data),
        "stripe_publishable_key": opll_extract_stripe_publishable_key(data),
        "billing_country": country,
        "currency": currency,
    }


def opll_stripe_key_for_checkout(checkout: dict | None = None) -> str:
    return str((checkout or {}).get("stripe_publishable_key") or "").strip() or DEFAULT_STRIPE_PK


def opll_stripe_init(cs_id: str, country: str, currency: str, proxy_url: str = "", payment_locale: str = "en", stripe: requests.Session | None = None, ctx: dict | None = None, checkout: dict | None = None) -> dict:
    browser_locale, elements_locale = locale_parts(payment_locale)
    stripe_pk = opll_stripe_key_for_checkout(checkout)
    stripe_session = stripe or requests.Session()
    if stripe is None:
        stripe_session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        if hasattr(stripe_session, "trust_env"):
            stripe_session.trust_env = False
        if proxy_url:
            stripe_session.proxies.update({"http": proxy_url, "https": proxy_url})
    response = stripe_session.post(
        f"https://api.stripe.com/v1/payment_pages/{cs_id}/init",
        data={
            "browser_locale": browser_locale,
            "browser_timezone": "Asia/Shanghai",
            "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
            "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
            "elements_session_client[elements_init_source]": "custom_checkout",
            "elements_session_client[referrer_host]": "chatgpt.com",
            "elements_session_client[stripe_js_id]": str((ctx or {}).get("stripe_js_id") or uuid.uuid4()),
            "elements_session_client[locale]": elements_locale,
            "elements_session_client[is_aggregation_expected]": "false",
            "elements_options_client[saved_payment_method][enable_save]": "never",
            "elements_options_client[saved_payment_method][enable_redisplay]": "never",
            "key": stripe_pk,
            "_stripe_version": STRIPE_VERSION_FULL,
        },
        timeout=PAY_LONG_LINK_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"stripe init failed: HTTP {response.status_code} {response.text[:500]}")
    return response.json() or {}


def opll_build_stripe_session(proxy_url: str = "") -> requests.Session:
    session = opll_new_http_session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return session


def opll_stripe_context(init_payload: dict, payment_locale: str = "en", ctx: dict | None = None) -> dict:
    _browser_locale, elements_locale = locale_parts(payment_locale)
    base = ctx or {}
    return {
        "stripe_js_id": str(base.get("stripe_js_id") or uuid.uuid4()),
        "elements_session_id": str(base.get("elements_session_id") or f"elements_session_{uuid.uuid4().hex[:11]}"),
        "elements_session_config_id": str(init_payload.get("config_id") or base.get("elements_session_config_id") or uuid.uuid4()),
        "config_id": str(init_payload.get("config_id") or ""),
        "init_checksum": str(init_payload.get("init_checksum") or ""),
        "checkout_amount": str(opll_expected_amount(init_payload)),
        "currency": str(init_payload.get("currency") or "").lower(),
        "locale": elements_locale,
        "runtime_version": str(base.get("runtime_version") or DEFAULT_STRIPE_RUNTIME_VERSION),
    }


def opll_expected_amount(init_payload: dict) -> str:
    return opll_stripe_amount_info(init_payload)[0]


def opll_stripe_amount_info(init_payload) -> tuple[str, str]:
    if not isinstance(init_payload, dict):
        return "0", "missing_payload"
    total_summary = init_payload.get("total_summary") if isinstance(init_payload, dict) else None
    if isinstance(total_summary, dict) and total_summary.get("due") is not None:
        return str(total_summary.get("due")), "total_summary.due"
    invoice = init_payload.get("invoice") if isinstance(init_payload, dict) else None
    if isinstance(invoice, dict) and invoice.get("amount_due") is not None:
        return str(invoice.get("amount_due")), "invoice.amount_due"
    line_items = init_payload.get("line_items") if isinstance(init_payload, dict) else None
    if isinstance(line_items, list):
        total = 0
        found = False
        for item in line_items:
            if isinstance(item, dict) and item.get("amount") is not None:
                try:
                    total += int(item.get("amount") or 0)
                    found = True
                except Exception:
                    pass
        if found:
            return str(total), "line_items.amount"
    return "0", "fallback_zero"


class AmountMismatchError(RuntimeError):
    def __init__(self, target_amount: str, actual_amount: str, stripe_amount_source: str):
        self.target_amount = target_amount
        self.actual_amount = actual_amount
        self.stripe_amount_source = stripe_amount_source
        super().__init__(f"金额不匹配: 目标 {target_amount}, 实际 {actual_amount}")


class ProxyExitCheckError(RuntimeError):
    def __init__(self, message: str, status: str = "代理检测失败"):
        self.status = status
        super().__init__(message)


LINK_PROXY_LOG_STEPS = (
    ("create", "第一步"),
    ("followup", "后续"),
    ("approve", "Approve"),
)
LINK_PROXY_LOG_PADDING = {
    "第一步": "    ",
    "后续": "      ",
    "Approve": "   ",
}


def _format_aligned_proxy_log(label: str, proxy_label: str) -> str:
    label = str(label or "").strip()
    proxy_text = str(proxy_label or "").strip() or "直连"
    return f"代理[{label}]{LINK_PROXY_LOG_PADDING.get(label, ' ')}\t: {proxy_text}"


def _format_aligned_exit_log(label: str, proxy_exit: str) -> str:
    label = str(label or "").strip()
    exit_text = str(proxy_exit or "").strip() or "未记录"
    return f"出口[{label}]{LINK_PROXY_LOG_PADDING.get(label, ' ')}\t: {exit_text}"


def _log_link_proxy_group(log_func, create_proxy, followup_proxy, approve_proxy, action_text: str = "") -> None:
    prefix = f"{str(action_text).strip()}，" if str(action_text or "").strip() else ""
    for label, proxy in (
        ("第一步", create_proxy),
        ("后续", followup_proxy),
        ("Approve", approve_proxy),
    ):
        proxy_label = getattr(proxy, "label", str(proxy or ""))
        log_func(f"{prefix}{_format_aligned_proxy_log(label, proxy_label)}")


def _proxy_exit_failed_text(proxy_exit: str) -> bool:
    return str(proxy_exit or "").strip().startswith("检测失败")


def _detect_link_proxy_exits_concurrently(detect_proxy_exit, log_func, create_proxy_url: str, followup_proxy_url: str, approve_proxy_url: str, require_japan: bool, proxy_exit_is_japan, cached_exits: dict[str, str] | None = None) -> dict[str, str]:
    proxy_urls = {
        "create": create_proxy_url,
        "followup": followup_proxy_url,
        "approve": approve_proxy_url,
    }
    cached_exits = cached_exits or {}
    exits: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="proxy-exit-check") as executor:
        futures = {
            key: executor.submit(detect_proxy_exit, proxy_urls.get(key, ""))
            for key, _label in LINK_PROXY_LOG_STEPS
            if key not in cached_exits
        }
        for key, _label in LINK_PROXY_LOG_STEPS:
            if key in cached_exits:
                exits[key] = cached_exits[key]
            else:
                try:
                    exits[key] = futures[key].result()
                except Exception as exc:
                    exits[key] = f"检测失败: {exc}"

    for key, label in LINK_PROXY_LOG_STEPS:
        log_func(_format_aligned_exit_log(label, exits.get(key, "")))

    for key, label in LINK_PROXY_LOG_STEPS:
        proxy_exit = exits.get(key, "")
        if _proxy_exit_failed_text(proxy_exit):
            raise ProxyExitCheckError(f"{label}代理出口检测失败，已放弃当前代理组: {proxy_exit}", "代理检测失败")

    if require_japan and not proxy_exit_is_japan(exits.get("create", "")):
        raise ProxyExitCheckError(f"第一步代理出口不是日本，已放弃当前代理组: {exits.get('create', '')}", "代理非日本")
    return exits


def opll_apply_amount_check(result: dict, target_amount: str = "") -> dict:
    target = str(target_amount).strip()
    actual = str(result.get("stripe_amount") or "").strip()
    source = str(result.get("stripe_amount_source") or "").strip()
    result["target_amount"] = target
    if not target:
        result["amount_check"] = "skipped"
        return result
    if actual != target:
        result["amount_check"] = "failed"
        raise AmountMismatchError(target, actual, source)
    result["amount_check"] = "passed"
    return result


def opll_random_postal_code(pattern: str) -> str:
    result = []
    for char in str(pattern or "#####"):
        if char == "#":
            result.append(str(random.randint(0, 9)))
        elif char == "A":
            result.append(chr(random.randint(ord("A"), ord("Z"))))
        else:
            result.append(char)
    return "".join(result)


def opll_billing_for_country(country: str) -> dict:
    country = normalize_opll_country(country)
    if country == "DE":
        first, last = random.choice(DE_BILLING_NAMES)
        line1, city, state, postal = random.choice(DE_BILLING_STREETS)
    elif country == "GB":
        first, last = random.choice(GB_BILLING_NAMES)
        line1, city, state, postal = random.choice(GB_BILLING_STREETS)
    elif country == "AU":
        first, last = random.choice(AU_BILLING_NAMES)
        line1, city, state, postal = random.choice(AU_BILLING_STREETS)
    elif country == "US":
        first, last = random.choice(US_BILLING_NAMES)
        line1, city, state, postal = random.choice(US_BILLING_STREETS)
    elif country in EXTRA_BILLING_STREETS:
        first, last = random.choice(EXTRA_BILLING_NAMES)
        line1, city, state, postal = random.choice(EXTRA_BILLING_STREETS[country])
    elif country in OPENAI_SUPPORTED_COUNTRY_CODES:
        profile = BILLING_PROFILE_BY_COUNTRY[country]
        first, last = random.choice(EXTRA_BILLING_NAMES)
        line1 = f"{random.randint(10, 999)} {random.choice(profile['street_pool'])}"
        city = random.choice(profile["city_pool"])
        state = country
        postal = opll_random_postal_code(str(profile.get("postal_pattern") or "#####"))
    else:
        raise RuntimeError(f"不支持的账单资料地区: {country}")
    suffix = random.randint(1000, 9999)
    phone_prefix = str(BILLING_PROFILE_BY_COUNTRY.get(country, {}).get("phone_prefix") or COUNTRY_PHONE_PREFIX.get(country, "+1"))
    return {
        "name": f"{first} {last}",
        "email": f"{first.lower()}.{last.lower()}{suffix}@example.com",
        "phone": f"{phone_prefix}{random.randint(100000000, 999999999)}",
        "country": country,
        "line1": line1,
        "city": city,
        "state": state,
        "postal_code": postal,
    }


def opll_random_jp_billing() -> dict:
    suffix = random.randint(1000, 9999)
    first = random.choice(["Haruto", "Yuto", "Sota", "Ren", "Yui", "Hina", "Aoi", "Sakura"])
    last = random.choice(["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto"])
    street, city, state, postal = random.choice([
        ("1-1 Marunouchi", "Chiyoda-ku", "Tokyo", "100-0005"),
        ("2-8-1 Nishi-Shinjuku", "Shinjuku-ku", "Tokyo", "160-0023"),
        ("1-1 Umeda", "Kita-ku Osaka", "Osaka", "530-0001"),
        ("3-1 Minatomirai", "Nishi-ku Yokohama", "Kanagawa", "220-0012"),
    ])
    return {"name": f"{first} {last}", "email": f"{first.lower()}.{last.lower()}{suffix}@example.com", "country": "JP", "line1": street, "city": city, "state": state, "postal_code": postal}


def opll_stripe_create_paypal_method(stripe: requests.Session, cs_id: str, ctx: dict, billing: dict, stripe_pk: str = "", payment_method_type: str = "paypal") -> str:
    runtime_version = str(ctx.get("runtime_version") or DEFAULT_STRIPE_RUNTIME_VERSION)
    payment_method_type = str(payment_method_type or "paypal").strip().lower()
    body = {
        "billing_details[name]": billing.get("name") or "John Doe",
        "billing_details[email]": billing.get("email") or "buyer@example.com",
        "billing_details[phone]": billing.get("phone") or "",
        "billing_details[address][country]": billing.get("country") or "US",
        "billing_details[address][line1]": billing.get("line1") or "3110 Sunset Boulevard",
        "billing_details[address][city]": billing.get("city") or "Los Angeles",
        "billing_details[address][postal_code]": billing.get("postal_code") or "90026",
        "billing_details[address][state]": billing.get("state") or "CA",
        "type": payment_method_type,
        "payment_user_agent": f"stripe.js/{runtime_version}; stripe-js-v3/{runtime_version}; payment-element; deferred-intent",
        "referrer": "https://chatgpt.com",
        "time_on_page": str(random.randint(25000, 55000)),
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
        "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
        "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
        "client_attribution_metadata[elements_session_config_id]": ctx["elements_session_config_id"],
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
        "key": stripe_pk or DEFAULT_STRIPE_PK,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    response = stripe.post("https://api.stripe.com/v1/payment_methods", data=body, timeout=PAY_LONG_LINK_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"stripe payment_methods failed: HTTP {response.status_code} {response.text[:500]}")
    pm_id = str((response.json() or {}).get("id") or "")
    if not pm_id.startswith("pm_"):
        raise RuntimeError(f"stripe payment_methods bad response: {response.text[:300]}")
    return pm_id


def opll_short_error(detail: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(detail or "")).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def opll_stripe_error_summary(prefix: str, response) -> str:
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    extra_fields = error.get("extra_fields") if isinstance(error.get("extra_fields"), dict) else {}
    parts = []
    for label, value in (
        ("code", error.get("code")),
        ("decline_code", error.get("decline_code")),
        ("type", error.get("type")),
        ("message", error.get("message")),
        ("payment_method_type", extra_fields.get("payment_method_type")),
        ("confirm_error_reason", extra_fields.get("confirm_error_reason")),
        ("confirm_error_code", extra_fields.get("confirm_error_code")),
        ("confirm_error_message", extra_fields.get("confirm_error_message")),
    ):
        if value is not None and value != "":
            parts.append(f"{label}={opll_short_error(str(value), 180)}")
    if parts:
        return f"{prefix}: " + ", ".join(parts)
    return f"{prefix}: {opll_short_error(response.text, 500)}"


def opll_is_external_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def opll_is_paypal_url(value: str) -> bool:
    host = (urlsplit(value).netloc or "").lower()
    return host == "paypal.com" or host.endswith(".paypal.com") or host == "paypalobjects.com" or host.endswith(".paypalobjects.com")


def opll_is_paypal_ba_approve_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    if not (host == "paypal.com" or host.endswith(".paypal.com")):
        return False
    path = parsed.path.rstrip("/").lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return path == "/agreements/approve" and bool(str(query.get("ba_token") or "").strip())


def opll_is_paypal_success_url(value: str) -> bool:
    if opll_is_paypal_ba_approve_url(value):
        return True
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == "pm-redirects.stripe.com"


def opll_is_ignored_resource_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    ignored_hosts = {"stripe-camo.global.ssl.fastly.net", "files.stripe.com", "q.stripe.com", "js.stripe.com", "m.stripe.network"}
    ignored_suffixes = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".css", ".js", ".woff", ".woff2")
    if host in ignored_hosts or any(host.endswith(f".{item}") for item in ignored_hosts):
        return True
    return path.endswith(ignored_suffixes)


def opll_collect_urls(payload, urls: list[str] | None = None) -> list[str]:
    found = urls if urls is not None else []
    if isinstance(payload, str):
        for match in re.findall(r"https?://[^\s\"'<>]+", payload):
            found.append(match.rstrip("),.;]"))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if key in ("url", "return_url", "redirect_url", "redirect_to_url") and isinstance(value, str) and opll_is_external_url(value):
                found.append(value)
            else:
                opll_collect_urls(value, found)
    elif isinstance(payload, list):
        for item in payload:
            opll_collect_urls(item, found)
    return found


def opll_extract_redirect_to_url(payload) -> str:
    if not isinstance(payload, dict):
        urls = opll_collect_urls(payload)
        return next(
            (item for item in urls if opll_is_paypal_ba_approve_url(item)),
            next((item for item in urls if opll_is_paypal_url(item) and not opll_is_ignored_resource_url(item)), ""),
        )
    next_action = payload.get("next_action")
    if isinstance(next_action, dict) and next_action.get("type") == "redirect_to_url":
        redirect_to_url = next_action.get("redirect_to_url") or {}
        if isinstance(redirect_to_url, dict):
            url = str(redirect_to_url.get("url") or "").strip()
            if url:
                return url
    for key in ("setup_intent", "payment_intent"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = opll_extract_redirect_to_url(nested)
            if found:
                return found
    urls = opll_collect_urls(payload)
    return next(
        (item for item in urls if opll_is_paypal_ba_approve_url(item)),
        next((item for item in urls if opll_is_paypal_url(item) and not opll_is_ignored_resource_url(item)), ""),
    )


def opll_extract_provider_redirect_url(payload) -> str:
    if not isinstance(payload, dict):
        urls = opll_collect_urls(payload)
        return next((item for item in urls if opll_is_external_url(item) and not opll_is_ignored_resource_url(item)), "")
    next_action = payload.get("next_action")
    if isinstance(next_action, dict) and next_action.get("type") == "redirect_to_url":
        redirect_to_url = next_action.get("redirect_to_url") or {}
        if isinstance(redirect_to_url, dict):
            url = str(redirect_to_url.get("url") or "").strip()
            if url:
                return url
    for key in ("setup_intent", "payment_intent"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = opll_extract_provider_redirect_url(nested)
            if found:
                return found
    urls = opll_collect_urls(payload)
    return next((item for item in urls if opll_is_external_url(item) and not opll_is_ignored_resource_url(item)), "")


def opll_first_non_empty(values: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(values.get(key) or "").strip()
        if value:
            return value
    return ""


def opll_submission_attempt_failure_fields(submission) -> dict[str, str]:
    wanted = {"error", "code", "message", "reason", "failure_reason", "decline_code", "failure_code", "failure_message"}
    found: dict[str, str] = {}

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key or "").strip()
                if normalized in wanted and normalized not in found:
                    if isinstance(item, (str, int, float, bool)):
                        text = str(item).strip()
                    elif isinstance(item, dict):
                        text = str(item.get("message") or item.get("code") or item.get("reason") or item.get("type") or "").strip()
                    else:
                        text = ""
                    if text:
                        found[normalized] = text[:240]
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    if isinstance(submission, dict):
        walk(submission)
    return found


def opll_find_submission_attempt(payload) -> dict:
    if isinstance(payload, dict):
        item = payload.get("submission_attempt")
        if isinstance(item, dict):
            return item
        for value in payload.values():
            found = opll_find_submission_attempt(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = opll_find_submission_attempt(value)
            if found:
                return found
    return {}


def opll_submission_attempt_summary(submission: dict) -> str:
    if not submission:
        return "未找到 submission_attempt"
    fields = opll_submission_attempt_failure_fields(submission)
    state = str(submission.get("state") or "未知").strip()
    reason = opll_first_non_empty(fields, "reason", "failure_reason", "decline_code", "failure_code", "code")
    code = opll_first_non_empty(fields, "code", "decline_code", "failure_code")
    message = opll_first_non_empty(fields, "message", "failure_message", "error")
    parts = [f"state={state}"]
    if reason:
        parts.append(f"reason={reason}")
    if code:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    return "，".join(parts)


def opll_stripe_payload_diagnostics(payload, ctx: dict) -> str:
    if not isinstance(payload, dict):
        return f"payload_type={type(payload).__name__}"
    keys = ",".join(sorted(payload.keys())[:12])
    urls = opll_collect_urls(payload)
    paypal_count = sum(1 for item in urls if opll_is_paypal_url(item))
    ba_count = sum(1 for item in urls if opll_is_paypal_ba_approve_url(item))
    ignored_count = sum(1 for item in urls if opll_is_ignored_resource_url(item))
    submission = opll_find_submission_attempt(payload)
    submission_state = str(submission.get("state") or "") if isinstance(submission, dict) else ""
    submission_fields = opll_submission_attempt_failure_fields(submission)
    submission_reason = opll_first_non_empty(submission_fields, "reason", "failure_reason", "decline_code", "failure_code", "code")
    submission_code = opll_first_non_empty(submission_fields, "code", "decline_code", "failure_code")
    submission_message = opll_first_non_empty(submission_fields, "message", "failure_message", "error")
    return (
        f"keys=[{keys}], urls={len(urls)}, paypal_urls={paypal_count}, ba_approve_urls={ba_count}, "
        f"ignored_resource_urls={ignored_count}, submission_attempt={bool(submission)}, submission_state={submission_state or '未知'}, "
        f"submission_reason={submission_reason or '无'}, submission_code={submission_code or '无'}, "
        f"submission_message={submission_message or '无'}, ctx_session={ctx.get('elements_session_id') or ''}"
    )


class OpllStripeRequiresApproval(Exception):
    pass


class OpllChatgptApproveBlocked(Exception):
    pass


OPLL_APPROVE_BURST_RESULTS = {"blocked", "exception"}


def opll_chatgpt_approve(chatgpt: requests.Session, cs_id: str, checkout: dict) -> None:
    entity = opll_processor_entity_for_country(checkout["billing_country"], checkout.get("processor_entity", ""))
    try:
        chatgpt.post(
            "https://chatgpt.com/backend-api/sentinel/ping",
            json={},
            headers={
                "Referer": "https://chatgpt.com/",
                "x-openai-target-path": "/backend-api/sentinel/ping",
                "x-openai-target-route": "/backend-api/sentinel/ping",
            },
            timeout=PAY_LONG_LINK_TIMEOUT,
        )
    except Exception:
        pass
    response = chatgpt.post(
        "https://chatgpt.com/backend-api/payments/checkout/approve",
        json={"checkout_session_id": cs_id, "processor_entity": entity},
        headers={"Referer": f"https://chatgpt.com/checkout/{entity}/{cs_id}", "x-openai-target-path": "/backend-api/payments/checkout/approve", "x-openai-target-route": "/backend-api/payments/checkout/approve"},
        timeout=PAY_LONG_LINK_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"chatgpt approve failed: HTTP {response.status_code} {response.text[:500]}")
    try:
        result = (response.json() or {}).get("result")
    except Exception:
        result = ""
    normalized_result = str(result or "").strip().lower()
    if normalized_result in OPLL_APPROVE_BURST_RESULTS:
        raise OpllChatgptApproveBlocked(f"chatgpt approve retryable result: {normalized_result!r}")
    if result != "approved":
        raise RuntimeError(f"chatgpt approve unexpected result: {result!r}")


def opll_chatgpt_approve_with_retry(access_token: str, cs_id: str, checkout: dict, proxy_url: str = "") -> requests.Session:
    last_error = ""
    for _ in range(3):
        try:
            chatgpt = opll_build_chatgpt_session(access_token, proxy_url)
            opll_chatgpt_approve(chatgpt, cs_id, checkout)
            return chatgpt
        except OpllChatgptApproveBlocked as exc:
            last_error = str(exc)
            break
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    raise RuntimeError(f"ChatGPT approve 连续失败: {last_error}")


def opll_stripe_payment_page_redirect_url(stripe: requests.Session, cs_id: str, stripe_pk: str, payment_locale: str = "en", timeout_seconds: int = 45, ctx: dict | None = None) -> str:
    deadline = time.time() + max(1, timeout_seconds)
    _browser_locale, elements_locale = locale_parts(payment_locale)
    ctx = ctx or {}
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": str(ctx.get("elements_session_id") or f"elements_session_{uuid.uuid4().hex[:11]}"),
        "elements_session_client[stripe_js_id]": str(ctx.get("stripe_js_id") or uuid.uuid4()),
        "elements_session_client[locale]": elements_locale,
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    last_err = ""
    while time.time() < deadline:
        response = stripe.get(
            f"https://api.stripe.com/v1/payment_pages/{cs_id}",
            params=params,
            timeout=PAY_LONG_LINK_TIMEOUT,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            redirect_url = opll_extract_redirect_to_url(payload)
            if redirect_url:
                return redirect_url
            submission = opll_find_submission_attempt(payload)
            if submission.get("state") == "requires_approval":
                raise OpllStripeRequiresApproval("payment page requires ChatGPT approval")
            if submission.get("state") == "failed":
                raise RuntimeError(f"stripe submission failed: {opll_stripe_payload_diagnostics(payload, ctx)}")
            last_err = opll_stripe_payload_diagnostics(payload, ctx)
        else:
            last_err = f"HTTP {response.status_code} {response.text[:120]}"
        time.sleep(1)
    raise RuntimeError(f"redirect url resolution timeout: {last_err}")


def opll_stripe_payment_page_provider_redirect_url(stripe: requests.Session, cs_id: str, stripe_pk: str, payment_locale: str = "en", timeout_seconds: int = 45, ctx: dict | None = None) -> str:
    deadline = time.time() + max(1, timeout_seconds)
    _browser_locale, elements_locale = locale_parts(payment_locale)
    ctx = ctx or {}
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": str(ctx.get("elements_session_id") or f"elements_session_{uuid.uuid4().hex[:11]}"),
        "elements_session_client[stripe_js_id]": str(ctx.get("stripe_js_id") or uuid.uuid4()),
        "elements_session_client[locale]": elements_locale,
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    last_err = ""
    while time.time() < deadline:
        response = stripe.get(
            f"https://api.stripe.com/v1/payment_pages/{cs_id}",
            params=params,
            timeout=PAY_LONG_LINK_TIMEOUT,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            redirect_url = opll_extract_provider_redirect_url(payload)
            if redirect_url:
                return redirect_url
            submission = opll_find_submission_attempt(payload)
            if submission.get("state") == "requires_approval":
                raise OpllStripeRequiresApproval("payment page requires ChatGPT approval")
            if submission.get("state") == "failed":
                raise RuntimeError(f"stripe submission failed: {opll_stripe_payload_diagnostics(payload, ctx)}")
            last_err = opll_stripe_payload_diagnostics(payload, ctx)
        else:
            last_err = f"HTTP {response.status_code} {response.text[:120]}"
        time.sleep(1)
    raise RuntimeError(f"provider redirect url resolution timeout: {last_err}")


def opll_resolve_external_redirect(stripe: requests.Session, redirect_url: str, preferred_hosts: tuple[str, ...] = ("paypal.com",)) -> str:
    current = str(redirect_url or "").strip()
    for _ in range(5):
        if not current:
            return ""
        if opll_is_paypal_success_url(current):
            return current
        host = (urlsplit(current).netloc or "").lower()
        if preferred_hosts and any(host == item or host.endswith(f".{item}") for item in preferred_hosts):
            return current
        try:
            response = stripe.get(current, allow_redirects=False, timeout=PAY_LONG_LINK_TIMEOUT)
        except Exception:
            return current
        if response.status_code not in (301, 302, 303, 307, 308):
            return current
        location = str(response.headers.get("Location") or "").strip()
        if not location:
            return current
        current = urljoin(current, location)
    return current


def opll_stripe_confirm(stripe: requests.Session, cs_id: str, pm_id: str, stripe_pk: str, init_payload: dict, ctx: dict, checkout: dict, stripe_hosted_url: str, payment_method_type: str = "paypal") -> dict:
    return_url = opll_stripe_confirm_return_url(cs_id, checkout, stripe_hosted_url)
    runtime_version = str(ctx.get("runtime_version") or DEFAULT_STRIPE_RUNTIME_VERSION)
    payment_method_type = str(payment_method_type or "paypal").strip().lower()
    response = stripe.post(
        f"https://api.stripe.com/v1/payment_pages/{cs_id}/confirm",
        data={
            "guid": uuid.uuid4().hex,
            "muid": uuid.uuid4().hex,
            "sid": uuid.uuid4().hex,
            "payment_method": pm_id,
            "init_checksum": str(init_payload.get("init_checksum") or ctx.get("init_checksum") or ""),
            "version": runtime_version,
            "expected_amount": str(ctx.get("checkout_amount") or opll_expected_amount(init_payload)),
            "expected_payment_method_type": payment_method_type,
            "return_url": return_url,
            "elements_session_client[session_id]": ctx["elements_session_id"],
            "elements_session_client[locale]": str(ctx.get("locale") or "en"),
            "elements_session_client[referrer_host]": "chatgpt.com",
            "elements_session_client[is_aggregation_expected]": "false",
            "elements_session_client[elements_init_source]": "custom_checkout",
            "elements_session_client[stripe_js_id]": ctx["stripe_js_id"],
            "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
            "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
            "elements_options_client[saved_payment_method][enable_save]": "never",
            "elements_options_client[saved_payment_method][enable_redisplay]": "never",
            "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
            "client_attribution_metadata[checkout_session_id]": cs_id,
            "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
            "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
            "client_attribution_metadata[elements_session_config_id]": ctx["elements_session_config_id"],
            "client_attribution_metadata[merchant_integration_source]": "checkout",
            "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
            "client_attribution_metadata[merchant_integration_version]": "custom",
            "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
            "client_attribution_metadata[payment_method_selection_flow]": "automatic",
            "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
            "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
            "consent[terms_of_service]": "accepted",
            "key": stripe_pk,
            "_stripe_version": STRIPE_VERSION_FULL,
        },
        timeout=PAY_LONG_LINK_TIMEOUT,
    )
    if response.status_code >= 400:
        raise RuntimeError(opll_stripe_error_summary("stripe confirm failed", response))
    return response.json() or {}


def opll_redirect_url_after_confirm(access_token: str, stripe: requests.Session, confirm_payload: dict, cs_id: str, stripe_pk: str, ctx: dict, checkout: dict, approve_proxy_url: str = "") -> str:
    redirect_url = opll_extract_redirect_to_url(confirm_payload)
    if redirect_url:
        return redirect_url
    submission = opll_find_submission_attempt(confirm_payload)
    if submission.get("state") == "requires_approval":
        opll_chatgpt_approve_with_retry(access_token, cs_id, checkout, approve_proxy_url)
        return opll_stripe_payment_page_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=45)
    if submission.get("state") == "failed":
        raise RuntimeError(f"stripe submission failed: {opll_stripe_payload_diagnostics(confirm_payload, ctx)}")
    try:
        return opll_stripe_payment_page_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=30)
    except OpllStripeRequiresApproval:
        opll_chatgpt_approve_with_retry(access_token, cs_id, checkout, approve_proxy_url)
        return opll_stripe_payment_page_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=45)


def opll_provider_redirect_url_after_confirm(access_token: str, stripe: requests.Session, confirm_payload: dict, cs_id: str, stripe_pk: str, ctx: dict, checkout: dict, approve_proxy_url: str = "") -> str:
    redirect_url = opll_extract_provider_redirect_url(confirm_payload)
    if redirect_url:
        return redirect_url
    submission = opll_find_submission_attempt(confirm_payload)
    if submission.get("state") == "requires_approval":
        opll_chatgpt_approve_with_retry(access_token, cs_id, checkout, approve_proxy_url)
        return opll_stripe_payment_page_provider_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=45)
    if submission.get("state") == "failed":
        raise RuntimeError(f"stripe submission failed: {opll_stripe_payload_diagnostics(confirm_payload, ctx)}")
    try:
        return opll_stripe_payment_page_provider_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=30)
    except OpllStripeRequiresApproval:
        opll_chatgpt_approve_with_retry(access_token, cs_id, checkout, approve_proxy_url)
        return opll_stripe_payment_page_provider_redirect_url(stripe, cs_id, stripe_pk, ctx=ctx, timeout_seconds=45)


def opll_combo_attempt_order(country: str) -> list[tuple[str, str]]:
    requested = normalize_opll_country(country)
    ordered = [(requested, requested)]
    if requested == "DE":
        ordered.extend([("US", "US"), ("DE", "US"), ("US", "DE")])
    result = []
    seen = set()
    for item in ordered:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def generate_opll_paypal_long_link(access_token: str, country: str, currency: str, create_proxy_url: str = "", followup_proxy_url: str = "", approve_proxy_url: str = "", target_amount: str = "") -> dict:
    create_proxy_url = str(create_proxy_url or "").strip()
    followup_proxy_url = str(followup_proxy_url or "").strip() or create_proxy_url
    approve_proxy_url = str(approve_proxy_url or "").strip() or followup_proxy_url
    failures: list[str] = []
    requested_country = normalize_opll_country(country)
    for checkout_country, pm_country in opll_combo_attempt_order(requested_country):
        try:
            checkout = opll_create_checkout(access_token, checkout_country, currency_for_country(checkout_country), create_proxy_url)
            stripe = opll_build_stripe_session(followup_proxy_url)
            init_payload = opll_stripe_init(checkout["cs_id"], checkout["billing_country"], checkout["currency"], followup_proxy_url, stripe=stripe, checkout=checkout)
            stripe_hosted_url = str(init_payload.get("stripe_hosted_url") or "").strip()
            if not stripe_hosted_url:
                raise RuntimeError(f"stripe init response missing stripe_hosted_url, keys={sorted(init_payload.keys())}")
            hosted_long_url = opll_to_openai_pay_url(stripe_hosted_url)
            stripe_pk = opll_stripe_key_for_checkout(checkout)
            ctx = opll_stripe_context(init_payload)
            if not ctx.get("currency"):
                ctx["currency"] = str(checkout.get("currency") or "").lower()
            stripe_amount, stripe_amount_source = opll_stripe_amount_info(init_payload)
            pm_id = opll_stripe_create_paypal_method(stripe, checkout["cs_id"], ctx, opll_billing_for_country(pm_country), stripe_pk)
            confirm_payload = opll_stripe_confirm(stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, ctx, checkout, stripe_hosted_url)
            stripe_redirect_url = opll_redirect_url_after_confirm(access_token, stripe, confirm_payload, checkout["cs_id"], stripe_pk, ctx, checkout, approve_proxy_url)
            provider_url = stripe_redirect_url if opll_is_paypal_success_url(stripe_redirect_url) else opll_resolve_external_redirect(stripe, stripe_redirect_url)
            if not opll_is_paypal_success_url(provider_url):
                resource_hint = "仅发现 Stripe 资源 URL，未发现 PayPal BA approve 链；" if opll_is_ignored_resource_url(provider_url) else ""
                raise RuntimeError(
                    f"{resource_hint}未提取到可用的 PayPal 跳转链接；当前结果: {provider_url or stripe_redirect_url}"
                )
            return opll_apply_amount_check({
                **checkout,
                "payment_method_country": pm_country,
                "payment_method_id": pm_id,
                "stripe_hosted_url": stripe_hosted_url,
                "stripe_redirect_url": stripe_redirect_url,
                "provider_redirect_url": provider_url,
                "fallback": (checkout_country, pm_country) != (requested_country, requested_country),
                "provider_error": "; ".join(failures),
                "long_url": provider_url or hosted_long_url,
                "stripe_amount": stripe_amount,
                "stripe_amount_source": stripe_amount_source,
            }, target_amount)
        except AmountMismatchError:
            raise
        except Exception as exc:
            failures.append(f"{checkout_country}+{pm_country}: {opll_short_error(str(exc))}")
    raise RuntimeError(f"所有组合均未提取到 PayPal BA approve 链；{'; '.join(failures)}")


def generate_opll_gopay_long_link(access_token: str, country: str, currency: str, create_proxy_url: str = "", followup_proxy_url: str = "", approve_proxy_url: str = "", target_amount: str = "") -> dict:
    create_proxy_url = str(create_proxy_url or "").strip()
    followup_proxy_url = str(followup_proxy_url or "").strip() or create_proxy_url
    approve_proxy_url = str(approve_proxy_url or "").strip() or followup_proxy_url
    checkout_country = normalize_opll_country(country or "ID")
    checkout = opll_create_checkout(access_token, checkout_country, currency_for_country(checkout_country), create_proxy_url)
    stripe = opll_build_stripe_session(followup_proxy_url)
    init_payload = opll_stripe_init(checkout["cs_id"], checkout["billing_country"], checkout["currency"], followup_proxy_url, stripe=stripe, checkout=checkout)
    stripe_hosted_url = str(init_payload.get("stripe_hosted_url") or "").strip()
    if not stripe_hosted_url:
        raise RuntimeError(f"stripe init response missing stripe_hosted_url, keys={sorted(init_payload.keys())}")
    hosted_long_url = opll_to_openai_pay_url(stripe_hosted_url)
    stripe_pk = opll_stripe_key_for_checkout(checkout)
    ctx = opll_stripe_context(init_payload)
    if not ctx.get("currency"):
        ctx["currency"] = str(checkout.get("currency") or "").lower()
    stripe_amount, stripe_amount_source = opll_stripe_amount_info(init_payload)
    pm_id = opll_stripe_create_paypal_method(stripe, checkout["cs_id"], ctx, opll_billing_for_country("ID"), stripe_pk, "gopay")
    confirm_payload = opll_stripe_confirm(stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, ctx, checkout, stripe_hosted_url, "gopay")
    stripe_redirect_url = opll_provider_redirect_url_after_confirm(access_token, stripe, confirm_payload, checkout["cs_id"], stripe_pk, ctx, checkout, approve_proxy_url)
    provider_url = opll_resolve_external_redirect(stripe, stripe_redirect_url, preferred_hosts=()) if stripe_redirect_url else ""
    long_url = provider_url or stripe_redirect_url or hosted_long_url
    if not long_url or not opll_is_external_url(long_url) or opll_is_ignored_resource_url(long_url):
        raise RuntimeError(f"未提取到有效 GoPay 跳转长链；当前结果: {long_url or stripe_redirect_url or stripe_hosted_url}")
    return opll_apply_amount_check({
        **checkout,
        "payment_method_country": "ID",
        "payment_method_id": pm_id,
        "stripe_hosted_url": stripe_hosted_url,
        "stripe_redirect_url": stripe_redirect_url,
        "provider_redirect_url": long_url,
        "long_url": long_url,
        "payment_method_type": "gopay",
        "stripe_amount": stripe_amount,
        "stripe_amount_source": stripe_amount_source,
    }, target_amount)


def generate_opll_hosted_long_link(access_token: str, country: str, currency: str, create_proxy_url: str = "", followup_proxy_url: str = "", approve_proxy_url: str = "", target_amount: str = "") -> dict:
    create_proxy_url = str(create_proxy_url or "").strip()
    followup_proxy_url = str(followup_proxy_url or "").strip() or create_proxy_url
    approve_proxy_url = str(approve_proxy_url or "").strip() or followup_proxy_url
    checkout = opll_create_checkout(access_token, country, currency, create_proxy_url)
    init_payload = opll_stripe_init(checkout["cs_id"], checkout["billing_country"], checkout["currency"], followup_proxy_url, checkout=checkout)
    stripe_hosted_url = str(init_payload.get("stripe_hosted_url") or "").strip()
    if not stripe_hosted_url:
        raise RuntimeError(f"stripe init response missing stripe_hosted_url, keys={sorted(init_payload.keys())}")
    stripe_amount, stripe_amount_source = opll_stripe_amount_info(init_payload)
    long_url = opll_to_openai_pay_url(stripe_hosted_url) or opll_stripe_checkout_long_url(
        checkout["cs_id"], checkout["billing_country"], checkout.get("processor_entity", "")
    )
    return opll_apply_amount_check({
        **checkout,
        "stripe_hosted_url": stripe_hosted_url,
        "long_url": long_url,
        "stripe_amount": stripe_amount,
        "stripe_amount_source": stripe_amount_source,
    }, target_amount)


def parse_expired_time(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return 0


def resolve_organization_id(id_claims: dict, access_claims: dict) -> str:
    id_auth = get_nested_record(id_claims, "https://api.openai.com/auth")
    access_auth = get_nested_record(access_claims, "https://api.openai.com/auth")
    organizations = id_auth.get("organizations") if isinstance(id_auth.get("organizations"), list) else access_auth.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        return ""
    first = organizations[0]
    return first_non_empty(first.get("id") if isinstance(first, dict) else "")


def normalize_openai_auth_record(email_addr: str, payload: dict) -> dict:
    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    id_token = str(payload.get("id_token") or "")
    if not access_token:
        raise RuntimeError(f"token响应缺少 access_token: {payload}")
    if not refresh_token:
        raise RuntimeError(f"token响应缺少 refresh_token: {payload}")
    if not id_token:
        raise RuntimeError(f"token响应缺少 id_token: {payload}")
    access_claims = decode_jwt_payload(access_token)
    id_claims = decode_jwt_payload(id_token)
    auth_claim = get_nested_record(access_claims, "https://api.openai.com/auth")
    id_auth_claim = get_nested_record(id_claims, "https://api.openai.com/auth")
    account_id = first_non_empty(auth_claim.get("chatgpt_account_id"), id_auth_claim.get("chatgpt_account_id"))
    exp = int(access_claims.get("exp") or 0)
    if not account_id:
        raise RuntimeError(f"token中缺少 account_id: {access_claims}")
    if not exp:
        raise RuntimeError(f"access_token中缺少 exp: {access_claims}")
    return {
        "access_token": access_token,
        "account_id": account_id,
        "disabled": False,
        "email": first_non_empty(id_claims.get("email"), access_claims.get("email"), email_addr),
        "expired": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z"),
        "id_token": id_token,
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "refresh_token": refresh_token,
        "type": "codex",
        "websockets": False,
    }


def build_sub2api_json(record: dict) -> dict:
    access_claims = decode_jwt_payload(str(record.get("access_token") or ""))
    id_claims = decode_jwt_payload(str(record.get("id_token") or ""))
    access_auth = get_nested_record(access_claims, "https://api.openai.com/auth")
    access_profile = get_nested_record(access_claims, "https://api.openai.com/profile")
    expires_at = parse_expired_time(str(record.get("expired") or "")) or int(access_claims.get("exp") or 0)
    issued_at = int(access_claims.get("iat") or 0)
    expires_in = max(expires_at - issued_at, 0) if expires_at and issued_at else 864000
    email_addr = first_non_empty(record.get("email"), access_profile.get("email"), id_claims.get("email"), access_claims.get("email"))
    sub = first_non_empty(access_claims.get("sub"), id_claims.get("sub"))
    return {
        "data": {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "proxies": [],
            "accounts": [{
                "name": email_addr or f"openai-{int(time.time())}",
                "platform": "openai",
                "type": "oauth",
                "credentials": {
                    "access_token": str(record.get("access_token") or ""),
                    "chatgpt_account_id": first_non_empty(record.get("account_id"), access_auth.get("chatgpt_account_id")),
                    "chatgpt_user_id": first_non_empty(access_auth.get("chatgpt_user_id"), access_auth.get("user_id"), access_claims.get("sub")),
                    "expires_at": expires_at,
                    "expires_in": expires_in,
                    "organization_id": resolve_organization_id(id_claims, access_claims),
                    "refresh_token": str(record.get("refresh_token") or ""),
                },
                "extra": {"email": email_addr, "sub": sub},
                "concurrency": 10,
                "priority": 1,
                "rate_multiplier": 1,
                "auto_pause_on_expired": True,
            }],
        },
        "skip_default_group_bind": True,
    }


def build_sub2api_account(record: dict) -> dict:
    access_claims = decode_jwt_payload(str(record.get("access_token") or ""))
    id_claims = decode_jwt_payload(str(record.get("id_token") or ""))
    access_auth = get_nested_record(access_claims, "https://api.openai.com/auth")
    id_auth = get_nested_record(id_claims, "https://api.openai.com/auth")
    access_profile = get_nested_record(access_claims, "https://api.openai.com/profile")
    expires_at = parse_expired_time(str(record.get("expired") or "")) or int(access_claims.get("exp") or 0)
    issued_at = int(access_claims.get("iat") or 0)
    expires_in = max(expires_at - issued_at, 0) if expires_at and issued_at else 864000
    email_addr = first_non_empty(record.get("email"), access_profile.get("email"), id_claims.get("email"), access_claims.get("email"))
    plan_type = first_non_empty(record.get("plan_type"), access_auth.get("chatgpt_plan_type"), id_auth.get("chatgpt_plan_type"))
    return {
        "name": email_addr or f"openai-{int(time.time())}",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "access_token": str(record.get("access_token") or ""),
            "chatgpt_account_id": first_non_empty(record.get("account_id"), access_auth.get("chatgpt_account_id"), id_auth.get("chatgpt_account_id")),
            "chatgpt_user_id": first_non_empty(access_auth.get("chatgpt_user_id"), access_auth.get("chatgpt_user_id"), access_auth.get("user_id"), access_claims.get("sub")),
            "client_id": DEFAULT_CLIENT_ID,
            "email": email_addr,
            "expires_at": expires_at,
            "expires_in": expires_in,
            "id_token": str(record.get("id_token") or ""),
            "organization_id": resolve_organization_id(id_claims, access_claims),
            "plan_type": plan_type,
            "refresh_token": str(record.get("refresh_token") or ""),
        },
        "extra": {"email": email_addr},
        "concurrency": 10,
        "priority": 1,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }


def build_sub2api_export(records: list[dict]) -> dict:
    accounts = [build_sub2api_account(record) for record in records]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "proxies": [],
        "accounts": accounts,
    }


def openai_record_from_refresh_payload(email_addr: str, payload: dict) -> dict:
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError("刷新 RT 后缺少 access_token")
    access_claims = decode_jwt_payload(access_token)
    access_auth = get_nested_record(access_claims, "https://api.openai.com/auth")
    account_id = first_non_empty(access_auth.get("chatgpt_account_id"), access_auth.get("account_id"))
    exp = int(access_claims.get("exp") or 0)
    refresh_token = str(payload.get("refresh_token") or "")
    if not refresh_token.startswith("rt_"):
        raise RuntimeError("刷新 RT 后缺少有效 refresh_token")
    if not account_id:
        raise RuntimeError(f"access_token 中缺少 account_id: {access_claims}")
    return {
        "access_token": access_token,
        "account_id": account_id,
        "email": email_addr,
        "expired": datetime.fromtimestamp(exp, timezone.utc).isoformat().replace("+00:00", "Z") if exp else "",
        "id_token": str(payload.get("id_token") or ""),
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "plan_type": first_non_empty(access_auth.get("chatgpt_plan_type")),
        "refresh_token": refresh_token,
        "type": "codex",
    }


def normalize_auth_continue_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("http") else urljoin(AUTH_BASE_URL, text)


def performance_now_ms() -> int:
    return time.perf_counter_ns() // 1_000_000


def base64_json(value) -> str:
    return base64.b64encode(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")


def sentinel_hash_hex(value: str) -> str:
    hash_value = 2166136261
    for char in value:
        hash_value ^= ord(char)
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    hash_value ^= hash_value >> 16
    hash_value = (hash_value * 2246822507) & 0xFFFFFFFF
    hash_value ^= hash_value >> 13
    hash_value = (hash_value * 3266489909) & 0xFFFFFFFF
    hash_value ^= hash_value >> 16
    return f"{hash_value & 0xFFFFFFFF:08x}"


def collect_sentinel_fingerprint_data(sid: str) -> list:
    return [
        1366 + 768,
        datetime.now().astimezone().strftime("%a %b %d %Y %H:%M:%S GMT%z (%Z)"),
        4294967296,
        random.random(),
        DEFAULT_USER_AGENT,
        "https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js",
        "20260219f9f6",
        "zh-CN",
        "zh-CN,zh",
        random.random(),
        random.choice([
            f"userAgent−{DEFAULT_USER_AGENT}",
            "language−zh-CN",
            "hardwareConcurrency−8",
        ]),
        "location",
        random.choice(["window", "self", "document", "navigator", "location", "screen", "history"]),
        performance_now_ms(),
        sid,
        "sv",
        8,
        int(time.time() * 1000),
        0,
        1,
        1,
        0,
        0,
        0,
        1,
    ]


def generate_sentinel_answer(seed: str, difficulty: str) -> str:
    started = performance_now_ms()
    sid = str(uuid.uuid4())
    data = collect_sentinel_fingerprint_data(sid)
    for attempt in range(500000):
        data[3] = attempt
        data[9] = round(performance_now_ms() - started)
        encoded = base64_json(data)
        digest = sentinel_hash_hex(seed + encoded)
        if digest[:len(difficulty)] <= difficulty:
            return f"{encoded}~S"
    return "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + base64_json("max attempts exceeded")


def openai_browser_headers(extra: dict | None = None) -> dict:
    headers = {
        "user-agent": DEFAULT_USER_AGENT,
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Google Chrome";v="146", "Chromium";v="146", "Not.A/Brand";v="24"',
        "sec-ch-ua-full-version-list": '"Google Chrome";v="146.0.0.0", "Chromium";v="146.0.0.0", "Not.A/Brand";v="24.0.0.0"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"15.0.0"',
        "sec-ch-viewport-width": '"1365"',
    }
    if extra:
        headers.update(extra)
    return headers


def refresh_openai_access_token(openai_rt: str, proxy_url: str = "") -> dict:
    if not str(openai_rt or "").startswith("rt_"):
        raise RuntimeError("当前保存的 rt_token 不是有效 OpenAI refresh_token，请重新授权获取 RT")
    session = requests.Session()
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    last_error = ""
    for token_url in AUTH_OAUTH_TOKEN_URLS:
        response = session.post(
            token_url,
            headers=openai_browser_headers({"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}),
            data={"grant_type": "refresh_token", "client_id": DEFAULT_CLIENT_ID, "refresh_token": openai_rt},
            timeout=30,
        )
        if response.ok:
            payload = response.json()
            if payload.get("access_token"):
                return payload
        last_error = f"endpoint={token_url} HTTP {response.status_code} {response.text[:300]}"
    raise RuntimeError(f"OpenAI RT 刷新 access_token 失败: {last_error}")


def infer_account_type_from_payload(payload) -> tuple[str, str]:
    found_free = ""
    found_paid = ""
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            lower_keys = {str(key).lower(): value for key, value in item.items()}
            for key in ["is_paid_subscription_active", "has_active_subscription", "is_plus_user", "is_subscribed"]:
                if key in lower_keys:
                    value = lower_keys[key]
                    if value is True:
                        found_paid = found_paid or f"{key}=true"
                    if value is False:
                        found_free = found_free or f"{key}=false"
            for key in ["subscription_plan", "plan_type", "plan", "account_plan", "product_name", "sku", "name"]:
                value = lower_keys.get(key)
                if isinstance(value, str):
                    text = value.lower()
                    if any(word in text for word in ["team", "enterprise"]):
                        return "team", f"{key}={value}"
                    if any(word in text for word in ["plus", "pro", "chatgptplusplan"]):
                        return "plus", f"{key}={value}"
                    if any(word in text for word in ["free", "none", "no_plan"]):
                        found_free = found_free or f"{key}={value}"
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    if found_paid:
        return "plus", found_paid
    return ("free", found_free) if found_free else ("", "未发现明确套餐字段")


def detect_openai_account_type(openai_rt: str, proxy_url: str = "") -> tuple[str, str, str]:
    token_payload = refresh_openai_access_token(openai_rt, proxy_url)
    access_token = str(token_payload.get("access_token") or "")
    new_rt = str(token_payload.get("refresh_token") or openai_rt)
    access_claims = decode_jwt_payload(access_token)
    auth_claim = get_nested_record(access_claims, "https://api.openai.com/auth")
    account_id = first_non_empty(auth_claim.get("chatgpt_account_id"), auth_claim.get("account_id"))
    session = requests.Session()
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    headers = openai_browser_headers({
        "accept": "application/json",
        "authorization": f"Bearer {access_token}",
        "origin": CHATGPT_BASE_URL,
        "referer": f"{CHATGPT_BASE_URL}/",
    })
    endpoints = [f"{CHATGPT_BASE_URL}/backend-api/accounts/check/v4-2023-04-27"]
    if account_id:
        endpoints.append(f"{CHATGPT_BASE_URL}/backend-api/accounts/{account_id}/subscription")
    endpoints.extend([
        f"{CHATGPT_BASE_URL}/backend-api/me",
        f"{CHATGPT_BASE_URL}/backend-api/models",
    ])
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            response = session.get(endpoint, headers=headers, timeout=30)
            if not response.ok:
                errors.append(f"{endpoint}: HTTP {response.status_code}")
                continue
            payload = response.json()
            account_type, detail = infer_account_type_from_payload(payload)
            if account_type:
                return account_type, f"{endpoint} -> {detail}", new_rt
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("无法判断 Free/Plus: " + " | ".join(errors[-3:]))


class ProxyChainServer:
    def __init__(self, local_proxy: str, dynamic_proxy: str, log):
        self.local_proxy = normalize_proxy_url(local_proxy)
        self.dynamic_proxy = normalize_proxy_url(dynamic_proxy)
        self.log = log
        self.lock = threading.Lock()
        self.active_sockets: set[socket.socket] = set()
        self.server: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.url = ""

    def __enter__(self):
        if not self.local_proxy and not self.dynamic_proxy:
            return self
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(64)
        port = self.server.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()

    def close(self) -> None:
        self.stop_event.set()
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        self.server = None

    def set_dynamic_proxy(self, dynamic_proxy: str) -> None:
        sockets: list[socket.socket]
        with self.lock:
            self.dynamic_proxy = normalize_proxy_url(dynamic_proxy)
            sockets = list(self.active_sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def _track_socket(self, sock: socket.socket) -> None:
        with self.lock:
            self.active_sockets.add(sock)

    def _untrack_socket(self, sock: socket.socket) -> None:
        with self.lock:
            self.active_sockets.discard(sock)

    def _serve(self) -> None:
        assert self.server is not None
        while not self.stop_event.is_set():
            try:
                client, _addr = self.server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        upstream = None
        self._track_socket(client)
        try:
            client.settimeout(30)
            head = self._read_http_head(client)
            if not head:
                return
            first_line = head.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
            parts = first_line.split()
            if len(parts) < 3:
                return
            method, target, version = parts[0].upper(), parts[1], parts[2]
            if method == "CONNECT":
                upstream = self._open_chain_to_target(target)
                self._track_socket(upstream)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self._relay(client, upstream)
                return
            rewritten = self._rewrite_plain_request(head, method, target, version)
            upstream = self._open_chain_to_target(self._target_from_plain_request(method, target, head))
            self._track_socket(upstream)
            upstream.sendall(rewritten)
            self._relay(client, upstream)
        except Exception:
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            except Exception:
                pass
        finally:
            self._untrack_socket(client)
            if upstream:
                self._untrack_socket(upstream)
            try:
                client.close()
            except Exception:
                pass

    def _read_http_head(self, client: socket.socket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _target_from_plain_request(self, method: str, target: str, head: bytes) -> str:
        if target.startswith("http://") or target.startswith("https://"):
            parsed = urlparse(target)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return f"{parsed.hostname}:{port}"
        host = ""
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"host:"):
                host = line.split(b":", 1)[1].strip().decode("latin1")
                break
        return host

    def _rewrite_plain_request(self, head: bytes, method: str, target: str, version: str) -> bytes:
        if not (target.startswith("http://") or target.startswith("https://")):
            return head
        parsed = urlparse(target)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        lines = head.split(b"\r\n")
        lines[0] = f"{method} {path} {version}".encode("latin1")
        return b"\r\n".join(lines)

    def _open_chain_to_target(self, target: str) -> socket.socket:
        with self.lock:
            local_proxy = self.local_proxy
            dynamic_proxy = self.dynamic_proxy
        if local_proxy:
            sock = self._connect_proxy(local_proxy)
            self._send_connect(sock, self._proxy_connect_target(dynamic_proxy) if dynamic_proxy else target)
            if dynamic_proxy:
                self._send_connect(sock, target, proxy_url=dynamic_proxy)
            return sock
        if dynamic_proxy:
            sock = self._connect_proxy(dynamic_proxy)
            self._send_connect(sock, target, proxy_url=dynamic_proxy)
            return sock
        host, port = self._split_host_port(target, 80)
        return socket.create_connection((host, port), timeout=30)

    def _connect_proxy(self, proxy_url: str) -> socket.socket:
        parsed = urlparse(proxy_url)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError(f"链式代理当前只支持 http/https 代理: {proxy_url}")
        host = parsed.hostname
        if not host:
            raise RuntimeError(f"代理地址缺少 host: {proxy_url}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        raw = socket.create_connection((host, port), timeout=30)
        if parsed.scheme == "https":
            return ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        return raw

    def _proxy_connect_target(self, proxy_url: str) -> str:
        parsed = urlparse(proxy_url)
        if not parsed.hostname:
            raise RuntimeError(f"动态代理地址缺少 host: {proxy_url}")
        return f"{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"

    def _send_connect(self, sock: socket.socket, target: str, proxy_url: str = "") -> None:
        headers = [f"CONNECT {target} HTTP/1.1", f"Host: {target}", "Proxy-Connection: keep-alive"]
        auth = self._proxy_auth(proxy_url)
        if auth:
            headers.append(f"Proxy-Authorization: Basic {auth}")
        request = ("\r\n".join(headers) + "\r\n\r\n").encode("latin1")
        sock.sendall(request)
        response = self._read_http_head(sock)
        status = response.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        if " 200 " not in f" {status} ":
            raise RuntimeError(f"代理 CONNECT 失败: {status}")

    def _proxy_auth(self, proxy_url: str) -> str:
        parsed = urlparse(proxy_url)
        if not parsed.username:
            return ""
        username = unquote(parsed.username)
        password = unquote(parsed.password or "")
        return base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")

    def _split_host_port(self, target: str, default_port: int) -> tuple[str, int]:
        if target.startswith("["):
            host, rest = target[1:].split("]", 1)
            port = int(rest[1:]) if rest.startswith(":") else default_port
            return host, port
        if ":" in target:
            host, port = target.rsplit(":", 1)
            return host, int(port)
        return target, default_port

    def _relay(self, left: socket.socket, right: socket.socket) -> None:
        sockets = [left, right]
        for sock in sockets:
            sock.settimeout(None)
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 60)
                if not readable:
                    return
                for src in readable:
                    dst = right if src is left else left
                    data = src.recv(65536)
                    if not data:
                        return
                    dst.sendall(data)
        finally:
            try:
                right.close()
            except Exception:
                pass


def decode_header_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def html_to_text(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text))


def extract_message_text(msg) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            parts.append(html_to_text(text) if content_type == "text/html" else text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            parts.append(html_to_text(text) if msg.get_content_type() == "text/html" else text)
    return "\n".join(parts)


def extract_openai_code(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or " ")
    patterns = [
        r"(?:OpenAI|ChatGPT|verification|verify|code|验证码|登录码)[^\d]{0,100}(\d{6})",
        r"\b(\d{6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.I)
        if match:
            return match.group(1)
    return ""


def refresh_hotmail_access_token(account: MailAccount, log, proxy_url: str = "") -> str:
    errors: list[str] = []
    for endpoint in TOKEN_ENDPOINTS:
        data = {
            "client_id": account.client_id,
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
        }
        if endpoint.get("scope"):
            data["scope"] = endpoint["scope"]
        if endpoint.get("resource"):
            data["resource"] = endpoint["resource"]
        try:
            log(f"尝试邮箱 Token 端点 {endpoint['name']}")
            resp = requests.post(
                endpoint["url"],
                data=data,
                headers={"Accept": "application/json"},
                timeout=10,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
            )
            payload = resp.json() if resp.text else {}
            if resp.ok and payload.get("access_token"):
                log(f"邮箱 Token 端点 {endpoint['name']} 成功")
                return str(payload["access_token"])
            msg = payload.get("error_description") or payload.get("error") or f"HTTP {resp.status_code}"
            errors.append(f"{endpoint['name']}: {msg}")
            log(f"邮箱 Token 端点 {endpoint['name']} 失败: {msg}")
        except Exception as exc:
            errors.append(f"{endpoint['name']}: {exc}")
            log(f"邮箱 Token 端点 {endpoint['name']} 异常: {exc}")
    raise RuntimeError("所有邮箱 Token 端点均失败 -> " + " | ".join(errors))


class ProxiedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, proxied_socket: socket.socket, timeout: float | None = None):
        self._proxied_socket = proxied_socket
        super().__init__(host=host, port=port, timeout=timeout)

    def open(self, host: str = "", port: int = 0, timeout: float | None = None):
        self.host = host
        self.port = port
        self.sock = self._proxied_socket
        self.file = self.sock.makefile("rb")


class HotmailOtpReader:
    def __init__(self, account: MailAccount, log, proxy_url: str = ""):
        self.account = account
        self.log = log
        self.proxy_url = proxy_url
        self.seen: set[str] = set()
        self.imap: imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        self.log(f"正在连接邮箱取码: {self.account.email}")
        access_token = refresh_hotmail_access_token(self.account, self.log, self.proxy_url)
        auth_string = f"user={self.account.email}\x01auth=Bearer {access_token}\x01\x01"
        if self.proxy_url:
            self.imap = self._connect_imap_via_proxy(self.proxy_url)
        else:
            self.log("正在连接 Outlook IMAP: outlook.office365.com:993")
            self.imap = imaplib.IMAP4_SSL("outlook.office365.com", 993, timeout=20)
            try:
                self.imap.sock.settimeout(20)
            except Exception:
                pass
        self.log("正在进行邮箱 XOAUTH2 认证")
        self.imap.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
        try:
            self.imap.sock.settimeout(30)
        except Exception:
            pass
        self.log("邮箱 IMAP 已连接，准备自动收 OpenAI 验证码")

    def _connect_imap_via_proxy(self, proxy_url: str) -> imaplib.IMAP4_SSL:
        parsed = urlparse(proxy_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise RuntimeError(f"IMAP 代理只支持 HTTP CONNECT: {proxy_url}")
        proxy_port = parsed.port or 80
        raw = socket.create_connection((parsed.hostname, proxy_port), timeout=30)
        target = "outlook.office365.com:993"
        request = [f"CONNECT {target} HTTP/1.1", f"Host: {target}", "Proxy-Connection: keep-alive"]
        if parsed.username:
            token = base64.b64encode(f"{unquote(parsed.username)}:{unquote(parsed.password or '')}".encode("utf-8")).decode("ascii")
            request.append(f"Proxy-Authorization: Basic {token}")
        raw.sendall(("\r\n".join(request) + "\r\n\r\n").encode("latin1"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 65536:
            chunk = raw.recv(4096)
            if not chunk:
                break
            response += chunk
        status = response.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        if " 200 " not in f" {status} ":
            raw.close()
            raise RuntimeError(f"IMAP 代理 CONNECT 失败: {status}")
        tls_sock = ssl.create_default_context().wrap_socket(raw, server_hostname="outlook.office365.com")
        try:
            tls_sock.settimeout(20)
        except Exception:
            pass
        return ProxiedIMAP4SSL("outlook.office365.com", 993, tls_sock, timeout=20)

    def close(self) -> None:
        if not self.imap:
            return
        try:
            self.imap.logout()
        except Exception:
            pass
        self.imap = None

    def wait_for_code(self, min_timestamp: float, timeout: int = 180) -> str:
        if not self.imap:
            try:
                self.connect()
            except Exception as exc:
                self.log(f"邮箱取码连接失败: {exc}")
                raise
        assert self.imap is not None
        started = time.time()
        last_notice = 0.0
        folders = ["INBOX", "Junk", "Junk Email"]
        while time.time() - started < timeout:
            for folder in folders:
                code = self._scan_folder(folder, min_timestamp)
                if code:
                    return code
            if time.time() - last_notice >= 20:
                remain = max(0, int(timeout - (time.time() - started)))
                self.log(f"仍在等待 OpenAI 新验证码邮件，剩余约 {remain}s")
                last_notice = time.time()
            time.sleep(5)
        raise TimeoutError("等待 OpenAI 邮箱验证码超时")

    def _select_folder(self, folder: str) -> bool:
        assert self.imap is not None
        for name in (folder, f'"{folder}"'):
            try:
                status, _ = self.imap.select(name, readonly=True)
                if status == "OK":
                    return True
            except Exception:
                continue
        return False

    def _select_folder_count(self, folder: str) -> int:
        assert self.imap is not None
        for name in (folder, f'"{folder}"'):
            try:
                status, data = self.imap.select(name, readonly=True)
                if status != "OK":
                    continue
                if data and data[0]:
                    return int(data[0])
                return 0
            except Exception:
                continue
        return -1

    def _scan_folder(self, folder: str, min_timestamp: float) -> str:
        assert self.imap is not None
        if not self._select_folder(folder):
            return ""
        status, data = self.imap.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return ""
        ids = data[0].split()[-30:]
        for msg_id in reversed(ids):
            key = f"{folder}:{msg_id.decode(errors='ignore')}"
            if key in self.seen:
                continue
            status, msg_data = self.imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw = next((item[1] for item in msg_data if isinstance(item, tuple)), None)
            if not raw:
                continue
            msg = email_pkg.message_from_bytes(raw)
            date_header = msg.get("Date")
            try:
                mail_time = parsedate_to_datetime(date_header).timestamp() if date_header else time.time()
            except Exception:
                mail_time = time.time()
            if mail_time + 30 < min_timestamp:
                continue
            subject = decode_header_text(msg.get("Subject"))
            from_addr = decode_header_text(msg.get("From"))
            body = extract_message_text(msg)
            haystack = f"{subject}\n{from_addr}\n{body}"
            if not re.search(r"openai|chatgpt", haystack, flags=re.I):
                continue
            self.seen.add(key)
            code = extract_openai_code(haystack)
            if code:
                self.log(f"收到 OpenAI 验证码: {code}")
                return code
        return ""


class OpenAIJsonAuthFlow:
    def __init__(self, account: MailAccount, log, phone_provider=None, input_callback=None, proxy_url: str = ""):
        self.account = account
        self.log = log
        self.phone_provider = phone_provider
        self.input_callback = input_callback
        self.session = requests.Session()
        self.proxy_url = proxy_url
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self.state = ""
        self.code_verifier = ""
        self.device_id = ""
        self.email_otp_requested_at = 0.0

    def _headers(self, extra: dict | None = None) -> dict:
        return openai_browser_headers(extra)

    def _format_error_response(self, response: requests.Response) -> str:
        body = response.text
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else error
            if code:
                return f"{response.status_code} code={code}"
        except Exception:
            pass
        return f"{response.status_code} body={body[:500]}"

    def _read_cookie(self, url: str, key: str) -> str:
        for cookie in self.session.cookies:
            if cookie.name == key and (not cookie.domain or urlparse(url).hostname.endswith(cookie.domain.lstrip("."))):
                return cookie.value
        return ""

    def _prepare_login_url(self) -> str:
        self.state = random_urlsafe_string(24)
        self.code_verifier = random_urlsafe_string(64)
        query = urlencode({
            "client_id": DEFAULT_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "scope": "openid email profile offline_access",
            "state": self.state,
            "code_challenge": pkce_code_challenge(self.code_verifier),
            "code_challenge_method": "S256",
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "login_hint": self.account.email,
        })
        return f"{AUTH_BASE_URL}/oauth/authorize?{query}"

    def _fetch_sentinel_token(self, flow: str) -> str:
        requirement_seed = str(random.random())
        req_token = f"gAAAAAC{generate_sentinel_answer(requirement_seed, '0')}"
        response = self.session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={"content-type": "application/json", "user-agent": DEFAULT_USER_AGENT},
            json={"p": req_token, "id": self.device_id, "flow": flow},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"请求 sentinel requirements 失败: {response.status_code} body={response.text[:300]}")
        requirements = response.json()
        if (requirements.get("turnstile") or {}).get("dx"):
            raise RuntimeError("当前 OpenAI 登录触发 Turnstile，服务器无浏览器模式暂不能自动通过")
        pow_data = requirements.get("proofofwork") or {}
        proof = None
        if pow_data.get("required") and pow_data.get("seed") and pow_data.get("difficulty"):
            proof = f"gAAAAAB{generate_sentinel_answer(str(pow_data['seed']), str(pow_data['difficulty']))}"
        return json.dumps({"p": proof, "t": None, "c": requirements.get("token"), "id": self.device_id, "flow": flow}, separators=(",", ":"))

    def _authorize_continue(self) -> str:
        sentinel_token = self._fetch_sentinel_token("authorize_continue")
        response = self.session.post(
            AUTH_AUTHORIZE_CONTINUE_URL,
            headers=self._headers({
                "content-type": "application/json",
                "openai-sentinel-token": sentinel_token,
            }),
            json={"username": {"kind": "email", "value": self.account.email}},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"AuthorizeContinue请求失败: {self._format_error_response(response)}")
        return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))

    def _send_email_otp(self) -> str:
        response = self.session.get(
            AUTH_EMAIL_OTP_SEND_URL,
            headers=self._headers({"accept": "application/json", "referer": f"{AUTH_BASE_URL}/log-in"}),
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"EmailOtpSend请求失败: {self._format_error_response(response)}")
        self.email_otp_requested_at = time.time()
        return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))

    def _email_otp_validate(self) -> str:
        last_error = ""
        for attempt in range(1, 3):
            otp_reader = HotmailOtpReader(self.account, self.log, "")
            try:
                code = otp_reader.wait_for_code(self.email_otp_requested_at or time.time() - 10)
            finally:
                otp_reader.close()
            response = self.session.post(
                AUTH_EMAIL_OTP_VALIDATE_URL,
                headers=self._headers({
                    "accept": "application/json",
                    "content-type": "application/json",
                    "origin": AUTH_BASE_URL,
                    "referer": f"{AUTH_BASE_URL}/email-verification",
                }),
                json={"code": code},
                timeout=30000,
            )
            if response.ok:
                return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))
            last_error = self._format_error_response(response)
            if "wrong_email_otp_code" not in last_error or attempt >= 2:
                raise RuntimeError(f"EmailOtpValidate请求失败: {last_error}")
            self.log("验证码疑似过期或取错，重新发码后重试")
            self._send_email_otp()
            time.sleep(2)
        raise RuntimeError(f"EmailOtpValidate请求失败: {last_error or 'unknown'}")

    def _resolve_workspace_id(self) -> str:
        cookie = self._read_cookie(AUTH_BASE_URL, "oai-client-auth-session")
        if not cookie:
            raise RuntimeError("未找到 oai-client-auth-session cookie，无法提取 workspace")
        encoded = cookie.split(".")[0]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        workspaces = payload.get("workspaces") or []
        workspace = next((item for item in workspaces if isinstance(item, dict) and item.get("kind") == "personal"), None)
        if not workspace and workspaces:
            workspace = workspaces[0]
        workspace_id = workspace.get("id") if isinstance(workspace, dict) else ""
        if not workspace_id:
            raise RuntimeError(f"当前会话未发现 workspace: {payload}")
        return str(workspace_id)

    def _select_workspace(self, consent_url: str) -> str:
        self.session.get(
            consent_url,
            headers=self._headers({
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "referer": f"{AUTH_BASE_URL}/email-verification",
            }),
            timeout=30,
        )
        workspace_id = self._resolve_workspace_id()
        response = self.session.post(
            AUTH_WORKSPACE_SELECT_URL,
            headers=self._headers({
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": consent_url,
            }),
            json={"workspace_id": workspace_id},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"WorkspaceSelect请求失败: {self._format_error_response(response)}")
        return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))

    def _send_phone_otp(self, phone_number: str) -> str:
        response = self.session.post(
            AUTH_PHONE_SEND_URL,
            headers=self._headers({
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/add-phone",
            }),
            json={"phone_number": phone_number},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"SendPhoneOtp请求失败: {self._format_error_response(response)}")
        return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))

    def _validate_phone_otp(self, code: str) -> str:
        response = self.session.post(
            AUTH_PHONE_OTP_VALIDATE_URL,
            headers=self._headers({
                "accept": "application/json",
                "content-type": "application/json",
                "origin": AUTH_BASE_URL,
                "referer": f"{AUTH_BASE_URL}/phone-verification",
            }),
            json={"code": code},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"PhoneOtpValidate请求失败: {self._format_error_response(response)}")
        return normalize_auth_continue_url(str(response.json().get("continue_url") or ""))

    def _handle_add_phone(self) -> str:
        if self.phone_provider:
            last_error = ""
            while True:
                phone = self.phone_provider("next", self.account.email, "")
                if not phone:
                    if last_error:
                        self.log(f"手机号池没有可用手机号，改为手动输入: {last_error}")
                    else:
                        self.log("手机号池为空或没有可用手机号，改为手动输入")
                    break
                phone_number = str(phone.get("number") or "").strip()
                self.log(f"提交手机号: {phone_number}")
                try:
                    self._send_phone_otp(phone_number)
                    code = self.phone_provider("code", self.account.email, phone)
                    if not code:
                        raise RuntimeError("短信链接未读取到验证码")
                    self.log(f"读取到短信验证码: {code}")
                    return self._validate_phone_otp(str(code))
                except Exception as exc:
                    last_error = str(exc)
                    self.phone_provider("bad", self.account.email, {**phone, "error": last_error})
                    self.log(f"手机号 {phone_number} 不可用，切换下一个: {last_error}")
                    continue

        if not self.input_callback:
            raise RuntimeError("未配置手机号池，也未配置手动输入回调")
        phone_number = self.input_callback("phone", self.account.email, "请输入手机号（包含国家码，例如 +1xxxxxxxxxx）")
        if not phone_number:
            raise RuntimeError("已取消手机号输入")
        self.log(f"提交手机号: {phone_number}")
        self._send_phone_otp(phone_number)
        code = self.input_callback("phone-code", self.account.email, f"请输入 {phone_number} 收到的短信验证码")
        if not code:
            raise RuntimeError("已取消短信验证码输入")
        self.log("提交短信验证码")
        return self._validate_phone_otp(code)

    def _extract_auth_result(self, callback_url: str) -> dict:
        parsed = urlparse(callback_url)
        query = parse_qs(parsed.query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not code:
            raise RuntimeError(f"callback 中缺少 code: {callback_url}")
        if not state:
            raise RuntimeError(f"callback 中缺少 state: {callback_url}")
        if self.state and state != self.state:
            raise RuntimeError(f"callback state 不匹配: expected={self.state} actual={state}")
        return {"callback_url": callback_url, "code": code, "state": state}

    def _follow_oauth_redirects(self, start_url: str) -> dict:
        current_url = start_url
        for _ in range(10):
            response = self.session.get(
                current_url,
                allow_redirects=False,
                headers=self._headers({"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}),
                timeout=30,
            )
            location = response.headers.get("location")
            if location:
                next_url = urljoin(current_url, location)
                if next_url.startswith(f"{AUTH_BASE_URL}/add-phone"):
                    current_url = self._handle_add_phone()
                    continue
                if next_url.startswith(DEFAULT_REDIRECT_URI):
                    return self._extract_auth_result(next_url)
                current_url = next_url
                continue
            if response.url.startswith(f"{AUTH_BASE_URL}/add-phone"):
                current_url = self._handle_add_phone()
                continue
            if response.url.startswith(DEFAULT_REDIRECT_URI):
                return self._extract_auth_result(response.url)
            raise RuntimeError(f"OAuth跳转未到达callback: status={response.status_code} url={response.url}")
        raise RuntimeError(f"OAuth跳转次数过多，最后停在: {current_url}")

    def _exchange_code_for_token(self, code: str) -> dict:
        last_error = ""
        for token_url in AUTH_OAUTH_TOKEN_URLS:
            response = self.session.post(
                token_url,
                headers=self._headers({
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                }),
                data={
                    "grant_type": "authorization_code",
                    "client_id": DEFAULT_CLIENT_ID,
                    "code": code,
                    "redirect_uri": DEFAULT_REDIRECT_URI,
                    "code_verifier": self.code_verifier,
                },
                timeout=30,
            )
            if not response.ok:
                last_error = f"endpoint={token_url} {self._format_error_response(response)}"
                continue
            return normalize_openai_auth_record(self.account.email, response.json())
        raise RuntimeError(f"Code换Token失败: {last_error}")

    def run(self) -> dict:
        self.log(f"开始 OpenAI 邮箱验证码授权: {self.account.email}")
        oauth_url = self._prepare_login_url()
        response = self.session.get(
            oauth_url,
            allow_redirects=True,
            headers=self._headers({
                "accept-encoding": "gzip, deflate",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
            }),
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(f"OauthUrl请求失败: {response.status_code}")
        if response.url.startswith(DEFAULT_REDIRECT_URI):
            result = self._extract_auth_result(response.url)
            return self._exchange_code_for_token(result["code"])

        allowed_start_urls = {
            f"{AUTH_BASE_URL}/log-in",
            f"{AUTH_BASE_URL}/email-verification",
            f"{AUTH_BASE_URL}/sign-in-with-chatgpt/codex/consent",
            f"{AUTH_BASE_URL}/add-phone",
        }
        if response.url not in allowed_start_urls and not response.url.startswith(f"{AUTH_BASE_URL}/add-phone"):
            raise RuntimeError(f"OauthUrl重定向到错误的URL: {response.url}")

        self.device_id = self._read_cookie("https://openai.com", "oai-did")
        if not self.device_id:
            self.device_id = str(uuid.uuid4())

        continue_url = response.url
        if continue_url == f"{AUTH_BASE_URL}/email-verification":
            self.email_otp_requested_at = time.time() - 10
        if continue_url == f"{AUTH_BASE_URL}/log-in":
            self.log("提交登录邮箱")
            continue_url = self._authorize_continue()
        if continue_url == f"{AUTH_BASE_URL}/log-in/password":
            raise RuntimeError("该账号进入密码登录页，无法无密码获取 RT")
        if continue_url == AUTH_EMAIL_OTP_SEND_URL:
            self.log("发送邮箱验证码")
            continue_url = self._send_email_otp()
        if continue_url == f"{AUTH_BASE_URL}/email-verification":
            self.log("等待并提交邮箱验证码")
            continue_url = self._email_otp_validate()
        if continue_url.startswith(f"{AUTH_BASE_URL}/add-phone"):
            self.log("遇到 add-phone，等待手动输入手机号和短信验证码")
            continue_url = self._handle_add_phone()
        if continue_url == f"{AUTH_BASE_URL}/sign-in-with-chatgpt/codex/consent":
            self.log("选择默认工作区")
            continue_url = self._select_workspace(continue_url)

        if continue_url.startswith(f"{AUTH_BASE_URL}/add-phone"):
            self.log("遇到 add-phone，等待手动输入手机号和短信验证码")
            continue_url = self._handle_add_phone()
        if continue_url == f"{AUTH_BASE_URL}/sign-in-with-chatgpt/codex/consent":
            self.log("选择默认工作区")
            continue_url = self._select_workspace(continue_url)

        self.log("交换授权 code 获取 refresh_token")
        result = self._follow_oauth_redirects(continue_url)
        return self._exchange_code_for_token(result["code"])


def random_profile() -> tuple[str, str]:
    age = random.randint(25, 34)
    today = datetime.now(timezone.utc)
    year = today.year - age
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}", f"{year:04d}-{month:02d}-{day:02d}"


class OpenAIRegisterPayLinkWorker:
    def __init__(self, account: MailAccount, payment_mode: str, headless: bool, register_proxy: ProxyConfig, extract_proxy: ProxyConfig, log, phone_provider=None, link_create_proxy: ProxyConfig | None = None, link_followup_proxy: ProxyConfig | None = None, link_approve_proxy: ProxyConfig | None = None, require_japan_extract_proxy: bool = False):
        self.account = account
        self.payment_mode = payment_mode
        self.headless = headless
        self.register_proxy = register_proxy
        self.extract_proxy = extract_proxy
        self.log = log
        self.phone_provider = phone_provider
        self.active_register_phone: dict | None = None
        self.otp_reader: HotmailOtpReader | None = None
        self.fingerprint = generate_register_fingerprint()
        self.current_proxy_health: ProxyHealthResult | None = None
        if link_create_proxy is not None or link_followup_proxy is not None or link_approve_proxy is not None or require_japan_extract_proxy:
            self.link_create_proxy = link_create_proxy or extract_proxy
            self.link_followup_proxy = link_followup_proxy or self.link_create_proxy
            self.link_approve_proxy = link_approve_proxy or self.link_followup_proxy
            self.require_japan_extract_proxy = require_japan_extract_proxy

    def run(self) -> dict:
        with sync_playwright() as p:
            register_browser = None
            register_context = None
            extract_browser = None
            extract_context = None
            register_page = None
            try:
                self._prepare_fingerprint_for_proxy(self.register_proxy, "认证")
                self._preconnect_otp_reader()
                register_browser, register_context = self._new_browser_context(p, self.register_proxy)
                register_context.clear_cookies()
                self.log(
                    f"浏览器指纹: Chrome/{self.fingerprint.chrome_major} "
                    f"{self.fingerprint.viewport_width}x{self.fingerprint.viewport_height} "
                    f"{self.fingerprint.locale} {self.fingerprint.timezone} "
                    f"cpu={self.fingerprint.hardware_concurrency} mem={self.fingerprint.device_memory}"
                )
                register_page = register_context.new_page()
                self._log_browser_proxy_status(register_page, self.register_proxy, "认证浏览器代理")
                self._register(register_page, register_context)
                self.log("[认证] 认证完成，当前窗口保持打开；开始读取 Session 信息")
                result = self._extract_session_info(register_context)
                old_session = KEPT_REGISTER_BROWSER_SESSIONS.pop(self.account.email.lower(), None)
                if old_session:
                    try:
                        old_session[0].close()
                        old_session[1].close()
                    except Exception:
                        pass
                KEPT_REGISTER_BROWSER_SESSIONS[self.account.email.lower()] = (register_context, register_browser, self.register_proxy.dynamic_proxy)
                register_context = None
                register_browser = None
                return result
            except Exception:
                if register_context and register_browser and register_page and self._has_chatgpt_session(register_page):
                    self.log("[认证] 检测到浏览器已登录成功，忽略前序页面异常并读取 Session")
                    result = self._extract_session_info(register_context)
                    old_session = KEPT_REGISTER_BROWSER_SESSIONS.pop(self.account.email.lower(), None)
                    if old_session:
                        try:
                            old_session[0].close()
                            old_session[1].close()
                        except Exception:
                            pass
                    KEPT_REGISTER_BROWSER_SESSIONS[self.account.email.lower()] = (register_context, register_browser, self.register_proxy.dynamic_proxy)
                    register_context = None
                    register_browser = None
                    return result
                raise
            finally:
                if self.otp_reader:
                    self.otp_reader.close()
                self._close_browser(register_context, register_browser)
                self._close_browser(extract_context, extract_browser)

    def run_auth_only(self) -> None:
        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            try:
                self._prepare_fingerprint_for_proxy(self.register_proxy, "认证")
                self._preconnect_otp_reader()
                browser, context = self._new_browser_context(p, self.register_proxy)
                context.clear_cookies()
                page = context.new_page()
                self._log_browser_proxy_status(page, self.register_proxy, "认证浏览器代理")
                self._register(page, context)
                old_session = KEPT_REGISTER_BROWSER_SESSIONS.pop(self.account.email.lower(), None)
                if old_session:
                    try:
                        old_session[0].close()
                        old_session[1].close()
                    except Exception:
                        pass
                KEPT_REGISTER_BROWSER_SESSIONS[self.account.email.lower()] = (context, browser, self.register_proxy.dynamic_proxy)
                context = None
                browser = None
                self.log("[认证] 注册或登录完成，浏览器窗口保持打开")
            except Exception:
                if context and browser and page and self._has_chatgpt_session(page):
                    old_session = KEPT_REGISTER_BROWSER_SESSIONS.pop(self.account.email.lower(), None)
                    if old_session:
                        try:
                            old_session[0].close()
                            old_session[1].close()
                        except Exception:
                            pass
                    KEPT_REGISTER_BROWSER_SESSIONS[self.account.email.lower()] = (context, browser, self.register_proxy.dynamic_proxy)
                    context = None
                    browser = None
                    self.log("[认证] 检测到浏览器已登录成功，忽略前序页面异常并保持窗口打开")
                    return
                raise
            finally:
                if self.otp_reader:
                    self.otp_reader.close()
                self._close_browser(context, browser)

    def _preconnect_otp_reader(self) -> None:
        if self.otp_reader:
            return
        self.log("提前连接邮箱 IMAP，准备接收 OpenAI 验证码")
        self.otp_reader = HotmailOtpReader(self.account, self.log, "")
        self.otp_reader.connect()

    def run_team(self) -> dict:
        self.fingerprint = generate_team_fingerprint()
        with sync_playwright() as p:
            browser = None
            context = None
            try:
                self._prepare_fingerprint_for_proxy(self.register_proxy, "Team 认证")
                browser, context = self._new_browser_context(p, self.register_proxy)
                context.clear_cookies()
                self.log(
                    f"Team 浏览器指纹: Chrome/{self.fingerprint.chrome_major} "
                    f"{self.fingerprint.viewport_width}x{self.fingerprint.viewport_height} "
                    f"{self.fingerprint.locale} {self.fingerprint.timezone} "
                    f"cpu={self.fingerprint.hardware_concurrency} mem={self.fingerprint.device_memory}"
                )
                page = context.new_page()
                self._log_browser_proxy_status(page, self.register_proxy, "Team 认证浏览器代理")
                self._register_team_sso(page, context)
                record = self._authorize_rt_from_browser(context, page)
                self.log("Team RT 获取成功")
                old_session = KEPT_REGISTER_BROWSER_SESSIONS.pop(self.account.email.lower(), None)
                if old_session:
                    try:
                        old_session[0].close()
                        old_session[1].close()
                    except Exception:
                        pass
                KEPT_REGISTER_BROWSER_SESSIONS[self.account.email.lower()] = (context, browser, self.register_proxy.dynamic_proxy)
                context = None
                browser = None
                session_payload = self._session_payload_from_record(record)
                return {
                    "url": "",
                    "access_token": str(record.get("access_token") or ""),
                    "session_json": json.dumps(session_payload, ensure_ascii=False, indent=2),
                    "storage_state_json": "",
                    "openai_rt": str(record.get("refresh_token") or ""),
                }
            finally:
                self._close_browser(context, browser)

    def relink(self) -> dict:
        with sync_playwright() as p:
            login_browser = None
            login_context = None
            extract_browser = None
            extract_context = None
            try:
                self._prepare_fingerprint_for_proxy(self.register_proxy, "登录")
                login_browser, login_context = self._new_browser_context(p, self.register_proxy)
                login_context.clear_cookies()
                self.log(
                    f"浏览器指纹: Chrome/{self.fingerprint.chrome_major} "
                    f"{self.fingerprint.viewport_width}x{self.fingerprint.viewport_height} "
                    f"{self.fingerprint.locale} {self.fingerprint.timezone} "
                    f"cpu={self.fingerprint.hardware_concurrency} mem={self.fingerprint.device_memory}"
                )
                login_page = login_context.new_page()
                self._log_browser_proxy_status(login_page, self.register_proxy, "登录浏览器代理")
                self._login_existing_account(login_page, login_context)
                storage_state = login_context.storage_state()
                self.log("登录完成，已保存登录态，切换到长链接提取代理")

                self._close_browser(login_context, login_browser)
                login_context = None
                login_browser = None

                self._prepare_fingerprint_for_proxy(self.extract_proxy, "支付链接")
                extract_browser, extract_context = self._new_browser_context(p, self.extract_proxy, storage_state)
                extract_page = extract_context.new_page()
                self._log_browser_proxy_status(extract_page, self.extract_proxy, "长链接浏览器代理")
                return self._extract_pay_link(extract_page)
            finally:
                if self.otp_reader:
                    self.otp_reader.close()
                self._close_browser(login_context, login_browser)
                self._close_browser(extract_context, extract_browser)

    def _new_browser_context(self, p, proxy: ProxyConfig, storage_state: dict | None = None):
        browser = p.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--lang={self.fingerprint.locale}",
                f"--window-size={self.fingerprint.outer_width},{self.fingerprint.outer_height}",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
            proxy={"server": proxy.chain_url} if proxy.chain_url else None,
        )
        context_options = {
            "user_agent": self.fingerprint.user_agent,
            "locale": self.fingerprint.locale,
            "timezone_id": self.fingerprint.timezone,
            "viewport": {"width": self.fingerprint.viewport_width, "height": self.fingerprint.viewport_height},
            "screen": {"width": self.fingerprint.screen_width, "height": self.fingerprint.screen_height},
            "device_scale_factor": self.fingerprint.device_scale_factor,
            "is_mobile": False,
            "has_touch": False,
        }
        if storage_state:
            context_options["storage_state"] = storage_state
        context = browser.new_context(**context_options)
        self._install_fingerprint(context)
        return browser, context

    def _prepare_fingerprint_for_proxy(self, proxy: ProxyConfig, label: str) -> ProxyHealthResult:
        proxy_url = proxy.chain_url or proxy.local_proxy or proxy.dynamic_proxy
        health = detect_proxy_health(proxy_url)
        if not health.success:
            raise ProxyExitCheckError(f"{label}代理健康检查失败: {health.summary}", "代理检测失败")
        self.current_proxy_health = health
        self.fingerprint = generate_fingerprint_for_exit(health)
        self.log(
            f"[代理] {label}出口检查通过: {health.ip} {health.location or health.country} "
            f"{health.timezone or 'UTC'} ChatGPT={health.chatgpt_status} Stripe={health.stripe_status}"
        )
        return health

    def _close_browser(self, context, browser) -> None:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass

    def _cleanup_profile_dir(self, profile_dir: str) -> None:
        for attempt in range(8):
            try:
                shutil.rmtree(profile_dir, ignore_errors=False)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                time.sleep(0.5 + attempt * 0.25)
            except OSError:
                time.sleep(0.5 + attempt * 0.25)
        self.log(f"临时浏览器目录清理失败，已忽略: {profile_dir}")

    def _install_fingerprint(self, context) -> None:
        fp = self.fingerprint
        fp_payload = json.dumps({
            "platform": fp.platform,
            "vendor": fp.vendor,
            "languages": fp.languages,
            "hardwareConcurrency": fp.hardware_concurrency,
            "deviceMemory": fp.device_memory,
            "maxTouchPoints": fp.max_touch_points,
            "screenWidth": fp.screen_width,
            "screenHeight": fp.screen_height,
            "outerWidth": fp.outer_width,
            "outerHeight": fp.outer_height,
            "deviceScaleFactor": fp.device_scale_factor,
            "chromeMajor": fp.chrome_major,
            "chromeFull": fp.chrome_full,
        }, ensure_ascii=False)
        context.set_extra_http_headers({
            "Accept-Language": fp.accept_language,
            "sec-ch-ua": f'"Google Chrome";v="{fp.chrome_major}", "Chromium";v="{fp.chrome_major}", "Not.A/Brand";v="24"',
            "sec-ch-ua-full-version-list": f'"Google Chrome";v="{fp.chrome_full}", "Chromium";v="{fp.chrome_full}", "Not.A/Brand";v="24.0.0.0"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": '"15.0.0"',
        })
        context.add_init_script(
            """(() => {
                const fp = __FP_PAYLOAD__;
                const defineGetter = (obj, prop, value) => {
                    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (_) {}
                };
                defineGetter(Navigator.prototype, 'webdriver', undefined);
                defineGetter(Navigator.prototype, 'platform', fp.platform);
                defineGetter(Navigator.prototype, 'vendor', fp.vendor);
                defineGetter(Navigator.prototype, 'language', fp.languages[0]);
                defineGetter(Navigator.prototype, 'languages', fp.languages);
                defineGetter(Navigator.prototype, 'hardwareConcurrency', fp.hardwareConcurrency);
                defineGetter(Navigator.prototype, 'deviceMemory', fp.deviceMemory);
                defineGetter(Navigator.prototype, 'maxTouchPoints', fp.maxTouchPoints);
                defineGetter(Screen.prototype, 'width', fp.screenWidth);
                defineGetter(Screen.prototype, 'height', fp.screenHeight);
                defineGetter(Screen.prototype, 'availWidth', fp.screenWidth);
                defineGetter(Screen.prototype, 'availHeight', fp.screenHeight - 40);
                defineGetter(window, 'outerWidth', fp.outerWidth);
                defineGetter(window, 'outerHeight', fp.outerHeight);
                defineGetter(window, 'devicePixelRatio', fp.deviceScaleFactor);
                if (!navigator.userAgentData) {
                    defineGetter(Navigator.prototype, 'userAgentData', {
                        mobile: false,
                        platform: 'Windows',
                        brands: [
                            { brand: 'Google Chrome', version: fp.chromeMajor },
                            { brand: 'Chromium', version: fp.chromeMajor },
                            { brand: 'Not.A/Brand', version: '24' },
                        ],
                        getHighEntropyValues: async hints => {
                            const values = {
                                architecture: 'x86', bitness: '64', mobile: false, model: '',
                                platform: 'Windows', platformVersion: '15.0.0', uaFullVersion: fp.chromeFull,
                                fullVersionList: [
                                    { brand: 'Google Chrome', version: fp.chromeFull },
                                    { brand: 'Chromium', version: fp.chromeFull },
                                    { brand: 'Not.A/Brand', version: '24.0.0.0' },
                                ],
                                wow64: false,
                            };
                            return Object.fromEntries(hints.filter(h => h in values).map(h => [h, values[h]]));
                        },
                    });
                }
                try {
                    const originalQuery = navigator.permissions && navigator.permissions.query;
                    if (originalQuery) {
                        navigator.permissions.query = params => params && params.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : originalQuery.call(navigator.permissions, params);
                    }
                } catch (_) {}
                try {
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                        return getParameter.call(this, parameter);
                    };
                } catch (_) {}
            }})();"""
            .replace("__FP_PAYLOAD__", fp_payload)
        )

    def _log_browser_proxy_status(self, page, proxy: ProxyConfig, label: str) -> None:
        health = self.current_proxy_health
        if health and health.success:
            self.log(f"[代理] {label}: {health.summary}")
        else:
            self.log(f"[代理] {label}: 未记录出口信息")

    def _register_team_sso(self, page, context) -> None:
        self.log(f"[认证] 开始 Team SSO 认证: {self.account.email}")
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        signin_url = self._create_openai_signin_url(context)
        page.goto(signin_url, wait_until="domcontentloaded", timeout=90000)
        self.log("[认证] 已打开 Team SSO 页面，准备填写随机邮箱")
        deadline = time.time() + 600
        route_error_retries = 0
        workspace_clicked = False
        approve_clicked = False
        last_wait_notice = 0.0
        bad_gateway_refreshes = 0
        while time.time() < deadline:
            refreshed, bad_gateway_refreshes = self._refresh_bad_gateway_if_visible(page, bad_gateway_refreshes, "Team SSO")
            if refreshed:
                time.sleep(3)
                continue
            error_text = self._detect_route_error(page)
            if error_text:
                if route_error_retries < 3 and self._retry_route_error(page):
                    route_error_retries += 1
                    self.log(f"Team SSO 页面超时，已点击重试 ({route_error_retries}/3)")
                    time.sleep(5)
                    continue
                raise RuntimeError(f"Team SSO 页面错误，通常是代理/风控导致接口超时: {error_text}")
            if self._complete_team_onboarding_if_visible(page):
                time.sleep(2)
                continue
            if self._has_chatgpt_session(page):
                if self._team_onboarding_pending(page):
                    if time.time() - last_wait_notice >= 15:
                        self.log("Team SSO 已登录，继续等待 onboarding 完成")
                        last_wait_notice = time.time()
                    time.sleep(2)
                    continue
                self.log("[认证] Team SSO 认证完成，已获得 ChatGPT 会话")
                return
            if not approve_clicked and self._approve_sso_login_if_visible(page):
                approve_clicked = True
                self._wait_team_sso_progress(page, "批准登录后跳转", 90)
                continue
            if not workspace_clicked and self._select_team_workspace_if_visible(page):
                workspace_clicked = True
                self._wait_team_sso_progress(page, "工作空间选择后跳转", 90)
                continue
            if self._fill_email_if_visible(page):
                self._wait_team_sso_progress(page, "提交 Team 邮箱后跳转", 60)
                continue
            if time.time() - last_wait_notice >= 15:
                self.log(f"Team SSO 等待页面推进中: {page.url[:100]}")
                last_wait_notice = time.time()
            time.sleep(2)
        raise TimeoutError("Team SSO 认证流程超时；如果浏览器停在人机验证或异常页面，请手动处理后重试")

    def _wait_team_sso_progress(self, page, label: str, timeout: int) -> None:
        started = time.time()
        start_url = page.url
        last_notice = 0.0
        bad_gateway_refreshes = 0
        while time.time() - started < timeout:
            refreshed, bad_gateway_refreshes = self._refresh_bad_gateway_if_visible(page, bad_gateway_refreshes, label)
            if refreshed:
                start_url = page.url
                time.sleep(3)
                continue
            if self._has_chatgpt_session(page):
                return
            current_url = page.url
            if current_url != start_url:
                self.log(f"{label}: 已跳转到 {current_url[:100]}")
                return
            if self._page_has_text(page, ["批准登录", "Approve login", "Approve sign-in", "Verify it's you", "验证是您本人", "sign-in-consent", "callback"]):
                return
            if time.time() - last_notice >= 15:
                remain = max(0, int(timeout - (time.time() - started)))
                self.log(f"{label}: 仍在等待页面响应，剩余约 {remain}s")
                last_notice = time.time()
            time.sleep(1)
        self.log(f"{label}: 等待 {timeout}s 未检测到跳转，继续轮询当前页面")

    def _refresh_bad_gateway_if_visible(self, page, refresh_count: int, label: str) -> tuple[bool, int]:
        try:
            title = page.title(timeout=1000)
        except Exception:
            title = ""
        try:
            body = page.locator("body").inner_text(timeout=1000)
        except Exception:
            body = ""
        text = f"{title}\n{body}"
        if not re.search(r"Bad gateway|Error code 502|Host\s+Error|HTTP\s*502", text, flags=re.I):
            return False, refresh_count
        if refresh_count >= 8:
            raise RuntimeError(f"{label}: 连续检测到 Bad gateway/502，已刷新 {refresh_count} 次仍未恢复")
        refresh_count += 1
        self.log(f"{label}: 检测到 Bad gateway/502，刷新页面重试 ({refresh_count}/8)")
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            self.log(f"{label}: 502 页面刷新失败，继续等待: {str(exc)[:120]}")
        return True, refresh_count

    def _page_has_text(self, page, texts: list[str]) -> bool:
        try:
            body = page.locator("body").inner_text(timeout=1000)
        except Exception:
            return False
        return any(text in body for text in texts)

    def _select_team_workspace_if_visible(self, page) -> bool:
        try:
            page_text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            page_text = ""
        if not re.search(r"采用何种方式|何种方式.*登录|工作空间|workspace|sign in", page_text, flags=re.I):
            return False
        try:
            clicked = page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(el => visible(el) && enabled(el));
                    const workspace = candidates.find(el => {
                        const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.replace(/\s+/g, ' ').trim();
                        return /工作空间|workspace|Trantow|Team/i.test(text)
                            && !/Google|Microsoft|Apple|密码|password|电话|phone/i.test(text);
                    });
                    if (!workspace) return false;
                    workspace.scrollIntoView({ block: 'center', inline: 'center' });
                    workspace.click();
                    return true;
                }"""
            )
        except Exception:
            clicked = False
        if clicked:
            self.log("已选择 Team 工作空间登录选项")
            return True
        return False

    def _team_onboarding_pending(self, page) -> bool:
        try:
            body = page.locator("body").inner_text(timeout=1000)
        except Exception:
            return False
        return bool(re.search(
            r"What kind of work do you do|Select the option that best applies|你从事哪种工作|你从事什么工作|借助\s*Codex|更快完成工作|选择你的工作应用|启用这些应用|work apps|Maybe later|Skip|稍后再说|跳过",
            body,
            flags=re.I,
        ))

    def _complete_team_onboarding_if_visible(self, page) -> bool:
        try:
            result = page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const body = document.body?.textContent || '';
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(el => visible(el) && enabled(el));

                    if (/What kind of work do you do|Select the option that best applies|你从事哪种工作|你从事什么工作/i.test(body)) {
                        const target = candidates.find(el => /Engineering|工程/i.test((el.textContent || '').trim())) || candidates[0];
                        if (!target) return '';
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        target.click();
                        return 'work';
                    }

                    const later = candidates.find(el => /Maybe later|Not now|稍后再说|稍後再說|以后再说|暫時不要/i.test((el.textContent || '').trim()));
                    if (later) {
                        later.scrollIntoView({ block: 'center', inline: 'center' });
                        later.click();
                        return 'later';
                    }

                    const skip = candidates.find(el => /Skip|跳过|跳過/i.test((el.textContent || '').trim()));
                    if (skip) {
                        skip.scrollIntoView({ block: 'center', inline: 'center' });
                        skip.click();
                        return 'skip';
                    }

                    return '';
                }"""
            )
        except Exception:
            result = ""
        if result:
            labels = {"work": "已选择 Team onboarding 工作类型: Engineering", "later": "已点击 Team onboarding 稍后再说", "skip": "已点击 Team onboarding 跳过"}
            self.log(labels.get(str(result), "已处理 Team onboarding"))
            return True
        return False

    def _approve_sso_login_if_visible(self, page) -> bool:
        try:
            clicked = page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]')).filter(el => visible(el) && enabled(el));
                    const approve = candidates.find(el => {
                        const text = `${el.value || ''} ${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.replace(/\s+/g, ' ').trim();
                        return /批准登录|批准登入|Approve\s+(login|sign[- ]?in)|Approve\s+sign[- ]?in/i.test(text)
                            && !/不认识|不認識|Not.*account|deny|cancel/i.test(text);
                    });
                    if (!approve) return false;
                    approve.scrollIntoView({ block: 'center', inline: 'center' });
                    approve.click();
                    return true;
                }"""
            )
        except Exception:
            clicked = False
        if clicked:
            self.log("已点击批准登录")
            return True
        return False

    def _prepare_browser_oauth_url(self) -> tuple[str, str]:
        state = random_urlsafe_string(24)
        code_verifier = random_urlsafe_string(64)
        query = urlencode({
            "client_id": DEFAULT_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": DEFAULT_REDIRECT_URI,
            "scope": "openid email profile offline_access",
            "state": state,
            "code_challenge": pkce_code_challenge(code_verifier),
            "code_challenge_method": "S256",
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "login_hint": self.account.email,
        })
        return f"{AUTH_BASE_URL}/oauth/authorize?{query}", code_verifier

    def _authorize_rt_from_browser(self, context, page) -> dict:
        oauth_url, code_verifier = self._prepare_browser_oauth_url()
        self.log("在当前 Team 标签页发起 OAuth 授权获取 RT")
        page.goto(oauth_url, wait_until="domcontentloaded", timeout=90000)
        started = time.time()
        approve_clicked = False
        last_notice = 0.0
        bad_gateway_refreshes = 0
        while time.time() - started < 180:
            refreshed, bad_gateway_refreshes = self._refresh_bad_gateway_if_visible(page, bad_gateway_refreshes, "Team OAuth")
            if refreshed:
                time.sleep(3)
                continue
            current_url = page.url
            if current_url.startswith(DEFAULT_REDIRECT_URI):
                result = self._extract_oauth_callback_from_url(current_url)
                self.log("已获取 OAuth 授权 code，交换 refresh_token")
                return self._exchange_browser_code_for_token(context, result["code"], code_verifier)
            if self._complete_team_onboarding_if_visible(page):
                time.sleep(2)
                continue
            if not approve_clicked and self._approve_sso_login_if_visible(page):
                approve_clicked = True
                self._wait_team_sso_progress(page, "OAuth 批准登录后跳转", 60)
                continue
            if self._click_codex_consent_if_visible(page):
                self._wait_team_sso_progress(page, "OAuth 授权确认后跳转", 60)
                continue
            if time.time() - last_notice >= 15:
                remain = max(0, int(180 - (time.time() - started)))
                self.log(f"Team OAuth 等待 callback 中，剩余约 {remain}s，当前 URL: {current_url[:100]}")
                last_notice = time.time()
            time.sleep(1)
        raise TimeoutError(f"Team OAuth 授权 180 秒内未到 callback，当前 URL: {page.url}")

    def _click_codex_consent_if_visible(self, page) -> bool:
        try:
            clicked = page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]')).filter(el => visible(el) && enabled(el));
                    const target = candidates.find(el => {
                        const text = `${el.value || ''} ${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.replace(/\s+/g, ' ').trim();
                        return /Authorize|授权|允許|允许|Continue|继续|続行|Approve/i.test(text);
                    });
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    target.click();
                    return true;
                }"""
            )
        except Exception:
            clicked = False
        if clicked:
            self.log("已点击授权/继续按钮")
            return True
        return False

    def _extract_oauth_callback_from_url(self, callback_url: str) -> dict:
        parsed = urlparse(callback_url)
        query = parse_qs(parsed.query)
        code = (query.get("code") or [""])[0]
        if not code:
            raise RuntimeError(f"callback 中缺少 code: {callback_url}")
        return {"callback_url": callback_url, "code": code}

    def _exchange_browser_code_for_token(self, context, code: str, code_verifier: str) -> dict:
        last_error = ""
        for token_url in AUTH_OAUTH_TOKEN_URLS:
            response = context.request.post(
                token_url,
                headers=openai_browser_headers({
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-site",
                }),
                data={
                    "grant_type": "authorization_code",
                    "client_id": DEFAULT_CLIENT_ID,
                    "code": code,
                    "redirect_uri": DEFAULT_REDIRECT_URI,
                    "code_verifier": code_verifier,
                },
                timeout=30000,
            )
            if response.ok:
                return normalize_openai_auth_record(self.account.email, response.json())
            last_error = f"endpoint={token_url} HTTP {response.status} {response.text()[:300]}"
        raise RuntimeError(f"Team Code换Token失败: {last_error}")

    def _session_payload_from_record(self, record: dict) -> dict:
        return {
            "user": {"email": self.account.email},
            "accessToken": str(record.get("access_token") or ""),
            "expires": str(record.get("expired") or ""),
        }

    def _register(self, page, context) -> None:
        self.log(f"[认证] 开始注册或登录: {self.account.email}")
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        signin_url = self._create_openai_signin_url(context)
        otp_min_timestamp = time.time() - 10
        page.goto(signin_url, wait_until="domcontentloaded", timeout=90000)
        self.log("[认证] 已打开 OpenAI 认证页；如出现人机验证，请在浏览器中手动完成")

        deadline = time.time() + 600
        email_code_submitted = False
        about_you_submitted = False
        about_you_submitted_at = 0.0
        about_you_submit_retry_at = 0.0
        route_error_retries = 0
        while time.time() < deadline:
            url = page.url
            error_text = self._detect_route_error(page)
            if error_text:
                if route_error_retries < 3 and self._retry_route_error(page):
                    route_error_retries += 1
                    self.log(f"OpenAI 页面超时，已点击重试 ({route_error_retries}/3)")
                    time.sleep(5)
                    continue
                raise RuntimeError(f"OpenAI 页面错误，通常是代理/风控导致接口超时: {error_text}")
            if self._has_chatgpt_session(page):
                self.log("[认证] 认证完成，已获得 ChatGPT 会话")
                return
            if "add-phone" in url or "phone-verification" in url:
                if self._handle_phone_continue_if_visible(page):
                    email_code_submitted = False
                    about_you_submitted = False
                    about_you_submitted_at = 0.0
                    about_you_submit_retry_at = 0.0
                    continue
                raise RuntimeError("当前账号触发手机验证，但未找到可自动处理的电话验证页面")
            if self._handle_phone_continue_if_visible(page):
                email_code_submitted = False
                about_you_submitted = False
                about_you_submitted_at = 0.0
                about_you_submit_retry_at = 0.0
                continue
            if "password" in url and self._has_visible_password(page):
                self._fill_password_step(page)
                email_code_submitted = False
                about_you_submitted = False
                about_you_submitted_at = 0.0
                about_you_submit_retry_at = 0.0
                continue
            if "about-you" in url or self._has_about_you_form(page):
                email_code_submitted = False
                if about_you_submitted:
                    now = time.time()
                    if now - about_you_submitted_at >= 10 and now - about_you_submit_retry_at >= 10:
                        if self._about_you_current_values_ok(page):
                            if self._click_finish_creating_account(page) or self._click_continue(page):
                                about_you_submit_retry_at = now
                                self.log("基础资料已提交但页面未跳转，已重新点击提交按钮")
                        else:
                            self.log("基础资料提交后输入值异常，将重新填写")
                            about_you_submitted = False
                            about_you_submitted_at = 0.0
                            about_you_submit_retry_at = 0.0
                            continue
                    time.sleep(1)
                    continue
                self._fill_about_you(page)
                about_you_submitted = True
                about_you_submitted_at = time.time()
                about_you_submit_retry_at = 0.0
                continue
            if "email-verification" in url or self._has_otp_input(page):
                if email_code_submitted:
                    time.sleep(2)
                    continue
                self._submit_email_code(page, otp_min_timestamp)
                email_code_submitted = True
                continue
            if self._fill_email_if_visible(page):
                otp_min_timestamp = time.time()
                email_code_submitted = False
                about_you_submitted = False
                about_you_submitted_at = 0.0
                about_you_submit_retry_at = 0.0
                continue
            if about_you_submitted:
                about_you_submitted = False
                about_you_submitted_at = 0.0
                about_you_submit_retry_at = 0.0
            time.sleep(2)

        raise TimeoutError("认证流程超时；如果浏览器停在人机验证或异常页面，请手动处理后重试")

    def _login_existing_account(self, page, context) -> None:
        self.log(f"开始登录已有账号: {self.account.email}")
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        signin_url = self._create_login_url(context)
        otp_min_timestamp = time.time() - 10
        page.goto(signin_url, wait_until="domcontentloaded", timeout=90000)
        self.log("已打开 OpenAI 登录页；如出现人机验证，请在浏览器中手动完成")

        deadline = time.time() + 600
        email_code_submitted = False
        route_error_retries = 0
        while time.time() < deadline:
            url = page.url
            error_text = self._detect_route_error(page)
            if error_text:
                if route_error_retries < 3 and self._retry_route_error(page):
                    route_error_retries += 1
                    self.log(f"OpenAI 登录页超时，已点击重试 ({route_error_retries}/3)")
                    time.sleep(5)
                    continue
                raise RuntimeError(f"OpenAI 登录页错误，通常是代理/风控导致接口超时: {error_text}")
            if self._has_chatgpt_session(page):
                self.log("登录完成，已获得 ChatGPT 会话")
                return
            if "add-phone" in url or "phone-verification" in url:
                raise RuntimeError("当前账号触发手机验证，重新获取长链接已停止")
            if "password" in url and self._has_visible_password(page):
                raise RuntimeError("该账号进入密码登录页，当前只支持邮箱验证码重新获取长链接")
            if "email-verification" in url or self._has_otp_input(page):
                if email_code_submitted:
                    time.sleep(2)
                    continue
                self._submit_email_code(page, otp_min_timestamp)
                email_code_submitted = True
                continue
            if self._fill_email_if_visible(page):
                otp_min_timestamp = time.time()
                email_code_submitted = False
                continue
            time.sleep(2)

        raise TimeoutError("重新获取长链接登录流程超时；如果浏览器停在人机验证或异常页面，请手动处理后重试")

    def _detect_route_error(self, page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=700)
        except Exception:
            return ""
        normalized = re.sub(r"\s+", " ", text).strip()
        if "糟糕，出错了" in normalized or "Operation timed out" in normalized or "Route Error" in normalized:
            return normalized[:400]
        return ""

    def _retry_route_error(self, page) -> bool:
        selectors = [
            'button:has-text("Try again")',
            'button:has-text("重试")',
            'a:has-text("Try again")',
            'a:has-text("重试")',
            '[role="button"]:has-text("Try again")',
            '[role="button"]:has-text("重试")',
        ]
        for selector in selectors:
            target = page.locator(selector).first
            try:
                if target.is_visible(timeout=800):
                    target.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    return True
            except Exception:
                continue
        try:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception:
            return False

    def _create_openai_signin_url(self, context) -> str:
        csrf_value, device_id = self._get_chatgpt_csrf_and_device(context)
        if not csrf_value:
            raise RuntimeError("未找到 ChatGPT CSRF cookie，无法打开认证页")
        if not device_id:
            device_id = str(uuid.uuid4())

        query = urlencode({
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": "0111",
            "screen_hint": "signup",
            "login_hint": self.account.email,
            "locale": self.fingerprint.locale,
        })
        response = context.request.post(
            f"{CHATGPT_BASE_URL}/api/auth/signin/openai?{query}",
            form={"callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_value, "json": "true"},
            headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language},
        )
        if not response.ok:
            raise RuntimeError(f"打开认证页失败: HTTP {response.status} {response.text()[:300]}")
        payload = response.json()
        signin_url = payload.get("url")
        if not signin_url:
            raise RuntimeError(f"打开认证页缺少跳转 URL: {payload}")
        return signin_url

    def _create_login_url(self, context) -> str:
        csrf_value, device_id = self._get_chatgpt_csrf_and_device(context)
        if not csrf_value:
            raise RuntimeError("未找到 ChatGPT CSRF cookie，无法打开登录页")
        if not device_id:
            device_id = str(uuid.uuid4())

        query = urlencode({
            "prompt": "login",
            "ext-oai-did": device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "ext-passkey-client-capabilities": "0111",
            "screen_hint": "login",
            "login_hint": self.account.email,
            "locale": self.fingerprint.locale,
        })
        response = context.request.post(
            f"{CHATGPT_BASE_URL}/api/auth/signin/openai?{query}",
            form={"callbackUrl": f"{CHATGPT_BASE_URL}/", "csrfToken": csrf_value, "json": "true"},
            headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language},
        )
        if not response.ok:
            raise RuntimeError(f"打开登录页失败: HTTP {response.status} {response.text()[:300]}")
        payload = response.json()
        signin_url = payload.get("url")
        if not signin_url:
            raise RuntimeError(f"打开登录页缺少跳转 URL: {payload}")
        return signin_url

    def _get_chatgpt_csrf_and_device(self, context) -> tuple[str, str]:
        cookies = context.cookies([CHATGPT_BASE_URL, "https://openai.com"])
        csrf_value = ""
        device_id = ""
        for cookie in cookies:
            if cookie.get("name") == "__Host-next-auth.csrf-token":
                csrf_value = unquote(cookie.get("value", "")).split("|")[0]
            if cookie.get("name") == "oai-did":
                device_id = cookie.get("value", "")
        if not csrf_value:
            try:
                response = context.request.get(
                    f"{CHATGPT_BASE_URL}/api/auth/csrf",
                    headers={"Accept": "application/json", "Accept-Language": self.fingerprint.accept_language, "Referer": f"{CHATGPT_BASE_URL}/"},
                    timeout=30000,
                )
                if response.ok:
                    payload = response.json()
                    csrf_value = str(payload.get("csrfToken") or "").strip()
            except Exception as exc:
                self.log(f"获取 ChatGPT CSRF 接口失败: {str(exc)[:160]}")
            if not csrf_value:
                cookies = context.cookies([CHATGPT_BASE_URL, "https://openai.com"])
                for cookie in cookies:
                    if cookie.get("name") == "__Host-next-auth.csrf-token":
                        csrf_value = unquote(cookie.get("value", "")).split("|")[0]
                        break
        if not device_id:
            cookies = context.cookies([CHATGPT_BASE_URL, "https://openai.com"])
            for cookie in cookies:
                if cookie.get("name") == "oai-did":
                    device_id = cookie.get("value", "")
                    break
        return csrf_value, device_id

    def _has_chatgpt_session(self, page) -> bool:
        pages = [page]
        try:
            for candidate in page.context.pages:
                if candidate not in pages:
                    pages.append(candidate)
        except Exception:
            pass
        for candidate in pages:
            try:
                if not str(candidate.url or "").startswith(CHATGPT_BASE_URL):
                    continue
                payload = candidate.evaluate(
                    """async () => {
                        const resp = await fetch('/api/auth/session', { credentials: 'include' });
                        if (!resp.ok) return null;
                        return await resp.json();
                    }"""
                )
                if payload and payload.get("accessToken"):
                    return True
            except Exception:
                continue
        return False

    def _context_has_chatgpt_page(self, page) -> bool:
        try:
            pages = list(page.context.pages)
        except Exception:
            pages = [page]
        for candidate in pages:
            try:
                if str(candidate.url or "").startswith(CHATGPT_BASE_URL):
                    return True
            except Exception:
                pass
        return False

    def _visible_inputs(self, page, selectors: list[str]):
        visible = []
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 12)
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if item.is_visible():
                        visible.append(item)
                except Exception:
                    pass
        return visible

    def _click_continue(self, page) -> bool:
        selectors = [
            'button:has-text("Finish creating account")',
            'button:has-text("Finalizar la creación de la cuenta")',
            'button:has-text("Finalizar la creacion de la cuenta")',
            'button[data-dd-action-name="Continue"][type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("アカウントの作成を完了する")',
            'button:has-text("作成を完了")',
            'button:has-text("继续")',
            'button:has-text("完成帐户创建")',
            'button:has-text("完成账户创建")',
            'button:has-text("Next")',
            'button:has-text("下一步")',
            'button:has-text("Create")',
            'button:has-text("完成")',
            'button[type="submit"]',
            '[role="button"]:has-text("Finish creating account")',
            '[role="button"]:has-text("Finalizar la creación de la cuenta")',
            '[role="button"]:has-text("Finalizar la creacion de la cuenta")',
            '[role="button"]:has-text("Continue")',
            '[role="button"]:has-text("アカウントの作成を完了する")',
            '[role="button"]:has-text("作成を完了")',
        ]
        for selector in selectors:
            button = page.locator(selector).first
            try:
                if button.is_visible(timeout=700):
                    button.click(timeout=5000)
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    return True
            except Exception:
                continue
        if self._click_submit_button_by_dom(page):
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            return True
        return False

    def _click_submit_button_by_dom(self, page) -> bool:
        return bool(page.evaluate(
            """() => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
                const button = buttons.find(el =>
                    (el.textContent || '').includes('Finish creating account')
                    || (el.textContent || '').includes('アカウントの作成を完了する')
                    || (el.textContent || '').includes('作成を完了')
                    || (el.textContent || '').includes('Continue')
                    || (el.textContent || '').includes('继续')
                    || (el.textContent || '').includes('完成帐户创建')
                    || (el.textContent || '').includes('完成账户创建')
                    || (el.textContent || '').includes('続行')
                    || (el.getAttribute('data-dd-action-name') || '') === 'Continue'
                    || (el.type || '').toLowerCase() === 'submit'
                );
                if (!button || button.getAttribute('aria-disabled') === 'true' || button.disabled) return false;
                button.scrollIntoView({ block: 'center', inline: 'center' });
                button.focus();
                const form = button.closest('form');
                if (form && typeof form.requestSubmit === 'function') {
                    form.requestSubmit(button);
                    return true;
                }
                button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse', isPrimary: true }));
                button.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
                button.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'mouse', isPrimary: true }));
                button.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }));
                button.click();
                return true;
            }"""
        ))

    def _fill_email_if_visible(self, page) -> bool:
        inputs = self._visible_inputs(page, [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="username"]',
            'input[autocomplete="email"]',
        ])
        if not inputs:
            return False
        self.log("[认证] 填写邮箱")
        inputs[0].fill(self.account.email)
        self._click_continue(page)
        return True

    def _handle_phone_continue_if_visible(self, page) -> bool:
        if not self.phone_provider:
            return False
        current_url = str(getattr(page, "url", "") or "")
        required_route = "add-phone" in current_url or "phone-verification" in current_url
        has_phone_form = self._has_register_phone_number_form(page)
        clicked_phone_continue = False
        if required_route and not has_phone_form:
            clicked_phone_continue = self._click_use_phone_number_continue(page)
            if clicked_phone_continue:
                time.sleep(1)
                has_phone_form = self._has_register_phone_number_form(page)
        if not has_phone_form:
            return False

        self.log("[手机] 服务要求电话验证，开始使用手机号池")
        last_error = ""
        for _ in range(30):
            phone = self.phone_provider("next", self.account.email, {"country": "US"})
            if not phone:
                detail = f"，最后错误: {last_error}" if last_error else ""
                raise RuntimeError(f"手机号池没有可用的美国 +1 手机号，无法继续电话验证{detail}")
            phone_number = str(phone.get("number") or "").strip()
            local_number = normalize_us_phone_for_form(phone_number)
            number_submitted = False
            try:
                if not phone_number.startswith("+1") or len(local_number) < 10:
                    raise RuntimeError("当前电话验证流程要求美国 +1 手机号")
                if not self._select_us_phone_country(page):
                    raise RuntimeError("未能将手机号国家切换为美国 +1")
                self.log(f"[手机] 填写美国电话验证手机号: {phone_number}")
                self._fill_register_phone_number(page, local_number[-10:])
                if not self._click_continue(page):
                    raise RuntimeError("手机号已填写，但未找到继续按钮")
                number_submitted = True
                self._wait_for_register_phone_code_form(page)
                code = self.phone_provider("code", self.account.email, phone)
                if not code:
                    raise RuntimeError("短信链接未读取到验证码")
                self.log(f"[手机] 读取到电话验证码: {code}")
                self._submit_register_phone_code(page, str(code))
                self.active_register_phone = dict(phone)
                self.log("[手机] 已提交电话验证码，继续认证流程")
                time.sleep(3)
                return True
            except Exception as exc:
                last_error = str(exc)
                if not number_submitted:
                    raise RuntimeError(last_error)
                self.phone_provider("bad", self.account.email, {**phone, "error": last_error})
                self.log(f"[手机] 手机号 {phone_number} 验证不可用，切换下一个: {last_error}")
                if not self._reset_phone_registration_for_next_number(page):
                    raise RuntimeError(f"手机号验证失败且无法回到号码输入页: {last_error}")
                time.sleep(1)
        raise RuntimeError(f"手机号验证失败次数过多: {last_error or 'unknown'}")

    def _has_register_phone_number_form(self, page) -> bool:
        inputs = self._visible_inputs(page, [
            'input[type="tel"]',
            'input[inputmode="tel"]',
            'input[name*="phone" i]',
            'input[autocomplete*="tel" i]',
            'input[aria-label*="phone" i]',
            'input[aria-label*="手机" i]',
            'input[placeholder*="phone" i]',
            'input[placeholder*="手机" i]',
        ])
        if not inputs:
            return False
        for input_box in inputs:
            try:
                if input_box.evaluate(
                    r"""el => {
                        const meta = [el.type, el.inputMode, el.name, el.id, el.placeholder, el.autocomplete, el.getAttribute('aria-label')]
                            .join(' ')
                            .toLowerCase();
                        return /phone|tel|手机|手機|電話|\+1|\+81/.test(meta);
                    }"""
                ):
                    return True
            except Exception:
                pass
        try:
            text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            text = ""
        return bool(re.search(r"country|国家|國家|日本|美国|美國|United States", text, flags=re.I))

    def _click_use_phone_number_continue(self, page) -> bool:
        try:
            return bool(page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(el => visible(el) && enabled(el));
                    const target = candidates.find(el => {
                        const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.replace(/\s+/g, ' ').trim();
                        const hasPhone = /使用电话号码|使用電話號碼|電話番号|phone number/i.test(text);
                        const hasContinue = /继续|繼續|続行|continue/i.test(text);
                        return hasPhone && hasContinue;
                    });
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    target.click();
                    return true;
                }"""
            ))
        except Exception:
            return False

    def _select_us_phone_country(self, page) -> bool:
        result = page.evaluate(
            r"""() => {
                const visible = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const setNativeValue = (el, value) => {
                    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) desc.set.call(el, value); else el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const isUnitedStates = text => {
                    const value = String(text || '').replace(/\s+/g, ' ').trim();
                    if (/美属|美屬|萨摩亚|薩摩亞|维尔京|維爾京|关岛|關島|波多黎各/.test(value)) return false;
                    if (/Samoa|Virgin|Guam|Mariana|Puerto Rico/i.test(value)) return false;
                    return /(^|\s)美国\s*(\(\+?1\)|\+?1)?$/i.test(value)
                        || /(^|\s)美國\s*(\(\+?1\)|\+?1)?$/i.test(value)
                        || /United States\s*(\(\+?1\)|\+?1)?/i.test(value);
                };

                for (const select of Array.from(document.querySelectorAll('select')).filter(visible)) {
                    const matched = Array.from(select.options || []).find(opt => {
                        const value = String(opt.value || '').trim().toUpperCase();
                        return value === 'US' || isUnitedStates(opt.textContent || '');
                    });
                    if (matched) {
                        setNativeValue(select, matched.value);
                        return 'select';
                    }
                }

                const buttons = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], [aria-haspopup]')).filter(visible);
                const current = buttons.find(el => {
                    const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.replace(/\s+/g, ' ');
                    return /\+81|日本|Japan|country|region|国家|國家/i.test(text);
                });
                if (current) {
                    current.scrollIntoView({ block: 'center', inline: 'center' });
                    current.click();
                    return 'opened';
                }
                return '';
            }"""
        )
        if result == "select":
            return True
        if result == "opened":
            time.sleep(0.8)
            selected = page.evaluate(
                r"""() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const isUnitedStates = text => {
                        const value = String(text || '').replace(/\s+/g, ' ').trim();
                        if (/美属|美屬|萨摩亚|薩摩亞|维尔京|維爾京|关岛|關島|波多黎各/.test(value)) return false;
                        if (/Samoa|Virgin|Guam|Mariana|Puerto Rico/i.test(value)) return false;
                        return /^(?:🇺🇸\s*)?美国\s*(\(\+?1\)|\+?1)?$/i.test(value)
                            || /^(?:🇺🇸\s*)?美國\s*(\(\+?1\)|\+?1)?$/i.test(value)
                            || /United States\s*(\(\+?1\)|\+?1)?/i.test(value);
                    };
                    const options = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], li, button, div'))
                        .filter(visible)
                        .filter(el => isUnitedStates(el.textContent || el.getAttribute('aria-label') || ''));
                    const target = options[0];
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    target.click();
                    return true;
                }"""
            )
            if selected:
                time.sleep(0.5)
                return True
        self.log("未能确认手机号国家已切换为美国")
        return False

    def _fill_register_phone_number(self, page, local_number: str) -> None:
        selectors = [
            'input[type="tel"]',
            'input[inputmode="tel"]',
            'input[inputmode="numeric"]',
            'input[name*="phone" i]',
            'input[autocomplete*="tel" i]',
            'input[aria-label*="phone" i]',
            'input[aria-label*="手机" i]',
            'input[placeholder*="phone" i]',
            'input[placeholder*="手机" i]',
        ]
        inputs = self._visible_inputs(page, selectors)
        if not inputs:
            raise RuntimeError("未找到手机号输入框")
        if not self._force_fill_locator(inputs[0], local_number):
            raise RuntimeError("手机号输入框填写失败")

    def _looks_like_register_phone_code_page(self, page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=1000)
        except Exception:
            text = ""
        normalized = re.sub(r"\s+", " ", str(text or ""))
        has_phone = re.search(r"短信|SMS|text message|手机号|手機|電話|phone number|\+\d", normalized, flags=re.I)
        has_code = re.search(r"验证码|驗證碼|コード|code|6[- ]?digit|verification", normalized, flags=re.I)
        has_email_only = re.search(r"email|邮件|郵件|邮箱|電子メール", normalized, flags=re.I)
        return bool(has_phone and has_code and not (has_email_only and not re.search(r"短信|SMS|text message|phone", normalized, flags=re.I)))

    def _register_phone_code_inputs(self, page):
        strict_inputs = self._visible_inputs(page, [
            'input[autocomplete="one-time-code"]',
            'input[name="code"]',
            'input[aria-label*="code" i]',
            'input[placeholder*="code" i]',
            'input[aria-label*="验证码" i]',
            'input[placeholder*="验证码" i]',
        ])
        if strict_inputs:
            return strict_inputs
        if self._looks_like_register_phone_code_page(page):
            numeric_inputs = self._visible_inputs(page, ['input[inputmode="numeric"]'])
            if len(numeric_inputs) >= 6:
                return numeric_inputs
            code_inputs = []
            for input_box in numeric_inputs:
                try:
                    if input_box.evaluate(
                        r"""el => {
                            const meta = [el.type, el.inputMode, el.name, el.id, el.placeholder, el.autocomplete, el.getAttribute('aria-label')]
                                .join(' ')
                                .toLowerCase();
                            const maxLength = Number(el.maxLength || 0);
                            if (/phone|tel|手机|手機|電話|\+1|\+81/.test(meta)) return false;
                            return maxLength > 0 && maxLength <= 8;
                        }"""
                    ):
                        code_inputs.append(input_box)
                except Exception:
                    pass
            return code_inputs
        return []

    def _wait_after_register_phone_code_submit(self, page, timeout: int = 30) -> None:
        started = time.time()
        while time.time() - started < timeout:
            if self._has_chatgpt_session(page):
                return
            if "about-you" in page.url or self._has_about_you_form(page):
                return
            if "password" in page.url and self._has_visible_password(page):
                return
            if not self._register_phone_code_inputs(page):
                return
            time.sleep(1)
        raise RuntimeError(f"手机验证码提交后仍停留在短信验证页: {self._page_text_summary(page)}")

    def _wait_for_register_phone_code_form(self, page, timeout: int = 45) -> None:
        started = time.time()
        while time.time() - started < timeout:
            if self._has_chatgpt_session(page):
                return
            if self._register_phone_code_inputs(page):
                return
            time.sleep(1)
        raise RuntimeError(f"提交手机号后未进入短信验证码页: {self._page_text_summary(page)}")

    def _submit_register_phone_code(self, page, code: str) -> None:
        inputs = self._register_phone_code_inputs(page)
        if not inputs:
            if self._has_chatgpt_session(page):
                return
            raise RuntimeError("页面未找到手机验证码输入框")
        if len(inputs) >= 6:
            for index, char in enumerate(code[:6]):
                inputs[index].fill(char)
        else:
            inputs[0].fill(code)
        self._click_continue(page)
        self._wait_after_register_phone_code_submit(page)

    def _reset_phone_registration_for_next_number(self, page) -> bool:
        if self._has_register_phone_number_form(page):
            return True
        if self._click_button_by_text(page, ["Change phone", "Edit", "Back", "更改", "编辑", "返回", "戻る"]):
            time.sleep(1)
            return self._has_register_phone_number_form(page)
        try:
            page.go_back(wait_until="domcontentloaded", timeout=15000)
            time.sleep(1)
            return self._has_register_phone_number_form(page)
        except Exception:
            return False

    def _has_visible_password(self, page) -> bool:
        return bool(self._visible_inputs(page, ['input[type="password"]', 'input[name="password"]']))

    def _fill_password_step(self, page) -> None:
        if not self.account.password:
            self.account.password = self._generate_password()
            self.log(f"账号需要密码步骤，已生成密码: {self.account.password}")
            openai_password = self.account.password
        elif len(self.account.password) < 12:
            openai_password = self.account.password + self.account.password
            self.log("账号需要密码步骤，导入密码不足 12 位，填写 OpenAI 时重复一遍")
        else:
            self.log("账号需要密码步骤，使用导入行已有密码继续")
            openai_password = self.account.password

        inputs = self._visible_inputs(page, ['input[type="password"]', 'input[name="password"]'])
        if not inputs:
            raise RuntimeError("进入密码步骤但未找到密码输入框")
        for input_box in inputs:
            self._force_fill_locator(input_box, openai_password)
        if not self._click_continue(page):
            raise RuntimeError("密码已填写，但未找到继续按钮")

    def _generate_password(self) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        suffix = "!A7"
        return "".join(random.choice(alphabet) for _ in range(13)) + suffix

    def _has_otp_input(self, page) -> bool:
        if "about-you" in page.url or self._has_about_you_form(page):
            return False
        if self._looks_like_register_phone_code_page(page):
            return False
        return bool(self._visible_inputs(page, [
            'input[autocomplete="one-time-code"]',
            'input[name="code"]',
            'input[aria-label*="code" i]',
            'input[placeholder*="code" i]',
            'input[aria-label*="验证码" i]',
            'input[placeholder*="验证码" i]',
        ]))

    def _submit_email_code(self, page, min_timestamp: float) -> None:
        self.log("等待 OpenAI 邮箱验证码")
        if not self.otp_reader:
            self.otp_reader = HotmailOtpReader(self.account, self.log, "")
        code = self.otp_reader.wait_for_code(min_timestamp)
        inputs = self._visible_inputs(page, [
            'input[autocomplete="one-time-code"]',
            'input[inputmode="numeric"]',
            'input[type="tel"]',
            'input[name="code"]',
        ])
        if not inputs:
            raise RuntimeError("页面未找到验证码输入框")
        if len(inputs) >= 6:
            for index, char in enumerate(code[:6]):
                inputs[index].fill(char)
        else:
            inputs[0].fill(code)
        continue_url = self._validate_email_code_api(page, code)
        self.log("已通过接口提交邮箱验证码")
        if continue_url:
            page.goto(continue_url, wait_until="domcontentloaded", timeout=90000)
        self._wait_after_otp_submit(page)

    def _validate_email_code_api(self, page, code: str) -> str:
        last_detail = ""
        for attempt in range(3):
            result = page.evaluate(
                """async ({code}) => {
                    const resp = await fetch('/api/accounts/email-otp/validate', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            accept: 'application/json',
                            'content-type': 'application/json',
                            origin: 'https://auth.openai.com',
                            referer: 'https://auth.openai.com/email-verification',
                        },
                        body: JSON.stringify({ code }),
                    });
                    const text = await resp.text();
                    let data = null;
                    try { data = JSON.parse(text); } catch (_) {}
                    return { ok: resp.ok, status: resp.status, text, data };
                }""",
                {"code": code},
            )
            if result.get("ok"):
                payload = result.get("data") or {}
                return str(payload.get("continue_url") or payload.get("page", {}).get("payload", {}).get("url") or "")

            last_detail = str(result.get("text") or result.get("status") or "")
            if self._is_cloudflare_challenge(last_detail) and attempt < 2:
                self.log("EmailOtpValidate 触发 Cloudflare challenge，正在浏览器中打开挑战页并等待放行")
                self._handle_cloudflare_challenge(page, last_detail)
                continue
            break

        if self._is_cloudflare_challenge(last_detail):
            raise RuntimeError("EmailOtpValidate 被 Cloudflare 持续拦截。请换更干净的动态代理，或在浏览器里的 Cloudflare 页面手动等待通过后重试。")
        raise RuntimeError(f"EmailOtpValidate 接口失败: {last_detail[:800]}")

    def _is_cloudflare_challenge(self, text: str) -> bool:
        value = str(text or "")
        return "challenges.cloudflare.com" in value or "__cf_chl" in value or "Just a moment" in value

    def _extract_cloudflare_challenge_url(self, text: str) -> str:
        value = unescape(str(text or ""))
        for pattern in [r'cUPMDTk:\s*"([^"]+)"', r'history\.replaceState\([^,]+,[^,]+,"([^"]+)"']:
            match = re.search(pattern, value)
            if match:
                raw = match.group(1).replace("\\/", "/")
                return raw if raw.startswith("http") else f"{AUTH_BASE_URL}{raw}"
        return ""

    def _handle_cloudflare_challenge(self, page, challenge_html: str) -> None:
        if self.headless:
            raise RuntimeError("触发 Cloudflare challenge，但当前开启了无头模式，无法手动验证；请取消 UI 中的“无头浏览器”后重试")
        challenge_url = self._extract_cloudflare_challenge_url(challenge_html)
        if not challenge_url:
            raise RuntimeError("触发 Cloudflare challenge，但未能解析挑战 URL；请换代理或手动刷新页面后重试")

        challenge_page = page.context.new_page()
        challenge_page.bring_to_front()
        challenge_page.goto(challenge_url, wait_until="domcontentloaded", timeout=90000)
        self.log("Cloudflare 页面已在新标签页打开，请在弹出的 Chromium 窗口中手动完成验证")
        started = time.time()
        last_notice = 0.0
        while time.time() - started < 120:
            try:
                challenge_page.bring_to_front()
            except Exception:
                pass
            if self._has_cloudflare_clearance(page):
                self.log("Cloudflare 已放行，重试提交邮箱验证码")
                break
            if time.time() - last_notice >= 10:
                remain = max(0, int(120 - (time.time() - started)))
                self.log(f"仍在等待 Cloudflare 放行，剩余约 {remain}s")
                last_notice = time.time()
            time.sleep(2)
        if not self._has_cloudflare_clearance(page):
            raise RuntimeError("Cloudflare 120 秒内未放行；当前代理/IP 风控过高，请更换动态代理后重试")
        try:
            challenge_page.close()
        except Exception:
            pass
        page.bring_to_front()
        page.goto(f"{AUTH_BASE_URL}/email-verification", wait_until="domcontentloaded", timeout=90000)

    def _has_cloudflare_clearance(self, page) -> bool:
        try:
            cookies = page.context.cookies([AUTH_BASE_URL])
            return any(cookie.get("name") == "cf_clearance" for cookie in cookies)
        except Exception:
            return False

    def _wait_after_otp_submit(self, page, timeout: int = 20) -> None:
        started = time.time()
        while time.time() - started < timeout:
            if self._has_chatgpt_session(page):
                return
            if self._context_has_chatgpt_page(page):
                time.sleep(1)
                continue
            if "about-you" in page.url or self._has_about_you_form(page):
                return
            if not ("email-verification" in page.url or self._has_otp_input(page)):
                return
            time.sleep(1)
        if self._context_has_chatgpt_page(page):
            self.log("邮箱验证码提交后已打开 ChatGPT 页面，继续等待 session 生效")
            return
        page_text = self._page_text_summary(page)
        raise RuntimeError(f"验证码提交后页面仍停留在邮箱验证页，可能验证码已过期/已使用或页面校验失败。页面内容: {page_text}")

    def _page_text_summary(self, page, max_length: int = 300) -> str:
        try:
            text = page.locator("body").inner_text(timeout=1500)
        except Exception:
            return page.url
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_length] or page.url

    def _has_about_you_form(self, page) -> bool:
        try:
            text = page.locator("body").inner_text(timeout=1000).lower()
            has_about_text = (
                "tell us about you" in text
                or "about you" in text
                or "birth" in text
                or "how old are you" in text
                or "full name" in text
                or "finish creating account" in text
                or "confirmemos tu edad" in text
                or "fecha de nacimiento" in text
                or "nombre y apellidos" in text
                or "finalizar la creación de la cuenta" in text
                or "finalizar la creacion de la cuenta" in text
                or "生まれた年" in text
                or "生年" in text
                or "年齢" in text
                or "アカウントの作成を完了する" in text
                or "出生年" in text
                or "年龄" in text
            )
            if not has_about_text:
                return False
            try:
                return len(self._visible_inputs(page, ['input', 'textarea', '[contenteditable="true"]'])) >= 2
            except Exception:
                return True
        except Exception:
            return False

    def _fill_about_you(self, page) -> None:
        name, birthdate = random_profile()
        birth_year = str(birthdate.split("-")[0])
        age = str(max(18, datetime.now(timezone.utc).year - int(birth_year)))
        self.log(f"填写基础资料: {name} / birthdate={birthdate} / birth_year={birth_year} / age={age}")
        self._wait_for_about_you_inputs(page)
        self._fill_about_you_inputs(page, name, birthdate, birth_year, age)
        self.log("基础资料已填写，等待 1.5 秒后提交")
        time.sleep(1.5)
        if not self._submit_about_you(page):
            raise RuntimeError("基础资料已填写，但未找到“完成帐户创建”按钮")

    def _about_you_submit_done(self, page, before_url: str) -> bool:
        if page.is_closed():
            raise RuntimeError("浏览器页面已关闭，无法等待基础资料提交结果")
        current_url = page.url
        if self._has_chatgpt_session(page):
            return True
        if current_url != before_url:
            return True
        if "add-phone" in current_url or "phone-verification" in current_url:
            return True
        if self._has_register_phone_number_form(page):
            return True
        if self._has_visible_password(page):
            return True
        return not self._has_about_you_form(page)

    def _submit_about_you(self, page) -> bool:
        before_url = page.url
        if not self._click_finish_creating_account(page) and not self._click_continue(page):
            if not self._click_button_by_text(page, ["Finish creating account", "Finalizar la creación de la cuenta", "Finalizar la creacion de la cuenta", "アカウントの作成を完了する", "作成を完了", "完成帐户创建", "完成账户创建", "Create account", "Continue", "完成"]):
                return False

        started = time.time()
        while time.time() - started < 30:
            if self._about_you_submit_done(page, before_url):
                return True
            time.sleep(0.25)
        self.log("基础资料提交后页面未跳转，继续检测当前页面状态")
        return True

    def _click_finish_creating_account(self, page) -> bool:
        selectors = [
            'button:has-text("Finish creating account")',
            'button:has-text("Finalizar la creación de la cuenta")',
            'button:has-text("Finalizar la creacion de la cuenta")',
            'button:has-text("アカウントの作成を完了する")',
            'button:has-text("作成を完了")',
            'button[type="submit"]:has-text("Finish")',
            'button[type="submit"]:has-text("作成")',
            'button[data-dd-action-name="Continue"][type="submit"]:has-text("Finish")',
        ]
        for selector in selectors:
            button = page.locator(selector).first
            try:
                if not button.is_visible(timeout=700):
                    continue
                button.scroll_into_view_if_needed(timeout=3000)
                button.click(timeout=5000, force=True)
                self.log(f"已 force click: {selector}")
                return True
            except Exception as exc:
                self.log(f"force click 失败 {selector}: {str(exc)[:120]}")
            try:
                box = button.bounding_box(timeout=3000)
                if box:
                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    time.sleep(0.1)
                    page.mouse.up()
                    self.log(f"已坐标点击: {selector}")
                    return True
            except Exception as exc:
                self.log(f"坐标点击失败 {selector}: {str(exc)[:120]}")
            try:
                button.focus(timeout=3000)
                page.keyboard.press("Enter")
                self.log(f"已聚焦按钮并回车: {selector}")
                return True
            except Exception as exc:
                self.log(f"按钮回车失败 {selector}: {str(exc)[:120]}")

        try:
            inputs = self._visible_inputs(page, ['input'])
            if len(inputs) >= 2:
                inputs[1].focus(timeout=3000)
                page.keyboard.press("Enter")
                self.log("已在年龄输入框按 Enter 提交")
                return True
        except Exception as exc:
            self.log(f"年龄输入框 Enter 提交失败: {str(exc)[:120]}")

        clicked = page.evaluate(
            """() => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const enabled = (el) => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                const buttons = Array.from(document.querySelectorAll('button')).filter(el => visible(el) && enabled(el));
                const finish = buttons.find(el => {
                    const text = (el.textContent || '').trim();
                    return text.includes('Finish creating account')
                        || text.includes('Finalizar la creación de la cuenta')
                        || text.includes('Finalizar la creacion de la cuenta')
                        || text.includes('アカウントの作成を完了する')
                        || text.includes('作成を完了')
                        || text.includes('完成帐户创建')
                        || text.includes('完成账户创建');
                });
                const submit = finish || buttons.find(el =>
                    (el.type || '').toLowerCase() === 'submit'
                    && (el.getAttribute('data-dd-action-name') || '') === 'Continue'
                    && /Finish|作成|完成/.test((el.textContent || '').trim())
                );
                if (!submit) return false;
                submit.scrollIntoView({ block: 'center', inline: 'center' });
                submit.focus();
                const form = submit.closest('form');
                if (form && typeof form.requestSubmit === 'function') {
                    form.requestSubmit(submit);
                    return true;
                }
                submit.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'mouse', isPrimary: true }));
                submit.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
                submit.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'mouse', isPrimary: true }));
                submit.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }));
                submit.click();
                return true;
            }"""
        )
        if clicked:
            self.log("已提交 Finish creating account 表单")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            return True
        return False

    def _click_button_by_text(self, page, texts: list[str]) -> bool:
        box = page.evaluate(
            """({texts}) => {
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const candidates = Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(visible)
                    .filter(el => texts.some(text => (el.textContent || '').includes(text)));
                const el = candidates[0];
                if (!el) return null;
                el.scrollIntoView({ block: 'center', inline: 'center' });
                const r = el.getBoundingClientRect();
                return { x: r.left + r.width / 2, y: r.top + r.height / 2, text: el.textContent || '' };
            }""",
            {"texts": texts},
        )
        if not box:
            return False
        page.mouse.click(float(box["x"]), float(box["y"]))
        self.log(f"已点击按钮: {str(box.get('text', '')).strip()[:40]}")
        return True

    def _wait_for_about_you_inputs(self, page, timeout: int = 30) -> None:
        started = time.time()
        while time.time() - started < timeout:
            count = page.evaluate("""() => Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            }).length""")
            if int(count or 0) >= 2:
                return
            time.sleep(0.5)
        raise RuntimeError("about-you 页面 30 秒内未出现姓名/年龄输入框")

    def _fill_about_you_inputs(self, page, name: str, birthdate: str, birth_year: str, age: str) -> None:
        second_context = self._about_you_second_field_context(page)
        second_kind = self._about_you_second_field_kind_from_context(second_context)
        second_value = self._about_you_second_field_value(second_kind, birth_year, age, birthdate, second_context)
        try:
            self._fill_visible_input_by_keyboard(page, 0, name)
            self._fill_visible_input_by_keyboard(page, 1, second_value)
            self._focus_about_you_submit_or_body(page)
            values = self._visible_input_values(page)
            if self._about_you_values_ok(values, second_kind):
                self.log("基础资料已通过键盘输入")
                return
        except Exception as exc:
            self.log(f"基础资料键盘输入失败，改用 DOM 填写: {str(exc)[:120]}")

        values = self._fill_about_you_inputs_by_dom(page, name, second_value, second_kind)
        if self._about_you_values_ok(values, second_kind):
            return

        filled_name = self._fill_first_visible(page, [
            'input[name="name"]',
            'input[autocomplete="name"]',
            'input[placeholder*="name" i]',
            'input[placeholder*="全名" i]',
            'input[aria-label*="name" i]',
            'input[aria-label*="全名" i]',
        ], name)
        second_selectors = self._about_you_second_field_selectors(second_kind)
        filled_second = self._fill_first_visible(page, second_selectors, second_value)

        if not filled_name or not filled_second:
            visible_inputs = self._visible_inputs(page, ['input'])
            if not filled_name and len(visible_inputs) >= 1:
                self._force_fill_locator(visible_inputs[0], name)
                filled_name = True
            if not filled_second and len(visible_inputs) >= 2:
                self._force_fill_locator(visible_inputs[1], second_value)
                filled_second = True

        values = page.evaluate("""() => Array.from(document.querySelectorAll('input')).filter(el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        }).map(el => el.value || '')""")
        if self._about_you_values_ok(values, second_kind):
            return

        self.log("基础资料 DOM 填写未生效，改用鼠标点击 + 键盘输入")
        self._fill_visible_input_by_keyboard(page, 0, name)
        self._fill_visible_input_by_keyboard(page, 1, second_value)
        self._focus_about_you_submit_or_body(page)
        values = self._visible_input_values(page)
        if not self._about_you_values_ok(values, second_kind):
            self.log(f"基础资料自动填写未确认成功，当前可见输入值={values}。请在浏览器中手动填写/提交；程序将继续等待登录完成")

    def _fill_about_you_inputs_by_dom(self, page, name: str, second_value: str, second_kind: str) -> list[str]:
        return page.evaluate(
            """({name, secondValue, secondKind}) => {
                const visible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                const controls = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(visible);
                const setValue = (el, value) => {
                    if (!el) return false;
                    el.scrollIntoView({ block: 'center', inline: 'center' });
                    el.focus();
                    if (el.isContentEditable) {
                        el.textContent = value;
                    } else {
                        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (desc && desc.set) desc.set.call(el, value); else el.value = value;
                    }
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    return true;
                };
                const attrMatch = (el, words) => words.some(word => [
                    el.getAttribute('name'), el.getAttribute('autocomplete'), el.getAttribute('placeholder'),
                    el.getAttribute('aria-label'), el.id, el.getAttribute('data-testid')
                ].some(value => String(value || '').toLowerCase().includes(word.toLowerCase())));
                const byLabel = (words) => {
                    for (const label of Array.from(document.querySelectorAll('label'))) {
                        const text = (label.textContent || '').trim().toLowerCase();
                        if (!words.some(word => text.includes(word.toLowerCase()))) continue;
                        if (label.htmlFor) {
                            const linked = document.getElementById(label.htmlFor);
                            if (visible(linked)) return linked;
                        }
                        const nested = label.querySelector('input, textarea, [contenteditable="true"]');
                        if (visible(nested)) return nested;
                        const sibling = label.parentElement?.querySelector('input, textarea, [contenteditable="true"]');
                        if (visible(sibling)) return sibling;
                    }
                    return null;
                };
                const nameEl = controls.find(el => attrMatch(el, ['name', 'full', 'fullname', 'nombre', '全名'])) || byLabel(['全名', 'name', 'nombre']) || controls[0];
                const birthDateWords = ['date of birth', 'birthdate', 'dob', 'fecha de nacimiento', 'nacimiento', 'geburtstag', 'geburtsdatum', 'dd/mm', 'mm/dd', 'tt.mm', 'aaaa', 'yyyy', 'jjjj'];
                const birthWords = secondKind === 'birth_date'
                    ? birthDateWords
                    : ['birth', 'born', 'year', '生年', '生まれた年', '出生年'];
                const ageWords = ['age', '年齢', '年龄', '年纪'];
                const preferredWords = secondKind === 'age' ? ageWords : birthWords;
                const fallbackWords = secondKind === 'age' ? birthWords : ageWords;
                const secondEl = controls.find(el => el !== nameEl && attrMatch(el, preferredWords))
                    || byLabel(preferredWords)
                    || controls.find(el => el !== nameEl && attrMatch(el, fallbackWords))
                    || byLabel(fallbackWords)
                    || controls.find(el => el !== nameEl && (el.type === 'number' || el.inputMode === 'numeric'))
                    || controls.find(el => el !== nameEl)
                    || controls[1];
                setValue(nameEl, name);
                setValue(secondEl, secondValue);
                return controls.map(el => el.isContentEditable ? (el.textContent || '') : (el.value || ''));
            }""",
            {"name": name, "secondValue": second_value, "secondKind": second_kind},
        )

    def _visible_input_values(self, page) -> list[str]:
        return page.evaluate("""() => Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        }).map(el => el.isContentEditable ? (el.textContent || '') : (el.value || ''))""")

    def _about_you_values_ok(self, values: list[str], second_kind: str = "birth_year") -> bool:
        normalized = [str(value).strip() for value in values]
        nonempty = [value for value in normalized if value]
        if len(nonempty) < 2:
            return False
        second = nonempty[1]
        if second_kind == "age":
            if not re.fullmatch(r"\d{1,3}", second):
                return False
            age = int(second)
            return 13 <= age <= 120
        if second_kind == "birth_date":
            parsed = self._about_you_birth_date_from_values(nonempty)
            if not parsed:
                return False
            year, month, day = parsed
            try:
                birth = datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return False
            today = datetime.now(timezone.utc)
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
            return 13 <= age <= 120
        if not re.fullmatch(r"\d{4}", second):
            return False
        year = int(second)
        current_year = datetime.now(timezone.utc).year
        return 1900 <= year <= current_year - 13

    def _about_you_birth_date_from_values(self, values: list[str]) -> tuple[int, int, int] | None:
        for value in values[1:]:
            parsed = self._parse_about_you_birth_date(value)
            if parsed:
                return parsed
        numeric = [value for value in values if re.fullmatch(r"\d{1,4}", value)]
        if len(numeric) >= 3:
            for index in range(0, len(numeric) - 2):
                first, second, third = numeric[index:index + 3]
                if len(first) == 4:
                    return int(first), int(second), int(third)
                if len(third) == 4:
                    return int(third), int(second), int(first)
        return None

    def _parse_about_you_birth_date(self, value: str) -> tuple[int, int, int] | None:
        text = str(value or "").strip()
        match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
        match = re.fullmatch(r"(\d{1,2})([-/.])(\d{1,2})[-/.](\d{4})", text)
        if match:
            first, separator, second, year = int(match.group(1)), match.group(2), int(match.group(3)), int(match.group(4))
            if separator == "." or first > 12:
                return year, second, first
            return year, first, second
        return None

    def _about_you_current_values_ok(self, page) -> bool:
        try:
            values = self._visible_input_values(page)
            return self._about_you_values_ok(values, self._about_you_second_field_kind(page))
        except Exception:
            return False

    def _about_you_second_field_selectors(self, second_kind: str) -> list[str]:
        if second_kind == "age":
            return [
                'input[name*="age" i]',
                'input[placeholder*="age" i]',
                'input[aria-label*="age" i]',
                'input[placeholder*="年齢" i]',
                'input[aria-label*="年齢" i]',
                'input[placeholder*="年龄" i]',
                'input[aria-label*="年龄" i]',
            ]
        if second_kind == "birth_date":
            return [
                'input[type="date"]',
                'input[name*="dateOfBirth" i]',
                'input[name*="birth" i]',
                'input[name*="dob" i]',
                'input[placeholder*="DD/MM" i]',
                'input[placeholder*="MM/DD" i]',
                'input[placeholder*="AAAA" i]',
                'input[placeholder*="YYYY" i]',
                'input[placeholder*="TT.MM" i]',
                'input[placeholder*="JJJJ" i]',
                'input[placeholder*="fecha" i]',
                'input[aria-label*="fecha" i]',
                'input[placeholder*="geburt" i]',
                'input[aria-label*="geburt" i]',
                'input[placeholder*="birth" i]',
                'input[aria-label*="birth" i]',
            ]
        return [
            'input[name*="birth" i]',
            'input[name*="year" i]',
            'input[placeholder*="birth" i]',
            'input[placeholder*="year" i]',
            'input[aria-label*="birth" i]',
            'input[aria-label*="year" i]',
            'input[placeholder*="生年" i]',
            'input[aria-label*="生年" i]',
            'input[placeholder*="出生年" i]',
            'input[aria-label*="出生年" i]',
        ]

    def _about_you_second_field_value(self, second_kind: str, birth_year: str, age: str, birthdate: str = "", context: str = "") -> str:
        if second_kind == "age":
            return str(age)
        if second_kind == "birth_date":
            return self._format_about_you_birth_date(birthdate or f"{birth_year}-01-01", context)
        return str(birth_year)

    def _format_about_you_birth_date(self, birthdate: str, context: str = "") -> str:
        parsed = self._parse_about_you_birth_date(birthdate)
        if not parsed:
            year, month, day = int(str(birthdate)[:4]), 1, 1
        else:
            year, month, day = parsed
        text = re.sub(r"\s+", " ", str(context or "")).strip().lower()
        if re.search(r"\btt\s*\.\s*mm\s*\.\s*jjjj\b", text) or "geburtstag" in text or "geburtsdatum" in text:
            return f"{day:02d}.{month:02d}.{year:04d}"
        if re.search(r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*(yyyy|aaaa)\b", text) or "fecha de nacimiento" in text:
            return f"{day:02d}/{month:02d}/{year:04d}"
        if re.search(r"\bmm\s*[/.-]\s*dd\s*[/.-]\s*yyyy\b", text):
            return f"{month:02d}/{day:02d}/{year:04d}"
        if re.search(r"\byyyy\s*[/.-]\s*mm\s*[/.-]\s*dd\b", text) or "type=date" in text:
            return f"{year:04d}-{month:02d}-{day:02d}"
        return f"{year:04d}-{month:02d}-{day:02d}"

    def _about_you_second_field_kind_from_context(self, context: str) -> str:
        text = re.sub(r"\s+", " ", str(context or "")).strip().lower()
        birth_date_patterns = [
            r"date\s*of\s*birth",
            r"birthdate",
            r"\bdob\b",
            r"fecha\s+de\s+nacimiento",
            r"\bnacimiento\b",
            r"\bgeburtstag\b",
            r"\bgeburtsdatum\b",
            r"\bdd\s*[/.-]\s*mm\s*[/.-]\s*(yyyy|aaaa)\b",
            r"\btt\s*\.\s*mm\s*\.\s*jjjj\b",
            r"\bmm\s*[/.-]\s*dd\s*[/.-]\s*yyyy\b",
            r"\byyyy\s*[/.-]\s*mm\s*[/.-]\s*dd\b",
            r"type=date",
        ]
        birth_patterns = [
            r"生まれた年",
            r"生年",
            r"出生年",
            r"出生年份",
            r"birth\s*year",
            r"year\s*of\s*birth",
            r"born\s*year",
        ]
        age_patterns = [
            r"\bage\b",
            r"how\s*old",
            r"年齢",
            r"年龄",
            r"年纪",
        ]
        if any(re.search(pattern, text, flags=re.I) for pattern in birth_date_patterns):
            return "birth_date"
        if any(re.search(pattern, text, flags=re.I) for pattern in birth_patterns):
            return "birth_year"
        if any(re.search(pattern, text, flags=re.I) for pattern in age_patterns):
            return "age"
        return "birth_year"

    def _about_you_second_field_context(self, page) -> str:
        try:
            return str(page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const controls = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(visible);
                    const el = controls[1];
                    if (!el) return document.body?.innerText || document.title || '';
                    const parts = [
                        `name=${el.getAttribute('name') || ''}`, `id=${el.id || ''}`,
                        `placeholder=${el.getAttribute('placeholder') || ''}`,
                        `aria-label=${el.getAttribute('aria-label') || ''}`,
                        `autocomplete=${el.getAttribute('autocomplete') || ''}`,
                        `inputmode=${el.getAttribute('inputmode') || ''}`,
                        `type=${el.getAttribute('type') || el.type || ''}`,
                        `data-testid=${el.getAttribute('data-testid') || ''}`
                    ];
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        for (const id of labelledBy.split(/\\s+/)) {
                            const labelEl = document.getElementById(id);
                            if (labelEl) parts.push(labelEl.textContent || '');
                        }
                    }
                    for (const label of Array.from(document.querySelectorAll('label'))) {
                        if (label.htmlFor && label.htmlFor === el.id) parts.push(label.textContent || '');
                        if (label.contains(el)) parts.push(label.textContent || '');
                    }
                    let node = el.parentElement;
                    for (let i = 0; node && i < 3; i += 1, node = node.parentElement) {
                        parts.push(node.textContent || '');
                    }
                    parts.push(document.querySelector('h1,h2')?.textContent || '');
                    parts.push(document.body?.innerText || '');
                    parts.push(document.title || '');
                    return parts.filter(Boolean).join(' ');
                }"""
            ) or "")
        except Exception:
            return ""

    def _about_you_second_field_kind(self, page) -> str:
        try:
            return self._about_you_second_field_kind_from_context(self._about_you_second_field_context(page))
        except Exception:
            return "birth_year"

    def _focus_about_you_submit_or_body(self, page) -> None:
        try:
            page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const enabled = (el) => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(el => visible(el) && enabled(el));
                    const button = buttons.find(el => {
                        const text = (el.textContent || '').trim();
                        return text.includes('Finish creating account')
                            || text.includes('Finalizar la creación de la cuenta')
                            || text.includes('Finalizar la creacion de la cuenta')
                            || text.includes('アカウントの作成を完了する')
                            || text.includes('作成を完了')
                            || text.includes('完成帐户创建')
                            || text.includes('完成账户创建')
                            || (el.type || '').toLowerCase() === 'submit';
                    });
                    if (button) {
                        button.focus();
                        return true;
                    }
                    if (!document.body.hasAttribute('tabindex')) document.body.setAttribute('tabindex', '-1');
                    document.body.focus();
                    return false;
                }"""
            )
        except Exception:
            pass

    def _fill_visible_input_by_keyboard(self, page, index: int, value: str) -> None:
        box = page.evaluate(
            """({index}) => {
                const controls = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                });
                const el = controls[index];
                if (!el) return null;
                el.scrollIntoView({ block: 'center', inline: 'center' });
                const r = el.getBoundingClientRect();
                return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
            }""",
            {"index": index},
        )
        if not box:
            raise RuntimeError(f"未找到第 {index + 1} 个可见输入框")
        page.mouse.click(float(box["x"]), float(box["y"]))
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(str(value), delay=30)
        page.evaluate(
            """({index}) => {
                const controls = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                });
                const el = controls[index];
                if (!el) return false;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
                return true;
            }""",
            {"index": index},
        )
        time.sleep(0.5)

    def _fill_first_visible(self, page, selectors: list[str], value: str) -> bool:
        for locator in self._visible_inputs(page, selectors):
            if self._force_fill_locator(locator, value):
                return True
        return False

    def _force_fill_locator(self, locator, value: str) -> bool:
        try:
            locator.click(timeout=3000)
            locator.fill(str(value), timeout=5000)
            locator.evaluate("""(el, value) => {
                const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value); else el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""", str(value))
            return True
        except Exception:
            return False

    def _target_amount_text(self) -> str:
        return str(self.target_amount.get() or "").strip()

    def _opll_amount_fields(self, link_result: dict) -> dict:
        return {
            "stripe_amount": str(link_result.get("stripe_amount") if "stripe_amount" in link_result else ""),
            "stripe_amount_source": str(link_result.get("stripe_amount_source") if "stripe_amount_source" in link_result else ""),
            "target_amount": str(link_result.get("target_amount") if "target_amount" in link_result else self._target_amount_text()),
            "amount_check": str(link_result.get("amount_check") if "amount_check" in link_result else ""),
        }

    def _opll_amount_log_text(self, email_addr: str, link_result: dict) -> str:
        target = str(link_result.get("target_amount") or "").strip()
        if not target:
            return ""
        actual = str(link_result.get("stripe_amount") or "").strip()
        source = str(link_result.get("stripe_amount_source") or "").strip() or "未知"
        check = str(link_result.get("amount_check") or "").strip()
        status = "通过" if check == "passed" else check or "完成"
        prefix = f"[{email_addr}] " if email_addr else ""
        return f"{prefix}金额检查{status}: 目标 {target}, 实际 {actual}, 来源 {source}"

    def _opll_error_text(self, exc: Exception) -> str:
        if isinstance(exc, AmountMismatchError):
            source = exc.stripe_amount_source or "未知"
            return f"{exc}; 来源: {source}"
        return str(exc)

    def _detect_proxy_exit(self, proxy_url: str) -> str:
        return detect_proxy_health(proxy_url).summary

    def _proxy_exit_is_japan(self, proxy_exit: str) -> bool:
        return bool(re.search(r"(?:^|\s)JP(?:/|\s|$)", str(proxy_exit or "")))

    def _detect_link_proxy_exits(self, create_proxy_url: str, followup_proxy_url: str, approve_proxy_url: str) -> dict[str, str]:
        return _detect_link_proxy_exits_concurrently(
            self._detect_proxy_exit,
            self.log,
            create_proxy_url,
            followup_proxy_url,
            approve_proxy_url,
            bool(self.require_japan_extract_proxy),
            self._proxy_exit_is_japan,
        )

    def _extract_pay_link(self, page) -> dict:
        mode = PAYMENT_MODES.get(self.payment_mode.get()) or PAYMENT_MODES["无卡长链接 US/USD"]
        trial_short_link = bool(mode.get("trial_short_link"))
        apple_pay_hosted = bool(mode.get("apple_pay_hosted"))
        payment_provider = str(mode.get("payment_provider") or "paypal").strip().lower()
        link_label = "试用短链" if trial_short_link else ("Apple Pay 支付页" if apple_pay_hosted else "GoPay 长链接" if payment_provider == "gopay" else "支付长链接")
        self.log(f"提取{link_label}: {self.payment_mode.get()}")
        if page.is_closed():
            raise RuntimeError(f"浏览器页面已关闭，无法提取{link_label}")
        page.goto(CHATGPT_BASE_URL, wait_until="domcontentloaded", timeout=60000)
        last_error = "未知错误"
        started = time.time()
        for attempt in range(1, 16):
            if page.is_closed():
                raise RuntimeError(f"浏览器页面已关闭，无法提取{link_label}")
            if time.time() - started > 120:
                break
            self.log(f"正在提取{link_label} ({attempt}/15)")
            try:
                if trial_short_link:
                    return self._extract_trial_short_link_by_click(page)
                result = page.evaluate(
                    """async () => {
                        const sessionResp = await fetch('/api/auth/session', { credentials: 'include' });
                        if (!sessionResp.ok) throw new Error(`Session 请求失败: HTTP ${sessionResp.status}`);
                        const session = await sessionResp.json();
                        if (!session.accessToken) throw new Error('无法获取 accessToken，请确认已登录');
                        return { accessToken: session.accessToken, session };
                    }"""
                )
                access_token = str((result or {}).get("accessToken") or "")
                if not access_token:
                    raise RuntimeError("无法获取 accessToken，请确认已登录")
                self.log("已提取 ChatGPT session/accessToken")
                country = str(mode.get("country") or "US")
                currency = str(mode.get("currency") or currency_for_country(country))
                create_proxy_url = self.link_create_proxy.chain_url or self.link_create_proxy.local_proxy or self.link_create_proxy.dynamic_proxy
                followup_proxy_url = self.link_followup_proxy.chain_url or self.link_followup_proxy.local_proxy or self.link_followup_proxy.dynamic_proxy or create_proxy_url
                approve_proxy_url = self.link_approve_proxy.chain_url or self.link_approve_proxy.local_proxy or self.link_approve_proxy.dynamic_proxy or followup_proxy_url
                create_used_proxy = self.link_create_proxy.dynamic_proxy or self.link_create_proxy.local_proxy
                followup_used_proxy = self.link_followup_proxy.dynamic_proxy or self.link_followup_proxy.local_proxy or create_used_proxy
                approve_used_proxy = self.link_approve_proxy.dynamic_proxy or self.link_approve_proxy.local_proxy or followup_used_proxy
                proxy_action_text = "生成 Apple Pay hosted 支付页" if apple_pay_hosted else ("提取 GoPay 长链接" if payment_provider == "gopay" else "")
                _log_link_proxy_group(self.log, self.link_create_proxy, self.link_followup_proxy, self.link_approve_proxy, proxy_action_text)
                proxy_exits = self._detect_link_proxy_exits(create_proxy_url, followup_proxy_url, approve_proxy_url)
                create_proxy_exit = proxy_exits.get("create", "")
                followup_proxy_exit = proxy_exits.get("followup", "")
                approve_proxy_exit = proxy_exits.get("approve", "")
                target_amount = self._target_amount_text()
                if apple_pay_hosted:
                    link_result = generate_opll_hosted_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(link_result.get("long_url") or link_result.get("stripe_hosted_url") or "").strip()
                    if not long_url:
                        raise RuntimeError(f"接口生成成功但没有返回 Apple Pay 支付页链接: {link_result}")
                    amount_log = self._opll_amount_log_text("", link_result)
                    if amount_log:
                        self.log(amount_log)
                    self.log("Apple Pay hosted 支付页已生成；请用 Safari/iPhone/Mac 打开并手动付款")
                    return {
                        "url": long_url,
                        "checkout_url": long_url,
                        "access_token": access_token,
                        "session_json": json.dumps((result or {}).get("session") or {}, ensure_ascii=False, indent=2),
                        "link_proxy": followup_used_proxy,
                        "link_proxy_label": self.link_followup_proxy.label,
                        "link_proxy_exit": followup_proxy_exit,
                        "link_create_proxy": create_used_proxy,
                        "link_create_proxy_label": self.link_create_proxy.label,
                        "link_create_proxy_exit": create_proxy_exit,
                        "link_followup_proxy": followup_used_proxy,
                        "link_followup_proxy_label": self.link_followup_proxy.label,
                        "link_followup_proxy_exit": followup_proxy_exit,
                        "link_approve_proxy": approve_used_proxy,
                        "link_approve_proxy_label": self.link_approve_proxy.label,
                        "link_approve_proxy_exit": approve_proxy_exit,
                        "payment_link_type": "apple_pay_hosted",
                        **self._opll_amount_fields(link_result),
                    }
                if payment_provider == "gopay":
                    link_result = generate_opll_gopay_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(link_result.get("provider_redirect_url") or link_result.get("long_url") or "").strip()
                    if not long_url:
                        raise RuntimeError(f"接口提取成功但没有返回 GoPay 长链: {link_result}")
                    amount_log = self._opll_amount_log_text("", link_result)
                    if amount_log:
                        self.log(amount_log)
                    self.log("GoPay 长链接已生成，注册浏览器窗口保持打开")
                    return {
                        "url": long_url,
                        "checkout_url": long_url,
                        "access_token": access_token,
                        "session_json": json.dumps((result or {}).get("session") or {}, ensure_ascii=False, indent=2),
                        "link_proxy": followup_used_proxy,
                        "link_proxy_label": self.link_followup_proxy.label,
                        "link_proxy_exit": followup_proxy_exit,
                        "link_create_proxy": create_used_proxy,
                        "link_create_proxy_label": self.link_create_proxy.label,
                        "link_create_proxy_exit": create_proxy_exit,
                        "link_followup_proxy": followup_used_proxy,
                        "link_followup_proxy_label": self.link_followup_proxy.label,
                        "link_followup_proxy_exit": followup_proxy_exit,
                        "link_approve_proxy": approve_used_proxy,
                        "link_approve_proxy_label": self.link_approve_proxy.label,
                        "link_approve_proxy_exit": approve_proxy_exit,
                        "payment_link_type": "gopay_redirect",
                        **self._opll_amount_fields(link_result),
                    }
                else:
                    link_result = generate_opll_paypal_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(link_result.get("provider_redirect_url") or link_result.get("long_url") or "").strip()
                    if not opll_is_paypal_success_url(long_url):
                        raise RuntimeError(f"返回的不是可用 PayPal 跳转链接，拒绝保存: {long_url[:160]}")
                    amount_log = self._opll_amount_log_text("", link_result)
                    if amount_log:
                        self.log(amount_log)
                    self.log("[支付链接] PayPal 跳转链接已生成，认证浏览器窗口保持打开")
                    return {
                        "url": long_url,
                        "checkout_url": long_url,
                        "access_token": access_token,
                        "session_json": json.dumps((result or {}).get("session") or {}, ensure_ascii=False, indent=2),
                        "link_proxy": followup_used_proxy,
                        "link_proxy_label": self.link_followup_proxy.label,
                        "link_proxy_exit": followup_proxy_exit,
                        "link_create_proxy": create_used_proxy,
                        "link_create_proxy_label": self.link_create_proxy.label,
                        "link_create_proxy_exit": create_proxy_exit,
                        "link_followup_proxy": followup_used_proxy,
                        "link_followup_proxy_label": self.link_followup_proxy.label,
                        "link_followup_proxy_exit": followup_proxy_exit,
                        "link_approve_proxy": approve_used_proxy,
                        "link_approve_proxy_label": self.link_approve_proxy.label,
                        "link_approve_proxy_exit": approve_proxy_exit,
                        "payment_link_type": "paypal_approve",
                        **self._opll_amount_fields(link_result),
                    }
            except Exception as exc:
                last_error = exc
                if isinstance(exc, ProxyExitCheckError):
                    raise
                if "Target page" in str(exc) or "closed" in str(exc).lower():
                    raise RuntimeError(f"浏览器被关闭，{link_label}提取已停止")
                self.log(f"{link_label}提取失败，准备重试: {self._opll_error_text(exc)[:180]}")
                time.sleep(4)
        raise RuntimeError(f"提取{link_label}失败: {last_error}")

    def _extract_session_info(self, context) -> dict:
        page = context.new_page()
        try:
            page.goto("https://chatgpt.com/api/auth/session", wait_until="domcontentloaded", timeout=60000)
            body = page.locator("body").inner_text(timeout=15000).strip()
            try:
                session = json.loads(body)
            except Exception as exc:
                raise RuntimeError(f"Session 接口返回不是有效 JSON: {body[:300]}") from exc
            access_token = str(session.get("accessToken") or "")
            if not access_token:
                self.log("[Session] Session JSON 已获取，但未发现 accessToken")
            else:
                self.log("[Session] Session JSON 和 Access Token 已获取")
            storage_state = context.storage_state()
            return {
                "url": "",
                "access_token": access_token,
                "session_json": json.dumps(session, ensure_ascii=False, indent=2),
                "storage_state_json": json.dumps(storage_state, ensure_ascii=False),
            }
        finally:
            try:
                page.bring_to_front()
            except Exception:
                pass

    def _extract_trial_short_link_by_click(self, page) -> dict:
        session_info = page.evaluate(
            """async () => {
                const sessionResp = await fetch('/api/auth/session', { credentials: 'include' });
                if (!sessionResp.ok) throw new Error(`Session 请求失败: HTTP ${sessionResp.status}`);
                const session = await sessionResp.json();
                if (!session.accessToken) throw new Error('无法获取 accessToken，请确认已登录');
                return { accessToken: session.accessToken, session };
            }"""
        )
        self.log("已提取 ChatGPT session/accessToken")
        page.goto("https://chatgpt.com/?promo_campaign=plus-1-month-free#pricing", wait_until="domcontentloaded", timeout=60000)
        self.log("已打开试用页面，准备点击领取按钮")
        clicked = page.evaluate(
            """() => {
                const visible = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                    .filter(el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
                const target = candidates.find(el => {
                    const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.trim();
                    return /领取\\s*Plus|免费优惠|Plus\\s*免费|Claim\\s*Plus|free\\s*trial|Get\\s*Plus|Start/i.test(text);
                });
                if (!target) return false;
                target.scrollIntoView({ block: 'center' });
                target.click();
                return true;
            }"""
        )
        if not clicked:
            raise RuntimeError("试用页面未找到领取 Plus 免费优惠按钮")
        started = time.time()
        while time.time() - started < 60:
            if page.is_closed():
                raise RuntimeError("浏览器页面已关闭，无法等待试用短链跳转")
            current_url = page.url
            if "pay.openai.com" in current_url or "checkout.stripe.com" in current_url or "paypal.com" in current_url:
                self.log("试用短链已通过页面点击跳转生成")
                return {
                    "url": current_url,
                    "checkout_url": current_url,
                    "access_token": str(session_info.get("accessToken") or ""),
                    "session_json": json.dumps(session_info.get("session") or {}, ensure_ascii=False, indent=2),
                }
            time.sleep(1)
        raise RuntimeError(f"点击试用按钮后 60 秒内未跳转到支付页，当前 URL: {page.url}")


class App:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x760")
        self.accounts: list[MailAccount] = []
        self.phones: list[PhoneEntry] = []
        self.payment_cards: list[PaymentCard] = []
        self.results: dict[str, str] = {}
        self.session_results: dict[str, dict] = {}
        self.link_attempt_counts: dict[str, int] = {}
        self.account_groups: list[str] = [ACCOUNT_DEFAULT_GROUP]
        self.state_store = StateStore(STATE_FILE)
        self._dirty_session_emails: set[str] = set()
        self._dirty_all_sessions = False
        self.events: queue.Queue = queue.Queue()
        self.ui_thread_id = threading.get_ident()
        self.link_proxy_exit_cache: dict[tuple[str, str], str] = {}
        self.link_proxy_exit_cache_lock = threading.Lock()
        self.log_seq = 0
        self.log_records: list[LogRecord] = []
        self.global_logs: list[LogRecord] = []
        self.logs_by_email: dict[str, list[LogRecord]] = {}
        self.pending_prompts: dict[str, queue.Queue] = {}
        self.running = False
        self.opening_payment_link = False
        self.stop_event = threading.Event()
        self.payment_context = None
        self.payment_contexts: set = set()
        self.trial_proxy_chain: ProxyChainServer | None = None
        self.trial_payment_dynamic_proxy = ""
        self.trial_account_email = ""
        self.open_payment_window_count = 0
        self.phone_lock = threading.Lock()
        self.payment_mode = StringVar(value="无卡长链接 US/USD")
        self.target_amount = StringVar(value="")
        self.headless = BooleanVar(value=False)
        self.local_proxy = StringVar(value="http://127.0.0.1:7890")
        self.payment_dynamic_proxy = StringVar(value="")
        self.followup_dynamic_proxy = StringVar(value="")
        self.approve_dynamic_proxy = StringVar(value="")
        self.reuse_payment_proxy = StringVar(value="")
        self.reuse_followup_proxy = StringVar(value="")
        self.reuse_approve_proxy = StringVar(value="")
        self.require_japan_extract_proxy = BooleanVar(value=False)
        self.register_with_payment_proxy = BooleanVar(value=False)
        self.auth_concurrency = IntVar(value=DEFAULT_AUTH_CONCURRENCY)
        self.link_race_concurrency = IntVar(value=1)
        self.link_proxy_precheck_limit = IntVar(value=DEFAULT_LINK_PROXY_PRECHECK_LIMIT)
        self.link_proxy_precheck_concurrency = IntVar(value=DEFAULT_LINK_PROXY_PRECHECK_CONCURRENCY)
        self.link_attempt_limit = IntVar(value=1)
        self.provider_proxy_vars = {}
        self.provider_proxy_status_vars = {}
        for role in PROVIDER_PROXY_ROLES:
            self.provider_proxy_vars[role] = {
                "enabled": BooleanVar(value=False),
                "username": StringVar(value=""),
                "password": StringVar(value=""),
                "endpoint": StringVar(value=""),
                "duration": IntVar(value=5),
                "regions": StringVar(value="JP"),
            }
            self.provider_proxy_status_vars[role] = StringVar(value="未启用")
        self.payment_extension_dir = StringVar(value=DEFAULT_PAYPAL_EXTENSION_DIR)
        self.paypal_phone = StringVar(value="")
        self.paypal_card = StringVar(value="")
        self.paypal_sms_url = StringVar(value="")
        self.paypal_phone_pool = StringVar(value="")
        self.export_name_prefix = StringVar(value="")
        self.phone_max_receive_count = IntVar(value=0)
        self.success_sound_enabled = BooleanVar(value=True)
        self.success_audio_device = StringVar(value=AUDIO_DEFAULT_DEVICE_LABEL)
        self.pause_others_on_link_success = BooleanVar(value=True)
        self.account_group_filter = StringVar(value=ACCOUNT_ALL_GROUP)
        self.account_sort_column = "email"
        self.account_sort_direction = ACCOUNT_SORT_CUSTOM
        self.account_drag_source_iid = ""
        self.audio_devices: list[dict] = []
        self.dynamic_proxy_index = 0
        self.paypal_phone_pool_index = 0
        self.proxy_pool_count_after_id = None
        self.saved_window_geometry = ""
        self.saved_window_zoomed = False
        self.main_sash_ratio = 0.58
        self.log_sash_ratio = 0.5
        self.body_sash_ratio = 0.34
        self.sash_restore_attempts = 0
        self.clearing_ui_focus = False
        self.proxy_pool_title_labels = {}
        self.proxy_pool_title_bases = {
            "register": "注册/获取 Session 动态代理池",
            "create": "创建长链第一步代理池",
            "followup": "创建长链后续代理池",
            "approve": "Approve 代理池",
        }
        self.provider_proxy_manager = ProviderProxyPoolManager(
            self._detect_provider_proxy_candidate,
            status_callback=self._provider_proxy_status_changed,
        )
        self.applied_provider_proxy_configs = None
        self.applied_provider_local_proxy = ""
        self._build_ui()
        self.load_state()
        self._show_loaded_provider_proxy_status()
        self._refresh_proxy_pool_counts()
        self.root.after_idle(self._restore_window_layout)
        self.root.after_idle(self._focus_root_startup)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_events)

    def _target_amount_text(self) -> str:
        return str(self.target_amount.get() or "").strip()

    def _link_attempt_limit(self) -> int:
        try:
            return min(10000, max(1, int(self.link_attempt_limit.get() or 1)))
        except Exception:
            return 1

    def _provider_proxy_config_from_vars(self, role: str) -> ProxyProviderConfig:
        variables = self.provider_proxy_vars[role]
        try:
            duration = int(variables["duration"].get() or 5)
        except Exception:
            duration = 5
        return ProxyProviderConfig(
            enabled=bool(variables["enabled"].get()),
            username=str(variables["username"].get() or "").strip(),
            password=str(variables["password"].get() or ""),
            endpoint=str(variables["endpoint"].get() or "").strip(),
            duration=duration,
            regions_text=str(variables["regions"].get() or "").strip(),
        )

    def _provider_proxy_configs(self) -> dict[str, ProxyProviderConfig]:
        return {role: self._provider_proxy_config_from_vars(role) for role in PROVIDER_PROXY_ROLES}

    def _set_provider_proxy_config_vars(self, role: str, config: ProxyProviderConfig) -> None:
        variables = self.provider_proxy_vars[role]
        variables["enabled"].set(bool(config.enabled))
        variables["username"].set(config.username)
        variables["password"].set(config.password)
        variables["endpoint"].set(config.endpoint)
        variables["duration"].set(int(config.duration))
        variables["regions"].set(config.regions_text)

    def _apply_provider_proxy_configs(self, show_message: bool = True) -> bool:
        try:
            configs = self._provider_proxy_configs()
            if bool(self.require_japan_extract_proxy.get()) and configs["create"].enabled:
                non_japan = [region for region in configs["create"].regions if region != "JP"]
                if non_japan:
                    raise ValueError("已启用“强制日本出口”，第一步提供商 region 只能填写 JP")
            self.provider_proxy_manager.update_max_workers(self._link_proxy_precheck_concurrency())
            self.provider_proxy_manager.configure(configs, normalize_proxy_url(self.local_proxy.get()))
            self.applied_provider_proxy_configs = configs
            self.applied_provider_local_proxy = normalize_proxy_url(self.local_proxy.get())
            self.save_state()
            enabled = [PROVIDER_PROXY_ROLE_LABELS[role] for role, config in configs.items() if config.enabled]
            if enabled:
                self.log(f"提供商代理池已应用并开始后台预热: {', '.join(enabled)}")
            if show_message:
                messagebox.showinfo(APP_TITLE, "提供商代理配置已应用，后台预热已启动")
            return True
        except Exception as exc:
            if show_message:
                messagebox.showwarning(APP_TITLE, f"提供商代理配置无效: {exc}")
            else:
                self.log(f"提供商代理配置未启动: {exc}")
            return False

    def _show_loaded_provider_proxy_status(self) -> None:
        for role, config in self._provider_proxy_configs().items():
            self.provider_proxy_status_vars[role].set("已启用，未预热" if config.enabled else "未启用")

    def _ensure_provider_proxy_pool_started(self) -> bool:
        configs = self._provider_proxy_configs()
        if not any(config.enabled for config in configs.values()):
            return True
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        if configs == self.applied_provider_proxy_configs and local_proxy == self.applied_provider_local_proxy:
            self.provider_proxy_manager.start()
            return True
        return self._apply_provider_proxy_configs(show_message=False)

    def apply_provider_proxy_configs(self) -> None:
        self._apply_provider_proxy_configs(show_message=True)

    def _provider_proxy_status_changed(self, role: str, status: dict) -> None:
        self.events.put(("provider-proxy-status", role, status))

    def _detect_provider_proxy_candidate(self, candidate: ProviderProxyCandidate, local_proxy: str) -> str:
        with ProxyChainServer(local_proxy, candidate.url, self._emit_log) as chain:
            proxy_url = chain.url or normalize_proxy_url(local_proxy) or candidate.url
            proxy_exit = self._detect_proxy_exit(proxy_url)
        self._set_link_proxy_cached_exit(local_proxy, candidate.url, proxy_exit)
        return proxy_exit

    def _opll_amount_fields(self, link_result: dict) -> dict:
        return {
            "stripe_amount": str(link_result.get("stripe_amount") if "stripe_amount" in link_result else ""),
            "stripe_amount_source": str(link_result.get("stripe_amount_source") if "stripe_amount_source" in link_result else ""),
            "target_amount": str(link_result.get("target_amount") if "target_amount" in link_result else self._target_amount_text()),
            "amount_check": str(link_result.get("amount_check") if "amount_check" in link_result else ""),
        }

    def _opll_amount_log_text(self, email_addr: str, link_result: dict) -> str:
        target = str(link_result.get("target_amount") or "").strip()
        if not target:
            return ""
        actual = str(link_result.get("stripe_amount") or "").strip()
        source = str(link_result.get("stripe_amount_source") or "").strip() or "未知"
        check = str(link_result.get("amount_check") or "").strip()
        status = "通过" if check == "passed" else check or "完成"
        prefix = f"[{email_addr}] " if email_addr else ""
        return f"{prefix}金额检查{status}: 目标 {target}, 实际 {actual}, 来源 {source}"

    def _opll_error_text(self, exc: Exception) -> str:
        if isinstance(exc, AmountMismatchError):
            source = exc.stripe_amount_source or "未知"
            return f"{exc}; 来源: {source}"
        return str(exc)

    def _button(self, parent, text: str, command, tooltip: str):
        button = ttk.Button(parent, text=text, command=command)
        ToolTip(button, tooltip)
        return button

    def _scrolled_text(self, parent, **kwargs):
        kwargs.setdefault("font", (UI_FONT_FAMILY, UI_TEXT_FONT_SIZE))
        return ScrolledText(parent, **kwargs)

    def _paned_window(self, parent, orient: str, sashwidth: int = 6):
        return PanedWindow(
            parent,
            orient=orient,
            opaqueresize=False,
            sashwidth=sashwidth,
            sashrelief="raised",
            borderwidth=0,
            showhandle=False,
        )

    def _add_pane(self, pane, child, minsize: int = 80, stretch: str = "always") -> None:
        try:
            pane.add(child, minsize=minsize, stretch=stretch)
        except Exception:
            try:
                pane.add(child, minsize=minsize)
            except Exception:
                pane.add(child)

    def _configure_log_text_tabs(self) -> None:
        if not hasattr(self, "account_log_text"):
            return
        for widget in (self.account_log_text, self.global_log_text):
            try:
                text_font = tkfont.Font(font=widget.cget("font"))
                tab_px = text_font.measure("[00:00:00] 出口[Approve]   ") + 24
                widget.configure(tabs=(tab_px,), state="disabled")
            except Exception:
                pass

    def _focus_root_startup(self) -> None:
        self._clear_ui_focus()

    def _clear_ui_focus(self) -> None:
        if getattr(self, "clearing_ui_focus", False):
            return
        self.clearing_ui_focus = True
        try:
            focused = self.root.focus_get()
            if focused is not None:
                try:
                    widget_class = focused.winfo_class()
                    if widget_class == "TCombobox":
                        try:
                            popdown = focused.tk.call("ttk::combobox::PopdownWindow", str(focused))
                            focused.tk.call("wm", "withdraw", popdown)
                        except Exception:
                            pass
                        focused.selection_clear()
                    elif widget_class in {"Entry", "TEntry", "Spinbox", "TSpinbox"}:
                        focused.selection_clear()
                    elif widget_class == "Text":
                        focused.tag_remove("sel", "1.0", END)
                except Exception:
                    pass
            focus_sink = getattr(self, "focus_sink", None)
            if focus_sink is not None:
                try:
                    focus_sink.focus_set()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self.clearing_ui_focus = False

    def _paned_sash_ratio(self, pane, orientation: str, fallback: float) -> float:
        try:
            total = pane.winfo_height() if orientation == "vertical" else pane.winfo_width()
            if total > 1:
                if hasattr(pane, "sashpos"):
                    position = pane.sashpos(0)
                else:
                    coords = pane.sash_coord(0)
                    position = coords[1] if orientation == "vertical" else coords[0]
                return min(0.9, max(0.1, float(position) / float(total)))
        except Exception:
            pass
        return fallback

    def _safe_set_paned_sash(self, pane, orientation: str, ratio: float) -> bool:
        try:
            total = pane.winfo_height() if orientation == "vertical" else pane.winfo_width()
            if total < 160:
                return False
            ratio = min(0.9, max(0.1, float(ratio or 0.5)))
            margin = 80 if total >= 240 else max(24, total // 6)
            position = min(total - margin, max(margin, int(total * ratio)))
            if hasattr(pane, "sashpos"):
                pane.sashpos(0, position)
            else:
                if orientation == "vertical":
                    pane.sash_place(0, 1, position)
                else:
                    pane.sash_place(0, position, 1)
            return True
        except Exception:
            return False

    def _restore_window_layout(self) -> None:
        try:
            if re.fullmatch(r"\d+x\d+(?:[+-]\d+){2}", self.saved_window_geometry):
                self.root.geometry(self.saved_window_geometry)
            if self.saved_window_zoomed:
                self.root.state("zoomed")
        except Exception:
            pass
        self.sash_restore_attempts = 0
        self.root.after(80, self._restore_paned_sashes)

    def _restore_paned_sashes(self) -> None:
        ok = True
        ok = self._safe_set_paned_sash(self.main_panes, "vertical", self.main_sash_ratio) and ok
        ok = self._safe_set_paned_sash(self.log_columns, "horizontal", self.log_sash_ratio) and ok
        ok = self._safe_set_paned_sash(self.body_panes, "horizontal", self.body_sash_ratio) and ok
        if ok:
            return
        self.sash_restore_attempts += 1
        if self.sash_restore_attempts <= 8:
            try:
                self.root.after(80, self._restore_paned_sashes)
            except Exception:
                pass

    def _current_window_geometry_for_state(self) -> str:
        try:
            if str(self.root.state()) != "normal":
                return self.saved_window_geometry
            geometry = str(self.root.geometry())
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
            if not match:
                return self.saved_window_geometry
            width, height = int(match.group(1)), int(match.group(2))
            x, y = int(match.group(3)), int(match.group(4))
            screen_width = max(1, int(self.root.winfo_screenwidth()))
            screen_height = max(1, int(self.root.winfo_screenheight()))
            maximized_like = width >= screen_width * 0.94 or height >= screen_height * 0.90
            snapped_left = x <= 0 and width <= screen_width * 0.62 and height >= screen_height * 0.80
            snapped_right = x + width >= screen_width - 2 and width <= screen_width * 0.62 and height >= screen_height * 0.80
            if maximized_like or snapped_left or snapped_right:
                return self.saved_window_geometry
            return geometry
        except Exception:
            return self.saved_window_geometry

    def _proxy_pool_nonempty_line_count(self, widget) -> int:
        try:
            return sum(1 for line in widget.get("1.0", END).splitlines() if line.strip())
        except Exception:
            return 0

    def _proxy_pool_title_text(self, key: str, count: int = 0) -> str:
        base = self.proxy_pool_title_bases.get(key, key)
        return f"{base}（剩余 {max(0, int(count or 0))}）"

    def _register_proxy_pool_title(self, key: str, label) -> None:
        self.proxy_pool_title_labels[key] = label
        try:
            label.configure(text=self._proxy_pool_title_text(key))
        except Exception:
            pass

    def _bind_proxy_pool_counter(self, widget) -> None:
        for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<FocusOut>"):
            widget.bind(sequence, lambda _event: self._schedule_proxy_pool_count_refresh(), add="+")

    def _schedule_proxy_pool_count_refresh(self) -> None:
        try:
            if self.proxy_pool_count_after_id is not None:
                self.root.after_cancel(self.proxy_pool_count_after_id)
        except Exception:
            pass
        try:
            self.proxy_pool_count_after_id = self.root.after(30, self._refresh_proxy_pool_counts)
        except Exception:
            self._refresh_proxy_pool_counts()

    def _refresh_proxy_pool_counts(self) -> None:
        self.proxy_pool_count_after_id = None
        widget_by_key = {
            "register": getattr(self, "proxy_text", None),
            "create": getattr(self, "payment_dynamic_proxy_text", None),
            "followup": getattr(self, "followup_dynamic_proxy_text", None),
            "approve": getattr(self, "approve_dynamic_proxy_text", None),
        }
        for key, label in getattr(self, "proxy_pool_title_labels", {}).items():
            widget = widget_by_key.get(key)
            count = self._proxy_pool_nonempty_line_count(widget) if widget is not None else 0
            try:
                label.configure(text=self._proxy_pool_title_text(key, count))
            except Exception:
                pass

    def _refresh_audio_devices(self, show_log: bool = True) -> None:
        previous = self.success_audio_device.get().strip() or AUDIO_DEFAULT_DEVICE_LABEL
        devices = [{"label": AUDIO_DEFAULT_DEVICE_LABEL, "index": None, "name": ""}]
        error = ""
        try:
            import sounddevice as sd  # type: ignore

            hostapis = sd.query_hostapis()
            for index, device in enumerate(sd.query_devices()):
                if int(device.get("max_output_channels", 0) or 0) <= 0:
                    continue
                hostapi_index = int(device.get("hostapi", -1) or -1)
                hostapi_name = ""
                if 0 <= hostapi_index < len(hostapis):
                    hostapi_name = str(hostapis[hostapi_index].get("name") or "")
                name = str(device.get("name") or f"设备 {index}").strip()
                suffix = f" / {hostapi_name}" if hostapi_name else ""
                devices.append({"label": f"{index}: {name}{suffix}", "index": index, "name": name})
        except Exception as exc:
            error = str(exc)

        labels = [item["label"] for item in devices]
        selected = AUDIO_DEFAULT_DEVICE_LABEL
        if previous in labels:
            selected = previous
        elif previous and previous != AUDIO_DEFAULT_DEVICE_LABEL:
            previous_name = previous.split(": ", 1)[1].split(" / ", 1)[0] if ": " in previous else previous
            selected = next((item["label"] for item in devices if item.get("name") == previous_name), AUDIO_DEFAULT_DEVICE_LABEL)

        self.audio_devices = devices
        self.success_audio_device.set(selected)
        if hasattr(self, "audio_device_combo"):
            self.audio_device_combo.configure(values=labels)
        if error and show_log:
            self.log(f"刷新音频输出设备失败: {error}")
        elif show_log:
            self.log(f"已刷新音频输出设备: {max(0, len(devices) - 1)} 个")

    def _selected_audio_device_index(self) -> int | None:
        selected = self.success_audio_device.get().strip()
        for item in self.audio_devices:
            if item.get("label") == selected:
                value = item.get("index")
                return int(value) if value is not None else None
        if selected and ":" in selected:
            try:
                return int(selected.split(":", 1)[0])
            except Exception:
                return None
        return None

    def _play_success_sound_async(self, force: bool = False) -> None:
        if not force and not bool(self.success_sound_enabled.get()):
            return
        threading.Thread(target=self._play_success_sound_worker, daemon=True).start()

    def _play_success_sound_worker(self) -> None:
        try:
            import numpy as np  # type: ignore
            import sounddevice as sd  # type: ignore

            sample_rate = 44100
            duration = 0.42
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            tone = 0.22 * np.sin(2 * np.pi * 880 * t) + 0.14 * np.sin(2 * np.pi * 1320 * t)
            envelope = np.minimum(1.0, np.linspace(0, 1, tone.size) * 18)
            envelope *= np.minimum(1.0, np.linspace(1, 0, tone.size) * 18)
            samples = (tone * envelope).astype(np.float32)
            sd.play(samples, samplerate=sample_rate, device=self._selected_audio_device_index(), blocking=True)
        except Exception as exc:
            self.events.put(("log", {"message": f"播放成功提示音失败: {exc}", "email": ""}))

    def _handle_link_success(self, email_addr: str) -> None:
        self._play_success_sound_async()
        if not bool(self.pause_others_on_link_success.get()):
            return
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.log(f"账号 {email_addr} 长链已提取，已暂停其他账户继续尝试")

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=BOTH, expand=True)
        self.focus_sink = Label(main, text="", takefocus=True)
        self.focus_sink.place(x=-100, y=-100, width=1, height=1)
        self.main_panes = self._paned_window(main, "vertical", sashwidth=8)
        self.main_panes.pack(fill=BOTH, expand=True)
        top_workspace = ttk.Frame(self.main_panes)
        bottom_workspace = ttk.Frame(self.main_panes)
        self._add_pane(self.main_panes, top_workspace, minsize=220)
        self._add_pane(self.main_panes, bottom_workspace, minsize=220)

        self.tabs = ttk.Notebook(top_workspace)
        tabs = self.tabs
        tabs.pack(fill=BOTH, expand=True, pady=(0, 6))

        import_tab = ttk.Frame(tabs, padding=8)
        tabs.add(import_tab, text="导入邮箱")
        import_log_panes = self._paned_window(import_tab, "vertical", sashwidth=6)
        import_log_panes.pack(fill=BOTH, expand=True)
        import_pane = ttk.Frame(import_log_panes)
        log_pane = ttk.Frame(import_log_panes)
        self._add_pane(import_log_panes, import_pane, minsize=90)
        self._add_pane(import_log_panes, log_pane, minsize=160)
        top = ttk.Frame(import_pane)
        top.pack(fill=X)
        ttk.Label(top, text="每行：email----password----client_id----refresh_token[----auth_phone=手机号----auth_phone_sms_url=接码链接]").pack(side=LEFT)
        self._button(top, "从文件导入", self.load_file, "选择邮箱账号文本文件并导入到上方输入框；不会立即开始注册。").pack(side=RIGHT)
        self.import_text = self._scrolled_text(import_pane, height=4)
        self.import_text.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.log_columns = self._paned_window(log_pane, "horizontal", sashwidth=6)
        self.log_columns.pack(fill=BOTH, expand=True, pady=(8, 0))
        account_log_frame = ttk.Frame(self.log_columns)
        global_log_frame = ttk.Frame(self.log_columns)
        self._add_pane(self.log_columns, account_log_frame, minsize=220)
        self._add_pane(self.log_columns, global_log_frame, minsize=220)
        self.log_label = ttk.Label(account_log_frame, text="选中邮箱日志：未选择邮箱")
        self.log_label.pack(anchor="w", pady=(0, 4))
        ttk.Label(global_log_frame, text="全局日志").pack(anchor="w", pady=(0, 4))
        log_font = ("Cascadia Mono", UI_TEXT_FONT_SIZE)
        self.account_log_text = self._scrolled_text(account_log_frame, height=7, font=log_font)
        self.account_log_text.pack(fill=BOTH, expand=True)
        self.global_log_text = self._scrolled_text(global_log_frame, height=7, font=log_font)
        self.global_log_text.pack(fill=BOTH, expand=True)
        self.log_text = self.account_log_text
        for log_widget in (self.account_log_text, self.global_log_text):
            log_widget.tag_configure("log_error", foreground="#b91c1c")
            log_widget.tag_configure("log_success", foreground="#15803d")
            log_widget.tag_configure("log_attention", foreground="#1d4ed8")
        self._configure_log_text_tabs()

        phone_frame = ttk.Frame(tabs, padding=8)
        tabs.add(phone_frame, text="手机号池")
        ttk.Label(phone_frame, text="每行：+手机号https://短信链接 或 +手机号----https://短信链接；同一手机号可连续授权，失败后自动标记不可用").pack(anchor="w")
        phone_limit_row = ttk.Frame(phone_frame)
        phone_limit_row.pack(fill=X, pady=(6, 0))
        ttk.Label(phone_limit_row, text="每个手机号最多接码次数（0=不限制）").pack(side=LEFT)
        ttk.Entry(phone_limit_row, textvariable=self.phone_max_receive_count, width=8).pack(side=LEFT, padx=(8, 0))
        phone_top = ttk.Frame(phone_frame)
        phone_top.pack(fill=X, pady=(6, 0))
        self.phone_text = self._scrolled_text(phone_top, height=3)
        self.phone_text.pack(side=LEFT, fill=X, expand=True)
        phone_buttons = ttk.Frame(phone_top)
        phone_buttons.pack(side=LEFT, padx=(8, 0), fill="y")
        self._button(phone_buttons, "导入手机号", self.import_phones, "把上方手机号池文本导入列表；用于注册或授权时接收短信验证码。").pack(fill=X)
        self._button(phone_buttons, "重置手机号", self.reset_phones, "把手机号状态恢复为可用，并清空最近验证码和失败信息。").pack(fill=X, pady=(8, 0))
        self._button(phone_buttons, "清空手机号", self.clear_phones, "清空手机号池列表和输入框；此操作会移除当前导入的接码号码。").pack(fill=X, pady=(8, 0))
        self._button(phone_buttons, "手动取码", self.fetch_selected_phone_code, "对当前选中的手机号立即请求短信页面，并把读取到的验证码弹出显示。").pack(fill=X, pady=(8, 0))
        ttk.Label(phone_frame, text="手机号状态").pack(anchor="w", pady=(8, 4))
        self.phone_list = ttk.Treeview(phone_frame, columns=("number", "count", "status", "code"), show="headings", height=3)
        self.phone_list.heading("number", text="手机号")
        self.phone_list.heading("count", text="接码次数")
        self.phone_list.heading("status", text="状态")
        self.phone_list.heading("code", text="最近验证码")
        self.phone_list.column("number", width=180)
        self.phone_list.column("count", width=80)
        self.phone_list.column("status", width=120)
        self.phone_list.column("code", width=120)
        self.phone_list.pack(fill=X)

        paypal_frame = ttk.Frame(tabs, padding=8)
        tabs.add(paypal_frame, text="PayPal扩展")
        ttk.Label(paypal_frame, text="支付 PP 用；这里的手机号不是授权接码手机号").pack(anchor="w")
        paypal_top = ttk.Frame(paypal_frame)
        paypal_top.pack(fill=X, pady=(6, 0))
        ttk.Label(paypal_top, text="PP手机号").pack(side=LEFT)
        ttk.Entry(paypal_top, textvariable=self.paypal_phone, width=24).pack(side=LEFT, padx=(8, 16))
        ttk.Label(paypal_top, text="卡信息").pack(side=LEFT)
        ttk.Entry(paypal_top, textvariable=self.paypal_card).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        self._button(paypal_top, "保存", self.save_paypal_settings, "保存当前 PayPal 手机号、卡信息和取码链接到本地状态文件。").pack(side=LEFT)
        paypal_ext_row = ttk.Frame(paypal_frame)
        paypal_ext_row.pack(fill=X, pady=(8, 0))
        ttk.Label(paypal_ext_row, text="支付链接扩展目录").pack(side=LEFT)
        ttk.Entry(paypal_ext_row, textvariable=self.payment_extension_dir, width=72).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        self._button(paypal_ext_row, "选择目录", self.select_payment_extension_dir, "选择解压后的 Chrome PayPal 支付扩展目录；打开支付链接时会加载它。").pack(side=LEFT, padx=(0, 8))
        ttk.Label(paypal_frame, text="卡信息格式：卡号----有效期----CVV----电话----sms-token----姓名----街道,城市 邮编,国家").pack(anchor="w", pady=(6, 0))
        paypal_sms_row = ttk.Frame(paypal_frame)
        paypal_sms_row.pack(fill=X, pady=(8, 0))
        ttk.Label(paypal_sms_row, text="PP取码链接").pack(side=LEFT)
        ttk.Entry(paypal_sms_row, textvariable=self.paypal_sms_url).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        ttk.Label(paypal_frame, text="PP手机号+接码池（每行一个：+手机号----https://接码链接；打开支付链接优先取第一行，用后移除）").pack(anchor="w", pady=(8, 4))
        self.paypal_phone_pool_text = self._scrolled_text(paypal_frame, height=3)
        self.paypal_phone_pool_text.pack(fill=X)
        ttk.Label(paypal_frame, text="支付卡池（每行：卡号|月|年|CVV；每次打开支付链接自动取一张未用卡替换卡信息前三段）").pack(anchor="w", pady=(8, 4))
        card_top = ttk.Frame(paypal_frame)
        card_top.pack(fill=X)
        self.payment_card_text = self._scrolled_text(card_top, height=3)
        self.payment_card_text.pack(side=LEFT, fill=X, expand=True)
        card_buttons = ttk.Frame(card_top)
        card_buttons.pack(side=LEFT, padx=(8, 0), fill="y")
        self._button(card_buttons, "导入卡", self.import_payment_cards, "把上方支付卡池导入列表；打开支付链接时会按顺序取未用卡。").pack(fill=X)
        self._button(card_buttons, "重置卡", self.reset_payment_cards, "把已导入卡片状态恢复为未用，便于重新测试支付流程。").pack(fill=X, pady=(8, 0))
        self.payment_card_list = ttk.Treeview(paypal_frame, columns=("card", "expiry", "cvv", "status"), show="headings", height=3)
        self.payment_card_list.heading("card", text="卡号")
        self.payment_card_list.heading("expiry", text="有效期")
        self.payment_card_list.heading("cvv", text="CVV")
        self.payment_card_list.heading("status", text="状态")
        self.payment_card_list.column("card", width=220)
        self.payment_card_list.column("expiry", width=100)
        self.payment_card_list.column("cvv", width=80)
        self.payment_card_list.column("status", width=80)
        self.payment_card_list.pack(fill=X, pady=(6, 0))

        proxy_frame = ttk.Frame(tabs, padding=8)
        tabs.add(proxy_frame, text="代理设置")
        ttk.Label(proxy_frame, text="链式：本地代理 -> 动态代理 -> 目标站点").pack(anchor="w")
        proxy_top = ttk.Frame(proxy_frame)
        proxy_top.pack(fill=X, pady=(6, 0))
        ttk.Label(proxy_top, text="本地代理").pack(side=LEFT)
        ttk.Entry(proxy_top, textvariable=self.local_proxy, width=36).pack(side=LEFT, padx=(8, 16))
        ttk.Label(proxy_top, text="留空=不走本地代理；例如 http://127.0.0.1:7890 / socks 请先转 HTTP").pack(side=LEFT)
        proxy_sources = self._paned_window(proxy_frame, "horizontal", sashwidth=6)
        proxy_sources.pack(fill=BOTH, expand=True, pady=(8, 0))
        manual_frame = ttk.LabelFrame(proxy_sources, text="手工代理池", padding=6)
        provider_frame = ttk.LabelFrame(proxy_sources, text="代理提供商配置（后台预检池）", padding=6)
        self._add_pane(proxy_sources, manual_frame, minsize=360)
        self._add_pane(proxy_sources, provider_frame, minsize=420)

        register_proxy_label = ttk.Label(manual_frame, text="")
        register_proxy_label.pack(anchor="w", pady=(0, 4))
        self._register_proxy_pool_title("register", register_proxy_label)
        self.proxy_text = self._scrolled_text(manual_frame, height=3)
        self.proxy_text.pack(fill=X)
        self._bind_proxy_pool_counter(self.proxy_text)
        payment_proxy_row = ttk.Frame(manual_frame)
        payment_proxy_row.pack(fill=X, pady=(8, 0))
        payment_proxy_label = ttk.Label(payment_proxy_row, text="")
        payment_proxy_label.pack(anchor="w")
        self._register_proxy_pool_title("create", payment_proxy_label)
        self.payment_dynamic_proxy_text = self._scrolled_text(payment_proxy_row, height=3)
        self.payment_dynamic_proxy_text.pack(fill=X, pady=(6, 0))
        self._bind_proxy_pool_counter(self.payment_dynamic_proxy_text)
        followup_proxy_row = ttk.Frame(manual_frame)
        followup_proxy_row.pack(fill=X, pady=(8, 0))
        followup_proxy_label = ttk.Label(followup_proxy_row, text="")
        followup_proxy_label.pack(anchor="w")
        self._register_proxy_pool_title("followup", followup_proxy_label)
        self.followup_dynamic_proxy_text = self._scrolled_text(followup_proxy_row, height=3)
        self.followup_dynamic_proxy_text.pack(fill=X, pady=(6, 0))
        self._bind_proxy_pool_counter(self.followup_dynamic_proxy_text)
        approve_proxy_row = ttk.Frame(manual_frame)
        approve_proxy_row.pack(fill=X, pady=(8, 0))
        approve_proxy_label = ttk.Label(approve_proxy_row, text="")
        approve_proxy_label.pack(anchor="w")
        self._register_proxy_pool_title("approve", approve_proxy_label)
        self.approve_dynamic_proxy_text = self._scrolled_text(approve_proxy_row, height=3)
        self.approve_dynamic_proxy_text.pack(fill=X, pady=(6, 0))
        self._bind_proxy_pool_counter(self.approve_dynamic_proxy_text)

        for role in PROVIDER_PROXY_ROLES:
            variables = self.provider_proxy_vars[role]
            role_frame = ttk.LabelFrame(provider_frame, text=PROVIDER_PROXY_ROLE_LABELS[role], padding=5)
            role_frame.pack(fill=X, pady=(0, 6))
            role_header = ttk.Frame(role_frame)
            role_header.pack(fill=X)
            ttk.Checkbutton(role_header, text="启用", variable=variables["enabled"]).pack(side=LEFT)
            ttk.Label(role_header, textvariable=self.provider_proxy_status_vars[role]).pack(side=RIGHT)
            credentials_row = ttk.Frame(role_frame)
            credentials_row.pack(fill=X, pady=(4, 0))
            ttk.Label(credentials_row, text="用户名").pack(side=LEFT)
            ttk.Entry(credentials_row, textvariable=variables["username"], width=17).pack(side=LEFT, padx=(4, 8), fill=X, expand=True)
            ttk.Label(credentials_row, text="密码").pack(side=LEFT)
            ttk.Entry(credentials_row, textvariable=variables["password"], width=17, show="*").pack(side=LEFT, padx=(4, 0), fill=X, expand=True)
            endpoint_row = ttk.Frame(role_frame)
            endpoint_row.pack(fill=X, pady=(4, 0))
            ttk.Label(endpoint_row, text="主机:端口").pack(side=LEFT)
            ttk.Entry(endpoint_row, textvariable=variables["endpoint"], width=28).pack(side=LEFT, padx=(4, 8), fill=X, expand=True)
            ttk.Label(endpoint_row, text="t (1–120)").pack(side=LEFT)
            ttk.Spinbox(endpoint_row, from_=1, to=120, textvariable=variables["duration"], width=5).pack(side=LEFT, padx=(4, 0))
            region_row = ttk.Frame(role_frame)
            region_row.pack(fill=X, pady=(4, 0))
            ttk.Label(region_row, text="国家代码（多选）").pack(side=LEFT)
            ttk.Entry(region_row, textvariable=variables["regions"], width=20).pack(side=LEFT, padx=(4, 8))
            ttk.Label(region_row, text="英文逗号分隔，例如 JP,US,DE；按顺序轮询").pack(side=LEFT)
        provider_controls = ttk.Frame(provider_frame)
        provider_controls.pack(fill=X)
        self._button(
            provider_controls,
            "应用配置并预热",
            self.apply_provider_proxy_configs,
            "校验三阶段提供商配置；变更的池会清空并在后台重新预检到 500 条。",
        ).pack(side=LEFT)
        ttk.Label(provider_controls, text="各启用池达到 200 后开跑；降到 200 自动补至 500").pack(side=LEFT, padx=(8, 0))

        race_row = ttk.Frame(proxy_frame)
        race_row.pack(fill=X, pady=(8, 0))
        ttk.Label(race_row, text="单账号撞链并发数").pack(side=LEFT)
        ttk.Spinbox(race_row, from_=1, to=30, textvariable=self.link_race_concurrency, width=6).pack(side=LEFT, padx=(8, 8))
        ttk.Label(race_row, text="仅批量提取长链时生效；账号并发不受此项限制，每个账号内部同时取多条支付代理尝试").pack(side=LEFT)
        precheck_row = ttk.Frame(proxy_frame)
        precheck_row.pack(fill=X, pady=(8, 0))
        ttk.Label(precheck_row, text="预检上限/池").pack(side=LEFT)
        ttk.Spinbox(precheck_row, from_=1, to=10000, textvariable=self.link_proxy_precheck_limit, width=8).pack(side=LEFT, padx=(8, 8))
        ttk.Label(precheck_row, text="预检并发").pack(side=LEFT)
        ttk.Spinbox(precheck_row, from_=1, to=MAX_LINK_PROXY_PRECHECK_CONCURRENCY, textvariable=self.link_proxy_precheck_concurrency, width=6).pack(side=LEFT, padx=(8, 8))
        self._button(precheck_row, "预检支付代理池", self.precheck_link_proxy_pools, "并发检测三段长链代理池出口；每池最多检测上限数量，失败代理会从池中移除。").pack(side=LEFT, padx=(0, 8))
        ttk.Label(precheck_row, text="默认 500 / 并发 100；通过的代理本轮任务内不重复检测").pack(side=LEFT)
        reuse_proxy_row = ttk.Frame(proxy_frame)
        reuse_proxy_row.pack(fill=X, pady=(8, 0))
        ttk.Label(reuse_proxy_row, text="第一步复用代理").pack(side=LEFT)
        ttk.Entry(reuse_proxy_row, textvariable=self.reuse_payment_proxy, width=72).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        ttk.Label(reuse_proxy_row, text="配置后第一步优先使用，不取用/移除第一步代理池").pack(side=LEFT)
        reuse_followup_row = ttk.Frame(proxy_frame)
        reuse_followup_row.pack(fill=X, pady=(8, 0))
        ttk.Label(reuse_followup_row, text="后续复用代理").pack(side=LEFT)
        ttk.Entry(reuse_followup_row, textvariable=self.reuse_followup_proxy, width=72).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        ttk.Label(reuse_followup_row, text="配置后创建长链后续步骤优先使用，不取用/移除后续代理池").pack(side=LEFT)
        reuse_approve_row = ttk.Frame(proxy_frame)
        reuse_approve_row.pack(fill=X, pady=(8, 0))
        ttk.Label(reuse_approve_row, text="Approve 复用代理").pack(side=LEFT)
        ttk.Entry(reuse_approve_row, textvariable=self.reuse_approve_proxy, width=72).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        ttk.Label(reuse_approve_row, text="配置后 approve 优先使用，不取用/移除 Approve 代理池").pack(side=LEFT)
        ttk.Checkbutton(proxy_frame, text="提取长链强制日本出口（不勾选=只记录出口，不限制）", variable=self.require_japan_extract_proxy).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(proxy_frame, text="注册时使用支付链接动态代理（特殊情况勾选；不勾选则用上方动态代理池）", variable=self.register_with_payment_proxy).pack(anchor="w", pady=(6, 0))
        extension_row = ttk.Frame(proxy_frame)
        extension_row.pack(fill=X, pady=(8, 0))
        ttk.Label(extension_row, text="支付链接扩展目录").pack(side=LEFT)
        ttk.Entry(extension_row, textvariable=self.payment_extension_dir, width=72).pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        self._button(extension_row, "选择目录", self.select_payment_extension_dir, "选择打开支付链接时加载的 Chrome 扩展目录；需为解压后的扩展文件夹。").pack(side=LEFT, padx=(0, 8))
        ttk.Label(extension_row, text="需选择解压后的 Chrome 扩展目录").pack(side=LEFT)

        sound_frame = ttk.Frame(tabs, padding=8)
        tabs.add(sound_frame, text="提示音")
        sound_top = ttk.Frame(sound_frame)
        sound_top.pack(fill=X)
        ttk.Checkbutton(sound_top, text="成功提示音", variable=self.success_sound_enabled, command=self.save_state).pack(side=LEFT, padx=(0, 16))
        ttk.Checkbutton(sound_top, text="长链成功后暂停其他账户", variable=self.pause_others_on_link_success, command=self.save_state).pack(side=LEFT)
        sound_device_row = ttk.Frame(sound_frame)
        sound_device_row.pack(fill=X, pady=(10, 0))
        ttk.Label(sound_device_row, text="输出设备").pack(side=LEFT)
        self.audio_device_combo = ttk.Combobox(sound_device_row, textvariable=self.success_audio_device, values=[AUDIO_DEFAULT_DEVICE_LABEL], state="readonly", width=72)
        self.audio_device_combo.pack(side=LEFT, padx=(8, 8), fill=X, expand=True)
        self.audio_device_combo.bind("<<ComboboxSelected>>", lambda _e: self.save_state())
        self._button(sound_device_row, "刷新设备", self._refresh_audio_devices, "重新扫描系统可用的音频输出设备，并更新下拉列表。").pack(side=LEFT, padx=(0, 8))
        self._button(sound_device_row, "测试提示音", lambda: self._play_success_sound_async(force=True), "立即播放一次成功提示音，用于确认当前输出设备可用。").pack(side=LEFT)
        self._refresh_audio_devices(show_log=True)

        controls = ttk.Frame(bottom_workspace)
        controls.pack(fill=X, pady=(0, 4))
        settings_row = ttk.Frame(controls)
        settings_row.pack(fill=X)
        ttk.Label(settings_row, text="支付模式").pack(side=LEFT)
        ttk.Combobox(settings_row, textvariable=self.payment_mode, values=list(PAYMENT_MODES.keys()), state="readonly", width=22).pack(side=LEFT, padx=(6, 12))
        ttk.Label(settings_row, text="目标金额").pack(side=LEFT)
        ttk.Entry(settings_row, textvariable=self.target_amount, width=9).pack(side=LEFT, padx=(4, 12))
        ttk.Label(settings_row, text="每账号尝试").pack(side=LEFT)
        ttk.Spinbox(settings_row, from_=1, to=10000, textvariable=self.link_attempt_limit, width=6).pack(side=LEFT, padx=(4, 12))
        ttk.Label(settings_row, text="认证并发").pack(side=LEFT)
        ttk.Spinbox(settings_row, from_=1, to=MAX_AUTH_CONCURRENCY, textvariable=self.auth_concurrency, width=6).pack(side=LEFT, padx=(4, 12))
        ttk.Checkbutton(settings_row, text="无头浏览器", variable=self.headless).pack(side=LEFT)
        ttk.Label(settings_row, text="导出名前缀").pack(side=LEFT, padx=(16, 4))
        ttk.Entry(settings_row, textvariable=self.export_name_prefix, width=18).pack(side=LEFT)

        primary_row = ttk.Frame(controls)
        primary_row.pack(fill=X, pady=(5, 0))
        account_tools = ttk.LabelFrame(primary_row, text="账号", padding=3)
        account_tools.pack(side=LEFT, padx=(0, 5))
        self._button(account_tools, "导入", self.import_accounts, "把导入框中的邮箱加入当前分组。").pack(side=LEFT, padx=2)
        self._button(account_tools, "删除选中", self.delete_selected_account, "删除当前选中的邮箱和本地结果。").pack(side=LEFT, padx=2)
        self._button(account_tools, "清空列表", self.clear_accounts, "清空全部邮箱及其结果。").pack(side=LEFT, padx=2)
        self._button(account_tools, "刷新类型", self.refresh_selected_account_type, "使用 OpenAI RT 刷新选中账号类型。").pack(side=LEFT, padx=2)

        auth_tools = ttk.LabelFrame(primary_row, text="注册 / Session", padding=3)
        auth_tools.pack(side=LEFT, padx=(0, 5))
        self._button(auth_tools, "注册或登录", self.start_auth_selected, "完成选中账号认证并保留浏览器，不获取 Session。").pack(side=LEFT, padx=2)
        self._button(auth_tools, "注册或登录并获取 Session", self.start_selected, "完成选中账号认证并保存 Session/Access Token。").pack(side=LEFT, padx=2)
        self._button(auth_tools, "Plus 授权获取 RT", self.start_authorize_selected, "为选中账号执行授权并获取 OpenAI refresh_token。").pack(side=LEFT, padx=2)
        self._button(auth_tools, "Team 随机注册获取 RT", self.start_team_random_register, "生成随机 Team 邮箱并注册获取 RT。").pack(side=LEFT, padx=2)

        self._button(primary_row, "停止当前任务", self.stop_current_task, "停止当前注册、提链或支付窗口任务。").pack(side=RIGHT, padx=2)

        secondary_row = ttk.Frame(controls)
        secondary_row.pack(fill=X, pady=(4, 0))
        link_tools = ttk.LabelFrame(secondary_row, text="支付链接", padding=3)
        link_tools.pack(side=LEFT, padx=(0, 5))
        self._button(link_tools, "Session 生成", self.generate_link_from_selected_session, "使用选中账号 Session 生成支付链接。").pack(side=LEFT, padx=2)
        self._button(link_tools, "粘贴 Session", self.generate_link_from_pasted_session, "粘贴并保存 Session JSON 或 Access Token。").pack(side=LEFT, padx=2)
        self._button(link_tools, "全选有 Session", self.select_all_session_accounts, "选择当前分组中已有 Access Token 的账号。").pack(side=LEFT, padx=2)
        self._button(link_tools, "批量生成选中", self.generate_links_from_selected_sessions, "为选中 Session 批量生成支付链接。").pack(side=LEFT, padx=2)
        self._button(link_tools, "重新获取", self.refetch_selected_link, "重新登录选中账号并获取支付链接。").pack(side=LEFT, padx=2)
        self._button(link_tools, "批量重新获取", self.refetch_selected_links_batch, "并发重新登录多个选中账号并获取支付链接。").pack(side=LEFT, padx=2)
        self._button(link_tools, "切换支付代理", self.switch_current_trial_to_payment_proxy, "试用流程中切换当前页面代理。").pack(side=LEFT, padx=2)

        export_tools = ttk.LabelFrame(secondary_row, text="导出", padding=3)
        export_tools.pack(side=LEFT, padx=(0, 5))
        self._button(export_tools, "已授权", self.export_authorized, "导出已授权账号。").pack(side=LEFT, padx=2)
        self._button(export_tools, "邮箱 RT", self.export_authorized_email_rt, "导出邮箱 refresh_token。").pack(side=LEFT, padx=2)
        self._button(export_tools, "sub2api", self.export_sub2api, "导出 sub2api 格式。").pack(side=LEFT, padx=2)
        self._button(export_tools, "选中 Session", self.export_selected_sessions, "导出当前选中的 Session JSON。").pack(side=LEFT, padx=2)
        self._button(export_tools, "选中 Raw", self.export_selected_raw, "导出当前选中账号的 Raw 内容。").pack(side=LEFT, padx=2)

        body = self._paned_window(bottom_workspace, "horizontal", sashwidth=8)
        self.body_panes = body
        body.pack(fill=BOTH, expand=True)

        left = ttk.Frame(body)
        self._add_pane(body, left, minsize=260)
        account_header = ttk.Frame(left)
        account_header.pack(fill=X)
        ttk.Label(account_header, text="邮箱列表").pack(side=LEFT)
        ttk.Label(account_header, text="分组").pack(side=LEFT, padx=(12, 4))
        self.account_group_combo = ttk.Combobox(
            account_header,
            textvariable=self.account_group_filter,
            state="readonly",
            width=16,
            values=[ACCOUNT_ALL_GROUP, ACCOUNT_DEFAULT_GROUP],
        )
        self.account_group_combo.pack(side=LEFT)
        self.account_group_combo.bind("<<ComboboxSelected>>", self._on_account_group_filter_changed)
        self._button(account_header, "新建", self.create_account_group, "新建自定义邮箱分组。").pack(side=LEFT, padx=(6, 2))
        self._button(account_header, "重命名", self.rename_account_group, "重命名当前自定义分组。").pack(side=LEFT, padx=2)
        self._button(account_header, "删除", self.delete_account_group, "删除当前分组并把其中邮箱移回未分组。").pack(side=LEFT, padx=2)
        account_list_frame = ttk.Frame(left)
        account_list_frame.pack(fill=BOTH, expand=True, pady=(6, 0))
        account_list_scrollbar = ttk.Scrollbar(account_list_frame, orient="vertical")
        self.account_list = ttk.Treeview(account_list_frame, columns=("email", "type", "status", "attempts"), show="headings", height=14, selectmode="extended", yscrollcommand=account_list_scrollbar.set)
        account_list_scrollbar.configure(command=self.account_list.yview)
        self._refresh_account_sort_headings()
        self.account_list.column("email", width=270)
        self.account_list.column("type", width=70)
        self.account_list.column("status", width=140)
        self.account_list.column("attempts", width=90, anchor="center")
        self.account_list.pack(side=LEFT, fill=BOTH, expand=True)
        account_list_scrollbar.pack(side=RIGHT, fill="y")
        self.account_list.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_account_link())
        self.account_list.bind("<Button-1>", self._on_account_list_click, add="+")
        self.account_list.bind("<ButtonPress-2>", self._on_account_list_middle_press)
        self.account_list.bind("<B2-Motion>", self._on_account_list_middle_drag)
        self.account_list.bind("<ButtonRelease-2>", self._on_account_list_middle_release)
        self.account_list.bind("<Button-3>", self._show_account_context_menu)
        self.root.bind_all("<Button-1>", self._on_global_click, add="+")
        self.root.bind_all("<Escape>", lambda _event: self._clear_ui_focus(), add="+")
        self.tabs.bind("<<NotebookTabChanged>>", lambda _event: self.root.after_idle(self._clear_ui_focus), add="+")

        right = ttk.Frame(body)
        self._add_pane(body, right, minsize=420)
        result_header = ttk.Frame(right)
        result_header.pack(fill=X)
        ttk.Label(result_header, text="当前选中邮箱链接（旧功能）").pack(side=LEFT)
        self._button(result_header, "批量打开选中", self.open_selected_links, "用支付代理依次打开选中账号的长链；每个窗口会取用一组支付资料。").pack(side=RIGHT, padx=(0, 8))
        self._button(result_header, "浏览器打开", self.open_link, "用新的支付代理打开当前长链，并加载 PayPal 扩展和支付资料。").pack(side=RIGHT)
        self._button(result_header, "用提链代理打开", self.open_link_with_extraction_proxy, "使用该长链生成时保存的后续代理打开支付窗口，便于保持支付链路一致。").pack(side=RIGHT, padx=(0, 8))
        self._button(result_header, "复制长链接", self.copy_link, "复制当前选中邮箱保存的长链到剪贴板。").pack(side=RIGHT, padx=(0, 8))

        link_bar = ttk.Frame(right)
        link_bar.pack(fill=X, pady=(6, 8))
        self.link_var = StringVar(value="")
        ttk.Entry(link_bar, textvariable=self.link_var).pack(side=LEFT, fill=X, expand=True)

        proxy_bar = ttk.Frame(right)
        proxy_bar.pack(fill=X, pady=(0, 8))
        ttk.Label(proxy_bar, text="长链使用代理").pack(side=LEFT)
        self.link_proxy_var = StringVar(value="")
        ttk.Entry(proxy_bar, textvariable=self.link_proxy_var).pack(side=LEFT, fill=X, expand=True, padx=(8, 8))
        self._button(proxy_bar, "复制代理", self.copy_link_proxy, "复制当前长链保存的三段代理摘要：第一步、后续、Approve。").pack(side=LEFT)

        session_header = ttk.Frame(right)
        session_header.pack(fill=X, pady=(4, 0))
        ttk.Label(session_header, text="当前选中邮箱 Session 信息").pack(side=LEFT)
        self._button(session_header, "复制 Access Token", self.copy_access_token, "复制当前选中邮箱保存的 ChatGPT Access Token。").pack(side=RIGHT, padx=(0, 8))
        self._button(session_header, "复制 Session JSON", self.copy_session_json, "复制当前选中邮箱保存的 Session JSON，便于备份或粘贴复用。").pack(side=RIGHT, padx=(0, 8))
        self.session_text = self._scrolled_text(right, height=5)
        self.session_text.pack(fill=X, pady=(6, 8))

    def load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = self.state_store.load()
            self.accounts = [account_from_dict(item) for item in data.get("accounts", [])]
            self.phones = [phone_from_dict(item) for item in data.get("phones", []) if item]
            self.payment_cards = [payment_card_from_dict(item) for item in data.get("payment_cards", []) if item]
            self.results = {str(k): str(v) for k, v in data.get("results", {}).items() if v}
            raw_sessions = data.get("session_results", {})
            self.session_results = {str(k): v for k, v in raw_sessions.items() if isinstance(v, dict)} if isinstance(raw_sessions, dict) else {}
            raw_attempt_counts = data.get("link_attempt_counts", {})
            self.link_attempt_counts = {str(k): max(0, int(v or 0)) for k, v in raw_attempt_counts.items()} if isinstance(raw_attempt_counts, dict) else {}
            settings = data.get("settings", {})
            self.saved_window_geometry = str(settings.get("window_geometry") or "")
            self.saved_window_zoomed = bool(settings.get("window_zoomed"))
            self.main_sash_ratio = min(0.85, max(0.2, float(settings.get("main_sash_ratio") or 0.58)))
            self.log_sash_ratio = min(0.8, max(0.2, float(settings.get("log_sash_ratio") or 0.5)))
            self.body_sash_ratio = min(0.8, max(0.2, float(settings.get("body_sash_ratio") or 0.34)))
            saved_groups = settings.get("account_groups", [])
            if isinstance(saved_groups, list):
                self.account_groups = [ACCOUNT_DEFAULT_GROUP]
                for value in saved_groups:
                    group = str(value or "").strip()
                    if group and group.casefold() not in {item.casefold() for item in self.account_groups} and group != ACCOUNT_ALL_GROUP:
                        self.account_groups.append(group)
            for account in self.accounts:
                group = str(account.group or ACCOUNT_DEFAULT_GROUP).strip() or ACCOUNT_DEFAULT_GROUP
                account.group = group
                if group.casefold() not in {item.casefold() for item in self.account_groups}:
                    self.account_groups.append(group)
            saved_group_filter = str(settings.get("account_group_filter") or ACCOUNT_ALL_GROUP)
            self.account_group_filter.set(saved_group_filter if saved_group_filter in [ACCOUNT_ALL_GROUP, *self.account_groups] else ACCOUNT_ALL_GROUP)
            self._set_account_sort_state(
                str(settings.get("account_sort_column") or "email"),
                str(settings.get("account_sort_direction") or ACCOUNT_SORT_CUSTOM),
            )
            self._refresh_account_group_combo()
            saved_payment_mode = str(settings.get("payment_mode") or "")
            if saved_payment_mode in PAYMENT_MODES:
                self.payment_mode.set(saved_payment_mode)
            elif saved_payment_mode in PAYMENT_MODE_ALIASES:
                self.payment_mode.set(PAYMENT_MODE_ALIASES[saved_payment_mode])
            if "headless" in settings:
                self.headless.set(bool(settings["headless"]))
            if "target_amount" in settings:
                self.target_amount.set(str(settings["target_amount"]))
            if "local_proxy" in settings:
                self.local_proxy.set(str(settings["local_proxy"]))
            if "dynamic_proxies" in settings:
                self.proxy_text.delete("1.0", END)
                self.proxy_text.insert(END, str(settings["dynamic_proxies"]))
            if "payment_dynamic_proxy" in settings:
                self.payment_dynamic_proxy.set(str(settings["payment_dynamic_proxy"]))
                self.payment_dynamic_proxy_text.delete("1.0", END)
                self.payment_dynamic_proxy_text.insert(END, str(settings["payment_dynamic_proxy"]))
            if "followup_dynamic_proxy" in settings:
                self.followup_dynamic_proxy.set(str(settings["followup_dynamic_proxy"]))
                self.followup_dynamic_proxy_text.delete("1.0", END)
                self.followup_dynamic_proxy_text.insert(END, str(settings["followup_dynamic_proxy"]))
            if "approve_dynamic_proxy" in settings:
                self.approve_dynamic_proxy.set(str(settings["approve_dynamic_proxy"]))
                self.approve_dynamic_proxy_text.delete("1.0", END)
                self.approve_dynamic_proxy_text.insert(END, str(settings["approve_dynamic_proxy"]))
            if "reuse_payment_proxy" in settings:
                self.reuse_payment_proxy.set(str(settings["reuse_payment_proxy"]))
            if "reuse_followup_proxy" in settings:
                self.reuse_followup_proxy.set(str(settings["reuse_followup_proxy"]))
            elif "reuse_payment_proxy" in settings:
                self.reuse_followup_proxy.set(str(settings["reuse_payment_proxy"]))
            if "reuse_approve_proxy" in settings:
                self.reuse_approve_proxy.set(str(settings["reuse_approve_proxy"]))
            if "require_japan_extract_proxy" in settings:
                self.require_japan_extract_proxy.set(bool(settings["require_japan_extract_proxy"]))
            if "register_with_payment_proxy" in settings:
                self.register_with_payment_proxy.set(bool(settings["register_with_payment_proxy"]))
            if "auth_concurrency" in settings:
                raw_auth_concurrency = settings["auth_concurrency"]
                auth_concurrency = DEFAULT_AUTH_CONCURRENCY if raw_auth_concurrency in ("", None) else int(raw_auth_concurrency)
                self.auth_concurrency.set(min(MAX_AUTH_CONCURRENCY, max(1, auth_concurrency)))
            if "link_race_concurrency" in settings:
                self.link_race_concurrency.set(min(30, max(1, int(settings["link_race_concurrency"] or 1))))
            if "link_proxy_precheck_limit" in settings:
                self.link_proxy_precheck_limit.set(max(1, int(settings["link_proxy_precheck_limit"] or DEFAULT_LINK_PROXY_PRECHECK_LIMIT)))
            if "link_proxy_precheck_concurrency" in settings:
                precheck_concurrency = settings["link_proxy_precheck_concurrency"]
                precheck_concurrency = (
                    DEFAULT_LINK_PROXY_PRECHECK_CONCURRENCY
                    if precheck_concurrency in ("", None)
                    else int(precheck_concurrency)
                )
                self.link_proxy_precheck_concurrency.set(
                    min(
                        MAX_LINK_PROXY_PRECHECK_CONCURRENCY,
                        max(1, precheck_concurrency),
                    )
                )
            if "link_attempt_limit" in settings:
                self.link_attempt_limit.set(min(10000, max(1, int(settings["link_attempt_limit"] or 1))))
            provider_settings = settings.get("provider_proxy_configs", {})
            if isinstance(provider_settings, dict):
                for role in PROVIDER_PROXY_ROLES:
                    self._set_provider_proxy_config_vars(role, ProxyProviderConfig.from_state(provider_settings.get(role)))
            if "payment_extension_dir" in settings:
                self.payment_extension_dir.set(str(settings["payment_extension_dir"]).strip() or DEFAULT_PAYPAL_EXTENSION_DIR)
            if "paypal_phone" in settings:
                self.paypal_phone.set(str(settings["paypal_phone"]))
            if "paypal_card" in settings:
                self.paypal_card.set(str(settings["paypal_card"]))
            if "paypal_sms_url" in settings:
                self.paypal_sms_url.set(str(settings["paypal_sms_url"]))
            if "paypal_phone_pool" in settings:
                self.paypal_phone_pool.set(str(settings["paypal_phone_pool"]))
                self.paypal_phone_pool_text.delete("1.0", END)
                self.paypal_phone_pool_text.insert(END, str(settings["paypal_phone_pool"]))
            if "export_name_prefix" in settings:
                self.export_name_prefix.set(str(settings["export_name_prefix"]))
            if "phone_max_receive_count" in settings:
                self.phone_max_receive_count.set(max(0, int(settings["phone_max_receive_count"] or 0)))
            if "paypal_phone_pool_index" in settings:
                self.paypal_phone_pool_index = max(0, int(settings["paypal_phone_pool_index"] or 0))
            if "success_sound_enabled" in settings:
                self.success_sound_enabled.set(bool(settings["success_sound_enabled"]))
            if "success_audio_device" in settings:
                self.success_audio_device.set(str(settings["success_audio_device"]).strip() or AUDIO_DEFAULT_DEVICE_LABEL)
            if "pause_others_on_link_success" in settings:
                self.pause_others_on_link_success.set(bool(settings["pause_others_on_link_success"]))
            self._refresh_audio_devices(show_log=False)
            self._render_accounts()
            self._render_phones()
            self._render_payment_cards()
            self._render_results()
            for warning in self.state_store.warnings:
                self.log(warning)
            self.log(f"已加载本地记录: {STATE_FILE}")
            if self.state_store.loaded_legacy:
                self._dirty_all_sessions = True
                self.save_state()
                self.log("检测到旧版 state.json，已排队迁移为轻量索引 + 拆分 Session 文件")
            elif self.state_store.missing_session_files:
                self._dirty_all_sessions = True
                self.save_state()
        except Exception as exc:
            self.log(f"加载本地记录失败: {exc}")

    def _build_state_snapshot(self) -> dict:
        return {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "accounts": [account_to_dict(account) for account in self.accounts],
            "phones": [phone_to_dict(phone) for phone in self.phones],
            "payment_cards": [payment_card_to_dict(card) for card in self.payment_cards],
            "results": self.results,
            "session_results": self.session_results,
            "link_attempt_counts": self.link_attempt_counts,
            "settings": {
                "payment_mode": self.payment_mode.get(),
                "target_amount": self.target_amount.get().strip(),
                "headless": bool(self.headless.get()),
                "local_proxy": self.local_proxy.get(),
                "dynamic_proxies": self.proxy_text.get("1.0", END).strip(),
                "payment_dynamic_proxy": self.payment_dynamic_proxy_text.get("1.0", END).strip(),
                "followup_dynamic_proxy": self.followup_dynamic_proxy_text.get("1.0", END).strip(),
                "approve_dynamic_proxy": self.approve_dynamic_proxy_text.get("1.0", END).strip(),
                "reuse_payment_proxy": self.reuse_payment_proxy.get().strip(),
                "reuse_followup_proxy": self.reuse_followup_proxy.get().strip(),
                "reuse_approve_proxy": self.reuse_approve_proxy.get().strip(),
                "require_japan_extract_proxy": bool(self.require_japan_extract_proxy.get()),
                "register_with_payment_proxy": bool(self.register_with_payment_proxy.get()),
                "auth_concurrency": self._auth_concurrency(),
                "link_race_concurrency": self._link_race_concurrency(),
                "link_proxy_precheck_limit": self._link_proxy_precheck_limit(),
                "link_proxy_precheck_concurrency": self._link_proxy_precheck_concurrency(),
                "link_attempt_limit": self._link_attempt_limit(),
                "provider_proxy_configs": {
                    role: self._provider_proxy_config_from_vars(role).state_dict()
                    for role in PROVIDER_PROXY_ROLES
                },
                "payment_extension_dir": self.payment_extension_dir.get().strip(),
                "paypal_phone": self.paypal_phone.get().strip(),
                "paypal_card": self.paypal_card.get().strip(),
                "paypal_sms_url": self.paypal_sms_url.get().strip(),
                "paypal_phone_pool": self.paypal_phone_pool_text.get("1.0", END).strip(),
                "export_name_prefix": self.export_name_prefix.get().strip(),
                "phone_max_receive_count": max(0, int(self.phone_max_receive_count.get() or 0)),
                "paypal_phone_pool_index": self.paypal_phone_pool_index,
                "success_sound_enabled": bool(self.success_sound_enabled.get()),
                "success_audio_device": self.success_audio_device.get().strip() or AUDIO_DEFAULT_DEVICE_LABEL,
                "pause_others_on_link_success": bool(self.pause_others_on_link_success.get()),
                "account_groups": list(self.account_groups),
                "account_group_filter": self.account_group_filter.get(),
                "account_sort_column": self.account_sort_column,
                "account_sort_direction": self.account_sort_direction,
                "window_geometry": self._current_window_geometry_for_state(),
                "window_zoomed": str(self.root.state()) == "zoomed",
                "main_sash_ratio": self._paned_sash_ratio(getattr(self, "main_panes", None), "vertical", self.main_sash_ratio),
                "log_sash_ratio": self._paned_sash_ratio(getattr(self, "log_columns", None), "horizontal", self.log_sash_ratio),
                "body_sash_ratio": self._paned_sash_ratio(getattr(self, "body_panes", None), "horizontal", self.body_sash_ratio),
            },
        }

    def save_state(self, flush: bool = False) -> None:
        dirty_session_emails = None if flush or self._dirty_all_sessions else set(self._dirty_session_emails)
        self._dirty_all_sessions = False
        self._dirty_session_emails.clear()
        self.state_store.save(self._build_state_snapshot(), dirty_session_emails, flush=flush)

    def _mark_session_dirty(self, email_addr: str) -> None:
        email_key = str(email_addr or "").strip()
        if email_key:
            self._dirty_session_emails.add(email_key)

    def _on_close(self) -> None:
        self.provider_proxy_manager.stop()
        for context, browser, _proxy in list(KEPT_REGISTER_BROWSER_SESSIONS.values()):
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        KEPT_REGISTER_BROWSER_SESSIONS.clear()
        try:
            self.save_state(flush=True)
        except Exception as exc:
            try:
                messagebox.showwarning(APP_TITLE, f"保存本地状态失败: {exc}")
            except Exception:
                pass
        self.root.destroy()

    def load_file(self) -> None:
        path = filedialog.askopenfilename(title="选择邮箱文件", filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not path:
            return
        self.import_text.delete("1.0", END)
        self.import_text.insert(END, Path(path).read_text(encoding="utf-8"))

    def select_payment_extension_dir(self) -> None:
        path = filedialog.askdirectory(title="选择解压后的 Chrome 扩展目录")
        if not path:
            return
        self.payment_extension_dir.set(path)
        self.save_state()

    def save_paypal_settings(self) -> None:
        self.save_state()
        self.log("PayPal 扩展资料已保存")

    def import_phones(self) -> None:
        lines = [line.strip() for line in self.phone_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            messagebox.showwarning(APP_TITLE, "请先粘贴手机号")
            return
        imported = 0
        errors = []
        for index, line in enumerate(lines, start=1):
            try:
                phone = parse_phone_line(line)
            except Exception as exc:
                errors.append(f"第 {index} 行: {exc}")
                continue
            old_index = next((i for i, item in enumerate(self.phones) if item.number == phone.number), -1)
            if old_index >= 0:
                self.phones[old_index].sms_url = phone.sms_url
                if self.phones[old_index].status == "不可用":
                    self.phones[old_index].status = "可用"
                    self.phones[old_index].last_error = ""
            else:
                self.phones.append(phone)
            imported += 1
        self._render_phones()
        self.save_state()
        self.log(f"已导入 {imported} 个手机号" + (f"；失败: {'; '.join(errors)}" if errors else ""))

    def reset_phones(self) -> None:
        for phone in self.phones:
            phone.status = "可用"
            phone.last_error = ""
            phone.receive_count = 0
        self._render_phones()
        self.save_state()
        self.log("手机号池已重置为可用")

    def clear_phones(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行，不能清空手机号池")
            return
        if not self.phones:
            return
        if not messagebox.askyesno(APP_TITLE, f"确认清空手机号池？\n当前共 {len(self.phones)} 个手机号"):
            return
        self.phones.clear()
        self._render_phones()
        self.save_state()
        self.log("手机号池已清空")

    def _phone_receive_limit(self) -> int:
        try:
            return max(0, int(self.phone_max_receive_count.get() or 0))
        except Exception:
            return 0

    def _phone_is_frozen(self, phone: PhoneEntry) -> bool:
        limit = self._phone_receive_limit()
        return limit > 0 and phone.receive_count >= limit

    def fetch_selected_phone_code(self) -> None:
        selected = self.phone_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中手机号")
            return
        try:
            index = int(selected[0])
        except ValueError:
            return
        if index < 0 or index >= len(self.phones):
            return
        phone = self.phones[index]
        threading.Thread(target=self._manual_fetch_phone_code_worker, args=(phone,), daemon=True).start()

    def _manual_fetch_phone_code_worker(self, phone: PhoneEntry) -> None:
        try:
            self._emit_log(f"[手动取码] 开始读取 {phone.number}")
            code = self._wait_for_phone_code(phone.number, phone.sms_url, timeout=30)
            self._emit_log(f"[手动取码] {phone.number} 读取到验证码: {code}")
            self.events.put(("phone-code-popup", phone.number, code))
        except Exception as exc:
            self._emit_log(f"[手动取码] {phone.number} 读取失败: {exc}")
            self.events.put(("phone-code-popup", phone.number, ""))

    def import_payment_cards(self) -> None:
        lines = [line.strip() for line in self.payment_card_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            messagebox.showwarning(APP_TITLE, "请先粘贴支付卡")
            return
        imported = 0
        errors = []
        for index, line in enumerate(lines, start=1):
            try:
                card = parse_payment_card_line(line)
            except Exception as exc:
                errors.append(f"第 {index} 行: {exc}")
                continue
            old_index = next((i for i, item in enumerate(self.payment_cards) if item.card == card.card), -1)
            if old_index >= 0:
                old_status = self.payment_cards[old_index].status
                self.payment_cards[old_index] = card
                self.payment_cards[old_index].status = old_status
            else:
                self.payment_cards.append(card)
            imported += 1
        self._render_payment_cards()
        self.save_state()
        self.log(f"已导入 {imported} 张支付卡" + (f"；失败: {'; '.join(errors)}" if errors else ""))

    def reset_payment_cards(self) -> None:
        for card in self.payment_cards:
            card.status = "未用"
        self._render_payment_cards()
        self.save_state()
        self.log("支付卡池已重置为未用")

    def import_accounts(self) -> None:
        lines = [line.strip() for line in self.import_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            messagebox.showwarning(APP_TITLE, "请先粘贴邮箱账户")
            return
        imported = 0
        errors = []
        active_group = self.account_group_filter.get()
        import_group = active_group if active_group not in {ACCOUNT_ALL_GROUP, ACCOUNT_DEFAULT_GROUP} else ACCOUNT_DEFAULT_GROUP
        for index, line in enumerate(lines, start=1):
            try:
                account = parse_account_line(line)
            except Exception as exc:
                errors.append(f"第 {index} 行: {exc}")
                continue
            old_index = next((i for i, item in enumerate(self.accounts) if item.email.lower() == account.email.lower()), -1)
            if old_index >= 0:
                account.account_type = self.accounts[old_index].account_type
                account.status = self.accounts[old_index].status
                account.openai_rt = self.accounts[old_index].openai_rt or account.openai_rt
                account.auth_phone_number = account.auth_phone_number or self.accounts[old_index].auth_phone_number
                account.auth_phone_sms_url = account.auth_phone_sms_url or self.accounts[old_index].auth_phone_sms_url
                account.group = self.accounts[old_index].group
                self.accounts[old_index] = account
            else:
                account.group = import_group
                self.accounts.append(account)
            imported += 1
        self._render_accounts()
        self.save_state()
        self.log(f"已导入 {imported} 个邮箱" + (f"；失败: {'; '.join(errors)}" if errors else ""))

    def clear_accounts(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行，不能清空列表")
            return
        self.accounts.clear()
        self.account_groups = [ACCOUNT_DEFAULT_GROUP]
        self.account_group_filter.set(ACCOUNT_ALL_GROUP)
        self.results.clear()
        self.link_attempt_counts.clear()
        self._render_accounts()
        self._render_results()
        self.link_var.set("")
        self.save_state()

    def delete_selected_account(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行，不能删除邮箱")
            return
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中要删除的邮箱")
            return
        indices = sorted({int(item) for item in selected if str(item).isdigit()}, reverse=True)
        accounts = [self.accounts[index] for index in indices if 0 <= index < len(self.accounts)]
        if not accounts:
            return
        if len(accounts) == 1:
            confirm_text = f"确认删除邮箱？\n{accounts[0].email}"
        else:
            preview = "\n".join(account.email for account in accounts[:20])
            if len(accounts) > 20:
                preview += f"\n... 另有 {len(accounts) - 20} 个"
            confirm_text = f"确认删除 {len(accounts)} 个邮箱？\n{preview}"
        if not messagebox.askyesno(APP_TITLE, confirm_text):
            return
        current_link = self.link_var.get().strip()
        deleted_emails = []
        clear_link = False
        for index in indices:
            if index < 0 or index >= len(self.accounts):
                continue
            account = self.accounts[index]
            old_link = self.results.pop(account.email, "")
            self.link_attempt_counts.pop(account.email, None)
            if old_link and current_link == old_link:
                clear_link = True
            deleted_emails.append(account.email)
            del self.accounts[index]
        if clear_link:
            self.link_var.set("")
        self._render_accounts()
        self._render_results()
        self.save_state()
        self.log(f"已删除邮箱 {len(deleted_emails)} 个: {', '.join(deleted_emails[:10])}" + (f" 等" if len(deleted_emails) > 10 else ""))

    def set_selected_account_type(self, account_type: str) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱，可多选")
            return
        updated = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if index < 0 or index >= len(self.accounts):
                continue
            account = self.accounts[index]
            account.account_type = account_type
            if account_type == "plus":
                account.status = account.status or "Plus"
            if account_type == "team":
                account.status = account.status or "Team待注册"
            if account_type == "free":
                account.status = ""
                account.openai_rt = ""
            updated.append(account.email)
        if not updated:
            return
        self._render_accounts()
        self.save_state()
        self.log(f"已将 {len(updated)} 个邮箱类型改为 {account_type}: {', '.join(updated[:10])}" + (" 等" if len(updated) > 10 else ""))

    def refresh_selected_account_type(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中一个邮箱")
            return
        index = int(selected[0])
        if index < 0 or index >= len(self.accounts):
            return
        account = self.accounts[index]
        if not account.openai_rt:
            messagebox.showwarning(APP_TITLE, "这个邮箱还没有 rt_token，请先 Plus授权获取RT")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        self.running = True
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxy = self._next_dynamic_proxy(self._read_dynamic_proxies())
        threading.Thread(target=self._refresh_account_type_worker, args=(account, local_proxy, dynamic_proxy), daemon=True).start()

    def _refresh_account_type_worker(self, account: MailAccount, local_proxy: str, dynamic_proxy: str) -> None:
        log_account = self._account_logger(account)
        try:
            self.events.put(("status", account.email, "刷新类型中"))
            with ProxyChainServer(local_proxy, dynamic_proxy, log_account) as chain:
                proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=dynamic_proxy, chain_url=chain.url)
                log_account(f"刷新类型使用代理: {proxy.label}")
                account_type, detail, new_rt = detect_openai_account_type(account.openai_rt, chain.url)
            account.account_type = account_type
            if new_rt:
                account.openai_rt = new_rt
            account.status = "Team" if account_type == "team" else "已绑定手机号" if account_type == "plus" else "Free"
            self.events.put(("account-updated", account.email))
            self.events.put(("status", account.email, account.status))
            log_account(f"当前类型: {account_type} ({detail})")
        except Exception as exc:
            log_account(f"刷新类型失败: {exc}")
            self.events.put(("status", account.email, "刷新失败"))
        finally:
            self.events.put(("done",))

    def stop_current_task(self) -> None:
        self.stop_event.set()
        if self.payment_context:
            try:
                self.payment_context.close()
            except Exception:
                pass
        for context in list(self.payment_contexts):
            try:
                context.close()
            except Exception:
                pass
        for prompt_id, result_queue in list(self.pending_prompts.items()):
            try:
                result_queue.put("")
            except Exception:
                pass
            self.pending_prompts.pop(prompt_id, None)
        if self.running or self.opening_payment_link:
            self.log("已请求停止当前任务")
        self.running = False
        self.opening_payment_link = False
        self.save_state()

    def start_selected(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱")
            return
        accounts = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self.accounts):
                accounts.append(self.accounts[index])
        if accounts:
            self._start_worker(accounts, collect_session=True)

    def start_auth_selected(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱")
            return
        accounts = [self.accounts[int(item)] for item in selected if str(item).isdigit() and 0 <= int(item) < len(self.accounts)]
        if accounts:
            self._start_worker(accounts, collect_session=False)

    def start_all(self) -> None:
        if not self.accounts:
            messagebox.showwarning(APP_TITLE, "请先导入邮箱")
            return
        self._start_worker(list(self.accounts))

    def start_team_random_register(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        email_addr = generate_team_email()
        account = MailAccount(
            email=email_addr,
            password="",
            client_id="",
            refresh_token="",
            raw=email_addr,
            account_type="team",
            status="Team待注册",
        )
        self.accounts.append(account)
        self._render_accounts()
        self._select_account_by_email(email_addr)
        self.running = True
        self.stop_event.clear()
        self.save_state()
        mode = self.payment_mode.get()
        headless = bool(self.headless.get())
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        use_payment_proxy_for_register = bool(self.register_with_payment_proxy.get())
        dynamic_proxy = self._peek_payment_dynamic_proxy() if use_payment_proxy_for_register else (self._take_dynamic_proxies(1)[0] if self._read_dynamic_proxies() else "")
        threading.Thread(target=self._run_team_account_worker, args=(account, mode, headless, local_proxy, dynamic_proxy, use_payment_proxy_for_register), daemon=True).start()

    def refetch_selected_link(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        index = int(selected[0])
        account = self.accounts[index]
        self.running = True
        self.stop_event.clear()
        self.save_state()
        mode = self.payment_mode.get()
        headless = bool(self.headless.get())
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxies = self._take_dynamic_proxies(1)
        reuse_proxy = normalize_proxy_url(self.reuse_payment_proxy.get())
        reuse_followup_proxy = normalize_proxy_url(self.reuse_followup_proxy.get())
        reuse_approve_proxy = normalize_proxy_url(self.reuse_approve_proxy.get())
        create_candidates = [reuse_proxy] if reuse_proxy else ([self._peek_payment_dynamic_proxy()] if self._peek_payment_dynamic_proxy() else [])
        followup_candidates = [reuse_followup_proxy] if reuse_followup_proxy else ([self._peek_followup_dynamic_proxy()] if self._peek_followup_dynamic_proxy() else [])
        approve_candidates = [reuse_approve_proxy] if reuse_approve_proxy else ([self._peek_approve_dynamic_proxy()] if self._peek_approve_dynamic_proxy() else [])
        wanted_link_proxy_pool = bool(create_candidates or followup_candidates or approve_candidates)
        link_triples = self._link_proxy_triples(create_candidates, followup_candidates, approve_candidates, 1 if wanted_link_proxy_pool else 0)
        if wanted_link_proxy_pool and not link_triples:
            self.log("支付代理池已耗尽，重新获取长链停止", account.email)
            account.status = "代理耗尽"
            self.running = False
            self._render_accounts()
            self.save_state()
            return
        create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = link_triples[0] if link_triples else ("", "", "")
        use_payment_proxy_for_register = bool(self.register_with_payment_proxy.get())
        threading.Thread(target=self._refetch_link_worker, args=(account, mode, headless, local_proxy, dynamic_proxies, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, use_payment_proxy_for_register), daemon=True).start()

    def refetch_selected_links_batch(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱，可多选")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        accounts = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self.accounts):
                accounts.append(self.accounts[index])
        if not accounts:
            messagebox.showwarning(APP_TITLE, "未找到有效选中邮箱")
            return
        self.running = True
        self.stop_event.clear()
        self.save_state()
        mode = self.payment_mode.get()
        headless = bool(self.headless.get())
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxies = self._take_dynamic_proxies(len(accounts))
        reuse_proxy = normalize_proxy_url(self.reuse_payment_proxy.get())
        reuse_followup_proxy = normalize_proxy_url(self.reuse_followup_proxy.get())
        reuse_approve_proxy = normalize_proxy_url(self.reuse_approve_proxy.get())
        create_dynamic_proxies = [reuse_proxy] if reuse_proxy else self._read_payment_dynamic_proxies()
        followup_dynamic_proxies = [reuse_followup_proxy] if reuse_followup_proxy else self._read_followup_dynamic_proxies()
        approve_dynamic_proxies = [reuse_approve_proxy] * len(accounts) if reuse_approve_proxy else self._read_approve_dynamic_proxies()
        use_payment_proxy_for_register = bool(self.register_with_payment_proxy.get())
        proxy_assignments = []
        proxy_index = 0
        wanted_link_proxy_pool = bool(create_dynamic_proxies or followup_dynamic_proxies or approve_dynamic_proxies)
        link_triples = self._link_proxy_triples(create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, len(accounts))
        if wanted_link_proxy_pool and not link_triples:
            self.log("支付代理池已耗尽，批量重新获取长链停止")
            for account in accounts:
                account.status = "代理耗尽"
            self.running = False
            self._render_accounts()
            self.save_state()
            return
        for index, account in enumerate(accounts):
            extract_dynamic_proxy = dynamic_proxies[proxy_index] if proxy_index < len(dynamic_proxies) else ""
            if proxy_index < len(dynamic_proxies):
                proxy_index += 1
            create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = link_triples[index] if index < len(link_triples) else ("", "", "")
            register_dynamic_proxy = create_dynamic_proxy if use_payment_proxy_for_register else extract_dynamic_proxy
            proxy_assignments.append((account, register_dynamic_proxy, extract_dynamic_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy))
        threading.Thread(target=self._refetch_links_batch_worker, args=(proxy_assignments, mode, headless, local_proxy, use_payment_proxy_for_register), daemon=True).start()

    def start_authorize_selected(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱，可多选")
            return
        accounts = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self.accounts):
                accounts.append(self.accounts[index])
        if not accounts:
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        self.running = True
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxies = self._read_dynamic_proxies()
        threading.Thread(target=self._authorize_accounts_worker, args=(accounts, local_proxy, dynamic_proxies), daemon=True).start()

    def _authorize_accounts_worker(self, accounts: list[MailAccount], local_proxy: str, dynamic_proxies: list[str]) -> None:
        try:
            for account in accounts:
                if self.stop_event.is_set():
                    self._emit_log("授权任务已手动停止")
                    break
                dynamic_proxy = self._next_dynamic_proxy(dynamic_proxies)
                self._authorize_account_once(account, local_proxy, dynamic_proxy)
        finally:
            self.events.put(("done",))

    def _authorize_account_worker(self, account: MailAccount, local_proxy: str, dynamic_proxy: str) -> None:
        try:
            self._authorize_account_once(account, local_proxy, dynamic_proxy)
        finally:
            self.events.put(("done",))

    def _authorize_account_once(self, account: MailAccount, local_proxy: str, dynamic_proxy: str) -> None:
        log_account = self._account_logger(account)
        try:
            self.events.put(("status", account.email, "授权中"))
            with ProxyChainServer(local_proxy, dynamic_proxy, log_account) as chain:
                proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=dynamic_proxy, chain_url=chain.url)
                log_account(f"授权使用代理: {proxy.label}")
                flow = OpenAIJsonAuthFlow(account, log_account, self._phone_provider, self._request_user_input, chain.url)
                record = flow.run()
            account.openai_rt = str(record.get("refresh_token") or "")
            if not account.openai_rt:
                raise RuntimeError("授权成功但未获取到 refresh_token")
            account.account_type = "plus"
            account.status = "已绑定手机号"
            self.events.put(("account-updated", account.email))
            self.events.put(("status", account.email, account.status))
            log_account("RT 获取成功，已标记为已绑定手机号")
        except Exception as exc:
            log_account(f"授权失败: {exc}")
            self.events.put(("status", account.email, "授权失败"))

    def _request_user_input(self, prompt_type: str, email_addr: str, prompt: str) -> str:
        prompt_id = str(uuid.uuid4())
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self.pending_prompts[prompt_id] = result_queue
        self.events.put(("prompt", prompt_id, prompt_type, email_addr, prompt))
        return str(result_queue.get())

    def _phone_provider(self, action: str, email_addr: str, payload) -> dict | str:
        if action == "next":
            requested_country = str((payload or {}).get("country") or "").upper() if isinstance(payload, dict) else ""
            with self.phone_lock:
                for account in self.accounts:
                    if account.email.lower() != email_addr.lower():
                        continue
                    if account.auth_phone_number and account.auth_phone_sms_url:
                        if requested_country == "US" and not account.auth_phone_number.startswith("+1"):
                            break
                        self._emit_log(f"使用导入授权手机号: {account.auth_phone_number}", email_addr)
                        return {"number": account.auth_phone_number, "sms_url": account.auth_phone_sms_url, "account_bound": True}
                    break
                for phone in self.phones:
                    if requested_country == "US" and not phone.number.startswith("+1"):
                        continue
                    if self._phone_is_frozen(phone):
                        if phone.status != "冻结":
                            phone.status = "冻结"
                            self.events.put(("phones-updated",))
                        continue
                    if phone.status not in {"不可用", "冻结", "使用中"}:
                        phone.status = "使用中"
                        self.events.put(("phones-updated",))
                        self._emit_log(f"使用手机号: {phone.number}", email_addr)
                        for account in self.accounts:
                            if account.email.lower() == email_addr.lower():
                                account.auth_phone_number = phone.number
                                account.auth_phone_sms_url = phone.sms_url
                                self.events.put(("account-updated", email_addr))
                                break
                        return {"number": phone.number, "sms_url": phone.sms_url}
            return {}
        if action == "code":
            return self._wait_for_phone_code(str(payload.get("number") or ""), str(payload.get("sms_url") or ""), timeout=120)
        if action == "bad":
            number = str(payload.get("number") or "")
            error = str(payload.get("error") or "")
            if bool(payload.get("account_bound")):
                self._emit_log(f"导入授权手机号不可用: {number} {error}", email_addr)
                return {}
            with self.phone_lock:
                for phone in self.phones:
                    if phone.number == number:
                        phone.status = "不可用"
                        phone.last_error = error
                        self.events.put(("phones-updated",))
                        break
            return {}
        return {}

    def _wait_for_phone_code(self, number: str, sms_url: str, timeout: int = 180) -> str:
        started = time.time()
        deadline = started + timeout
        last_text = ""
        while time.time() < deadline:
            try:
                request_timeout = max(1, min(20, int(deadline - time.time())))
                response = requests.get(sms_url, timeout=request_timeout)
                text = response.text.strip()
                last_text = text[:300]
                code = self._extract_phone_code(text)
                if code:
                    with self.phone_lock:
                        for phone in self.phones:
                            if phone.number == number:
                                phone.receive_count += 1
                                phone.status = "冻结" if self._phone_is_frozen(phone) else "可用"
                                phone.last_code = code
                                phone.last_error = ""
                                self.events.put(("phones-updated",))
                                break
                    return code
            except Exception as exc:
                last_text = str(exc)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(5, remaining))
        raise RuntimeError(f"等待手机号 {number} 短信验证码超时，最后返回: {last_text}")

    def _extract_phone_code(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or ""))
        patterns = [
            r"OpenAI[^\d]{0,80}(\d{6})",
            r"验证代码[^\d]{0,20}(\d{6})",
            r"验证码[^\d]{0,20}(\d{6})",
            r"\b(\d{6})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.I)
            if match:
                return match.group(1)
        return ""

    def _start_worker(self, accounts: list[MailAccount], collect_session: bool = True) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        self.running = True
        self.stop_event.clear()
        self.save_state()
        mode = self.payment_mode.get()
        headless = bool(self.headless.get())
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        use_payment_proxy_for_register = bool(self.register_with_payment_proxy.get())
        use_proxy_pool = bool(self._read_payment_dynamic_proxies()) if use_payment_proxy_for_register else bool(self._read_dynamic_proxies())
        threading.Thread(
            target=self._run_accounts,
            args=(accounts, mode, headless, local_proxy, use_proxy_pool, use_payment_proxy_for_register, collect_session, self._auth_concurrency()),
            daemon=True,
        ).start()

    def _read_dynamic_proxies(self) -> list[str]:
        lines = [line.strip() for line in self.proxy_text.get("1.0", END).splitlines() if line.strip()]
        return [normalize_proxy_url(line) for line in lines]

    def _read_payment_dynamic_proxies(self) -> list[str]:
        lines = [line.strip() for line in self.payment_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        return [normalize_proxy_url(line) for line in lines]

    def _read_followup_dynamic_proxies(self) -> list[str]:
        lines = [line.strip() for line in self.followup_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        return [normalize_proxy_url(line) for line in lines]

    def _read_approve_dynamic_proxies(self) -> list[str]:
        lines = [line.strip() for line in self.approve_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        return [normalize_proxy_url(line) for line in lines]

    def _link_proxy_pair(self, create_proxy: str = "", followup_proxy: str = "") -> tuple[str, str]:
        create_proxy = normalize_proxy_url(create_proxy)
        followup_proxy = normalize_proxy_url(followup_proxy) or create_proxy
        return create_proxy, followup_proxy

    def _link_proxy_triple(self, create_proxy: str = "", followup_proxy: str = "", approve_proxy: str = "") -> tuple[str, str, str]:
        create_proxy, followup_proxy = self._link_proxy_pair(create_proxy, followup_proxy)
        approve_proxy = normalize_proxy_url(approve_proxy) or followup_proxy
        return create_proxy, followup_proxy, approve_proxy

    def _coerce_link_proxy_pair(self, value) -> tuple[str, str]:
        if isinstance(value, (tuple, list)):
            create_proxy = str(value[0] if len(value) > 0 else "")
            followup_proxy = str(value[1] if len(value) > 1 else "")
            return self._link_proxy_pair(create_proxy, followup_proxy)
        return self._link_proxy_pair(str(value or ""), "")

    def _coerce_link_proxy_triple(self, value) -> tuple[str, str, str]:
        if isinstance(value, (tuple, list)):
            create_proxy = str(value[0] if len(value) > 0 else "")
            followup_proxy = str(value[1] if len(value) > 1 else "")
            approve_proxy = str(value[2] if len(value) > 2 else "")
            return self._link_proxy_triple(create_proxy, followup_proxy, approve_proxy)
        return self._link_proxy_triple(str(value or ""), "", "")

    def _link_proxy_pairs(self, create_proxies: list[str], followup_proxies: list[str], count: int = 0) -> list[tuple[str, str]]:
        create_proxies = [normalize_proxy_url(proxy) for proxy in create_proxies if str(proxy or "").strip()]
        followup_proxies = [normalize_proxy_url(proxy) for proxy in followup_proxies if str(proxy or "").strip()]
        total = max(count, len(create_proxies), len(followup_proxies))
        pairs = []
        for index in range(total):
            create_proxy = create_proxies[index] if index < len(create_proxies) else ""
            followup_proxy = followup_proxies[index] if index < len(followup_proxies) else create_proxy
            pairs.append(self._link_proxy_pair(create_proxy, followup_proxy))
        return pairs

    def _link_proxy_triples(self, create_proxies: list[str], followup_proxies: list[str], approve_proxies: list[str], count: int = 0) -> list[tuple[str, str, str]]:
        create_proxies = [normalize_proxy_url(proxy) for proxy in create_proxies if str(proxy or "").strip()]
        followup_proxies = [normalize_proxy_url(proxy) for proxy in followup_proxies if str(proxy or "").strip()]
        approve_proxies = [normalize_proxy_url(proxy) for proxy in approve_proxies if str(proxy or "").strip()]
        if not create_proxies:
            if followup_proxies or approve_proxies:
                return []
            return [("", "", "") for _ in range(max(0, count))]
        create_capacity = count if count and len(create_proxies) == 1 else len(create_proxies)
        followup_capacity = len(followup_proxies) if followup_proxies else create_capacity
        approve_capacity = len(approve_proxies) if approve_proxies else followup_capacity
        total = min(create_capacity, followup_capacity, approve_capacity)
        if not count:
            total = max(0, total)
        triples = []
        for index in range(total):
            create_proxy = create_proxies[index] if index < len(create_proxies) else create_proxies[0]
            followup_proxy = followup_proxies[index] if index < len(followup_proxies) else create_proxy
            approve_proxy = approve_proxies[index] if index < len(approve_proxies) else followup_proxy
            triples.append(self._link_proxy_triple(create_proxy, followup_proxy, approve_proxy))
        return triples

    def _link_race_concurrency(self) -> int:
        try:
            return min(30, max(1, int(self.link_race_concurrency.get() or 1)))
        except Exception:
            return 1

    def _auth_concurrency(self) -> int:
        try:
            raw = self.auth_concurrency.get()
            value = DEFAULT_AUTH_CONCURRENCY if raw in ("", None) else int(raw)
            return min(MAX_AUTH_CONCURRENCY, max(1, value))
        except Exception:
            return DEFAULT_AUTH_CONCURRENCY

    def _link_proxy_precheck_limit(self) -> int:
        try:
            return max(1, int(self.link_proxy_precheck_limit.get() or DEFAULT_LINK_PROXY_PRECHECK_LIMIT))
        except Exception:
            return DEFAULT_LINK_PROXY_PRECHECK_LIMIT

    def _link_proxy_precheck_concurrency(self) -> int:
        try:
            raw = self.link_proxy_precheck_concurrency.get()
            value = DEFAULT_LINK_PROXY_PRECHECK_CONCURRENCY if raw in ("", None) else int(raw)
            return min(MAX_LINK_PROXY_PRECHECK_CONCURRENCY, max(1, value))
        except Exception:
            return DEFAULT_LINK_PROXY_PRECHECK_CONCURRENCY

    def _link_proxy_cache_key(self, local_proxy: str, dynamic_proxy: str) -> tuple[str, str]:
        return normalize_proxy_url(local_proxy), normalize_proxy_url(dynamic_proxy)

    def _get_link_proxy_cached_exit(self, local_proxy: str, dynamic_proxy: str) -> str:
        dynamic_proxy = normalize_proxy_url(dynamic_proxy)
        if not dynamic_proxy:
            return ""
        with self.link_proxy_exit_cache_lock:
            return self.link_proxy_exit_cache.get(self._link_proxy_cache_key(local_proxy, dynamic_proxy), "")

    def _set_link_proxy_cached_exit(self, local_proxy: str, dynamic_proxy: str, proxy_exit: str) -> None:
        dynamic_proxy = normalize_proxy_url(dynamic_proxy)
        proxy_exit = str(proxy_exit or "").strip()
        if not dynamic_proxy or not proxy_exit or self._proxy_exit_failed(proxy_exit):
            return
        with self.link_proxy_exit_cache_lock:
            self.link_proxy_exit_cache[self._link_proxy_cache_key(local_proxy, dynamic_proxy)] = proxy_exit

    def _cached_link_proxy_exits_for_triple(self, local_proxy: str, create_proxy: str, followup_proxy: str, approve_proxy: str) -> dict[str, str]:
        cached: dict[str, str] = {}
        for key, dynamic_proxy in (
            ("create", create_proxy),
            ("followup", followup_proxy),
            ("approve", approve_proxy),
        ):
            proxy_exit = self._get_link_proxy_cached_exit(local_proxy, dynamic_proxy)
            if proxy_exit:
                cached[key] = proxy_exit
        return cached

    def _precheck_link_proxy_lists(
        self,
        local_proxy: str,
        create_proxies: list[str],
        followup_proxies: list[str],
        approve_proxies: list[str],
        log_func=None,
        remove_payment_pool: bool = True,
        remove_followup_pool: bool = True,
        remove_approve_pool: bool = True,
    ) -> tuple[list[str], list[str], list[str]]:
        log_func = log_func or self._emit_log
        limit = self._link_proxy_precheck_limit()
        configured_concurrency = self._link_proxy_precheck_concurrency()
        role_lists = {
            "create": [normalize_proxy_url(proxy) for proxy in create_proxies if str(proxy or "").strip()],
            "followup": [normalize_proxy_url(proxy) for proxy in followup_proxies if str(proxy or "").strip()],
            "approve": [normalize_proxy_url(proxy) for proxy in approve_proxies if str(proxy or "").strip()],
        }
        candidates: list[tuple[str, str]] = []
        for role, proxies in role_lists.items():
            candidates.extend((role, proxy) for proxy in proxies[:limit])
        if not candidates:
            return role_lists["create"], role_lists["followup"], role_lists["approve"]

        total_text = "，".join(
            f"{label} {min(len(role_lists[key]), limit)}/{len(role_lists[key])}"
            for key, label in (("create", "第一步"), ("followup", "后续"), ("approve", "Approve"))
            if role_lists[key]
        )
        log_func(f"支付代理池出口预检启动: 每池最多 {limit} 个，并发 {configured_concurrency}；{total_text}")

        unique_proxies = []
        seen_proxies: set[str] = set()
        cached_results: dict[str, str] = {}
        for _role, proxy in candidates:
            if proxy in seen_proxies:
                continue
            seen_proxies.add(proxy)
            cached_exit = self._get_link_proxy_cached_exit(local_proxy, proxy)
            if cached_exit:
                cached_results[proxy] = cached_exit
            else:
                unique_proxies.append(proxy)

        def detect_one(dynamic_proxy: str) -> tuple[str, str]:
            try:
                with ProxyChainServer(local_proxy, dynamic_proxy, log_func) as chain:
                    proxy_url = chain.url or normalize_proxy_url(local_proxy) or dynamic_proxy
                    return dynamic_proxy, self._detect_proxy_exit(proxy_url)
            except Exception as exc:
                return dynamic_proxy, f"检测失败: {exc}"

        detected_results = dict(cached_results)
        if unique_proxies:
            max_workers = min(configured_concurrency, len(unique_proxies))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="link-proxy-precheck") as executor:
                futures = [executor.submit(detect_one, proxy) for proxy in unique_proxies]
                for future in futures:
                    proxy, proxy_exit = future.result()
                    detected_results[proxy] = proxy_exit
                    self._set_link_proxy_cached_exit(local_proxy, proxy, proxy_exit)

        failed_by_role: dict[str, set[str]] = {"create": set(), "followup": set(), "approve": set()}
        failed_details: list[str] = []
        passed_by_role: dict[str, int] = {"create": 0, "followup": 0, "approve": 0}
        cached_by_role: dict[str, int] = {"create": 0, "followup": 0, "approve": 0}
        role_labels = {"create": "第一步", "followup": "后续", "approve": "Approve"}
        for role, proxy in candidates:
            proxy_exit = detected_results.get(proxy, "")
            failed = self._proxy_exit_failed(proxy_exit)
            if role == "create" and bool(self.require_japan_extract_proxy.get()) and not self._proxy_exit_is_japan(proxy_exit):
                failed = True
            if failed:
                failed_by_role[role].add(proxy)
                if len(failed_details) < 8:
                    failed_details.append(f"{role_labels[role]} {mask_proxy_url(proxy)} => {opll_short_error(proxy_exit, 140)}")
            else:
                passed_by_role[role] += 1
                if proxy in cached_results:
                    cached_by_role[role] += 1

        for proxy in failed_by_role["create"]:
            if remove_payment_pool:
                self.events.put(("remove-payment-proxy", proxy))
        for proxy in failed_by_role["followup"]:
            if remove_followup_pool:
                self.events.put(("remove-followup-proxy", proxy))
        for proxy in failed_by_role["approve"]:
            if remove_approve_pool:
                self.events.put(("remove-approve-proxy", proxy))

        summaries = []
        for role in ("create", "followup", "approve"):
            checked = sum(1 for candidate_role, _proxy in candidates if candidate_role == role)
            if checked:
                summaries.append(
                    f"{role_labels[role]} 检查 {checked}，通过 {passed_by_role[role]}，失败移除 {len(failed_by_role[role])}，缓存命中 {cached_by_role[role]}"
                )
        log_func("支付代理池出口预检完成: " + "；".join(summaries))
        if failed_details:
            remaining = sum(len(items) for items in failed_by_role.values()) - len(failed_details)
            suffix = f"；另有 {remaining} 个失败已省略" if remaining > 0 else ""
            log_func("支付代理池失败明细: " + "；".join(failed_details) + suffix)

        def filter_failed(role: str) -> list[str]:
            failed = failed_by_role[role]
            return [proxy for proxy in role_lists[role][:limit] if proxy not in failed]

        return filter_failed("create"), filter_failed("followup"), filter_failed("approve")

    def precheck_link_proxy_pools(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        self.running = True
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        create_dynamic_proxies = self._read_payment_dynamic_proxies()
        followup_dynamic_proxies = self._read_followup_dynamic_proxies()
        approve_dynamic_proxies = self._read_approve_dynamic_proxies()
        threading.Thread(
            target=self._precheck_link_proxy_pools_worker,
            args=(local_proxy, create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies),
            daemon=True,
        ).start()

    def _precheck_link_proxy_pools_worker(self, local_proxy: str, create_dynamic_proxies: list[str], followup_dynamic_proxies: list[str], approve_dynamic_proxies: list[str]) -> None:
        try:
            self._precheck_link_proxy_lists(local_proxy, create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, self._emit_log)
        finally:
            self.events.put(("done",))

    def _take_dynamic_proxies(self, count: int, log_take: bool = True) -> list[str]:
        lines = [line.strip() for line in self.proxy_text.get("1.0", END).splitlines() if line.strip()]
        if count <= 0 or not lines:
            return []
        taken = lines[:count]
        rest = "\n".join(lines[count:])
        self.proxy_text.delete("1.0", END)
        if rest:
            self.proxy_text.insert(END, rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        proxies = [normalize_proxy_url(line) for line in taken]
        if log_take:
            self.log(f"注册/获取 Session 动态代理已取用并移除 {len(proxies)} 个")
        return proxies

    def _remove_register_dynamic_proxy_value(self, proxy_url: str) -> bool:
        target = normalize_proxy_url(proxy_url)
        if not target:
            return False
        lines = [line.strip() for line in self.proxy_text.get("1.0", END).splitlines() if line.strip()]
        kept = []
        removed = False
        for line in lines:
            if not removed and normalize_proxy_url(line) == target:
                removed = True
                continue
            kept.append(line)
        if not removed:
            return False
        rest = "\n".join(kept)
        self.proxy_text.delete("1.0", END)
        if rest:
            self.proxy_text.insert(END, rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"失败注册代理已移除: {target}")
        return True

    def _remove_failed_auth_proxy(self, dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        dynamic_proxy = normalize_proxy_url(dynamic_proxy)
        if not dynamic_proxy:
            return
        if use_payment_proxy_for_register:
            self.events.put(("remove-payment-proxy", dynamic_proxy))
        else:
            self.events.put(("remove-register-proxy", dynamic_proxy))

    def _take_auth_dynamic_proxy_for_worker(self, use_payment_proxy_for_register: bool) -> str:
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self.events.put(("take-auth-proxy", bool(use_payment_proxy_for_register), result_queue))
        while not self.stop_event.is_set():
            try:
                return normalize_proxy_url(result_queue.get(timeout=0.2))
            except queue.Empty:
                continue
        return ""

    def _handle_take_auth_proxy_event(self, use_payment_proxy_for_register: bool, result_queue: queue.Queue) -> None:
        try:
            if use_payment_proxy_for_register:
                proxies = self._take_payment_dynamic_proxies(1, log_take=False)
            else:
                proxies = self._take_dynamic_proxies(1, log_take=False)
            result_queue.put(proxies[0] if proxies else "")
        except Exception as exc:
            self.log(f"认证代理取用失败: {exc}")
            result_queue.put("")

    def _peek_payment_dynamic_proxy(self) -> str:
        proxies = self._read_payment_dynamic_proxies()
        return proxies[0] if proxies else ""

    def _peek_followup_dynamic_proxy(self) -> str:
        proxies = self._read_followup_dynamic_proxies()
        return proxies[0] if proxies else ""

    def _peek_approve_dynamic_proxy(self) -> str:
        proxies = self._read_approve_dynamic_proxies()
        return proxies[0] if proxies else ""

    def _take_payment_dynamic_proxy(self) -> str:
        lines = [line.strip() for line in self.payment_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            return ""
        value = normalize_proxy_url(lines[0])
        rest = "\n".join(lines[1:])
        self.payment_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.payment_dynamic_proxy_text.insert(END, rest)
        self.payment_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"支付链接动态代理已取用并移除: {value}")
        return value

    def _take_payment_dynamic_proxies(self, count: int, log_take: bool = True) -> list[str]:
        lines = [line.strip() for line in self.payment_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        if count <= 0 or not lines:
            return []
        taken = lines[:count]
        rest = "\n".join(lines[count:])
        self.payment_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.payment_dynamic_proxy_text.insert(END, rest)
        self.payment_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        proxies = [normalize_proxy_url(line) for line in taken]
        if log_take:
            self.log(f"支付链接动态代理已取用并移除 {len(proxies)} 个用于注册/获取 Session")
        return proxies

    def _take_followup_dynamic_proxy(self) -> str:
        lines = [line.strip() for line in self.followup_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            return ""
        value = normalize_proxy_url(lines[0])
        rest = "\n".join(lines[1:])
        self.followup_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.followup_dynamic_proxy_text.insert(END, rest)
        self.followup_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"后续动态代理已取用并移除: {value}")
        return value

    def _take_followup_or_payment_dynamic_proxy(self) -> str:
        followup_proxy = self._take_followup_dynamic_proxy()
        if followup_proxy:
            return followup_proxy
        return self._take_payment_dynamic_proxy()

    def _remove_payment_dynamic_proxy_value(self, proxy_url: str) -> bool:
        target = normalize_proxy_url(proxy_url)
        if not target:
            return False
        lines = [line.strip() for line in self.payment_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        kept = []
        removed = False
        for line in lines:
            if not removed and normalize_proxy_url(line) == target:
                removed = True
                continue
            kept.append(line)
        if not removed:
            return False
        rest = "\n".join(kept)
        self.payment_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.payment_dynamic_proxy_text.insert(END, rest)
        self.payment_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"失败支付代理已移除: {mask_proxy_url(target)}")
        return True

    def _remove_followup_dynamic_proxy_value(self, proxy_url: str) -> bool:
        target = normalize_proxy_url(proxy_url)
        if not target:
            return False
        lines = [line.strip() for line in self.followup_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        kept = []
        removed = False
        for line in lines:
            if not removed and normalize_proxy_url(line) == target:
                removed = True
                continue
            kept.append(line)
        if not removed:
            return False
        rest = "\n".join(kept)
        self.followup_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.followup_dynamic_proxy_text.insert(END, rest)
        self.followup_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"失败后续代理已移除: {mask_proxy_url(target)}")
        return True

    def _remove_approve_dynamic_proxy_value(self, proxy_url: str) -> bool:
        target = normalize_proxy_url(proxy_url)
        if not target:
            return False
        lines = [line.strip() for line in self.approve_dynamic_proxy_text.get("1.0", END).splitlines() if line.strip()]
        kept = []
        removed = False
        for line in lines:
            if not removed and normalize_proxy_url(line) == target:
                removed = True
                continue
            kept.append(line)
        if not removed:
            return False
        rest = "\n".join(kept)
        self.approve_dynamic_proxy_text.delete("1.0", END)
        if rest:
            self.approve_dynamic_proxy_text.insert(END, rest)
        self.approve_dynamic_proxy.set(rest)
        self.save_state()
        self._refresh_proxy_pool_counts()
        self.log(f"失败 Approve 代理已移除: {mask_proxy_url(target)}")
        return True

    def _remove_failed_link_proxy_pair(self, create_proxy: str, followup_proxy: str) -> None:
        create_proxy = normalize_proxy_url(create_proxy)
        followup_proxy = normalize_proxy_url(followup_proxy)
        if create_proxy:
            self.events.put(("remove-payment-proxy", create_proxy))
        if followup_proxy and followup_proxy != create_proxy:
            self.events.put(("remove-followup-proxy", followup_proxy))

    def _remove_failed_link_proxy_triple(
        self,
        create_proxy: str,
        followup_proxy: str,
        approve_proxy: str,
        reuse_create_proxy_enabled: bool = False,
        reuse_followup_proxy_enabled: bool = False,
        reuse_approve_proxy_enabled: bool = False,
    ) -> None:
        create_proxy, followup_proxy, approve_proxy = self._link_proxy_triple(create_proxy, followup_proxy, approve_proxy)
        if create_proxy and not reuse_create_proxy_enabled:
            self.events.put(("remove-payment-proxy", create_proxy))
        if followup_proxy and followup_proxy != create_proxy and not reuse_followup_proxy_enabled:
            self.events.put(("remove-followup-proxy", followup_proxy))
        if approve_proxy and not reuse_approve_proxy_enabled and approve_proxy not in {create_proxy, followup_proxy}:
            self.events.put(("remove-approve-proxy", approve_proxy))

    def _take_paypal_phone_config(self) -> tuple[str, str] | None:
        lines = [line.strip() for line in self.paypal_phone_pool_text.get("1.0", END).splitlines() if line.strip()]
        if lines:
            line = lines[self.paypal_phone_pool_index % len(lines)]
            try:
                phone = parse_paypal_phone_line(line)
            except Exception as exc:
                messagebox.showwarning(APP_TITLE, f"PP手机号+接码池第 {self.paypal_phone_pool_index % len(lines) + 1} 行格式错误: {exc}")
                return None
            self.paypal_phone_pool_index += 1
            self.paypal_phone_pool.set("\n".join(lines))
            self.save_state()
            self.log(f"PP手机号+接码已轮询取用: {phone.number}")
            return phone.number, phone.sms_url
        return self.paypal_phone.get().strip(), self.paypal_sms_url.get().strip()

    def _next_dynamic_proxy(self, dynamic_proxies: list[str]) -> str:
        if not dynamic_proxies:
            return ""
        value = dynamic_proxies[self.dynamic_proxy_index % len(dynamic_proxies)]
        self.dynamic_proxy_index += 1
        return value

    def _run_accounts(self, accounts: list[MailAccount], mode: str, headless: bool, local_proxy: str, use_proxy_pool: bool, use_payment_proxy_for_register: bool, collect_session: bool = True, auth_concurrency: int = DEFAULT_AUTH_CONCURRENCY) -> None:
        try:
            concurrency_value = DEFAULT_AUTH_CONCURRENCY if auth_concurrency in ("", None) else int(auth_concurrency)
            concurrency = min(max(1, concurrency_value), MAX_AUTH_CONCURRENCY, max(1, len(accounts)))
            if concurrency > 1:
                self.events.put(("log", f"注册/登录认证并发窗口数: {concurrency}"))
            if use_proxy_pool:
                source = "支付链接第一步代理池" if use_payment_proxy_for_register else "注册动态代理池"
                self.events.put(("log", f"认证代理池已启用（来源: {source}；按账号尝试时取用，预检失败会自动换下一个）"))
            else:
                self.events.put(("log", "认证动态代理为空，使用当前本地代理/直连尝试"))

            account_queue: queue.Queue = queue.Queue()
            for account in accounts:
                account_queue.put(account)

            def worker_loop() -> None:
                if self.stop_event.is_set():
                    return
                while not self.stop_event.is_set():
                    try:
                        account = account_queue.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        self._run_account_thread(account, mode, headless, local_proxy, use_proxy_pool, use_payment_proxy_for_register, collect_session)
                    finally:
                        account_queue.task_done()

            threads = []
            for _index in range(concurrency):
                thread = threading.Thread(target=worker_loop, daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
            if self.stop_event.is_set():
                self.events.put(("log", "任务已手动停止"))
        finally:
            self.events.put(("done",))

    def _run_account_thread(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, use_proxy_pool: bool, use_payment_proxy_for_register: bool, collect_session: bool = True) -> None:
        log_account = self._account_logger(account)
        if self.stop_event.is_set():
            return
        attempts = 0
        while not self.stop_event.is_set():
            if use_proxy_pool:
                register_dynamic_proxy = self._take_auth_dynamic_proxy_for_worker(use_payment_proxy_for_register)
                if not register_dynamic_proxy:
                    if self.stop_event.is_set():
                        return
                    log_account("认证代理池已耗尽，停止该账号")
                    self.events.put(("status", account.email, "代理耗尽"))
                    return
            else:
                if attempts > 0:
                    return
                register_dynamic_proxy = ""
            attempts += 1
            try:
                self._run_account_once(account, mode, headless, local_proxy, register_dynamic_proxy, use_payment_proxy_for_register, collect_session)
                return
            except Exception as exc:
                if isinstance(exc, ProxyExitCheckError) and exc.status == "代理检测失败" and use_proxy_pool:
                    self._remove_failed_auth_proxy(register_dynamic_proxy, use_payment_proxy_for_register)
                    log_account(f"认证代理预检失败，自动换下一个代理重试: {exc}")
                    continue
                if isinstance(exc, ProxyExitCheckError):
                    self._remove_failed_auth_proxy(register_dynamic_proxy, use_payment_proxy_for_register)
                self.events.put(("log", f"[{account.email}] 失败: {exc}"))
                status = exc.status if isinstance(exc, ProxyExitCheckError) else "失败"
                self.events.put(("status", account.email, status))
                return

    def _run_account_once(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, register_dynamic_proxy: str, use_payment_proxy_for_register: bool, collect_session: bool = True) -> None:
        log_account = self._account_logger(account)
        if account.account_type == "team" and collect_session:
            self._run_team_account_once(account, mode, headless, local_proxy, register_dynamic_proxy, use_payment_proxy_for_register)
            return
        self.events.put(("status", account.email, "处理中"))
        with ProxyChainServer(local_proxy, register_dynamic_proxy, log_account) as register_chain:
            register_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=register_dynamic_proxy, chain_url=register_chain.url)
            extract_proxy = register_proxy
            register_source = "支付链接动态代理" if use_payment_proxy_for_register else "注册动态代理池"
            self.events.put(("log", f"[{account.email}] {format_named_proxy_log('注册使用代理', register_proxy, register_source)}"))
            self.events.put(("log", f"[{account.email}] {format_named_proxy_log('获取 Session 复用注册代理', extract_proxy)}"))
            worker = OpenAIRegisterPayLinkWorker(account, mode, headless, register_proxy, extract_proxy, log_account, self._phone_provider)
            if collect_session:
                result = worker.run()
            else:
                worker.run_auth_only()
                result = None
        self.events.put(("account-updated", account.email))
        if collect_session:
            self.events.put(("result", account.email, result))
            self.events.put(("status", account.email, "Session已获取"))
        else:
            self.events.put(("status", account.email, "已登录"))

    def _run_team_account_worker(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        try:
            self._run_team_account_once(account, mode, headless, local_proxy, dynamic_proxy, use_payment_proxy_for_register)
        finally:
            self.events.put(("done",))

    def _run_team_account_once(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, register_dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        log_account = self._account_logger(account)
        self.events.put(("status", account.email, "Team注册中"))
        try:
            with ProxyChainServer(local_proxy, register_dynamic_proxy, log_account) as register_chain:
                register_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=register_dynamic_proxy, chain_url=register_chain.url)
                source = "支付链接动态代理" if use_payment_proxy_for_register else "注册动态代理池"
                log_account(format_named_proxy_log("Team 注册使用代理", register_proxy, source))
                worker = OpenAIRegisterPayLinkWorker(account, mode, headless, register_proxy, register_proxy, log_account, require_japan_extract_proxy=bool(self.require_japan_extract_proxy.get()))
                result = worker.run_team()
            account.openai_rt = str(result.get("openai_rt") or "")
            if not account.openai_rt:
                raise RuntimeError("Team 注册成功但未获取到 refresh_token")
            account.account_type = "team"
            account.status = "Team RT已获取"
            self.events.put(("account-updated", account.email))
            self.events.put(("result", account.email, result))
            self.events.put(("status", account.email, account.status))
            log_account("Team RT 获取成功")
        except Exception as exc:
            if isinstance(exc, ProxyExitCheckError):
                self._remove_failed_auth_proxy(register_dynamic_proxy, use_payment_proxy_for_register)
            log_account(f"Team 注册失败: {exc}")
            status = exc.status if isinstance(exc, ProxyExitCheckError) else "Team失败"
            self.events.put(("status", account.email, status))

    def _refetch_account_once(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, register_dynamic_proxy: str, extract_dynamic_proxy: str, create_dynamic_proxy: str, followup_dynamic_proxy: str, approve_dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        log_account = self._account_logger(account)
        self.events.put(("status", account.email, "重新获取中"))
        create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = self._link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
        with ExitStack() as stack:
            register_chain = stack.enter_context(ProxyChainServer(local_proxy, register_dynamic_proxy, log_account))
            extract_chain = stack.enter_context(ProxyChainServer(local_proxy, extract_dynamic_proxy, log_account))
            link_chains: dict[str, ProxyChainServer] = {}

            def link_chain_for(dynamic_proxy: str) -> ProxyChainServer:
                key = normalize_proxy_url(dynamic_proxy)
                if key not in link_chains:
                    link_chains[key] = stack.enter_context(ProxyChainServer(local_proxy, key, log_account))
                return link_chains[key]

            create_chain = link_chain_for(create_dynamic_proxy)
            followup_chain = link_chain_for(followup_dynamic_proxy)
            approve_chain = link_chain_for(approve_dynamic_proxy)
            register_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=register_dynamic_proxy, chain_url=register_chain.url)
            extract_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=extract_dynamic_proxy, chain_url=extract_chain.url)
            create_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=create_dynamic_proxy, chain_url=create_chain.url)
            followup_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=followup_dynamic_proxy, chain_url=followup_chain.url)
            approve_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=approve_dynamic_proxy, chain_url=approve_chain.url)
            register_source = "支付链接动态代理" if use_payment_proxy_for_register else "注册动态代理池"
            log_account(format_named_proxy_log("重新获取长链接登录使用代理", register_proxy, register_source))
            log_account(format_named_proxy_log("重新获取长链接浏览器提取使用代理", extract_proxy))
            _log_link_proxy_group(log_account, create_proxy, followup_proxy, approve_proxy, "重新获取长链接")
            worker = OpenAIRegisterPayLinkWorker(account, mode, headless, register_proxy, extract_proxy, log_account, link_create_proxy=create_proxy, link_followup_proxy=followup_proxy, link_approve_proxy=approve_proxy, require_japan_extract_proxy=bool(self.require_japan_extract_proxy.get()))
            link = worker.relink()
        self.events.put(("result", account.email, link))
        self.events.put(("status", account.email, "成功"))

    def _refetch_link_worker(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, dynamic_proxies: list[str], create_dynamic_proxy: str, followup_dynamic_proxy: str, approve_dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        try:
            extract_dynamic_proxy = self._next_dynamic_proxy(dynamic_proxies)
            register_dynamic_proxy = create_dynamic_proxy if use_payment_proxy_for_register else extract_dynamic_proxy
            self._refetch_account_once(account, mode, headless, local_proxy, register_dynamic_proxy, extract_dynamic_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, use_payment_proxy_for_register)
        except Exception as exc:
            if isinstance(exc, ProxyExitCheckError):
                register_dynamic_proxy = create_dynamic_proxy if use_payment_proxy_for_register else extract_dynamic_proxy
                self._remove_failed_auth_proxy(register_dynamic_proxy, use_payment_proxy_for_register)
            self._emit_log(f"重新获取长链接失败: {exc}", account.email)
            status = exc.status if isinstance(exc, ProxyExitCheckError) else "失败"
            self.events.put(("status", account.email, status))
        finally:
            self.events.put(("done",))

    def _refetch_links_batch_worker(self, proxy_assignments: list[tuple[MailAccount, str, str, str, str, str]], mode: str, headless: bool, local_proxy: str, use_payment_proxy_for_register: bool) -> None:
        threads = []
        try:
            for account, register_dynamic_proxy, extract_dynamic_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy in proxy_assignments:
                if self.stop_event.is_set():
                    self._emit_log("任务已手动停止")
                    break
                thread = threading.Thread(
                    target=self._refetch_account_thread,
                    args=(account, mode, headless, local_proxy, register_dynamic_proxy, extract_dynamic_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, use_payment_proxy_for_register),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
        finally:
            self.events.put(("done",))

    def _refetch_account_thread(self, account: MailAccount, mode: str, headless: bool, local_proxy: str, register_dynamic_proxy: str, extract_dynamic_proxy: str, create_dynamic_proxy: str, followup_dynamic_proxy: str, approve_dynamic_proxy: str, use_payment_proxy_for_register: bool) -> None:
        try:
            self._refetch_account_once(account, mode, headless, local_proxy, register_dynamic_proxy, extract_dynamic_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, use_payment_proxy_for_register)
        except Exception as exc:
            if isinstance(exc, ProxyExitCheckError):
                self._remove_failed_auth_proxy(register_dynamic_proxy, use_payment_proxy_for_register)
            self._emit_log(f"重新获取长链接失败: {exc}", account.email)
            status = exc.status if isinstance(exc, ProxyExitCheckError) else "失败"
            self.events.put(("status", account.email, status))

    def _open_payment_link_worker(self, link: str, local_proxy: str, dynamic_proxy: str, extension_dir: str, paypal_phone: str, paypal_card: str, paypal_sms_url: str, email_addr: str = "") -> None:
        profile_dir = ""
        context = None
        log_payment = lambda msg: self._emit_log(str(msg), email_addr)
        try:
            extension_path = Path(extension_dir).resolve() if extension_dir else None
            if extension_path and not extension_path.is_dir():
                raise RuntimeError(f"扩展目录不存在: {extension_path}")
            if extension_path and not (extension_path / "manifest.json").exists():
                raise RuntimeError(f"扩展目录缺少 manifest.json: {extension_path}")

            with ProxyChainServer(local_proxy, dynamic_proxy, log_payment) as chain:
                proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=dynamic_proxy, chain_url=chain.url)
                log_payment(f"[支付窗口] 使用代理: {mask_proxy_url(proxy.dynamic_proxy or proxy.local_proxy)}")
                health = detect_proxy_health(chain.url or local_proxy or dynamic_proxy)
                if not health.success:
                    raise ProxyExitCheckError(f"支付窗口代理健康检查失败: {health.summary}", "代理检测失败")
                fingerprint = generate_fingerprint_for_exit(health)
                log_payment(
                    f"[代理] 支付窗口出口检查通过: {health.ip} {health.location or health.country} "
                    f"{health.timezone or 'UTC'} ChatGPT={health.chatgpt_status} Stripe={health.stripe_status}"
                )
                profile_dir = tempfile.mkdtemp(prefix="paylink-profile-")
                self._seed_payment_browser_preferences(profile_dir)
                log_payment(f"支付窗口全新隔离浏览器环境: {profile_dir}")
                args = [
                    "--disable-blink-features=AutomationControlled",
                    f"--lang={fingerprint.locale}",
                    f"--window-size={fingerprint.outer_width},{fingerprint.outer_height}",
                    "--disable-features=IsolateOrigins,site-per-process,AutofillServerCommunication,AutofillEnableAccountWalletStorage,AutofillCreditCardUpload,AutofillEnablePaymentsMandatoryReauth",
                    "--disable-save-password-bubble",
                ]
                if extension_path:
                    ext = str(extension_path)
                    args.extend([
                        f"--disable-extensions-except={ext}",
                        f"--load-extension={ext}",
                    ])
                    log_payment(f"已加载支付链接扩展目录: {ext}")
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        headless=False,
                        args=args,
                        proxy={"server": chain.url} if chain.url else None,
                        user_agent=fingerprint.user_agent,
                        locale=fingerprint.locale,
                        timezone_id=fingerprint.timezone,
                        viewport={"width": fingerprint.viewport_width, "height": fingerprint.viewport_height},
                        screen={"width": fingerprint.screen_width, "height": fingerprint.screen_height},
                        device_scale_factor=fingerprint.device_scale_factor,
                        is_mobile=False,
                        has_touch=False,
                    )
                    self.payment_context = context
                    self.payment_contexts.add(context)
                    context.clear_cookies()
                    self._install_payment_fingerprint(context, fingerprint)
                    if paypal_card or paypal_sms_url:
                        paypal_payload = json.dumps({"phone": paypal_phone, "card": paypal_card, "smsUrl": paypal_sms_url}, ensure_ascii=False)
                        context.add_init_script(
                            """(() => {
                                const data = __PAYPAL_PAYLOAD__;
                                const phone = data.phone || '';
                                const card = data.card || '';
                                const smsUrl = data.smsUrl || '';
                                try {
                                    localStorage.setItem('opencode_paypal_phone', phone);
                                    localStorage.setItem('opencode_paypal_card', card);
                                    localStorage.setItem('ppaf_phone', phone);
                                    localStorage.setItem('ppaf_card', card);
                                    localStorage.setItem('opencode_paypal_sms_url', smsUrl);
                                    localStorage.setItem('ppaf_sms_url', smsUrl);
                                } catch (_) {}
                            })();""".replace("__PAYPAL_PAYLOAD__", paypal_payload)
                        )
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(link, wait_until="domcontentloaded", timeout=90000)
                    paypal_signup_logged: set[str] = set()
                    success_ready_at: dict[int, float] = {}
                    cpay_click_ready_at: dict[int, float] = {}
                    cpay_clicked: set[str] = set()
                    cpay_clicked_url: dict[int, str] = {}
                    log_payment("支付链接已在支持扩展的全新 Chromium 窗口打开；关闭窗口后任务结束")
                    while not self.stop_event.is_set() and context.pages:
                        for current_page in list(context.pages):
                            if current_page.is_closed():
                                continue
                            if "pay.openai.com/c/pay/" in current_page.url:
                                page_id = id(current_page)
                                url_key = f"{page_id}:{current_page.url}"
                                if url_key not in cpay_clicked:
                                    if page_id not in cpay_click_ready_at or cpay_clicked_url.get(page_id, "") != current_page.url:
                                        cpay_click_ready_at[page_id] = time.time() + 5
                                        cpay_clicked_url[page_id] = current_page.url
                                        log_payment(f"检测到 OpenAI 支付确认页，等待 5 秒后点击确认按钮: {current_page.url[:80]}")
                                    if time.time() >= cpay_click_ready_at[page_id]:
                                        if self._click_openai_pay_confirm(current_page):
                                            cpay_clicked.add(url_key)
                                            success_ready_at.pop(page_id, None)
                                            log_payment(f"已点击 OpenAI 支付确认按钮，等待后续跳转: {current_page.url[:80]}")
                                        else:
                                            cpay_click_ready_at[page_id] = time.time() + 1
                                    continue
                                if current_page.url == cpay_clicked_url.get(page_id, ""):
                                    continue
                                if page_id not in success_ready_at:
                                    success_ready_at[page_id] = time.time() + 5
                                    log_payment(f"检测到支付确认后跳转页，等待 5 秒后关闭并标记 Plus: {current_page.url[:80]}")
                                if time.time() >= success_ready_at[page_id]:
                                    if self._click_openai_pay_confirm(current_page):
                                        cpay_clicked.add(url_key)
                                        cpay_clicked_url[page_id] = current_page.url
                                        success_ready_at.pop(page_id, None)
                                        log_payment(f"已点击返回后的 OpenAI 支付确认按钮: {current_page.url[:80]}")
                                        continue
                                    if email_addr:
                                        self.events.put(("mark-plus", email_addr))
                                    try:
                                        current_page.close()
                                    except Exception:
                                        pass
                                    try:
                                        context.close()
                                    except Exception:
                                            pass
                                    return
                            current_url = current_page.url
                            current_parts = urlsplit(current_url)
                            is_paypal_agreements_page = current_parts.netloc.lower().endswith("paypal.com") and current_parts.path.startswith("/agreements/approve")
                            if is_paypal_agreements_page:
                                paypal_action = self._handle_paypal_agreements_page(current_page)
                                if paypal_action == "clicked_create_account":
                                    log_payment(f"已点击 PayPal 创建账户按钮: {current_page.url[:80]}")
                                    continue
                                if paypal_action == "submitted_signup_email":
                                    log_payment(f"已填写 PayPal 随机邮箱并点击继续支付: {current_page.url[:80]}")
                                    continue
                            is_paypal_signup_page = current_parts.netloc.lower() == "www.paypal.com" and current_parts.path.startswith("/checkoutweb/signup")
                            if is_paypal_signup_page:
                                key = f"{id(current_page)}:{current_page.url.split('?')[0]}"
                                if key not in paypal_signup_logged:
                                    paypal_signup_logged.add(key)
                                    log_payment(f"检测到 PayPal 创建账户页，扩展将自动填写一次: {current_page.url[:80]}")
                        time.sleep(1)
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as exc:
            err = str(exc)
            if "Target page" in err and "closed" in err.lower():
                log_payment("支付窗口已关闭或任务已停止，已取消当前打开流程")
            else:
                log_payment(f"打开支付链接失败: {exc}")
        finally:
            if profile_dir:
                self._cleanup_profile_dir(profile_dir)
            if context in self.payment_contexts:
                self.payment_contexts.discard(context)
            self.payment_context = None
            self.events.put(("open-link-done",))

    def _cleanup_profile_dir(self, profile_dir: str) -> None:
        for attempt in range(8):
            try:
                shutil.rmtree(profile_dir, ignore_errors=False)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                time.sleep(0.5 + attempt * 0.25)
            except OSError:
                time.sleep(0.5 + attempt * 0.25)
        self._emit_log(f"临时支付浏览器目录清理失败，已忽略: {profile_dir}")

    def _seed_payment_browser_preferences(self, profile_dir: str) -> None:
        default_dir = Path(profile_dir) / "Default"
        try:
            default_dir.mkdir(parents=True, exist_ok=True)
            preferences_path = default_dir / "Preferences"
            if preferences_path.exists():
                return
            preferences = {
                "autofill": {
                    "credit_card_enabled": False,
                    "profile_enabled": False,
                },
                "credentials_enable_service": False,
                "profile": {
                    "password_manager_enabled": False,
                },
                "payments": {
                    "can_make_payment_enabled": False,
                },
            }
            preferences_path.write_text(json.dumps(preferences), encoding="utf-8")
        except Exception as exc:
            self._emit_log(f"支付浏览器偏好写入失败，已忽略: {exc}")

    def _install_payment_fingerprint(self, context, fp: DeviceFingerprint) -> None:
        fp_payload = json.dumps({
            "platform": fp.platform,
            "vendor": fp.vendor,
            "languages": fp.languages,
            "hardwareConcurrency": fp.hardware_concurrency,
            "deviceMemory": fp.device_memory,
            "maxTouchPoints": fp.max_touch_points,
            "screenWidth": fp.screen_width,
            "screenHeight": fp.screen_height,
            "outerWidth": fp.outer_width,
            "outerHeight": fp.outer_height,
            "deviceScaleFactor": fp.device_scale_factor,
            "chromeMajor": fp.chrome_major,
            "chromeFull": fp.chrome_full,
        }, ensure_ascii=False)
        context.set_extra_http_headers({
            "Accept-Language": fp.accept_language,
            "sec-ch-ua": f'"Google Chrome";v="{fp.chrome_major}", "Chromium";v="{fp.chrome_major}", "Not.A/Brand";v="24"',
            "sec-ch-ua-full-version-list": f'"Google Chrome";v="{fp.chrome_full}", "Chromium";v="{fp.chrome_full}", "Not.A/Brand";v="24.0.0.0"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": '"15.0.0"',
        })
        context.add_init_script(
            """(() => {
                const fp = __FP_PAYLOAD__;
                const defineGetter = (obj, prop, value) => {
                    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (_) {}
                };
                defineGetter(Navigator.prototype, 'webdriver', undefined);
                defineGetter(Navigator.prototype, 'platform', fp.platform);
                defineGetter(Navigator.prototype, 'vendor', fp.vendor);
                defineGetter(Navigator.prototype, 'language', fp.languages[0]);
                defineGetter(Navigator.prototype, 'languages', fp.languages);
                defineGetter(Navigator.prototype, 'hardwareConcurrency', fp.hardwareConcurrency);
                defineGetter(Navigator.prototype, 'deviceMemory', fp.deviceMemory);
                defineGetter(Navigator.prototype, 'maxTouchPoints', fp.maxTouchPoints);
                defineGetter(Screen.prototype, 'width', fp.screenWidth);
                defineGetter(Screen.prototype, 'height', fp.screenHeight);
                defineGetter(Screen.prototype, 'availWidth', fp.screenWidth);
                defineGetter(Screen.prototype, 'availHeight', fp.screenHeight - 40);
                defineGetter(window, 'outerWidth', fp.outerWidth);
                defineGetter(window, 'outerHeight', fp.outerHeight);
                defineGetter(window, 'devicePixelRatio', fp.deviceScaleFactor);
                if (!navigator.userAgentData) {
                    defineGetter(Navigator.prototype, 'userAgentData', {
                        mobile: false,
                        platform: 'Windows',
                        brands: [
                            { brand: 'Google Chrome', version: fp.chromeMajor },
                            { brand: 'Chromium', version: fp.chromeMajor },
                            { brand: 'Not.A/Brand', version: '24' },
                        ],
                        getHighEntropyValues: async hints => {
                            const values = {
                                architecture: 'x86', bitness: '64', mobile: false, model: '',
                                platform: 'Windows', platformVersion: '15.0.0', uaFullVersion: fp.chromeFull,
                                fullVersionList: [
                                    { brand: 'Google Chrome', version: fp.chromeFull },
                                    { brand: 'Chromium', version: fp.chromeFull },
                                    { brand: 'Not.A/Brand', version: '24.0.0.0' },
                                ],
                                wow64: false,
                            };
                            return Object.fromEntries(hints.filter(h => h in values).map(h => [h, values[h]]));
                        },
                    });
                }
                try {
                    const originalQuery = navigator.permissions && navigator.permissions.query;
                    if (originalQuery) {
                        navigator.permissions.query = params => params && params.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : originalQuery.call(navigator.permissions, params);
                    }
                } catch (_) {}
                try {
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) return 'Intel Inc.';
                        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                        return getParameter.call(this, parameter);
                    };
                } catch (_) {}
            }})();""".replace("__FP_PAYLOAD__", fp_payload)
        )

    def _click_openai_pay_confirm(self, page) -> bool:
        try:
            return bool(page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'));
                    const target = buttons.find(el => {
                        if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                        const text = `${el.textContent || ''} ${el.getAttribute('value') || ''} ${el.getAttribute('aria-label') || ''}`.trim().toLowerCase();
                        if (/cancel|back|return|キャンセル|戻る/.test(text)) return false;
                        return /subscribe|confirm|continue|pay|complete|同意|続行|確認|支払|購入|登録/.test(text);
                    });
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center' });
                    target.click();
                    return true;
                }"""
            ))
        except Exception:
            return False

    def _handle_paypal_agreements_page(self, page) -> str:
        try:
            return str(page.evaluate(
                """() => {
                    const visible = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const setValue = (el, value) => {
                        if (!el) return false;
                        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (desc && desc.set) desc.set.call(el, value); else el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    };
                    const randomEmail = () => `pp${Date.now()}${Math.floor(Math.random() * 10000)}@gmail.com`;
                    const candidates = Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'));
                    const createBtn = candidates.find(el => {
                        if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                        const text = `${el.textContent || ''} ${el.getAttribute('value') || ''} ${el.getAttribute('aria-label') || ''}`.trim().toLowerCase();
                        return text.includes('アカウントを開設') || text.includes('アカウントを作成') || text.includes('create account') || text.includes('sign up');
                    });
                    if (createBtn) {
                        createBtn.scrollIntoView({ block: 'center' });
                        createBtn.click();
                        return 'clicked_create_account';
                    }
                    const emailInput = Array.from(document.querySelectorAll('input')).find(input => {
                        if (!visible(input) || input.disabled) return false;
                        const meta = `${input.type || ''} ${input.name || ''} ${input.id || ''} ${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}`.toLowerCase();
                        return meta.includes('email') || meta.includes('login_email') || meta.includes('メール');
                    });
                    if (!emailInput || String(emailInput.value || '').trim()) return '';
                    setValue(emailInput, randomEmail());
                    const continueBtn = candidates.find(el => {
                        if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                        const text = `${el.textContent || ''} ${el.getAttribute('value') || ''} ${el.getAttribute('aria-label') || ''}`.trim().toLowerCase();
                        if (/cancel|back|return|キャンセル|戻る/.test(text)) return false;
                        return text.includes('支払いを続ける') || text.includes('continue to payment') || text.includes('continue') || text.includes('次へ');
                    });
                    if (!continueBtn) return '';
                    continueBtn.scrollIntoView({ block: 'center' });
                    continueBtn.click();
                    return 'submitted_signup_email';
                }"""
            ) or "")
        except Exception:
            return ""

    def _autofill_payment_extension(self, page, paypal_phone: str, paypal_card: str, paypal_sms_url: str, email_addr: str = "") -> bool:
        if not paypal_card:
            return False
        try:
            parts = urlsplit(page.url)
            if parts.netloc.lower() != "www.paypal.com" or not parts.path.startswith("/checkoutweb/signup"):
                return False
            result = page.evaluate(
                """async ({phone, card, smsUrl}) => {
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const setValue = (el, value) => {
                        if (!el) return false;
                        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (desc && desc.set) desc.set.call(el, value); else el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    };
                    const waitFor = async selector => {
                        for (let i = 0; i < 12; i++) {
                            const el = document.querySelector(selector);
                            if (el) return el;
                            await sleep(500);
                        }
                        return null;
                    };
                    let filled = false;
                    try { localStorage.setItem('ppaf_phone', phone); localStorage.setItem('ppaf_card', card); localStorage.setItem('ppaf_sms_url', smsUrl || ''); } catch (_) {}
                    try { chrome.storage.local.set({ lastCardInput: card, lastPhone: phone, paypalSmsUrl: smsUrl || '', lastCardSavedAt: Date.now() }); } catch (_) {}
                    const stripeBtn = document.querySelector('#stripe-autofill-btn');
                    if (stripeBtn) {
                        stripeBtn.click();
                        const input = await waitFor('#saf-input');
                        const ok = await waitFor('#saf-ok');
                        if (input && ok) {
                            setValue(input, card);
                            ok.click();
                            filled = true;
                        }
                    }
                    const paypalBtn = document.querySelector('#ppaf-btn');
                    if (paypalBtn) {
                        paypalBtn.click();
                        const phoneInput = await waitFor('#ppaf-phone');
                        const cardInput = await waitFor('#ppaf-card');
                        const fillBtn = await waitFor('#ppaf-fill');
                        if (phoneInput && cardInput && fillBtn) {
                            setValue(phoneInput, phone);
                            setValue(cardInput, card);
                            fillBtn.click();
                            filled = true;
                        }
                    }
                    return filled;
                }""",
                {"phone": paypal_phone, "card": paypal_card, "smsUrl": paypal_sms_url},
            )
            if result:
                self._emit_log(f"已自动填入支付扩展资料: {page.url[:80]}", email_addr)
                return True
            self._emit_log(f"未找到支付扩展面板按钮，稍后重试: {page.url[:80]}", email_addr)
        except Exception:
            pass
        return False

    def _log_email_key(self, email_addr: str | None) -> str:
        return str(email_addr or "").strip().lower()

    def _infer_log_email(self, message: str) -> str:
        match = re.match(r"^\[([^\]\s]+@[^\]\s]+)\]", str(message or ""))
        return self._log_email_key(match.group(1)) if match else ""

    def _selected_log_email_key(self) -> str:
        if not hasattr(self, "account_list"):
            return ""
        selected = self.account_list.selection()
        if not selected:
            return ""
        try:
            index = int(selected[0])
        except ValueError:
            return ""
        if index < 0 or index >= len(self.accounts):
            return ""
        return self._log_email_key(self.accounts[index].email)

    def _selected_log_email_text(self) -> str:
        key = self._selected_log_email_key()
        if not key:
            return ""
        for account in self.accounts:
            if self._log_email_key(account.email) == key:
                return account.email
        return key

    def _emit_log(self, message: str, email_addr: str | None = None) -> None:
        self.events.put(("log", {"message": str(message), "email": self._log_email_key(email_addr)}))

    def _account_logger(self, account_or_email):
        if isinstance(account_or_email, MailAccount):
            email_addr = account_or_email.email
        else:
            email_addr = str(account_or_email or "")
        return lambda msg: self._emit_log(str(msg), email_addr)

    def _coerce_log_event(self, event) -> tuple[str, str]:
        payload = event[1] if len(event) > 1 else ""
        email_addr = ""
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("msg") or "")
            email_addr = self._log_email_key(payload.get("email"))
        else:
            message = str(payload)
            if len(event) > 2:
                email_addr = self._log_email_key(event[2])
        if not email_addr:
            email_addr = self._infer_log_email(message)
        return message, email_addr

    def _store_log_record(self, message: str, email_addr: str | None = None) -> tuple[LogRecord, bool]:
        email_key = self._log_email_key(email_addr)
        message = self._normalize_log_message(message)
        self.log_seq += 1
        record = LogRecord(
            seq=self.log_seq,
            time_text=datetime.now().strftime("%H:%M:%S"),
            message=message,
            email=email_key,
            scope="account" if email_key else "global",
        )
        self.log_records.append(record)
        if email_key:
            records = self.logs_by_email.setdefault(email_key, [])
            records.append(record)
            if len(records) > MAX_LOG_RECORDS_PER_VIEW:
                del records[:len(records) - MAX_LOG_RECORDS_PER_VIEW]
        else:
            self.global_logs.append(record)
            if len(self.global_logs) > MAX_LOG_RECORDS_PER_VIEW:
                del self.global_logs[:len(self.global_logs) - MAX_LOG_RECORDS_PER_VIEW]
        if len(self.log_records) > MAX_TOTAL_LOG_RECORDS:
            del self.log_records[:len(self.log_records) - MAX_TOTAL_LOG_RECORDS]
        visible_email = self._selected_log_email_key()
        visible = (not email_key) or (visible_email and visible_email == email_key)
        return record, bool(visible)

    def _normalize_log_message(self, message: str) -> str:
        text = str(message or "").strip()
        text = re.sub(r"^\[[^\]\s]+@[^\]\s]+\]\s*", "", text)
        if "Call log:" in text or "\n" in text:
            text = re.sub(r"\s+", " ", text)
            if len(text) > 360:
                text = text[:357] + "..."
        known = ("系统", "代理", "认证", "邮箱", "手机", "Session", "支付链接", "支付窗口", "导出")
        if any(text.startswith(f"[{item}]") for item in known):
            return text
        lowered = text.lower()
        if any(word in lowered for word in ("proxy", "代理", "出口", "ipinfo", "stripe=")):
            module = "代理"
        elif any(word in lowered for word in ("imap", "邮箱", "邮件", "验证码邮件")):
            module = "邮箱"
        elif any(word in lowered for word in ("手机号", "电话验证", "短信")):
            module = "手机"
        elif any(word in lowered for word in ("session", "access token", "accesstoken")):
            module = "Session"
        elif any(word in lowered for word in ("支付窗口", "chromium 窗口", "paypal 扩展")):
            module = "支付窗口"
        elif any(word in lowered for word in ("长链", "支付链接", "checkout", "paypal", "gopay", "apple pay")):
            module = "支付链接"
        elif any(word in lowered for word in ("导出", "sub2api")):
            module = "导出"
        elif any(word in lowered for word in ("注册", "登录", "认证", "oauth", "rt 获取")):
            module = "认证"
        else:
            module = "系统"
        return f"[{module}] {text}"

    def _append_log_record(self, message: str, email_addr: str | None = None) -> None:
        record, visible = self._store_log_record(message, email_addr)
        if visible:
            widget = self.global_log_text if not record.email else self.account_log_text
            self._insert_log_record(record, widget=widget)

    def _append_log_records(self, entries: list[tuple[str, str]]) -> None:
        if not entries:
            return
        account_records = []
        global_records = []
        for message, email_addr in entries:
            record, visible = self._store_log_record(message, email_addr)
            if visible:
                (account_records if record.email else global_records).append(record)
        for widget, records in ((self.account_log_text, account_records), (self.global_log_text, global_records)):
            if records:
                for record in records:
                    self._insert_log_record(record, scroll=False, widget=widget)
                try:
                    widget.see(END)
                except Exception:
                    pass

    def _insert_log_record(self, record: LogRecord, scroll: bool = True, widget=None) -> None:
        widget = widget or (self.global_log_text if not record.email else self.account_log_text)
        if widget is None:
            return
        try:
            widget.configure(state="normal")
        except Exception:
            pass
        tag = self._log_record_tag(record)
        if tag:
            widget.insert(END, self._format_log_record(record), tag)
        else:
            widget.insert(END, self._format_log_record(record))
        if scroll:
            widget.see(END)
        try:
            widget.configure(state="disabled")
        except Exception:
            pass

    def _log_record_tag(self, record: LogRecord) -> str:
        message = str(record.message or "")
        error_words = ("失败", "异常", "错误", "超时", "不可用", "拒绝", "耗尽")
        success_words = ("成功", "完成", "已获得", "已保存", "已提取", "已复制")
        attention_words = ("等待", "重试", "手动", "暂停", "风控")
        if any(word in message for word in error_words):
            return "log_error"
        if any(word in message for word in success_words):
            return "log_success"
        if any(word in message for word in attention_words) or re.search(r"(?<!\d)502(?!\d)", message):
            return "log_attention"
        return ""

    def _format_log_record(self, record: LogRecord) -> str:
        return f"[{record.time_text}] {record.message}\n"

    def _render_log_view(self) -> None:
        if not hasattr(self, "account_log_text"):
            return
        email_key = self._selected_log_email_key()
        if hasattr(self, "log_label"):
            if email_key:
                self.log_label.configure(text=f"选中邮箱日志：{self._selected_log_email_text()}")
            else:
                self.log_label.configure(text="选中邮箱日志：未选择邮箱")
        records = self.logs_by_email.get(email_key, []) if email_key else []
        self.account_log_text.configure(state="normal")
        self.account_log_text.delete("1.0", END)
        if records:
            for record in records:
                self._insert_log_record(record, scroll=False, widget=self.account_log_text)
            self.account_log_text.see(END)
        self.account_log_text.configure(state="disabled")

    def _drain_events(self) -> None:
        processed = 0
        started = time.monotonic()
        log_batch: list[tuple[str, str]] = []
        try:
            while processed < 300 and time.monotonic() - started < 0.05:
                event = self.events.get_nowait()
                processed += 1
                kind = event[0]
                if kind == "log":
                    message, email_addr = self._coerce_log_event(event)
                    log_batch.append((message, email_addr))
                elif kind == "link-attempt":
                    email_addr = event[1]
                    self.link_attempt_counts[email_addr] = max(0, int(self.link_attempt_counts.get(email_addr, 0) or 0)) + 1
                    self._set_account_attempt_count(email_addr)
                elif kind == "status":
                    email_addr = event[1]
                    status = event[2]
                    has_link = bool(str(self.results.get(email_addr, "") or "").strip())
                    failure_statuses = {"提取长链失败", "代理耗尽", "代理非日本", "代理检测失败", "不可自动重试"}
                    if has_link and status in failure_statuses:
                        self.log(f"已有长链结果，忽略后续失败状态: {status}", email_addr)
                    else:
                        self._set_account_status(email_addr, status)
                elif kind == "result":
                    email_addr = event[1]
                    payload = event[2]
                    link_url = ""
                    if isinstance(payload, dict):
                        if payload.get("url"):
                            link_url = str(payload.get("url") or "").strip()
                            self.results[email_addr] = link_url
                        old_session = self.session_results.get(email_addr, {})
                        self.session_results[email_addr] = {
                            "access_token": str(payload.get("access_token") or old_session.get("access_token") or ""),
                            "session_json": str(payload.get("session_json") or old_session.get("session_json") or ""),
                            "checkout_url": str(payload.get("checkout_url") or old_session.get("checkout_url") or ""),
                            "storage_state_json": str(payload.get("storage_state_json") or old_session.get("storage_state_json") or ""),
                            "openai_rt": str(payload.get("openai_rt") or old_session.get("openai_rt") or ""),
                            "link_proxy": str(payload.get("link_proxy") or old_session.get("link_proxy") or ""),
                            "link_proxy_label": str(payload.get("link_proxy_label") or old_session.get("link_proxy_label") or ""),
                            "link_proxy_exit": str(payload.get("link_proxy_exit") or old_session.get("link_proxy_exit") or ""),
                            "link_create_proxy": str(payload.get("link_create_proxy") or old_session.get("link_create_proxy") or ""),
                            "link_create_proxy_label": str(payload.get("link_create_proxy_label") or old_session.get("link_create_proxy_label") or ""),
                            "link_create_proxy_exit": str(payload.get("link_create_proxy_exit") or old_session.get("link_create_proxy_exit") or ""),
                            "link_followup_proxy": str(payload.get("link_followup_proxy") or old_session.get("link_followup_proxy") or payload.get("link_proxy") or old_session.get("link_followup_proxy") or ""),
                            "link_followup_proxy_label": str(payload.get("link_followup_proxy_label") or old_session.get("link_followup_proxy_label") or payload.get("link_proxy_label") or old_session.get("link_followup_proxy_label") or ""),
                            "link_followup_proxy_exit": str(payload.get("link_followup_proxy_exit") or old_session.get("link_followup_proxy_exit") or payload.get("link_proxy_exit") or old_session.get("link_proxy_exit") or ""),
                            "link_approve_proxy": str(payload.get("link_approve_proxy") or old_session.get("link_approve_proxy") or ""),
                            "link_approve_proxy_label": str(payload.get("link_approve_proxy_label") or old_session.get("link_approve_proxy_label") or ""),
                            "link_approve_proxy_exit": str(payload.get("link_approve_proxy_exit") or old_session.get("link_approve_proxy_exit") or ""),
                            "payment_link_type": str(payload.get("payment_link_type") or old_session.get("payment_link_type") or ""),
                            "stripe_amount": str(payload.get("stripe_amount") if "stripe_amount" in payload else old_session.get("stripe_amount") or ""),
                            "stripe_amount_source": str(payload.get("stripe_amount_source") if "stripe_amount_source" in payload else old_session.get("stripe_amount_source") or ""),
                            "target_amount": str(payload.get("target_amount") if "target_amount" in payload else old_session.get("target_amount") or ""),
                            "amount_check": str(payload.get("amount_check") if "amount_check" in payload else old_session.get("amount_check") or ""),
                        }
                        self._mark_session_dirty(email_addr)
                    else:
                        link_url = str(payload).strip()
                        self.results[email_addr] = link_url
                    self._set_account_status(email_addr, "长链已提取")
                    self.link_var.set(self.results.get(email_addr, ""))
                    self._render_results()
                    self._select_account_by_email(email_addr)
                    if link_url:
                        self._handle_link_success(email_addr)
                    self.save_state()
                elif kind == "account-updated":
                    self._render_accounts()
                    self.save_state()
                elif kind == "phones-updated":
                    self._render_phones()
                    self.save_state()
                elif kind == "remove-payment-proxy":
                    self._remove_payment_dynamic_proxy_value(event[1])
                elif kind == "remove-register-proxy":
                    self._remove_register_dynamic_proxy_value(event[1])
                elif kind == "remove-followup-proxy":
                    self._remove_followup_dynamic_proxy_value(event[1])
                elif kind == "remove-approve-proxy":
                    self._remove_approve_dynamic_proxy_value(event[1])
                elif kind == "take-auth-proxy":
                    self._handle_take_auth_proxy_event(event[1], event[2])
                elif kind == "provider-proxy-status":
                    role = str(event[1])
                    status = event[2] if isinstance(event[2], dict) else {}
                    if role in self.provider_proxy_status_vars:
                        if status.get("enabled"):
                            self.provider_proxy_status_vars[role].set(
                                f"可用 {int(status.get('ready') or 0)}/{int(status.get('target') or PROVIDER_PROXY_TARGET_STOCK)}"
                                f" 检测中 {int(status.get('inflight') or 0)}"
                            )
                        else:
                            self.provider_proxy_status_vars[role].set("未启用")
                elif kind == "export-authorized-ready":
                    self._finish_export_authorized(event[1], event[2])
                elif kind == "export-email-rt-ready":
                    self._finish_export_authorized_email_rt(event[1])
                elif kind == "export-sub2api-ready":
                    self._start_sub2api_export_with_accounts(event[1])
                elif kind == "phone-code-popup":
                    number = event[1]
                    code = event[2]
                    if code:
                        messagebox.showinfo(APP_TITLE, f"{number}\n验证码: {code}")
                    else:
                        messagebox.showwarning(APP_TITLE, f"{number}\n未读取到验证码")
                elif kind == "done":
                    self.running = False
                    self.stop_event.clear()
                    self.save_state(flush=True)
                    self.log("任务结束")
                elif kind == "open-link-done":
                    self.open_payment_window_count = max(0, self.open_payment_window_count - 1)
                    self.opening_payment_link = self.open_payment_window_count > 0
                    self.stop_event.clear()
                    self.log("支付链接窗口任务结束")
                elif kind == "mark-plus":
                    self._mark_account_plus(event[1])
                    self.save_state()
                    self.log("已标记为 Plus", event[1])
                elif kind == "prompt":
                    self._handle_prompt_event(event[1], event[2], event[3], event[4])
        except queue.Empty:
            pass
        self._append_log_records(log_batch)
        self.root.after(10 if not self.events.empty() else 100, self._drain_events)

    def _handle_prompt_event(self, prompt_id: str, prompt_type: str, email_addr: str, prompt: str) -> None:
        title = "输入手机号" if prompt_type == "phone" else "输入短信验证码"
        value = simpledialog.askstring(title, f"{email_addr}\n{prompt}", parent=self.root)
        result_queue = self.pending_prompts.pop(prompt_id, None)
        if result_queue:
            result_queue.put(value or "")

    def _selected_account_email_keys(self) -> list[str]:
        selected_emails = []
        for item in self.account_list.selection():
            try:
                values = self.account_list.item(item, "values")
            except Exception:
                continue
            if values:
                email_addr = str(values[0]).strip().lower()
                if email_addr:
                    selected_emails.append(email_addr)
        return selected_emails

    def _set_account_sort_state(self, column: str, direction: str) -> None:
        self.account_sort_column = column if column in ACCOUNT_SORT_COLUMNS else "email"
        self.account_sort_direction = direction if direction in ACCOUNT_SORT_DIRECTIONS else ACCOUNT_SORT_CUSTOM

    def _refresh_account_sort_headings(self) -> None:
        if not hasattr(self, "account_list"):
            return
        for column in ACCOUNT_SORT_COLUMNS:
            label = ACCOUNT_SORT_LABELS[column]
            if self.account_sort_direction == ACCOUNT_SORT_ASC and self.account_sort_column == column:
                label = f"{label}↑"
            elif self.account_sort_direction == ACCOUNT_SORT_DESC and self.account_sort_column == column:
                label = f"{label}↓"
            self.account_list.heading(column, text=label, command=lambda sort_column=column: self._toggle_account_sort(sort_column))

    def _toggle_account_sort(self, column: str) -> None:
        if column not in ACCOUNT_SORT_COLUMNS:
            return
        if self.account_sort_column != column or self.account_sort_direction == ACCOUNT_SORT_CUSTOM:
            self._set_account_sort_state(column, ACCOUNT_SORT_ASC)
        elif self.account_sort_direction == ACCOUNT_SORT_ASC:
            self._set_account_sort_state(column, ACCOUNT_SORT_DESC)
        else:
            self._set_account_sort_state(column, ACCOUNT_SORT_CUSTOM)
        self._render_accounts(preserve_yview=False)
        self.save_state()

    def _account_status_text(self, account: MailAccount) -> str:
        if str(self.results.get(account.email, "") or "").strip():
            status = "长链已提取"
        else:
            status = account.status or ("Session已获取" if account.email in self.session_results else "成功" if account.email in self.results else "待处理")
        if not account.openai_rt and account.auth_phone_number and account.auth_phone_sms_url and status == "待处理":
            status = "待获取RT(带授权手机号)"
        return status

    def _account_attempt_count(self, account: MailAccount) -> int:
        return max(0, int(self.link_attempt_counts.get(account.email, 0) or 0))

    def _account_row_values(self, account: MailAccount) -> tuple[str, str, str, int]:
        return (account.email, account.account_type, self._account_status_text(account), self._account_attempt_count(account))

    def _account_sort_key(self, index: int, column: str):
        account = self.accounts[index]
        if column == "type":
            return str(account.account_type or "").casefold()
        if column == "status":
            return self._account_status_text(account).casefold()
        if column == "attempts":
            return self._account_attempt_count(account)
        return str(account.email or "").casefold()

    def _account_visible_indices(self, active_group: str | None = None) -> list[int]:
        group = active_group
        if group is None:
            group = self.account_group_filter.get() if hasattr(self, "account_group_filter") else ACCOUNT_ALL_GROUP
        return [
            index
            for index, account in enumerate(self.accounts)
            if group == ACCOUNT_ALL_GROUP or (account.group or ACCOUNT_DEFAULT_GROUP) == group
        ]

    def _account_display_indices(self, active_group: str | None = None) -> list[int]:
        indices = self._account_visible_indices(active_group)
        if self.account_sort_direction == ACCOUNT_SORT_CUSTOM:
            return indices
        reverse = self.account_sort_direction == ACCOUNT_SORT_DESC
        column = self.account_sort_column if self.account_sort_column in ACCOUNT_SORT_COLUMNS else "email"
        return sorted(indices, key=lambda index: self._account_sort_key(index, column), reverse=reverse)

    def _apply_account_visible_order(self, ordered_visible_indices: list[int], active_group: str | None = None) -> bool:
        visible_indices = self._account_visible_indices(active_group)
        if sorted(visible_indices) != sorted(ordered_visible_indices):
            return False
        visible_slots = set(visible_indices)
        ordered_accounts = iter([self.accounts[index] for index in ordered_visible_indices])
        self.accounts = [next(ordered_accounts) if index in visible_slots else account for index, account in enumerate(self.accounts)]
        return True

    def _restore_account_selection(self, selected_email_keys: list[str], reveal_first: bool = False) -> bool:
        if not selected_email_keys:
            return False
        selected_key_set = set(selected_email_keys)
        restored_ids = [
            str(index)
            for index, account in enumerate(self.accounts)
            if account.email.strip().lower() in selected_key_set
        ]
        if not restored_ids:
            return False
        try:
            self.account_list.selection_set(restored_ids)
            self.account_list.focus(restored_ids[0])
            if reveal_first:
                self.account_list.see(restored_ids[0])
        except Exception:
            return False
        return True

    def _render_accounts(self, preserve_yview: bool = True) -> None:
        selected_email_keys = self._selected_account_email_keys()
        yview = self.account_list.yview() if preserve_yview else None
        self._refresh_account_sort_headings()
        active_group = self.account_group_filter.get() if hasattr(self, "account_group_filter") else ACCOUNT_ALL_GROUP
        for item in self.account_list.get_children():
            self.account_list.delete(item)
        for index in self._account_display_indices(active_group):
            account = self.accounts[index]
            self.account_list.insert("", END, iid=str(index), values=self._account_row_values(account))
        if selected_email_keys:
            self._restore_account_selection(selected_email_keys)
            self._show_selected_account_link()
        if yview:
            self.account_list.yview_moveto(yview[0])
            self.root.after_idle(lambda top=yview[0]: self.account_list.yview_moveto(top))
        elif preserve_yview is False:
            self.account_list.yview_moveto(0)

    def _render_phones(self) -> None:
        for item in self.phone_list.get_children():
            self.phone_list.delete(item)
        for index, phone in enumerate(self.phones):
            if self._phone_is_frozen(phone) and phone.status not in {"不可用", "冻结"}:
                phone.status = "冻结"
            self.phone_list.insert("", END, iid=str(index), values=(phone.number, phone.receive_count, phone.status, phone.last_code))

    def _render_payment_cards(self) -> None:
        for item in self.payment_card_list.get_children():
            self.payment_card_list.delete(item)
        for index, card in enumerate(self.payment_cards):
            self.payment_card_list.insert("", END, iid=str(index), values=(card.card, f"{card.year}/{card.month}", card.cvv, card.status))

    def _set_account_status(self, email_addr: str, status: str) -> None:
        for index, account in enumerate(self.accounts):
            if account.email.lower() == email_addr.lower():
                account.status = status
                try:
                    self.account_list.set(str(index), "status", status)
                except Exception:
                    pass
                return

    def _set_account_attempt_count(self, email_addr: str) -> None:
        for index, account in enumerate(self.accounts):
            if account.email.lower() == str(email_addr or "").lower():
                attempts = max(0, int(self.link_attempt_counts.get(account.email, 0) or 0))
                try:
                    self.account_list.set(str(index), "attempts", attempts)
                except Exception:
                    pass
                return

    def _mark_account_plus(self, email_addr: str) -> None:
        for index, account in enumerate(self.accounts):
            if account.email.lower() == email_addr.lower():
                account.account_type = "plus"
                account.status = "Plus"
                self.account_list.set(str(index), "type", "plus")
                self.account_list.set(str(index), "status", "Plus")
                return

    def _render_results(self) -> None:
        self._show_selected_account_link()

    def _show_selected_result(self) -> None:
        self._show_selected_account_link()

    def _clear_selected_account_details(self, render_log: bool = True) -> None:
        if hasattr(self, "link_var"):
            self.link_var.set("")
        if hasattr(self, "link_proxy_var"):
            self.link_proxy_var.set("")
        if hasattr(self, "session_text"):
            self.session_text.delete("1.0", END)
        if render_log:
            self._render_log_view()

    def _clear_account_selection(self) -> None:
        if not hasattr(self, "account_list"):
            return
        selected = self.account_list.selection()
        if selected:
            self.account_list.selection_remove(*selected)
        try:
            self.account_list.focus("")
        except Exception:
            pass
        self._clear_selected_account_details(render_log=True)

    def _refresh_account_group_combo(self) -> None:
        if not hasattr(self, "account_group_combo"):
            return
        values = [ACCOUNT_ALL_GROUP, *self.account_groups]
        self.account_group_combo.configure(values=values)
        if self.account_group_filter.get() not in values:
            self.account_group_filter.set(ACCOUNT_ALL_GROUP)

    def _on_account_group_filter_changed(self, _event=None) -> None:
        self._clear_account_selection()
        self._render_accounts()
        self.save_state()

    def _validate_account_group_name(self, value: str, old_name: str = "") -> str:
        name = str(value or "").strip()
        if not name or len(name) > 32:
            raise ValueError("分组名称长度必须为 1–32 个字符")
        if name in {ACCOUNT_ALL_GROUP, ACCOUNT_DEFAULT_GROUP}:
            raise ValueError(f"“{name}”是保留名称")
        for group in self.account_groups:
            if group.casefold() == name.casefold() and group != old_name:
                raise ValueError("已有同名分组")
        return name

    def create_account_group(self) -> None:
        value = simpledialog.askstring("新建邮箱分组", "分组名称（1–32 个字符）", parent=self.root)
        if value is None:
            return
        try:
            name = self._validate_account_group_name(value)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        self.account_groups.append(name)
        self.account_group_filter.set(name)
        self._refresh_account_group_combo()
        self._render_accounts()
        self.save_state()

    def rename_account_group(self) -> None:
        old_name = self.account_group_filter.get()
        if old_name in {ACCOUNT_ALL_GROUP, ACCOUNT_DEFAULT_GROUP}:
            messagebox.showinfo(APP_TITLE, "请选择一个自定义分组")
            return
        value = simpledialog.askstring("重命名邮箱分组", "新的分组名称", initialvalue=old_name, parent=self.root)
        if value is None:
            return
        try:
            new_name = self._validate_account_group_name(value, old_name=old_name)
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        self.account_groups = [new_name if group == old_name else group for group in self.account_groups]
        for account in self.accounts:
            if account.group == old_name:
                account.group = new_name
        self.account_group_filter.set(new_name)
        self._refresh_account_group_combo()
        self._render_accounts()
        self.save_state()

    def delete_account_group(self) -> None:
        group = self.account_group_filter.get()
        if group in {ACCOUNT_ALL_GROUP, ACCOUNT_DEFAULT_GROUP}:
            messagebox.showinfo(APP_TITLE, "请选择一个自定义分组")
            return
        if not messagebox.askyesno(APP_TITLE, f"删除分组“{group}”？\n组内邮箱将移回“{ACCOUNT_DEFAULT_GROUP}”。"):
            return
        for account in self.accounts:
            if account.group == group:
                account.group = ACCOUNT_DEFAULT_GROUP
        self.account_groups = [item for item in self.account_groups if item != group]
        self.account_group_filter.set(ACCOUNT_DEFAULT_GROUP)
        self._refresh_account_group_combo()
        self._render_accounts()
        self.save_state()

    def _move_selected_accounts_to_group(self, group: str) -> None:
        indices = [int(item) for item in self.account_list.selection() if str(item).isdigit()]
        for index in indices:
            if 0 <= index < len(self.accounts):
                self.accounts[index].group = group
        self._render_accounts()
        self.save_state()

    def _show_account_context_menu(self, event):
        row_id = self.account_list.identify_row(event.y)
        if not row_id:
            self._clear_account_selection()
            return "break"
        if row_id not in self.account_list.selection():
            self.account_list.selection_set(row_id)
            self.account_list.focus(row_id)
        menu = Menu(self.root, tearoff=False)
        type_menu = Menu(menu, tearoff=False)
        type_menu.add_command(label="Free", command=lambda: self.set_selected_account_type("free"))
        type_menu.add_command(label="Plus", command=lambda: self.set_selected_account_type("plus"))
        type_menu.add_command(label="Team", command=lambda: self.set_selected_account_type("team"))
        menu.add_cascade(label="设置类型", menu=type_menu)
        group_menu = Menu(menu, tearoff=False)
        for group in self.account_groups:
            group_menu.add_command(label=group, command=lambda target=group: self._move_selected_accounts_to_group(target))
        menu.add_cascade(label="移动到分组", menu=group_menu)
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _treeview_click_has_range_modifier(self, event) -> bool:
        return bool(int(getattr(event, "state", 0) or 0) & 0x0005)

    def _on_account_list_click(self, event):
        try:
            region = self.account_list.identify_region(event.x, event.y)
            if region in {"heading", "separator"}:
                return None
        except Exception:
            pass
        row_id = self.account_list.identify_row(event.y)
        if not row_id:
            self._clear_account_selection()
            self._focus_root_startup()
            return "break"
        selected = self.account_list.selection()
        if not self._treeview_click_has_range_modifier(event) and len(selected) == 1 and row_id in selected:
            self._clear_account_selection()
            self._focus_root_startup()
            return "break"
        return None

    def _on_account_list_middle_press(self, event):
        row_id = self.account_list.identify_row(event.y)
        self.account_drag_source_iid = row_id or ""
        if not row_id:
            return "break"
        if row_id not in self.account_list.selection():
            self.account_list.selection_set(row_id)
            self.account_list.focus(row_id)
        try:
            self.account_list.configure(cursor="sb_v_double_arrow")
        except Exception:
            pass
        return "break"

    def _on_account_list_middle_drag(self, event):
        if not self.account_drag_source_iid:
            return "break"
        row_id = self.account_list.identify_row(event.y)
        if row_id:
            try:
                self.account_list.focus(row_id)
            except Exception:
                pass
        return "break"

    def _account_drop_position(self, target_iid: str, event_y: int, visible_indices: list[int]) -> int:
        if not target_iid:
            if visible_indices:
                first = str(visible_indices[0])
                last = str(visible_indices[-1])
                try:
                    first_bbox = self.account_list.bbox(first)
                    last_bbox = self.account_list.bbox(last)
                    if first_bbox and event_y < first_bbox[1]:
                        return 0
                    if last_bbox and event_y > last_bbox[1] + last_bbox[3]:
                        return len(visible_indices)
                except Exception:
                    pass
            return len(visible_indices)
        try:
            target_index = visible_indices.index(int(target_iid))
        except (ValueError, TypeError):
            return len(visible_indices)
        try:
            bbox = self.account_list.bbox(target_iid)
            if bbox and event_y > bbox[1] + (bbox[3] / 2):
                return target_index + 1
        except Exception:
            pass
        return target_index

    def _on_account_list_middle_release(self, event):
        source_iid = self.account_drag_source_iid
        self.account_drag_source_iid = ""
        try:
            self.account_list.configure(cursor="")
        except Exception:
            pass
        if not source_iid:
            return "break"
        try:
            source_index = int(source_iid)
        except ValueError:
            return "break"
        active_group = self.account_group_filter.get() if hasattr(self, "account_group_filter") else ACCOUNT_ALL_GROUP
        visible_indices = self._account_display_indices(active_group)
        if source_index not in visible_indices:
            return "break"
        moved_email = self.accounts[source_index].email
        target_iid = self.account_list.identify_row(event.y)
        drop_position = self._account_drop_position(target_iid, event.y, visible_indices)
        source_position = visible_indices.index(source_index)
        reordered = list(visible_indices)
        moved = reordered.pop(source_position)
        if source_position < drop_position:
            drop_position -= 1
        drop_position = min(len(reordered), max(0, drop_position))
        if drop_position == source_position:
            return "break"
        reordered.insert(drop_position, moved)
        selected_email_keys = self._selected_account_email_keys()
        if self._apply_account_visible_order(reordered, active_group):
            self._set_account_sort_state(self.account_sort_column, ACCOUNT_SORT_CUSTOM)
            self._render_accounts()
            self._restore_account_selection(selected_email_keys or [moved_email.strip().lower()], reveal_first=True)
            self.save_state()
        return "break"

    def _is_interactive_focus_widget(self, widget) -> bool:
        interactive_classes = {
            "Entry",
            "TEntry",
            "Text",
            "Spinbox",
            "TSpinbox",
            "TCombobox",
            "Button",
            "TButton",
            "Checkbutton",
            "TCheckbutton",
            "Radiobutton",
            "TRadiobutton",
            "Treeview",
            "Scrollbar",
            "TScrollbar",
            "Scale",
            "TScale",
            "Notebook",
            "TNotebook",
        }
        try:
            return widget is not None and widget.winfo_class() in interactive_classes
        except Exception:
            return False

    def _on_global_click(self, event) -> None:
        widget = getattr(event, "widget", None)
        if self._is_interactive_focus_widget(widget):
            return
        self._clear_ui_focus()

    def _link_proxy_value(self, payload: dict, key: str, label_key: str) -> str:
        return str(payload.get(key) or payload.get(label_key) or "").strip()

    def _link_proxy_summary(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        create_proxy = self._link_proxy_value(payload, "link_create_proxy", "link_create_proxy_label")
        followup_proxy = self._link_proxy_value(payload, "link_followup_proxy", "link_followup_proxy_label") or self._link_proxy_value(payload, "link_proxy", "link_proxy_label")
        approve_proxy = self._link_proxy_value(payload, "link_approve_proxy", "link_approve_proxy_label")
        if not any((create_proxy, followup_proxy, approve_proxy)):
            return ""
        create_proxy = create_proxy or "直连"
        followup_proxy = followup_proxy or create_proxy
        approve_proxy = approve_proxy or followup_proxy
        return f"第一步={create_proxy} | 后续={followup_proxy} | Approve={approve_proxy}"

    def _link_proxy_exit_summary(self, payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""
        create_exit = str(payload.get("link_create_proxy_exit") or "").strip()
        followup_exit = str(payload.get("link_followup_proxy_exit") or payload.get("link_proxy_exit") or "").strip()
        approve_exit = str(payload.get("link_approve_proxy_exit") or "").strip()
        if not any((create_exit, followup_exit, approve_exit)):
            return ""
        create_exit = create_exit or "未记录"
        followup_exit = followup_exit or create_exit
        approve_exit = approve_exit or followup_exit
        return f"第一步={create_exit}\n后续={followup_exit}\nApprove={approve_exit}"

    def _show_selected_account_link(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            self._clear_selected_account_details(render_log=True)
            return
        index = int(selected[0])
        if index < 0 or index >= len(self.accounts):
            self._clear_selected_account_details(render_log=True)
            return
        email_addr = self.accounts[index].email
        self.link_var.set(self.results.get(email_addr, ""))
        payload = self.session_results.get(email_addr, {})
        self.link_proxy_var.set(self._link_proxy_summary(payload))
        self._show_session_result(email_addr)
        self._render_log_view()

    def _show_session_result(self, email_addr: str) -> None:
        if not hasattr(self, "session_text"):
            return
        payload = self.session_results.get(email_addr, {})
        access_token = str(payload.get("access_token") or "")
        session_json = str(payload.get("session_json") or "")
        checkout_url = str(payload.get("checkout_url") or "")
        link_proxy = str(payload.get("link_proxy") or "")
        link_proxy_label = str(payload.get("link_proxy_label") or "")
        link_proxy_exit = str(payload.get("link_proxy_exit") or "")
        link_create_proxy = str(payload.get("link_create_proxy") or "")
        link_create_proxy_label = str(payload.get("link_create_proxy_label") or "")
        link_create_proxy_exit = str(payload.get("link_create_proxy_exit") or "")
        link_followup_proxy = str(payload.get("link_followup_proxy") or "")
        link_followup_proxy_label = str(payload.get("link_followup_proxy_label") or "")
        link_followup_proxy_exit = str(payload.get("link_followup_proxy_exit") or "")
        link_approve_proxy = str(payload.get("link_approve_proxy") or "")
        link_approve_proxy_label = str(payload.get("link_approve_proxy_label") or "")
        link_approve_proxy_exit = str(payload.get("link_approve_proxy_exit") or "")
        payment_link_type = str(payload.get("payment_link_type") or "")
        stripe_amount = str(payload.get("stripe_amount") or "")
        stripe_amount_source = str(payload.get("stripe_amount_source") or "")
        target_amount = str(payload.get("target_amount") or "")
        amount_check = str(payload.get("amount_check") or "")
        text = ""
        if access_token:
            text += f"Access Token:\n{access_token}\n"
        if checkout_url:
            text += ("\n" if text else "") + f"Checkout URL:\n{checkout_url}\n"
        if payment_link_type:
            text += ("\n" if text else "") + f"Payment Link Type:\n{payment_link_type}\n"
        if stripe_amount or target_amount or stripe_amount_source or amount_check:
            text += ("\n" if text else "") + (
                "Amount Check:\n"
                f"Stripe Amount: {stripe_amount}\n"
                f"Target Amount: {target_amount}\n"
                f"Source: {stripe_amount_source}\n"
                f"Status: {amount_check}\n"
            )
        if link_proxy or link_proxy_label:
            text += ("\n" if text else "") + f"Long Link Proxy:\n{link_proxy or link_proxy_label}\n"
        if link_create_proxy or link_create_proxy_label:
            text += ("\n" if text else "") + f"Create Step Proxy:\n{link_create_proxy or link_create_proxy_label}\n"
        if link_followup_proxy or link_followup_proxy_label:
            text += ("\n" if text else "") + f"Followup Proxy:\n{link_followup_proxy or link_followup_proxy_label}\n"
        if link_approve_proxy or link_approve_proxy_label:
            text += ("\n" if text else "") + f"Approve Proxy:\n{link_approve_proxy or link_approve_proxy_label}\n"
        exit_summary = self._link_proxy_exit_summary({
            "link_create_proxy_exit": link_create_proxy_exit,
            "link_followup_proxy_exit": link_followup_proxy_exit,
            "link_approve_proxy_exit": link_approve_proxy_exit,
            "link_proxy_exit": link_proxy_exit,
        })
        if exit_summary:
            text += ("\n" if text else "") + f"Long Link Proxy Exits:\n{exit_summary}\n"
        if session_json:
            text += ("\n" if text else "") + f"Session JSON:\n{session_json}"
        self.session_text.delete("1.0", END)
        if text:
            self.session_text.insert(END, text)

    def _select_account_by_email(self, email_addr: str) -> None:
        for index, account in enumerate(self.accounts):
            if account.email.lower() == email_addr.lower():
                iid = str(index)
                try:
                    self.account_list.selection_set(iid)
                    self.account_list.see(iid)
                except Exception:
                    pass
                return

    def copy_link(self) -> None:
        link = self.link_var.get().strip()
        if not link:
            messagebox.showwarning(APP_TITLE, "暂无长链接")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(link)
        self.log("长链接已复制到剪贴板")

    def copy_link_proxy(self) -> None:
        proxy_url = self.link_proxy_var.get().strip()
        if not proxy_url:
            messagebox.showwarning(APP_TITLE, "当前选中邮箱暂无长链使用代理")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(proxy_url)
        self.log("长链使用代理已复制到剪贴板")

    def _selected_session_payload(self) -> dict:
        selected = self.account_list.selection()
        if not selected:
            return {}
        try:
            index = int(selected[0])
        except ValueError:
            return {}
        if index < 0 or index >= len(self.accounts):
            return {}
        return self.session_results.get(self.accounts[index].email, {})

    def _selected_account(self) -> MailAccount | None:
        selected = self.account_list.selection()
        if not selected:
            return None
        try:
            index = int(selected[0])
        except ValueError:
            return None
        if index < 0 or index >= len(self.accounts):
            return None
        return self.accounts[index]

    def _create_pasted_session_account(self) -> MailAccount:
        base = datetime.now().strftime("pasted-session-%Y%m%d-%H%M%S")
        existing = {account.email.lower() for account in self.accounts}
        email_addr = base
        counter = 1
        while email_addr.lower() in existing:
            counter += 1
            email_addr = f"{base}-{counter}"
        account = MailAccount(
            email=email_addr,
            password="",
            client_id="",
            refresh_token="",
            raw="",
            account_type="free",
            status="Session已获取",
        )
        self.accounts.append(account)
        self._render_accounts()
        self._select_account_by_email(account.email)
        self.save_state()
        self.log(f"已创建临时 Session 记录: {account.email}")
        return account

    def _start_session_long_link_generation(self, account: MailAccount, access_token: str) -> None:
        if not self._ensure_provider_proxy_pool_started():
            messagebox.showwarning(APP_TITLE, "提供商代理配置无效，无法启动长链接任务")
            return
        self.running = True
        self.stop_event.clear()
        self.link_attempt_counts[account.email] = 0
        self._render_accounts()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        reuse_proxy = normalize_proxy_url(self.reuse_payment_proxy.get())
        reuse_followup_proxy = normalize_proxy_url(self.reuse_followup_proxy.get())
        reuse_approve_proxy = normalize_proxy_url(self.reuse_approve_proxy.get())
        create_dynamic_proxies = [reuse_proxy] if reuse_proxy else self._read_payment_dynamic_proxies()
        followup_dynamic_proxies = [reuse_followup_proxy] if reuse_followup_proxy else self._read_followup_dynamic_proxies()
        approve_reuse_count = max(len(create_dynamic_proxies), len(followup_dynamic_proxies), 1)
        approve_dynamic_proxies = [reuse_approve_proxy] * approve_reuse_count if reuse_approve_proxy else self._read_approve_dynamic_proxies()
        if reuse_proxy:
            self.log(f"Session 生成长链接第一步优先使用复用代理: {mask_proxy_url(reuse_proxy)}")
        elif create_dynamic_proxies or followup_dynamic_proxies:
            self.log(f"Session 生成长链接代理组数量: {max(len(create_dynamic_proxies), len(followup_dynamic_proxies), 1)}")
        if reuse_followup_proxy:
            self.log(f"Session 生成长链接后续优先使用复用代理: {mask_proxy_url(reuse_followup_proxy)}")
        if reuse_approve_proxy:
            self.log(f"Session 生成长链接 Approve 优先使用复用代理: {mask_proxy_url(reuse_approve_proxy)}")
        elif approve_dynamic_proxies:
            self.log(f"Session 生成长链接 Approve 代理组数量: {len(approve_dynamic_proxies)}")
        fixed_proxies = {
            role: proxy
            for role, proxy in (
                ("create", reuse_proxy),
                ("followup", reuse_followup_proxy),
                ("approve", reuse_approve_proxy),
            )
            if proxy
        }
        effective_provider_roles = set(self.provider_proxy_manager.enabled_roles()) - set(fixed_proxies)
        if effective_provider_roles:
            threading.Thread(
                target=self._generate_provider_links_worker,
                args=([(account, access_token)], local_proxy, create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, False),
                kwargs={"fixed_proxies": fixed_proxies},
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._generate_single_opll_link_retry_worker,
                args=(account, access_token, local_proxy, create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, bool(reuse_proxy), bool(reuse_followup_proxy), bool(reuse_approve_proxy)),
                daemon=True,
            ).start()

    def generate_link_from_selected_session(self) -> None:
        account = self._selected_account()
        if not account:
            messagebox.showwarning(APP_TITLE, "请先选中一个已获取 Session 的邮箱")
            return
        payload = self.session_results.get(account.email, {})
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            messagebox.showwarning(APP_TITLE, "当前邮箱暂无 Access Token，请先执行“注册或登录并获取 Session”")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        self._start_session_long_link_generation(account, access_token)

    def generate_link_from_pasted_session(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        dialog = Toplevel(self.root)
        dialog.title("粘贴 Session JSON")
        dialog.geometry("720x420")
        ttk.Label(dialog, text="粘贴 ChatGPT Session JSON / Access Token").pack(anchor="w", padx=10, pady=(10, 4))
        text_box = self._scrolled_text(dialog, height=16)
        text_box.pack(fill=BOTH, expand=True, padx=10, pady=(0, 8))

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=X, padx=10, pady=(0, 10))

        def submit() -> None:
            session_text = text_box.get("1.0", END).strip()
            access_token = extract_access_token_from_session_text(session_text)
            if not access_token:
                messagebox.showwarning(APP_TITLE, "未从粘贴内容中解析到 accessToken")
                return
            account = self._create_pasted_session_account()
            self.session_results[account.email] = {
                "access_token": access_token,
                "session_json": session_text,
            }
            self._mark_session_dirty(account.email)
            account.status = "Session已获取"
            self._render_accounts()
            self._select_account_by_email(account.email)
            self._show_selected_account_link()
            self.save_state()
            dialog.destroy()
            self.log("已从粘贴 Session JSON 解析 Access Token，可继续粘贴或多选后批量提取长链", account.email)

        self._button(buttons, "取消", dialog.destroy, "关闭粘贴窗口，不保存当前输入内容。").pack(side=RIGHT)
        self._button(buttons, "保存Session", submit, "解析粘贴内容中的 Access Token，保存为临时 Session 账号。").pack(side=RIGHT, padx=(0, 8))
        text_box.focus_set()

    def select_all_session_accounts(self) -> None:
        ids = []
        visible_ids = set(self.account_list.get_children())
        for index, account in enumerate(self.accounts):
            if str(index) not in visible_ids:
                continue
            payload = self.session_results.get(account.email, {})
            access_token = str(payload.get("access_token") or "").strip()
            if access_token:
                ids.append(str(index))
        if not ids:
            messagebox.showwarning(APP_TITLE, "当前列表没有可批量提取的 Access Token")
            return
        self.account_list.selection_set(ids)
        self.account_list.focus(ids[0])
        self.account_list.see(ids[0])
        self.log(f"已选中 {len(ids)} 个有 Access Token 的 Session")

    def generate_links_from_selected_sessions(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中已获取 Session 的邮箱")
            return
        accounts = []
        missing = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if index < 0 or index >= len(self.accounts):
                continue
            account = self.accounts[index]
            payload = self.session_results.get(account.email, {})
            access_token = str(payload.get("access_token") or "").strip()
            if access_token:
                accounts.append((account, access_token))
            else:
                missing.append(account.email)
        if not accounts:
            messagebox.showwarning(APP_TITLE, "选中的邮箱暂无 Access Token，请先执行“注册或登录并获取 Session”")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        if not self._ensure_provider_proxy_pool_started():
            messagebox.showwarning(APP_TITLE, "提供商代理配置无效，无法启动批量长链接任务")
            return
        if missing:
            self.log(f"批量提取跳过无 Access Token 邮箱: {', '.join(missing[:5])}" + (f" 等 {len(missing)} 个" if len(missing) > 5 else ""))
        for account, _access_token in accounts:
            self.link_attempt_counts[account.email] = 0
        self._render_accounts()
        self.running = True
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        reuse_proxy = normalize_proxy_url(self.reuse_payment_proxy.get())
        reuse_followup_proxy = normalize_proxy_url(self.reuse_followup_proxy.get())
        reuse_approve_proxy = normalize_proxy_url(self.reuse_approve_proxy.get())
        create_dynamic_proxies = [reuse_proxy] if reuse_proxy else self._read_payment_dynamic_proxies()
        followup_dynamic_proxies = [reuse_followup_proxy] if reuse_followup_proxy else self._read_followup_dynamic_proxies()
        approve_dynamic_proxies = [reuse_approve_proxy] * len(accounts) if reuse_approve_proxy else self._read_approve_dynamic_proxies()
        race_concurrency = self._link_race_concurrency()
        if reuse_proxy:
            self.log(f"批量提取长链第一步优先使用复用代理: {mask_proxy_url(reuse_proxy)}")
        if reuse_followup_proxy:
            self.log(f"批量提取长链后续优先使用复用代理: {mask_proxy_url(reuse_followup_proxy)}")
        if reuse_approve_proxy:
            self.log(f"批量提取长链 Approve 优先使用复用代理: {mask_proxy_url(reuse_approve_proxy)}")
        elif not create_dynamic_proxies and not followup_dynamic_proxies:
            self.log("创建长链代理池为空，批量提取长链改用当前本地代理")
        elif race_concurrency > 1:
            self.log(f"单账号撞链并发数: {race_concurrency}")
        threading.Thread(target=self._generate_opll_links_from_sessions_worker, args=(accounts, local_proxy, create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, bool(reuse_proxy), bool(reuse_followup_proxy), bool(reuse_approve_proxy), race_concurrency), daemon=True).start()

    def _generate_opll_links_from_sessions_worker(self, accounts: list[tuple[MailAccount, str]], local_proxy: str, create_dynamic_proxies: list[str], followup_dynamic_proxies: list[str], approve_dynamic_proxies: list[str], reuse_create_proxy_enabled: bool = False, reuse_followup_proxy_enabled: bool = False, reuse_approve_proxy_enabled: bool = False, race_concurrency: int = 1) -> None:
        try:
            total = len(accounts)
            race_concurrency = min(30, max(1, int(race_concurrency or 1)))
            self._emit_log(f"批量并发提取选中长链启动: {total} 个账号，单账号撞链并发={race_concurrency}")
            fixed_proxies = {}
            if reuse_create_proxy_enabled and create_dynamic_proxies:
                fixed_proxies["create"] = create_dynamic_proxies[0]
            if reuse_followup_proxy_enabled and followup_dynamic_proxies:
                fixed_proxies["followup"] = followup_dynamic_proxies[0]
            if reuse_approve_proxy_enabled and approve_dynamic_proxies:
                fixed_proxies["approve"] = approve_dynamic_proxies[0]
            provider_enabled = bool(set(self.provider_proxy_manager.enabled_roles()) - set(fixed_proxies))
            if provider_enabled:
                self._generate_provider_links_worker(
                    accounts,
                    local_proxy,
                    create_dynamic_proxies,
                    followup_dynamic_proxies,
                    approve_dynamic_proxies,
                    True,
                    race_concurrency,
                    emit_done=False,
                    fixed_proxies=fixed_proxies,
                )
                return
            wanted_proxy_pool = bool(create_dynamic_proxies or followup_dynamic_proxies or approve_dynamic_proxies)
            if wanted_proxy_pool:
                create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies = self._precheck_link_proxy_lists(
                    local_proxy,
                    create_dynamic_proxies,
                    followup_dynamic_proxies,
                    approve_dynamic_proxies,
                    self._emit_log,
                    remove_payment_pool=not reuse_create_proxy_enabled,
                    remove_followup_pool=not reuse_followup_proxy_enabled,
                    remove_approve_pool=not reuse_approve_proxy_enabled,
                )
                if self.stop_event.is_set():
                    self._emit_log("批量提取长链已停止")
                    return
            proxy_queue: queue.Queue = queue.Queue()
            proxy_triples = self._link_proxy_triples(create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies, total)
            if wanted_proxy_pool and not proxy_triples:
                self._emit_log("支付代理池已耗尽，批量提取长链停止")
                for account, _access_token in accounts:
                    self.events.put(("status", account.email, "代理耗尽"))
                return
            for proxy_triple in proxy_triples:
                proxy_queue.put(proxy_triple)
            threads = []
            for index, (account, access_token) in enumerate(accounts, start=1):
                thread = threading.Thread(
                    target=self._generate_opll_link_retry_worker,
                    args=(account, access_token, local_proxy, proxy_queue, index, total, reuse_create_proxy_enabled, reuse_followup_proxy_enabled, reuse_approve_proxy_enabled, race_concurrency, self._link_attempt_limit()),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
        finally:
            self.events.put(("done",))

    def _manual_provider_fallback_queues(
        self,
        create_proxies: list[str],
        followup_proxies: list[str],
        approve_proxies: list[str],
    ) -> dict[str, queue.Queue]:
        result = {}
        for role, proxies in (
            ("create", create_proxies),
            ("followup", followup_proxies),
            ("approve", approve_proxies),
        ):
            role_queue: queue.Queue = queue.Queue()
            for proxy in proxies:
                if str(proxy or "").strip():
                    role_queue.put(normalize_proxy_url(proxy))
            result[role] = role_queue
        return result

    def _acquire_provider_proxy_triple(self, manual_queues: dict[str, queue.Queue], log_func, fixed_proxies: dict[str, str] | None = None) -> tuple[str, str, str] | None:
        enabled_roles = set(self.provider_proxy_manager.enabled_roles())
        fixed_proxies = fixed_proxies or {}
        deadline = time.monotonic() + PROVIDER_PROXY_TAKE_TIMEOUT
        selected: dict[str, str] = {}
        for role in PROVIDER_PROXY_ROLES:
            fallback = selected.get("create", "") if role == "followup" else selected.get("followup", "")
            proxy = ""
            if fixed_proxies.get(role):
                proxy = normalize_proxy_url(fixed_proxies[role])
            elif role in enabled_roles:
                remaining = max(0.0, deadline - time.monotonic())
                candidate = self.provider_proxy_manager.take(role, remaining, self.stop_event)
                if candidate:
                    proxy = candidate.url
                else:
                    if self.stop_event.is_set():
                        return None
                    log_func(f"{PROVIDER_PROXY_ROLE_LABELS[role]}提供商池等待 60 秒仍无可用代理，尝试手工池兜底")
                    try:
                        proxy = manual_queues[role].get_nowait()
                    except queue.Empty:
                        return None
            else:
                try:
                    proxy = manual_queues[role].get_nowait()
                except queue.Empty:
                    proxy = fallback
            selected[role] = proxy
        return self._link_proxy_triple(selected["create"], selected["followup"], selected["approve"])

    def _generate_provider_links_worker(
        self,
        accounts: list[tuple[MailAccount, str]],
        local_proxy: str,
        create_dynamic_proxies: list[str],
        followup_dynamic_proxies: list[str],
        approve_dynamic_proxies: list[str],
        wait_for_stock: bool,
        race_concurrency: int = 1,
        emit_done: bool = True,
        fixed_proxies: dict[str, str] | None = None,
    ) -> None:
        try:
            fixed_proxies = {role: normalize_proxy_url(proxy) for role, proxy in (fixed_proxies or {}).items() if proxy}
            enabled_roles = tuple(role for role in self.provider_proxy_manager.enabled_roles() if role not in fixed_proxies)
            if not enabled_roles and not fixed_proxies:
                self._emit_log("提供商代理池未启用")
                return
            if wait_for_stock:
                labels = "、".join(PROVIDER_PROXY_ROLE_LABELS[role] for role in enabled_roles)
                self._emit_log(f"等待提供商代理池达到启动库存 {PROVIDER_PROXY_LOW_WATER}: {labels}")
                if not self.provider_proxy_manager.wait_until_ready(PROVIDER_PROXY_LOW_WATER, self.stop_event, enabled_roles):
                    self._emit_log("等待提供商代理池时任务已停止")
                    return
                self._emit_log("提供商代理池已达到启动库存，开始批量提链")

            create_manual = [] if fixed_proxies.get("create") else create_dynamic_proxies
            followup_manual = [] if fixed_proxies.get("followup") else followup_dynamic_proxies
            approve_manual = [] if fixed_proxies.get("approve") else approve_dynamic_proxies
            wanted_manual_pool = bool(create_manual or followup_manual or approve_manual)
            if wanted_manual_pool:
                create_manual, followup_manual, approve_manual = self._precheck_link_proxy_lists(
                    local_proxy,
                    create_manual,
                    followup_manual,
                    approve_manual,
                    self._emit_log,
                )
                if self.stop_event.is_set():
                    return
            manual_queues = self._manual_provider_fallback_queues(
                create_manual,
                followup_manual,
                approve_manual,
            )
            race_concurrency = min(30, max(1, int(race_concurrency or 1)))
            max_attempts = self._link_attempt_limit()
            threads = []
            total = len(accounts)
            for index, (account, access_token) in enumerate(accounts, start=1):
                thread = threading.Thread(
                    target=self._generate_provider_link_retry_worker,
                    args=(account, access_token, local_proxy, manual_queues, index, total, race_concurrency, max_attempts, fixed_proxies),
                    daemon=True,
                )
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
        finally:
            if emit_done:
                self.events.put(("done",))

    def _generate_provider_link_retry_worker(
        self,
        account: MailAccount,
        access_token: str,
        local_proxy: str,
        manual_queues: dict[str, queue.Queue],
        index: int,
        total: int,
        race_concurrency: int,
        max_attempts: int,
        fixed_proxies: dict[str, str] | None = None,
    ) -> None:
        log_account = self._account_logger(account)
        attempt = 0
        while not self.stop_event.is_set() and attempt < max_attempts:
            proxy_batch = []
            wanted = min(race_concurrency, max_attempts - attempt)
            for _ in range(wanted):
                proxy_triple = self._acquire_provider_proxy_triple(manual_queues, log_account, fixed_proxies)
                if proxy_triple is None:
                    break
                proxy_batch.append(proxy_triple)
            if not proxy_batch:
                if not self.stop_event.is_set():
                    log_account("提供商与手工代理池均已耗尽")
                    self.events.put(("status", account.email, "代理耗尽"))
                return

            start_attempt = attempt + 1
            attempt += len(proxy_batch)
            if len(proxy_batch) == 1:
                create_proxy, followup_proxy, approve_proxy = proxy_batch[0]
                label = (
                    f"第 {attempt} 次: 第一步={mask_proxy_url(create_proxy) if create_proxy else '本地'} "
                    f"后续={mask_proxy_url(followup_proxy) if followup_proxy else '本地'} "
                    f"Approve={mask_proxy_url(approve_proxy) if approve_proxy else '本地'}"
                )
            else:
                label = f"第 {start_attempt}-{attempt} 次，{len(proxy_batch)} 路同时撞链"
            log_account(f"提供商批量提链({index}/{total}) {label}")

            result_queue: queue.Queue = queue.Queue()
            threads = []

            def run_one(proxy_triple: tuple[str, str, str]) -> None:
                create_proxy, followup_proxy, approve_proxy = proxy_triple
                ok = self._generate_opll_link_for_account(
                    account,
                    access_token,
                    local_proxy,
                    create_proxy,
                    followup_proxy,
                    approve_proxy,
                )
                result_queue.put((proxy_triple, ok))

            for proxy_triple in proxy_batch:
                thread = threading.Thread(target=run_one, args=(proxy_triple,), daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()

            success = False
            non_retryable = False
            failed = []
            while not result_queue.empty():
                proxy_triple, ok = result_queue.get()
                if ok:
                    success = True
                elif ok is None:
                    non_retryable = True
                else:
                    failed.append(proxy_triple)
            for proxy_triple in failed:
                self._remove_failed_link_proxy_triple(
                    *proxy_triple,
                    reuse_create_proxy_enabled=bool(fixed_proxies and fixed_proxies.get("create")),
                    reuse_followup_proxy_enabled=bool(fixed_proxies and fixed_proxies.get("followup")),
                    reuse_approve_proxy_enabled=bool(fixed_proxies and fixed_proxies.get("approve")),
                )
            if success:
                log_account("已有成功结果，停止该账号后续尝试")
                return
            if non_retryable:
                log_account("支付链接生成失败且不可自动重试，停止该账号")
                return
            if attempt < max_attempts and not self.stop_event.is_set():
                log_account("本轮失败，继续从后台可用池获取下一组代理")
                time.sleep(1)
        if attempt >= max_attempts and not self.stop_event.is_set():
            log_account(f"已达到每账号最多尝试次数 {max_attempts}")
            self.events.put(("status", account.email, "提取长链失败"))

    def _generate_single_opll_link_retry_worker(self, account: MailAccount, access_token: str, local_proxy: str, create_dynamic_proxies: list[str], followup_dynamic_proxies: list[str], approve_dynamic_proxies: list[str], reuse_create_proxy_enabled: bool = False, reuse_followup_proxy_enabled: bool = False, reuse_approve_proxy_enabled: bool = False) -> None:
        log_account = self._account_logger(account)
        try:
            wanted_proxy_pool = bool(create_dynamic_proxies or followup_dynamic_proxies or approve_dynamic_proxies)
            if wanted_proxy_pool:
                create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies = self._precheck_link_proxy_lists(
                    local_proxy,
                    create_dynamic_proxies,
                    followup_dynamic_proxies,
                    approve_dynamic_proxies,
                    log_account,
                    remove_payment_pool=not reuse_create_proxy_enabled,
                    remove_followup_pool=not reuse_followup_proxy_enabled,
                    remove_approve_pool=not reuse_approve_proxy_enabled,
                )
                if self.stop_event.is_set():
                    log_account("Session 生成长链接已停止")
                    return
            proxy_triples = self._link_proxy_triples(create_dynamic_proxies, followup_dynamic_proxies, approve_dynamic_proxies)
            if not proxy_triples:
                if wanted_proxy_pool:
                    log_account("支付代理池已耗尽，停止重试")
                    self.events.put(("status", account.email, "代理耗尽"))
                    return
                proxy_triples = [("", "", "")]
            max_attempts = self._link_attempt_limit()
            proxy_triples = proxy_triples[:max_attempts]
            total = len(proxy_triples)
            for attempt, base_proxy_triple in enumerate(proxy_triples, start=1):
                if self.stop_event.is_set():
                    log_account("Session 生成长链接已停止")
                    return
                create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = self._coerce_link_proxy_triple(base_proxy_triple)
                log_account(
                    "Session 生成长链接使用代理 "
                    f"第 {attempt}/{total} 组: "
                    f"第一步={mask_proxy_url(create_dynamic_proxy) if create_dynamic_proxy else '本地'} "
                    f"后续={mask_proxy_url(followup_dynamic_proxy) if followup_dynamic_proxy else '本地'} "
                    f"Approve={mask_proxy_url(approve_dynamic_proxy) if approve_dynamic_proxy else '本地'}"
                )
                ok = self._generate_opll_link_for_account(account, access_token, local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
                if ok:
                    return
                if ok is None:
                    log_account("支付链接生成失败且已标记不可自动重试，停止该账号后续重试")
                    return
                self._remove_failed_link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, reuse_create_proxy_enabled, reuse_followup_proxy_enabled, reuse_approve_proxy_enabled)
                if attempt < total:
                    log_account("支付链接生成失败，已移除当前支付代理，换下一组代理继续")
                    time.sleep(1)
            log_account("支付代理池已耗尽，停止重试")
            self.events.put(("status", account.email, "代理耗尽"))
        finally:
            self.events.put(("done",))

    def _generate_opll_link_retry_worker(self, account: MailAccount, access_token: str, local_proxy: str, proxy_queue: queue.Queue, index: int, total: int, reuse_create_proxy_enabled: bool = False, reuse_followup_proxy_enabled: bool = False, reuse_approve_proxy_enabled: bool = False, race_concurrency: int = 1, max_attempts: int = 1) -> None:
        log_account = self._account_logger(account)
        attempt = 0
        race_concurrency = min(30, max(1, int(race_concurrency or 1)))
        max_attempts = min(10000, max(1, int(max_attempts or 1)))
        if reuse_create_proxy_enabled and reuse_followup_proxy_enabled and reuse_approve_proxy_enabled:
            create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = self._coerce_link_proxy_triple(proxy_queue.queue[0] if proxy_queue.qsize() else ("", "", ""))
            log_account(
                f"批量提取长链使用复用代理({index}/{total}): "
                f"第一步={mask_proxy_url(create_dynamic_proxy)} "
                f"后续={mask_proxy_url(followup_dynamic_proxy)} "
                f"Approve={mask_proxy_url(approve_dynamic_proxy)}"
            )
            self._generate_opll_link_for_account(account, access_token, local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
            return
        if proxy_queue.empty():
            log_account(f"批量提取长链使用本地代理({index}/{total})")
            self._generate_opll_link_for_account(account, access_token, local_proxy, "", "", "")
            return
        if race_concurrency <= 1:
            while not self.stop_event.is_set() and attempt < max_attempts:
                try:
                    base_proxy_triple = proxy_queue.get_nowait()
                except queue.Empty:
                    log_account("支付代理池已耗尽，停止重试")
                    self.events.put(("status", account.email, "代理耗尽"))
                    return
                attempt += 1
                create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = self._coerce_link_proxy_triple(base_proxy_triple)
                if create_dynamic_proxy or followup_dynamic_proxy or approve_dynamic_proxy:
                    log_account(f"批量提取长链使用代理({index}/{total}) 第 {attempt} 次: 第一步={mask_proxy_url(create_dynamic_proxy) if create_dynamic_proxy else '本地'} 后续={mask_proxy_url(followup_dynamic_proxy) if followup_dynamic_proxy else '本地'} Approve={mask_proxy_url(approve_dynamic_proxy) if approve_dynamic_proxy else '本地'}")
                ok = self._generate_opll_link_for_account(account, access_token, local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
                if ok:
                    return
                if ok is None:
                    proxy_queue.put((create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy))
                    log_account("支付链接生成失败且已标记不可自动重试，停止该账号后续重试")
                    return
                self._remove_failed_link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, reuse_create_proxy_enabled, reuse_followup_proxy_enabled, reuse_approve_proxy_enabled)
                if self.stop_event.is_set():
                    break
                log_account("支付链接生成失败，已移除当前支付代理，换下一个代理继续重试")
                time.sleep(1)
            if attempt >= max_attempts:
                log_account(f"已达到每账号最多尝试次数 {max_attempts}，停止重试")
                self.events.put(("status", account.email, "提取长链失败"))
                return
            log_account("批量生成支付链接已停止")
            return
        while not self.stop_event.is_set() and attempt < max_attempts:
            proxy_batch = []
            for _ in range(min(race_concurrency, max_attempts - attempt)):
                try:
                    proxy_batch.append(self._coerce_link_proxy_triple(proxy_queue.get_nowait()))
                except queue.Empty:
                    break
            if not proxy_batch:
                log_account("支付代理池已耗尽，停止重试")
                self.events.put(("status", account.email, "代理耗尽"))
                return
            start_attempt = attempt + 1
            attempt += len(proxy_batch)
            if len(proxy_batch) == 1:
                create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = proxy_batch[0]
                label = f"第 {attempt} 次: 第一步={mask_proxy_url(create_dynamic_proxy) if create_dynamic_proxy else '本地'} 后续={mask_proxy_url(followup_dynamic_proxy) if followup_dynamic_proxy else '本地'} Approve={mask_proxy_url(approve_dynamic_proxy) if approve_dynamic_proxy else '本地'}"
            else:
                label = f"第 {start_attempt}-{attempt} 次，{len(proxy_batch)} 路同时撞链"
            log_account(f"批量提取长链使用支付代理({index}/{total}) {label}")
            result_queue: queue.Queue = queue.Queue()
            threads = []

            def run_one(base_proxy_triple: tuple[str, str, str]) -> None:
                create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = base_proxy_triple
                ok = self._generate_opll_link_for_account(account, access_token, local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
                result_queue.put((base_proxy_triple, ok))

            for base_proxy_triple in proxy_batch:
                thread = threading.Thread(target=run_one, args=(base_proxy_triple,), daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
            success = False
            non_retryable = False
            failed_proxies = []
            while not result_queue.empty():
                base_proxy_triple, ok = result_queue.get()
                if ok:
                    success = True
                elif ok is None:
                    non_retryable = True
                else:
                    failed_proxies.append(base_proxy_triple)
            if success:
                for create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy in failed_proxies:
                    self._remove_failed_link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, reuse_create_proxy_enabled, reuse_followup_proxy_enabled, reuse_approve_proxy_enabled)
                log_account("并发撞链已有成功结果，停止该账号后续重试")
                return
            if non_retryable:
                for base_proxy_triple in proxy_batch:
                    proxy_queue.put(base_proxy_triple)
                log_account("支付链接生成失败且已标记不可自动重试，停止该账号后续重试")
                return
            for create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy in failed_proxies:
                self._remove_failed_link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy, reuse_create_proxy_enabled, reuse_followup_proxy_enabled, reuse_approve_proxy_enabled)
            if self.stop_event.is_set():
                break
            log_account(f"本轮 {len(proxy_batch)} 路撞链均失败，已移除失败代理，换下一轮继续")
            time.sleep(1)
        if attempt >= max_attempts and not self.stop_event.is_set():
            log_account(f"已达到每账号最多尝试次数 {max_attempts}，停止重试")
            self.events.put(("status", account.email, "提取长链失败"))
            return
        log_account("批量生成支付链接已停止")

    def _generate_opll_link_from_session_worker(self, account: MailAccount, access_token: str, local_proxy: str, create_dynamic_proxy: str, followup_dynamic_proxy: str, approve_dynamic_proxy: str) -> None:
        try:
            self._generate_opll_link_for_account(account, access_token, local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
        finally:
            self.events.put(("done",))

    def _detect_proxy_exit(self, proxy_url: str) -> str:
        return detect_proxy_health(proxy_url).summary

    def _proxy_exit_is_japan(self, proxy_exit: str) -> bool:
        return bool(re.search(r"(?:^|\s)JP(?:/|\s|$)", str(proxy_exit or "")))

    def _proxy_exit_failed(self, proxy_exit: str) -> bool:
        return _proxy_exit_failed_text(proxy_exit)

    def _detect_link_proxy_exits(self, create_proxy_url: str, followup_proxy_url: str, approve_proxy_url: str, log_account, cached_exits: dict[str, str] | None = None) -> dict[str, str]:
        return _detect_link_proxy_exits_concurrently(
            self._detect_proxy_exit,
            log_account,
            create_proxy_url,
            followup_proxy_url,
            approve_proxy_url,
            bool(self.require_japan_extract_proxy.get()),
            self._proxy_exit_is_japan,
            cached_exits,
        )

    def _generate_opll_link_for_account(self, account: MailAccount, access_token: str, local_proxy: str, create_dynamic_proxy: str, followup_dynamic_proxy: str = "", approve_dynamic_proxy: str = "") -> bool | None:
        log_account = self._account_logger(account)
        try:
            if self.stop_event.is_set():
                return False
            self.events.put(("link-attempt", account.email))
            mode = PAYMENT_MODES.get(self.payment_mode.get(), PAYMENT_MODES["无卡长链接 US/USD"])
            country = str(mode.get("country") or "US")
            currency = str(mode.get("currency") or currency_for_country(country))
            apple_pay_hosted = bool(mode.get("apple_pay_hosted"))
            payment_provider = str(mode.get("payment_provider") or "paypal").strip().lower()
            target_amount = self._target_amount_text()
            create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy = self._link_proxy_triple(create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
            create_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=create_dynamic_proxy, chain_url="")
            followup_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=followup_dynamic_proxy, chain_url="")
            approve_proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=approve_dynamic_proxy, chain_url="")
            create_used_proxy = create_dynamic_proxy or local_proxy
            followup_used_proxy = followup_dynamic_proxy or create_used_proxy
            approve_used_proxy = approve_dynamic_proxy or followup_used_proxy
            if apple_pay_hosted:
                status_text = "生成ApplePay页中"
                action_text = "生成 Apple Pay hosted 支付页"
            elif payment_provider == "gopay":
                status_text = "提取GoPay链中"
                action_text = "提取 GoPay 长链接"
            else:
                status_text = "提取PP链中"
                action_text = "按截图逻辑提取 PayPal approve 长链"
            self.events.put(("status", account.email, status_text))
            _log_link_proxy_group(
                log_account,
                create_proxy,
                followup_proxy,
                approve_proxy,
                "" if not apple_pay_hosted and payment_provider != "gopay" else action_text,
            )
            with ExitStack() as stack:
                link_chains: dict[str, ProxyChainServer] = {}

                def link_chain_for(dynamic_proxy: str) -> ProxyChainServer:
                    key = normalize_proxy_url(dynamic_proxy)
                    if key not in link_chains:
                        link_chains[key] = stack.enter_context(ProxyChainServer(local_proxy, key, log_account))
                    return link_chains[key]

                create_chain = link_chain_for(create_dynamic_proxy)
                followup_chain = link_chain_for(followup_dynamic_proxy)
                approve_chain = link_chain_for(approve_dynamic_proxy)
                create_proxy_url = create_chain.url or local_proxy or create_dynamic_proxy
                followup_proxy_url = followup_chain.url or local_proxy or followup_dynamic_proxy or create_proxy_url
                approve_proxy_url = approve_chain.url or local_proxy or approve_dynamic_proxy or followup_proxy_url
                cached_exits = self._cached_link_proxy_exits_for_triple(local_proxy, create_dynamic_proxy, followup_dynamic_proxy, approve_dynamic_proxy)
                proxy_exits = self._detect_link_proxy_exits(create_proxy_url, followup_proxy_url, approve_proxy_url, log_account, cached_exits)
                create_proxy_exit = proxy_exits.get("create", "")
                followup_proxy_exit = proxy_exits.get("followup", "")
                approve_proxy_exit = proxy_exits.get("approve", "")
                self._set_link_proxy_cached_exit(local_proxy, create_dynamic_proxy, create_proxy_exit)
                self._set_link_proxy_cached_exit(local_proxy, followup_dynamic_proxy, followup_proxy_exit)
                self._set_link_proxy_cached_exit(local_proxy, approve_dynamic_proxy, approve_proxy_exit)
                proxy_exit = followup_proxy_exit
                if apple_pay_hosted:
                    result = generate_opll_hosted_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(result.get("long_url") or result.get("stripe_hosted_url") or "").strip()
                    if not long_url:
                        raise RuntimeError(f"接口生成成功但没有返回 Apple Pay 支付页链接: {result}")
                    amount_log = self._opll_amount_log_text(account.email, result)
                    if amount_log:
                        log_account(amount_log)
                    self.events.put(("result", account.email, {"url": long_url, "checkout_url": long_url, "access_token": access_token, "link_proxy": followup_used_proxy, "link_proxy_label": followup_proxy.label, "link_proxy_exit": proxy_exit, "link_create_proxy": create_used_proxy, "link_create_proxy_label": create_proxy.label, "link_create_proxy_exit": create_proxy_exit, "link_followup_proxy": followup_used_proxy, "link_followup_proxy_label": followup_proxy.label, "link_followup_proxy_exit": followup_proxy_exit, "link_approve_proxy": approve_used_proxy, "link_approve_proxy_label": approve_proxy.label, "link_approve_proxy_exit": approve_proxy_exit, "payment_link_type": "apple_pay_hosted", **self._opll_amount_fields(result)}))
                    self.events.put(("status", account.email, "ApplePay页已生成"))
                    log_account(f"Apple Pay hosted 支付页已生成，请用 Safari/iPhone/Mac 打开并手动付款: {long_url}")
                elif payment_provider == "gopay":
                    result = generate_opll_gopay_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(result.get("provider_redirect_url") or result.get("long_url") or "").strip()
                    if not long_url:
                        raise RuntimeError(f"接口提取成功但没有返回 GoPay 长链: {result}")
                    amount_log = self._opll_amount_log_text(account.email, result)
                    if amount_log:
                        log_account(amount_log)
                    self.events.put(("result", account.email, {"url": long_url, "checkout_url": long_url, "access_token": access_token, "link_proxy": followup_used_proxy, "link_proxy_label": followup_proxy.label, "link_proxy_exit": proxy_exit, "link_create_proxy": create_used_proxy, "link_create_proxy_label": create_proxy.label, "link_create_proxy_exit": create_proxy_exit, "link_followup_proxy": followup_used_proxy, "link_followup_proxy_label": followup_proxy.label, "link_followup_proxy_exit": followup_proxy_exit, "link_approve_proxy": approve_used_proxy, "link_approve_proxy_label": approve_proxy.label, "link_approve_proxy_exit": approve_proxy_exit, "payment_link_type": "gopay_redirect", **self._opll_amount_fields(result)}))
                    self.events.put(("status", account.email, "长链已提取"))
                    log_account(f"GoPay 长链接提取完成: {long_url}")
                else:
                    result = generate_opll_paypal_long_link(access_token, country, currency, create_proxy_url, followup_proxy_url, approve_proxy_url, target_amount)
                    long_url = str(result.get("provider_redirect_url") or result.get("long_url") or "").strip()
                    if not long_url:
                        raise RuntimeError(f"接口提取成功但没有返回 PayPal approve 长链: {result}")
                    if not opll_is_paypal_success_url(long_url):
                        raise RuntimeError(f"返回的不是可用 PayPal 跳转链接，拒绝保存: {long_url[:160]}")
                    amount_log = self._opll_amount_log_text(account.email, result)
                    if amount_log:
                        log_account(amount_log)
                    self.events.put(("result", account.email, {"url": long_url, "checkout_url": long_url, "access_token": access_token, "link_proxy": followup_used_proxy, "link_proxy_label": followup_proxy.label, "link_proxy_exit": proxy_exit, "link_create_proxy": create_used_proxy, "link_create_proxy_label": create_proxy.label, "link_create_proxy_exit": create_proxy_exit, "link_followup_proxy": followup_used_proxy, "link_followup_proxy_label": followup_proxy.label, "link_followup_proxy_exit": followup_proxy_exit, "link_approve_proxy": approve_used_proxy, "link_approve_proxy_label": approve_proxy.label, "link_approve_proxy_exit": approve_proxy_exit, "payment_link_type": "paypal_approve", **self._opll_amount_fields(result)}))
                    self.events.put(("status", account.email, "长链已提取"))
                    log_account(f"[支付链接] PayPal 跳转链接提取完成: {long_url}")
                return True
        except Exception as exc:
            log_account(f"接口提取长链失败: {self._opll_error_text(exc)}")
            non_retryable = opll_is_non_retryable_link_error(exc)
            status = exc.status if isinstance(exc, ProxyExitCheckError) else ("不可自动重试" if non_retryable else "提取长链失败")
            self.events.put(("status", account.email, status))
            if non_retryable:
                return None
        return False

    def _open_trial_payment_from_session_worker(self, account: MailAccount, storage_state_text: str, local_proxy: str, payment_dynamic_proxy: str) -> None:
        context = None
        log_account = self._account_logger(account)
        try:
            self.events.put(("status", account.email, "打开试用页中"))
            storage_state = json.loads(storage_state_text)
            register_dynamic_proxy = ""
            kept = KEPT_REGISTER_BROWSER_SESSIONS.get(account.email.lower())
            if kept:
                try:
                    register_context, _register_browser = kept[0], kept[1]
                    storage_state = register_context.storage_state()
                    if len(kept) >= 3:
                        register_dynamic_proxy = str(kept[2] or "")
                except Exception:
                    pass
            with ProxyChainServer(local_proxy, register_dynamic_proxy, log_account) as chain:
                proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=register_dynamic_proxy, chain_url=chain.url)
                log_account(f"打开试用页阶段使用注册代理: {proxy.label}")
                self.trial_proxy_chain = chain
                self.trial_payment_dynamic_proxy = payment_dynamic_proxy
                self.trial_account_email = account.email
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled", "--disable-features=IsolateOrigins,site-per-process"],
                        proxy={"server": chain.url} if chain.url else None,
                    )
                    context = browser.new_context(storage_state=storage_state, user_agent=DEFAULT_USER_AGENT, locale="en-US", timezone_id="America/New_York")
                    self.payment_context = context
                    self.payment_contexts.add(context)
                    page = context.new_page()
                    page.goto("https://chatgpt.com/?promo_campaign=plus-1-month-free#pricing", wait_until="domcontentloaded", timeout=300000)
                    self.events.put(("status", account.email, "试用页已打开"))
                    log_account("已用注册代理打开试用页面。看到领取按钮后，请先点软件里的“切换支付代理”，再手动点击网页按钮")
                    while not self.stop_event.is_set() and context.pages:
                        time.sleep(1)
        except Exception as exc:
            log_account(f"打开试用支付页失败: {exc}")
            self.events.put(("status", account.email, "打开支付页失败"))
        finally:
            if context in self.payment_contexts:
                self.payment_contexts.discard(context)
            self.payment_context = None
            if self.trial_account_email == account.email:
                self.trial_proxy_chain = None
                self.trial_payment_dynamic_proxy = ""
                self.trial_account_email = ""
            self.events.put(("done",))

    def _switch_trial_click_proxy(self, account: MailAccount, chain: ProxyChainServer, local_proxy: str, payment_dynamic_proxy: str) -> None:
        chain.set_dynamic_proxy(payment_dynamic_proxy)
        proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=payment_dynamic_proxy, chain_url=chain.url)
        self._emit_log(f"已找到试用按钮，点击前切换到支付代理: {proxy.label}", account.email)
        time.sleep(1)

    def switch_current_trial_to_payment_proxy(self) -> None:
        if not self.trial_proxy_chain or not self.trial_account_email:
            messagebox.showwarning(APP_TITLE, "当前没有打开中的试用页窗口")
            return
        payment_dynamic_proxy = str(self.trial_payment_dynamic_proxy or "").strip()
        if not payment_dynamic_proxy:
            messagebox.showwarning(APP_TITLE, "当前没有已取用的支付链接动态代理，请重新打开试用页")
            return
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        self.trial_proxy_chain.set_dynamic_proxy(payment_dynamic_proxy)
        proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=payment_dynamic_proxy, chain_url=self.trial_proxy_chain.url)
        self.log(f"已手动切换到支付代理: {proxy.label}，现在可以手动点击网页里的领取按钮", self.trial_account_email)

    def _click_trial_claim_button(self, page, before_click=None) -> bool:
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                found = page.evaluate(
                    """() => {
                        const visible = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                        };
                        const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                        const clickableFor = el => el?.closest?.('button, a, [role="button"], [onclick], [tabindex]') || el;
                        const candidates = Array.from(new Set([
                            ...Array.from(document.querySelectorAll('button, a, [role="button"], [onclick], [tabindex]')),
                            ...Array.from(document.querySelectorAll('body *')).map(clickableFor),
                        ])).filter(el => visible(el) && enabled(el));
                        const score = el => {
                            const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('data-testid') || ''}`.trim();
                            if (/Claim[\t\n\r ]*free[\t\n\r ]*offer|领取[\t\n\r ]*Plus|Plus[\t\n\r ]*免费|免费优惠|無料.*Plus|Plus.*無料|Claim[\t\n\r ]*Plus|Get[\t\n\r ]*Plus|Start.*trial|free.*trial|Try[\t\n\r ]*Plus/i.test(text)) return 10;
                            if (/Plus/i.test(text) && /free|trial|claim|get|start|upgrade|subscribe|continue|领取|免费|無料|続行|開始|アップグレード/i.test(text)) return 8;
                            if (/claim|get|start|upgrade|subscribe|continue|领取|免费|無料|続行|開始|購入|登録/i.test(text)) return 3;
                            return 0;
                        };
                        const target = candidates
                            .map(el => ({ el, score: score(el) }))
                            .filter(item => item.score > 0)
                            .sort((a, b) => b.score - a.score)[0]?.el;
                        if (!target) return false;
                        target.scrollIntoView({ block: 'center' });
                        return true;
                    }"""
                )
                if found:
                    if before_click:
                        before_click()
                    clicked = False
                    try:
                        button = page.get_by_text("Claim free offer", exact=True).first
                        button.click(timeout=5000)
                        clicked = True
                    except Exception:
                        clicked = page.evaluate(
                        """() => {
                            const visible = el => {
                                if (!el) return false;
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                            };
                            const enabled = el => el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                            const clickableFor = el => el?.closest?.('button, a, [role="button"], [onclick], [tabindex]') || el;
                            const candidates = Array.from(new Set([
                                ...Array.from(document.querySelectorAll('button, a, [role="button"], [onclick], [tabindex]')),
                                ...Array.from(document.querySelectorAll('body *')).map(clickableFor),
                            ])).filter(el => visible(el) && enabled(el));
                            const score = el => {
                                const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''} ${el.getAttribute('data-testid') || ''}`.trim();
                                if (/Claim[\t\n\r ]*free[\t\n\r ]*offer|领取[\t\n\r ]*Plus|Plus[\t\n\r ]*免费|免费优惠|無料.*Plus|Plus.*無料|Claim[\t\n\r ]*Plus|Get[\t\n\r ]*Plus|Start.*trial|free.*trial|Try[\t\n\r ]*Plus/i.test(text)) return 10;
                                if (/Plus/i.test(text) && /free|trial|claim|get|start|upgrade|subscribe|continue|领取|免费|無料|続行|開始|アップグレード/i.test(text)) return 8;
                                if (/claim|get|start|upgrade|subscribe|continue|领取|免费|無料|続行|開始|購入|登録/i.test(text)) return 3;
                                return 0;
                            };
                            const target = candidates
                                .map(el => ({ el, score: score(el) }))
                                .filter(item => item.score > 0)
                                .sort((a, b) => b.score - a.score)[0]?.el;
                            if (!target) return false;
                            target.scrollIntoView({ block: 'center' });
                            target.click();
                            return true;
                        }"""
                        )
                    if not clicked:
                        return False
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def copy_access_token(self) -> None:
        token = str(self._selected_session_payload().get("access_token") or "").strip()
        if not token:
            messagebox.showwarning(APP_TITLE, "当前邮箱暂无 Access Token")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(token)
        self.log("Access Token 已复制到剪贴板")

    def copy_session_json(self) -> None:
        session_json = str(self._selected_session_payload().get("session_json") or "").strip()
        if not session_json:
            messagebox.showwarning(APP_TITLE, "当前邮箱暂无 Session JSON")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(session_json)
        self.log("Session JSON 已复制到剪贴板")

    def _preview_and_save_text(self, title: str, text: str, default_extension: str = ".txt", filetypes=None) -> str:
        dialog = Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("760x520")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="请先核对导出内容，可复制；点击“确定导出”后选择保存文件。").pack(anchor="w", padx=10, pady=(10, 6))
        preview = self._scrolled_text(dialog, height=24)
        preview.pack(fill=BOTH, expand=True, padx=10, pady=(0, 8))
        preview.insert(END, text)

        result = {"path": ""}

        def copy_preview() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(preview.get("1.0", END).rstrip("\n"))
            self.log("导出预览内容已复制到剪贴板")

        def confirm_export() -> None:
            path = filedialog.asksaveasfilename(
                parent=dialog,
                title=title,
                defaultextension=default_extension,
                filetypes=filetypes or [("Text", "*.txt"), ("All", "*.*")],
            )
            if not path:
                return
            result["path"] = path
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill=X, padx=10, pady=(0, 10))
        self._button(buttons, "复制内容", copy_preview, "把预览内容复制到剪贴板，便于检查或手动保存。").pack(side=LEFT)
        self._button(buttons, "取消", cancel, "关闭导出预览窗口，不写入导出文件。").pack(side=RIGHT)
        self._button(buttons, "确定导出", confirm_export, "确认预览内容无误后写入所选导出文件。").pack(side=RIGHT, padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        self.root.wait_window(dialog)
        return result["path"]

    def export_authorized(self) -> None:
        accounts = self._selected_accounts_for_export()
        if not accounts:
            return
        if self._ensure_export_accounts_have_rt(accounts, "authorized"):
            return
        self._finish_export_authorized(accounts, self.export_name_prefix.get().strip())

    def _finish_export_authorized(self, accounts: list[MailAccount], prefix: str) -> None:
        accounts = [account for account in accounts if account.openai_rt]
        if not accounts:
            messagebox.showwarning(APP_TITLE, "没有可导出的已授权 RT")
            return
        text = "\n".join(account_export_line(account, prefix) for account in accounts) + "\n"
        path = self._preview_and_save_text("导出已授权邮箱", text)
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.log(f"已导出 {len(accounts)} 个已授权邮箱 TXT: {path}")

    def export_authorized_email_rt(self) -> None:
        accounts = self._selected_accounts_for_export()
        if not accounts:
            return
        if self._ensure_export_accounts_have_rt(accounts, "email_rt"):
            return
        self._finish_export_authorized_email_rt(accounts)

    def _finish_export_authorized_email_rt(self, accounts: list[MailAccount]) -> None:
        accounts = [account for account in accounts if account.openai_rt]
        if not accounts:
            messagebox.showwarning(APP_TITLE, "没有可导出的已授权 RT")
            return
        text = "\n".join(f"{account.email}----{account.openai_rt}" for account in accounts) + "\n"
        path = self._preview_and_save_text("导出邮箱----RT", text)
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.log(f"已导出 {len(accounts)} 个邮箱----RT TXT: {path}")

    def export_selected_sessions(self) -> None:
        accounts = self._selected_accounts_for_export()
        if not accounts:
            return
        rows = []
        missing = []
        for account in accounts:
            payload = self.session_results.get(account.email, {})
            session_json = str(payload.get("session_json") or "").strip() if isinstance(payload, dict) else ""
            if session_json:
                rows.append({"email": account.email, "session_json": session_json})
            else:
                missing.append(account.email)
        if not rows:
            messagebox.showwarning(APP_TITLE, "选中的邮箱没有可导出的 Session JSON")
            return
        text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
        path = self._preview_and_save_text(
            "导出选中Session",
            text,
            default_extension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        if missing:
            self.log(f"导出 Session 跳过 {len(missing)} 个无 Session 邮箱")
        self.log(f"已导出 {len(rows)} 个选中邮箱 Session JSON: {path}")

    def export_selected_raw(self) -> None:
        accounts = self._selected_accounts_for_export()
        if not accounts:
            return
        prefix = self.export_name_prefix.get().strip()
        text = "\n".join(account_export_line(account, prefix) for account in accounts) + "\n"
        path = self._preview_and_save_text("导出选中Raw", text)
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self.log(f"已导出 {len(accounts)} 个选中邮箱 Raw TXT: {path}")

    def _selected_accounts_for_export(self) -> list[MailAccount]:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先在左侧选择要导出的邮箱，可多选")
            return []
        selected_accounts = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self.accounts):
                selected_accounts.append(self.accounts[index])
        return selected_accounts

    def _selected_authorized_accounts(self) -> list[MailAccount]:
        accounts = [account for account in self._selected_accounts_for_export() if account.openai_rt]
        if not accounts:
            messagebox.showwarning(APP_TITLE, "选中的邮箱里没有已授权 RT")
        return accounts

    def _ensure_export_accounts_have_rt(self, accounts: list[MailAccount], export_kind: str) -> bool:
        missing = [account for account in accounts if not account.openai_rt]
        if not missing:
            return False
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return True
        preview = "\n".join(account.email for account in missing[:12])
        if len(missing) > 12:
            preview += f"\n... 另有 {len(missing) - 12} 个"
        if not messagebox.askyesno(APP_TITLE, f"选中邮箱中有 {len(missing)} 个没有 RT，将先自动授权获取 RT 后再导出。\n{preview}\n\n是否继续？"):
            return True
        self.running = True
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxies = self._read_dynamic_proxies()
        threading.Thread(target=self._authorize_missing_rt_then_export_worker, args=(accounts, missing, local_proxy, dynamic_proxies, export_kind, self.export_name_prefix.get().strip()), daemon=True).start()
        return True

    def _authorize_missing_rt_then_export_worker(self, accounts: list[MailAccount], missing: list[MailAccount], local_proxy: str, dynamic_proxies: list[str], export_kind: str, prefix: str) -> None:
        done_sent = False
        try:
            self._emit_log(f"导出前自动获取 RT: {len(missing)} 个账号")
            for account in missing:
                if self.stop_event.is_set():
                    self._emit_log("导出前授权任务已手动停止")
                    break
                dynamic_proxy = self._next_dynamic_proxy(dynamic_proxies)
                self._authorize_account_once(account, local_proxy, dynamic_proxy)
            ready = [account for account in accounts if account.openai_rt]
            failed = [account.email for account in accounts if not account.openai_rt]
            if failed:
                self._emit_log(f"以下账号仍无 RT，导出时跳过: {', '.join(failed[:8])}" + (f" 等 {len(failed)} 个" if len(failed) > 8 else ""))
            self.events.put(("done",))
            done_sent = True
            if export_kind == "authorized":
                self.events.put(("export-authorized-ready", ready, prefix))
            elif export_kind == "email_rt":
                self.events.put(("export-email-rt-ready", ready))
            elif export_kind == "sub2api":
                self.events.put(("export-sub2api-ready", ready))
        finally:
            if not done_sent:
                self.events.put(("done",))

    def export_sub2api(self) -> None:
        accounts = self._selected_accounts_for_export()
        if not accounts:
            return
        if self._ensure_export_accounts_have_rt(accounts, "sub2api"):
            return
        self._start_sub2api_export_with_accounts(accounts)

    def _start_sub2api_export_with_accounts(self, accounts: list[MailAccount]) -> None:
        accounts = [account for account in accounts if account.openai_rt]
        if not accounts:
            messagebox.showwarning(APP_TITLE, "没有可导出的已授权 RT")
            return
        if self.running:
            messagebox.showinfo(APP_TITLE, "任务正在运行")
            return
        path = filedialog.asksaveasfilename(
            title="导出 sub2api JSON",
            defaultextension=".sub2api.json",
            filetypes=[("sub2api JSON", "*.sub2api.json"), ("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        self.running = True
        self.stop_event.clear()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        dynamic_proxy = self._next_dynamic_proxy(self._read_dynamic_proxies())
        threading.Thread(target=self._export_sub2api_worker, args=(accounts, path, local_proxy, dynamic_proxy, self.export_name_prefix.get().strip()), daemon=True).start()

    def _export_sub2api_worker(self, accounts: list[MailAccount], path: str, local_proxy: str, dynamic_proxy: str, prefix: str) -> None:
        try:
            records = []
            with ProxyChainServer(local_proxy, dynamic_proxy, self._emit_log) as chain:
                proxy = ProxyConfig(local_proxy=local_proxy, dynamic_proxy=dynamic_proxy, chain_url=chain.url)
                self._emit_log(f"导出 sub2api 使用代理: {proxy.label}")
                for account in accounts:
                    if self.stop_event.is_set():
                        break
                    token_payload = refresh_openai_access_token(account.openai_rt, chain.url)
                    refreshed_rt = str(token_payload.get("refresh_token") or "")
                    if refreshed_rt.startswith("rt_"):
                        account.openai_rt = refreshed_rt
                    token_payload["refresh_token"] = account.openai_rt
                    export_email = f"({prefix}){account.email}" if prefix else account.email
                    record = openai_record_from_refresh_payload(export_email, token_payload)
                    records.append(record)
                    self._emit_log(f"已刷新 sub2api token: {account.email}")
            if not records:
                raise RuntimeError("没有可导出的 sub2api 记录")
            Path(path).write_text(json.dumps(build_sub2api_export(records), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._emit_log(f"已导出 {len(records)} 个账号 sub2api JSON: {path}")
            self.events.put(("account-updated", ""))
        except Exception as exc:
            self._emit_log(f"导出 sub2api 失败: {exc}")
        finally:
            self.events.put(("done",))

    def open_link(self) -> None:
        link = self.link_var.get().strip()
        if not link:
            messagebox.showwarning(APP_TITLE, "暂无长链接")
            return
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        extension_dir = self.payment_extension_dir.get().strip()
        paypal_config = self._take_paypal_phone_config()
        if paypal_config is None:
            self.opening_payment_link = self.open_payment_window_count > 0
            return
        paypal_phone, paypal_sms_url = paypal_config
        paypal_card = self._next_paypal_card_text()
        if paypal_card is None:
            self.opening_payment_link = self.open_payment_window_count > 0
            return
        dynamic_proxy = self._take_followup_or_payment_dynamic_proxy()
        email_addr = ""
        selected = self.account_list.selection()
        if selected:
            try:
                index = int(selected[0])
                if 0 <= index < len(self.accounts) and self.results.get(self.accounts[index].email, "").strip() == link:
                    email_addr = self.accounts[index].email
            except Exception:
                email_addr = ""
        self.open_payment_window_count += 1
        self.opening_payment_link = True
        threading.Thread(target=self._open_payment_link_worker, args=(link, local_proxy, dynamic_proxy, extension_dir, paypal_phone, paypal_card, paypal_sms_url, email_addr), daemon=True).start()

    def open_link_with_extraction_proxy(self) -> None:
        link = self.link_var.get().strip()
        if not link:
            messagebox.showwarning(APP_TITLE, "暂无长链接")
            return
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱")
            return
        email_addr = ""
        link_proxy = ""
        try:
            index = int(selected[0])
            if 0 <= index < len(self.accounts):
                email_addr = self.accounts[index].email
                payload = self.session_results.get(email_addr, {})
                link_proxy = str(payload.get("link_proxy") or payload.get("link_followup_proxy") or "").strip()
        except Exception:
            email_addr = ""
            link_proxy = ""
        if not link_proxy:
            messagebox.showwarning(APP_TITLE, "当前选中邮箱暂无长链提取代理")
            return
        self.stop_event.clear()
        self.save_state()
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        extension_dir = self.payment_extension_dir.get().strip()
        paypal_config = self._take_paypal_phone_config()
        if paypal_config is None:
            self.opening_payment_link = self.open_payment_window_count > 0
            return
        paypal_phone, paypal_sms_url = paypal_config
        paypal_card = self._next_paypal_card_text()
        if paypal_card is None:
            self.opening_payment_link = self.open_payment_window_count > 0
            return
        self.log(f"使用长链提取代理打开支付窗口: {link_proxy}", email_addr)
        self.open_payment_window_count += 1
        self.opening_payment_link = True
        threading.Thread(target=self._open_payment_link_worker, args=(link, local_proxy, link_proxy, extension_dir, paypal_phone, paypal_card, paypal_sms_url, email_addr), daemon=True).start()

    def open_selected_links(self) -> None:
        selected = self.account_list.selection()
        if not selected:
            messagebox.showwarning(APP_TITLE, "请先选中邮箱")
            return
        links = []
        for item in selected:
            try:
                index = int(item)
            except ValueError:
                continue
            if 0 <= index < len(self.accounts):
                account = self.accounts[index]
                link = self.results.get(account.email, "").strip()
                if link:
                    links.append((account.email, link))
        if not links:
            messagebox.showwarning(APP_TITLE, "选中的邮箱里没有可打开的长链接")
            return
        local_proxy = normalize_proxy_url(self.local_proxy.get())
        extension_dir = self.payment_extension_dir.get().strip()
        started = 0
        self.stop_event.clear()
        for email_addr, link in links:
            paypal_config = self._take_paypal_phone_config()
            if paypal_config is None:
                break
            paypal_phone, paypal_sms_url = paypal_config
            paypal_card = self._next_paypal_card_text()
            if paypal_card is None:
                break
            dynamic_proxy = self._take_followup_or_payment_dynamic_proxy()
            self.open_payment_window_count += 1
            self.opening_payment_link = True
            threading.Thread(target=self._open_payment_link_worker, args=(link, local_proxy, dynamic_proxy, extension_dir, paypal_phone, paypal_card, paypal_sms_url, email_addr), daemon=True).start()
            self.log("已启动独立支付窗口", email_addr)
            started += 1
        if started:
            self.save_state()

    def _next_paypal_card_text(self) -> str | None:
        base_card = self.paypal_card.get().strip()
        if not base_card:
            return ""
        for card in self.payment_cards:
            if card.status == "未用":
                try:
                    value = replace_paypal_card_head(base_card, card)
                except Exception as exc:
                    messagebox.showwarning(APP_TITLE, str(exc))
                    return None
                card.status = "已用"
                self._render_payment_cards()
                self.save_state()
                self.log(f"本次支付使用卡: {card.card}")
                return value
        if self.payment_cards:
            messagebox.showwarning(APP_TITLE, "支付卡池没有未用卡，请导入新卡或重置卡池")
            return None
        return base_card

    def log(self, message: str, email_addr: str | None = None) -> None:
        if threading.get_ident() != self.ui_thread_id:
            self._emit_log(message, email_addr)
            return
        self._append_log_record(message, email_addr)


def configure_process_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def configure_tk_appearance(root: Tk) -> None:
    try:
        scaling = max(1.0, float(root.winfo_fpixels("1i")) / 72.0)
        root.tk.call("tk", "scaling", scaling)
    except Exception:
        pass

    root.option_add("*Font", f"{{{UI_FONT_FAMILY}}} {UI_FONT_SIZE}")
    root.option_add("*Text.Font", f"{{{UI_FONT_FAMILY}}} {UI_TEXT_FONT_SIZE}")
    for font_name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkFixedFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            named_font = tkfont.nametofont(font_name)
            size = UI_TEXT_FONT_SIZE if font_name in {"TkTextFont", "TkFixedFont"} else UI_FONT_SIZE
            named_font.configure(family=UI_FONT_FAMILY, size=size)
        except Exception:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    ui_font = (UI_FONT_FAMILY, UI_FONT_SIZE)
    text_font = (UI_FONT_FAMILY, UI_TEXT_FONT_SIZE)
    style.configure(".", font=ui_font)
    style.configure("TButton", font=ui_font)
    style.configure("TLabel", font=ui_font)
    style.configure("TEntry", font=text_font)
    style.configure("TCombobox", font=ui_font)
    style.configure("TCheckbutton", font=ui_font)
    style.configure("Treeview", font=ui_font, rowheight=24)
    style.configure("Treeview.Heading", font=(UI_FONT_FAMILY, UI_FONT_SIZE, "bold"))
    style.configure("TNotebook.Tab", font=ui_font)


def main() -> None:
    configure_process_dpi_awareness()
    root = Tk()
    configure_tk_appearance(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
