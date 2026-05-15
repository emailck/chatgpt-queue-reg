"""
Sentinel Token 生成器模块（纯 Python 方案）。
"""

import base64
import json
import random
import time
import uuid

from .fingerprint import normalize_impersonate


SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
SENTINEL_REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6"


def _fingerprint_str(user_agent=None, sec_ch_ua=None, impersonate=None):
    ua = str(user_agent or "").strip() or "-"
    sec = str(sec_ch_ua or "").strip() or "-"
    imp = str(impersonate or "").strip() or "-"
    return f"ua={ua} sec_ch_ua={sec} impersonate={imp}"


class SentinelTokenGenerator:
    """
    Sentinel Token 纯 Python 生成器。

    说明：
    - 该实现不依赖 Node / JS。
    - t 字段按当前纯 Python 方案固定空串，由上游接口判定可用性。
    """

    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id=None, user_agent=None, viewport_width=1920, viewport_height=1080,
                 is_firefox=False, gsi_loaded=0):
        self.device_id = device_id or str(uuid.uuid4())
        self.is_firefox = is_firefox or (user_agent and "Firefox" in user_agent)
        self.gsi_loaded = gsi_loaded
        if user_agent:
            self.user_agent = user_agent
        elif self.is_firefox:
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) "
                "Gecko/20100101 Firefox/135.0"
            )
        else:
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            )
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text):
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self):
        from datetime import datetime, timezone, timedelta

        tz_offsets = [
            (8, "GMT+0800", "China Standard Time"),
            (9, "GMT+0900", "Japan Standard Time"),
            (0, "GMT+0000", "Coordinated Universal Time"),
            (-5, "GMT-0500", "Eastern Standard Time"),
            (-8, "GMT-0800", "Pacific Standard Time"),
            (1, "GMT+0100", "Central European Standard Time"),
        ]
        offset_hours, tz_str, tz_name = random.choice(tz_offsets)
        now = datetime.now(timezone(timedelta(hours=offset_hours)))
        date_str = now.strftime("%a %b %d %Y %H:%M:%S") + f" {tz_str} ({tz_name})"

        from .sentinel_pow import (
            SDK_VERSION,
            CACHED_SCRIPTS,
            CACHED_DPL,
            ARROW,
            NAVIGATOR_KEYS_CHROME,
            NAVIGATOR_KEYS_FIREFOX,
            DOCUMENT_KEYS_CHROME,
            DOCUMENT_KEYS_FIREFOX,
            WINDOW_KEYS_CHROME,
            WINDOW_KEYS_FIREFOX,
        )

        is_ff = self.is_firefox
        navigator_keys = NAVIGATOR_KEYS_FIREFOX if is_ff else NAVIGATOR_KEYS_CHROME
        document_keys = DOCUMENT_KEYS_FIREFOX if is_ff else DOCUMENT_KEYS_CHROME
        window_keys = WINDOW_KEYS_FIREFOX if is_ff else WINDOW_KEYS_CHROME

        # React container ID
        chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        prefix = random.choice(["__reactContainer$", "_reactListening"])
        suffix = "".join(random.choices(chars, k=random.randint(8, 12)))
        react_id = prefix + suffix

        # p[3] GSI loaded — 由 _prefetch_google_gsi 决定，确保与网络流量一致
        gsi_loaded = self.gsi_loaded
        script_url = random.choice(CACHED_SCRIPTS) if gsi_loaded else None

        nav_prop = random.choice(navigator_keys)
        doc_prop = random.choice([react_id, "location"] + document_keys)
        win_prop = random.choice(window_keys)

        perf_counter = random.randint(1000, 30000)
        timestamp_ms = int(time.time() * 1000) + random.randint(-5000, 5000)

        return [
            SDK_VERSION,                                    # [0]
            date_str,                                       # [1]
            None,                                           # [2]
            gsi_loaded,                                     # [3]
            self.user_agent,                                # [4]
            script_url,                                     # [5]
            random.choice(CACHED_DPL),                      # [6]
            "en-US",                                        # [7]
            "en-US,en",                                     # [8]
            random.randint(0, 1),                           # [9]
            nav_prop,                                       # [10]
            doc_prop,                                       # [11]
            win_prop,                                       # [12]
            perf_counter,                                   # [13]
            self.device_id,                                 # [14]
            "",                                             # [15]
            random.randint(10, 12),                         # [16]
            timestamp_ms,                                   # [17]
            0,                                              # [18]
            0,                                              # [19]
            0,                                              # [20]
            0,                                              # [21]
            0,                                              # [22]
            1,                                              # [23]
            1,                                              # [24]
        ]

    @staticmethod
    def _base64_encode(data):
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def _run_check(self, start_time, seed, difficulty, config, nonce):
        config[9] = nonce
        config[17] = int(time.time() * 1000) + round((time.time() - start_time) * 1000)
        encoded = self._base64_encode(config)
        digest = self._fnv1a_32(seed + encoded)
        if digest[: len(difficulty)] <= difficulty:
            return encoded + "~S"
        return None

    def generate_token(self, seed=None, difficulty=None):
        seed = seed or self.requirements_seed
        difficulty = difficulty or "0"
        start_time = time.time()
        config = self._get_config()
        for nonce in range(self.MAX_ATTEMPTS):
            value = self._run_check(start_time, seed, difficulty, config, nonce)
            if value:
                return "gAAAAAB" + value
        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))

    def generate_requirements_token(self):
        config = self._get_config()
        # config[3] (gsi_loaded) 由 _get_config 根据 self.gsi_loaded 设置
        config[9] = random.randint(0, 1)
        return "gAAAAAC" + self._base64_encode(config)


