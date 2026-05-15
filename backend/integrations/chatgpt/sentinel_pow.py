"""
OpenAI Sentinel Token 生成
基于 https://github.com/leetanshaj/openai-sentinel
流程: PoW 计算 → 请求 /sentinel/req → 解析 Turnstile → 组装 Token
"""
import hashlib
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import pybase64

logger = logging.getLogger(__name__)

# ── Sentinel SDK 版本 (从 HAR 解码的 p 字段 [0] 获得) ──
SDK_VERSION = 2426

# ── 浏览器环境模拟常量 ──

CACHED_SCRIPTS = [
    "https://accounts.google.com/gsi/client",
    "https://chatgpt.com/backend-api/sentinel/sdk.js",
    None,
]

CACHED_DPL = ["prod-84bfe620fd5dd2d44306ba8091c5a8429e22c609"]

# U+2212 (MINUS SIGN) — Sentinel SDK 用此字符分隔属性名与值
ARROW = "−"

NAVIGATOR_KEYS_CHROME = [
    f"registerProtocolHandler{ARROW}function registerProtocolHandler() {{ [native code] }}",
    f"storage{ARROW}[object StorageManager]",
    f"locks{ARROW}[object LockManager]",
    f"appCodeName{ARROW}Mozilla",
    f"permissions{ARROW}[object Permissions]",
    f"webdriver{ARROW}false",
    f"product{ARROW}Gecko",
    f"clipboard{ARROW}[object Clipboard]",
    f"productSub{ARROW}20030107",
    f"vendor{ARROW}Google Inc.",
    f"onLine{ARROW}true",
    f"cookieEnabled{ARROW}true",
    f"hardwareConcurrency{ARROW}32",
    f"pdfViewerEnabled{ARROW}true",
    f"appName{ARROW}Netscape",
]

NAVIGATOR_KEYS_FIREFOX = [
    f"registerProtocolHandler{ARROW}function registerProtocolHandler() {{ [native code] }}",
    f"storage{ARROW}[object StorageManager]",
    f"locks{ARROW}[object LockManager]",
    f"appCodeName{ARROW}Mozilla",
    f"permissions{ARROW}[object Permissions]",
    f"webdriver{ARROW}false",
    f"product{ARROW}Gecko",
    f"clipboard{ARROW}[object Clipboard]",
    f"productSub{ARROW}20030107",
    f"vendor{ARROW}",
    f"onLine{ARROW}true",
    f"cookieEnabled{ARROW}true",
    f"hardwareConcurrency{ARROW}32",
    f"pdfViewerEnabled{ARROW}true",
    f"appName{ARROW}Netscape",
    f"serviceWorker{ARROW}[object ServiceWorkerContainer]",
    f"vendorSub{ARROW}",
]

DOCUMENT_KEYS_CHROME = [
    "location", "__reactContainer$", "close", "innerWidth",
    "clientInformation", "onformdata", "onstorage",
]

DOCUMENT_KEYS_FIREFOX = [
    "location", "__reactContainer$", "close", "innerWidth",
    "clientInformation", "mozInnerScreenY", "scrollByLines",
    "onformdata", "onstorage", "getDefaultComputedStyle",
    "_reactListening", "parent",
]

WINDOW_KEYS_CHROME = [
    "chrome", "performance", "navigator", "document",
    "crypto", "fetch", "localStorage", "indexedDB",
    "ondeviceorientation", "clientInformation",
]

WINDOW_KEYS_FIREFOX = [
    "performance", "navigator", "document",
    "crypto", "fetch", "localStorage", "indexedDB",
    "ondeviceorientation", "getDefaultComputedStyle",
    "mozInnerScreenY", "scrollByLines", "innerWidth",
]

MAX_ITERATION = 500000
DEFAULT_UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
DEFAULT_UA_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) "
    "Gecko/20100101 Firefox/135.0"
)


# ── PoW 计算 ──

