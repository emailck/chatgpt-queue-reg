"""Shared ChatGPT browser fingerprint helpers."""

from __future__ import annotations

from dataclasses import dataclass
import random
import re


ACCEPT_LANGUAGE_CHOICES = (
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,zh-CN;q=0.8",
    "en,en-US;q=0.9",
    "en-US,en;q=0.8",
)

ACCEPT_LANGUAGE_CHOICES_FIREFOX = (
    "en-US,en;q=0.5",
    "en,en-US;q=0.5",
    "en-US,en;q=0.5,zh-CN;q=0.4",
    "en-US,en;q=0.4",
)

# ---------------------------------------------------------------------------
# BrowserFingerprint: 统一 Chrome / Firefox / 不同 OS 的指纹数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserFingerprint:
    """一个 session 内统一使用的浏览器指纹，贯穿 HTTP / TLS / Sentinel / Camoufox。"""

    browser_type: str          # "chrome" | "firefox"
    impersonate: str           # curl_cffi impersonate 标识
    user_agent: str
    sec_ch_ua: str             # Firefox 为空串
    accept_language: str
    platform: str              # "Windows" | "macOS"
    platform_version: str      # sec-ch-ua-platform-version, Firefox 为空
    chrome_full: str           # Chrome 完整版本号, Firefox 为空
    viewport_width: int
    viewport_height: int

    @property
    def is_firefox(self) -> bool:
        return self.browser_type == "firefox"

    @property
    def passkey_capabilities(self) -> str:
        return "0100" if self.is_firefox else "1111"

    def base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
        }
        if not self.is_firefox:
            headers.update({
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": f'"{self.platform}"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": self.platform_version,
            })
        return headers

    # 保留旧接口的属性别名，兼容 ChatGPTClient
    @property
    def chrome_major(self) -> int:
        if self.is_firefox:
            return 0
        m = re.search(r"Chrome/(\d+)", self.user_agent)
        return int(m.group(1)) if m else 0


# 向后兼容别名
ChromeFingerprint = BrowserFingerprint

# ---------------------------------------------------------------------------
# Chrome profiles — 多版本 + 多 OS
# ---------------------------------------------------------------------------

_CHROME_OS_VARIANTS = {
    "windows": {
        "ua_template": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        ),
        "platform": "Windows",
        "platform_version_range": (10, 15),
        "viewport_choices": [(1920, 1080), (2560, 1440), (1366, 768), (1536, 864)],
        "weight": 7,
    },
    "macos": {
        "ua_template": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
        ),
        "platform": "macOS",
        "platform_version_range": (10, 15),
        "viewport_choices": [(1440, 900), (1680, 1050), (2560, 1600), (1920, 1080)],
        "weight": 3,
    },
}

_CHROME_PROFILES = (
    {
        "major": 131,
        "impersonate": "chrome131",
        "build": 6778,
        "patch_range": (40, 200),
        "sec_ch_ua": '"Google Chrome";v="131", "Not.A/Brand";v="8", "Chromium";v="131"',
        "weight": 6,
    },
    {
        "major": 133,
        "impersonate": "chrome133a",
        "build": 6943,
        "patch_range": (40, 200),
        "sec_ch_ua": '"Google Chrome";v="133", "Not.A/Brand";v="8", "Chromium";v="133"',
        "weight": 8,
    },
    {
        "major": 136,
        "impersonate": "chrome136",
        "build": 7103,
        "patch_range": (40, 120),
        "sec_ch_ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
        "weight": 8,
    },
    {
        "major": 142,
        "impersonate": "chrome142",
        "build": 7444,
        "patch_range": (40, 180),
        "sec_ch_ua": '"Google Chrome";v="142", "Not.A/Brand";v="8", "Chromium";v="142"',
        "weight": 8,
    },
    {
        "major": 145,
        "impersonate": "chrome145",
        "build": 7575,
        "patch_range": (40, 180),
        "sec_ch_ua": '"Google Chrome";v="145", "Not.A/Brand";v="8", "Chromium";v="145"',
        "weight": 8,
    },
    {
        "major": 146,
        "impersonate": "chrome146",
        "build": 7136,
        "patch_range": (50, 183),
        "sec_ch_ua": '"Google Chrome";v="146", "Not.A/Brand";v="8", "Chromium";v="146"',
        "weight": 8,
    },
)