def fetch_sentinel_challenge(
    session,
    device_id,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
    platform="Windows",
    request_p=None,
    logger=None,
    gsi_loaded=0,
):
    impersonate = normalize_impersonate(impersonate, "chrome136") if impersonate else None
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent,
                                       gsi_loaded=gsi_loaded)
    if logger:
        try:
            logger(
                "Sentinel challenge 请求: "
                + _fingerprint_str(
                    user_agent=user_agent,
                    sec_ch_ua=sec_ch_ua,
                    impersonate=impersonate,
                )
            )
        except Exception:
            pass
    req_body = {
        "p": str(request_p or "").strip() or generator.generate_requirements_token(),
        "id": device_id,
        "flow": flow,
    }
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": SENTINEL_REFERER,
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent or "Mozilla/5.0",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    # Firefox 不发 sec-ch-ua 系列头；Chrome 必须发
    is_firefox = "Firefox" in (user_agent or "")
    if not is_firefox:
        headers["sec-ch-ua"] = sec_ch_ua or '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = f'"{platform}"'
    kwargs = {"data": json.dumps(req_body), "headers": headers, "timeout": 20}
    if impersonate:
        kwargs["impersonate"] = impersonate
    try:
        response = session.post(SENTINEL_REQ_URL, **kwargs)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def _prefetch_google_gsi(session, *, user_agent=None, impersonate=None, logger=None):
    """预请求 accounts.google.com/gsi/client，使网络流量与 p[3]=1 一致。"""
    impersonate = normalize_impersonate(impersonate, "chrome136") if impersonate else None
    try:
        gsi_headers = {
            "Accept": "*/*",
            "Referer": "https://auth.openai.com/",
            "User-Agent": user_agent or "Mozilla/5.0",
        }
        kwargs = {"headers": gsi_headers, "timeout": 8, "allow_redirects": True}
        if impersonate:
            kwargs["impersonate"] = impersonate
        resp = session.get("https://accounts.google.com/gsi/client", **kwargs)
        if resp.status_code == 200:
            if logger:
                try:
                    logger("GSI 预请求成功 (p[3]=1)")
                except Exception:
                    pass
            return 1
    except Exception:
        pass
    if logger:
        try:
            logger("GSI 预请求失败, p[3]=0")
        except Exception:
            pass
    return 0


def _build_sentinel_token_python(
    session,
    device_id,
    *,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
    platform="Windows",
    is_firefox=False,
    viewport_width=1920,
    viewport_height=1080,
    logger=None,
):
    # 预请求 Google GSI，使 p[3] 与实际网络流量一致
    gsi_loaded = _prefetch_google_gsi(
        session, user_agent=user_agent, impersonate=impersonate, logger=logger,
    )

    challenge = fetch_sentinel_challenge(
        session,
        device_id,
        flow=flow,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
        platform=platform,
        logger=logger,
        gsi_loaded=gsi_loaded,
    )
    if not challenge:
        return None

    c_value = str(challenge.get("token") or "").strip()
    if not c_value:
        return None

    generator = SentinelTokenGenerator(
        device_id=device_id,
        user_agent=user_agent,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        is_firefox=is_firefox,
        gsi_loaded=gsi_loaded,
    )
    pow_data = challenge.get("proofofwork") or {}
    if pow_data.get("required") and pow_data.get("seed"):
        p_value = generator.generate_token(
            seed=pow_data.get("seed"),
            difficulty=pow_data.get("difficulty", "0"),
        )
    else:
        p_value = generator.generate_requirements_token()

    return json.dumps(
        {
            "p": p_value,
            "t": "",
            "c": c_value,
            "id": device_id,
            "flow": flow,
        },
        separators=(",", ":"),
    )


def build_sentinel_token(
    session,
    device_id,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
    platform="Windows",
    is_firefox=False,
    viewport_width=1920,
    viewport_height=1080,
    logger=None,
):
    """默认 Sentinel token 构造：纯 Python。"""
    return _build_sentinel_token_python(
        session,
        device_id,
        flow=flow,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
        platform=platform,
        is_firefox=is_firefox,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        logger=logger,
    )


def build_sentinel_token_vm_only(
    session,
    device_id,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
    platform="Windows",
    is_firefox=False,
    viewport_width=1920,
    viewport_height=1080,
    logger=None,
):
    """VM 分支专用构造器（命名保持不变，内部使用纯 Python）。"""
    return _build_sentinel_token_python(
        session,
        device_id,
        flow=flow,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
        platform=platform,
        is_firefox=is_firefox,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        logger=logger,
    )