def _get_parse_time() -> str:
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
    return now.strftime("%a %b %d %Y %H:%M:%S") + f" {tz_str} ({tz_name})"


def _random_react_id() -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    prefix = random.choice(["__reactContainer$", "_reactListening"])
    suffix = "".join(random.choices(chars, k=random.randint(8, 12)))
    return prefix + suffix


def _build_config(
    user_agent: str,
    device_id: str = "",
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    is_firefox: bool = False,
    gsi_loaded: int = 0,
) -> list:
    """构建 Sentinel PoW p 字段的 25 元素配置数组。

    结构来自 HAR 抓包逆向：
      [0]  SDK version (int)
      [1]  浏览器日期时间 (str, timezone-aware)
      [2]  null
      [3]  google GSI client loaded (0 or 1; 纯 Python 路径固定 0)
      [4]  navigator.userAgent (str)
      [5]  script URL (仅当 [3]=1 时有值, 否则 null)
      [6]  build/deployment ID (str, "prod-xxx")
      [7]  navigator.language (str)
      [8]  navigator.languages joined (str)
      [9]  flag (0 or 1)
      [10] navigator 属性 + 值 (str, "prop−value")
      [11] DOM 属性 #1 (str, React container / location)
      [12] DOM 属性 #2 (str, window/document prop)
      [13] performance counter (int)
      [14] device UUID (str)
      [15] empty string
      [16] counter (int, ~10-12)
      [17] timestamp ms (int, performance.now origin)
      [18-22] reserved (0)
      [23] flag (1)
      [24] flag (1)
    """
    is_ff = is_firefox or "Firefox" in user_agent
    navigator_keys = NAVIGATOR_KEYS_FIREFOX if is_ff else NAVIGATOR_KEYS_CHROME
    document_keys = DOCUMENT_KEYS_FIREFOX if is_ff else DOCUMENT_KEYS_CHROME
    window_keys = WINDOW_KEYS_FIREFOX if is_ff else WINDOW_KEYS_CHROME

    # [11] DOM prop: React container or location
    doc_prop = random.choice([
        _random_react_id(),
        "location",
    ])

    # [12] window/document prop: can be a standard prop or a React listener
    if random.random() < 0.3:
        win_prop = _random_react_id()
    else:
        win_prop = random.choice(window_keys)

    # [13] performance counter: 随机数值，模拟 document.height / 事件计数
    perf_counter = random.randint(1000, 30000)

    # [16] counter: HAR 中观察到 10-12
    counter = random.randint(10, 12)

    # [17] timestamp: performance.now() 风格的 ms 值
    timestamp_ms = int(time.time() * 1000) + random.randint(-5000, 5000)

    # [3] google GSI loaded — 纯 Python 路径无 GSI 网络请求，必须与实际流量一致
    # [5] script_url — 仅当 gsi_loaded=1 时有意义
    script_url = random.choice(CACHED_SCRIPTS) if gsi_loaded else None

    return [
        SDK_VERSION,                                    # [0]
        _get_parse_time(),                              # [1]
        None,                                           # [2]
        gsi_loaded,                                     # [3]
        user_agent,                                     # [4]
        script_url,                                     # [5]
        random.choice(CACHED_DPL),                      # [6]
        "en-US",                                        # [7]
        "en-US,en",                                     # [8]
        random.randint(0, 1),                           # [9]
        random.choice(navigator_keys),                  # [10]
        doc_prop,                                       # [11]
        win_prop,                                       # [12]
        perf_counter,                                   # [13]
        str(uuid.uuid4()) if not device_id else device_id,  # [14]
        "",                                             # [15]
        counter,                                        # [16]
        timestamp_ms,                                   # [17]
        0,                                              # [18]
        0,                                              # [19]
        0,                                              # [20]
        0,                                              # [21]
        0,                                              # [22]
        1,                                              # [23]
        1,                                              # [24]
    ]