# ---------------------------------------------------------------------------
# Firefox profiles — 多版本 + 多 OS
# ---------------------------------------------------------------------------

_FIREFOX_OS_VARIANTS = {
    "windows": {
        "ua_template": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{ver}) "
            "Gecko/20100101 Firefox/{ver}"
        ),
        "viewport_choices": [(1920, 1080), (1366, 768), (1536, 864), (2560, 1440)],
        "weight": 7,
    },
    "macos": {
        "ua_template": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{ver}) "
            "Gecko/20100101 Firefox/{ver}"
        ),
        "viewport_choices": [(1440, 900), (1680, 1050), (1920, 1080)],
        "weight": 3,
    },
}

_FIREFOX_PROFILES = (
    {"major": 133, "impersonate": "firefox133", "weight": 6},
    {"major": 135, "impersonate": "firefox135", "weight": 8},
    {"major": 144, "impersonate": "firefox144", "weight": 8},
    {"major": 147, "impersonate": "firefox147", "weight": 10},
)

# 默认 auto 全走 Firefox；Chrome 只在显式 browser_mode="chrome" 时使用。
_BROWSER_TYPE_WEIGHTS_CAMOUFOX = {"chrome": 0, "firefox": 100}
_BROWSER_TYPE_WEIGHTS_NO_CAMOUFOX = {"chrome": 0, "firefox": 100}


# ---------------------------------------------------------------------------
# 随机生成
# ---------------------------------------------------------------------------


def random_accept_language() -> str:
    return random.choice(ACCEPT_LANGUAGE_CHOICES)


def random_accept_language_firefox() -> str:
    return random.choice(ACCEPT_LANGUAGE_CHOICES_FIREFOX)


def random_fingerprint(browser_mode: str = "auto") -> BrowserFingerprint:
    """随机生成一个完整浏览器指纹。

    双链路架构：
      browser_mode="auto"  — 默认 Firefox 指纹（→ Camoufox Firefox）
      browser_mode="chrome" — 强制 Chrome 指纹（→ QuickJS fallback，Patchright 当前禁用）
      browser_mode="firefox" — 强制 Firefox 指纹（→ Camoufox Firefox）

    Chrome 指纹：curl_cffi chrome impersonate + QuickJS fallback
    Firefox 指纹：curl_cffi firefox impersonate + Camoufox Firefox = TLS/UA 一致
    """
    if browser_mode == "chrome":
        return _random_chrome_fingerprint()
    if browser_mode == "firefox":
        return _random_firefox_fingerprint()
    return _random_firefox_fingerprint()


def _random_chrome_fingerprint() -> BrowserFingerprint:
    profile = random.choices(
        _CHROME_PROFILES,
        weights=[int(p["weight"]) for p in _CHROME_PROFILES],
        k=1,
    )[0]
    major = int(profile["major"])
    build = int(profile["build"])
    patch = random.randint(*profile["patch_range"])
    chrome_full = f"{major}.0.{build}.{patch}"

    # 随机选 OS
    os_key = random.choices(
        list(_CHROME_OS_VARIANTS.keys()),
        weights=[v["weight"] for v in _CHROME_OS_VARIANTS.values()],
        k=1,
    )[0]
    os_spec = _CHROME_OS_VARIANTS[os_key]
    ua = os_spec["ua_template"].format(ver=chrome_full)
    viewport = random.choice(os_spec["viewport_choices"])
    plat_ver = f'"{random.randint(*os_spec["platform_version_range"])}.0.0"'

    return BrowserFingerprint(
        browser_type="chrome",
        impersonate=str(profile["impersonate"]),
        user_agent=ua,
        sec_ch_ua=str(profile["sec_ch_ua"]),
        accept_language=random_accept_language(),
        platform=os_spec["platform"],
        platform_version=plat_ver,
        chrome_full=chrome_full,
        viewport_width=viewport[0],
        viewport_height=viewport[1],
    )