def _generate_answer(seed: str, diff: str, config: list):
    diff_len = len(diff) // 2
    target_diff = bytes.fromhex(diff)
    seed_encoded = seed.encode()
    config_json = json.dumps(config, separators=(",", ":")).encode()
    static_config_part1 = b'[' + config_json + b','
    static_config_part3 = b']'

    for i in range(MAX_ITERATION):
        dynamic_json_i = str(i).encode()
        dynamic_json_j = str(i >> 1).encode()
        final_json_bytes = (
            static_config_part1 + dynamic_json_i + b"," +
            dynamic_json_j + static_config_part3
        )
        base_encode = pybase64.b64encode(final_json_bytes)
        hash_value = hashlib.sha3_512(seed_encoded + base_encode).digest()
        if hash_value[:diff_len] <= target_diff:
            return base_encode.decode(), True

    fallback = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + pybase64.b64encode(
        f'"{seed}"'.encode()
    ).decode()
    return fallback, False


def generate_pow_token(user_agent: str | None = None, is_firefox: bool = False,
                       viewport_width: int = 1920, viewport_height: int = 1080,
                       device_id: str = "", gsi_loaded: int = 0) -> str:
    """生成 Proof of Work token (gAAAAAC...)"""
    if user_agent is None:
        user_agent = DEFAULT_UA_FIREFOX if is_firefox else DEFAULT_UA_CHROME
    config = _build_config(user_agent, device_id=device_id,
                           viewport_width=viewport_width,
                           viewport_height=viewport_height,
                           is_firefox=is_firefox,
                           gsi_loaded=gsi_loaded)
    seed = format(random.random())
    diff = "0fffff"
    solution, found = _generate_answer(seed, diff, config)
    token = "gAAAAAC" + solution
    if found:
        logger.debug(f"PoW 计算成功, token 长度: {len(token)}")
    else:
        logger.warning("PoW 计算未找到解, 使用 fallback")
    return token


# ── Sentinel Token 完整流程 ──

def get_sentinel_token(
    session,
    device_id: str,
    flow: str = "authorize_continue",
    user_agent: str | None = None,
    is_firefox: bool = False,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    gsi_loaded: int = 0,
) -> str:
    """
    完整 Sentinel Token 生成:
    1. PoW 计算
    2. 请求 /sentinel/req 获取 Turnstile challenge
    3. 组装完整 token
    """
    if user_agent is None:
        user_agent = DEFAULT_UA_FIREFOX if is_firefox else DEFAULT_UA_CHROME
    is_ff = is_firefox or "Firefox" in user_agent

    # Step 1: PoW
    pow_token = generate_pow_token(user_agent, is_firefox=is_ff,
                                   viewport_width=viewport_width,
                                   viewport_height=viewport_height,
                                   device_id=device_id,
                                   gsi_loaded=gsi_loaded)
    logger.info(f"PoW token 生成完成 (长度: {len(pow_token)})")

    # Step 2: 请求 sentinel/req
    payload = json.dumps({
        "p": pow_token,
        "id": device_id,
        "flow": flow,
    })
    headers = {
        "Origin": "https://sentinel.openai.com",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
        "Content-Type": "text/plain;charset=UTF-8",
        "User-Agent": user_agent,
    }
    if not is_ff:
        headers.update({
            "sec-ch-ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })

    resp = session.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        headers=headers,
        data=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning(f"Sentinel req 失败: {resp.status_code}, 回退到无 PoW 模式")
        return json.dumps({
            "p": pow_token, "t": "", "c": "",
            "id": device_id, "flow": flow,
        })

    result = resp.json()
    server_token = result.get("token", "")
    turnstile_dx = result.get("turnstile", {}).get("dx", "")

    logger.info(f"Sentinel 响应: token={bool(server_token)}, turnstile_dx={bool(turnstile_dx)}")

    # Step 3: 组装完整 Sentinel Token
    sentinel = json.dumps({
        "p": pow_token,
        "t": turnstile_dx,
        "c": server_token,
        "id": device_id,
        "flow": flow,
    })
    logger.info("Sentinel Token 组装完成")
    return sentinel