def _random_firefox_fingerprint() -> BrowserFingerprint:
    profile = random.choices(
        _FIREFOX_PROFILES,
        weights=[int(p["weight"]) for p in _FIREFOX_PROFILES],
        k=1,
    )[0]
    major = int(profile["major"])
    ff_ver = f"{major}.0"

    os_key = random.choices(
        list(_FIREFOX_OS_VARIANTS.keys()),
        weights=[v["weight"] for v in _FIREFOX_OS_VARIANTS.values()],
        k=1,
    )[0]
    os_spec = _FIREFOX_OS_VARIANTS[os_key]
    ua = os_spec["ua_template"].format(ver=ff_ver)
    viewport = random.choice(os_spec["viewport_choices"])

    return BrowserFingerprint(
        browser_type="firefox",
        impersonate=str(profile["impersonate"]),
        user_agent=ua,
        sec_ch_ua="",
        accept_language=random_accept_language_firefox(),
        platform="Windows" if os_key == "windows" else "macOS",
        platform_version="",
        chrome_full="",
        viewport_width=viewport[0],
        viewport_height=viewport[1],
    )


# ---------------------------------------------------------------------------
# 向后兼容
# ---------------------------------------------------------------------------


def random_chrome_fingerprint() -> BrowserFingerprint:
    """向后兼容：只返回 Chrome 指纹。新代码应使用 random_fingerprint()。"""
    return _random_chrome_fingerprint()


def chrome_major_from_user_agent(user_agent: str | None) -> int | None:
    match = re.search(r"Chrome/(\d+)", str(user_agent or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def firefox_major_from_user_agent(user_agent: str | None) -> int | None:
    match = re.search(r"Firefox/(\d+)", str(user_agent or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def normalize_impersonate(impersonate: str | None, default: str = "chrome136") -> str:
    value = str(impersonate or "").strip()
    aliases = {
        "chrome133": "chrome133a",
        "chrome134": "chrome136",
        "chrome135": "chrome136",
        "chrome130": "chrome131",
        "chrome132": "chrome131",
    }
    supported = {str(p["impersonate"]) for p in _CHROME_PROFILES} | {str(p["impersonate"]) for p in _FIREFOX_PROFILES}
    if value in aliases:
        return aliases[value]
    if value in supported:
        return value
    if value in {"chrome", "edge", "safari", "safari_ios", "chrome_android", "firefox"}:
        return value
    return default


def impersonate_from_user_agent(user_agent: str | None, default: str = "chrome136") -> str:
    default = normalize_impersonate(default, "chrome136")
    if not user_agent:
        return default
    # Firefox
    if "Firefox" in user_agent:
        major = firefox_major_from_user_agent(user_agent)
        for p in _FIREFOX_PROFILES:
            if int(p["major"]) == major:
                return normalize_impersonate(str(p["impersonate"]), default)
        return default
    # Chrome
    major = chrome_major_from_user_agent(user_agent)
    for p in _CHROME_PROFILES:
        if int(p["major"]) == major:
            return normalize_impersonate(str(p["impersonate"]), default)
    return default


def sec_ch_ua_from_user_agent(user_agent: str | None, default: str = "") -> str:
    if not user_agent or "Firefox" in user_agent:
        return default
    major = chrome_major_from_user_agent(user_agent)
    for p in _CHROME_PROFILES:
        if int(p["major"]) == major:
            return str(p["sec_ch_ua"])
    return default


def is_firefox_user_agent(user_agent: str | None) -> bool:
    return bool(user_agent and "Firefox" in user_agent)
