const EXPOSE_PATCH = "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t}({});";
const EXPOSE_REPLACEMENT =
  "return o?(Object.defineProperty(globalThis,'__debug_so',{value:o,writable:true,configurable:true}), r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o):null},t.token=ye,t.__debug_n=_n,t.__debug_bindProof=D,t}({});";
const INSTANCE_PATCH = "var P=new _;";
const INSTANCE_REPLACEMENT = "var P=new _;Object.defineProperty(globalThis,'__debugP',{value:P,writable:true,configurable:true});";
const SDK_GLOBAL_PATCH = "var SentinelSDK=";
const SDK_GLOBAL_REPLACEMENT = "globalThis.SentinelSDK=";
// Expose ne Map so solve action can inject the challenge for sessionObserverToken
const NE_PATCH = "const Xn=5e3,te=Hn(36),ne=new Map,ee=new Map;function re(t)";
const NE_REPLACEMENT =
  "const Xn=5e3,te=Hn(36),ne=new Map,ee=new Map;Object.defineProperty(globalThis,'__debug_ne',{value:ne,writable:true,configurable:true});function re(t)";
const nativeSetTimeout = globalThis.setTimeout
  ? globalThis.setTimeout.bind(globalThis)
  : null;
const nativeClearTimeout = globalThis.clearTimeout
  ? globalThis.clearTimeout.bind(globalThis)
  : null;
const nativeSetInterval = globalThis.setInterval
  ? globalThis.setInterval.bind(globalThis)
  : null;
const nativeClearInterval = globalThis.clearInterval
  ? globalThis.clearInterval.bind(globalThis)
  : null;

function bytesToBase64(bytes) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let out = "";
  let i = 0;
  while (i < bytes.length) {
    const b0 = bytes[i++] || 0;
    const b1 = bytes[i++] || 0;
    const b2 = bytes[i++] || 0;
    const n = (b0 << 16) | (b1 << 8) | b2;
    out += chars[(n >> 18) & 63];
    out += chars[(n >> 12) & 63];
    out += i - 2 < bytes.length ? chars[(n >> 6) & 63] : "=";
    out += i - 1 < bytes.length ? chars[n & 63] : "=";
  }
  return out;
}

function base64ToBytes(base64) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const clean = String(base64 || "").replace(/[^A-Za-z0-9+/=]/g, "");
  const bytes = [];
  for (let i = 0; i < clean.length; i += 4) {
    const c0 = chars.indexOf(clean[i]);
    const c1 = chars.indexOf(clean[i + 1]);
    const c2 = chars.indexOf(clean[i + 2]);
    const c3 = chars.indexOf(clean[i + 3]);
    const n = ((c0 & 63) << 18) | ((c1 & 63) << 12) | (((c2 < 0 ? 0 : c2) & 63) << 6) | ((c3 < 0 ? 0 : c3) & 63);
    bytes.push((n >> 16) & 255);
    if (clean[i + 2] !== "=") bytes.push((n >> 8) & 255);
    if (clean[i + 3] !== "=") bytes.push(n & 255);
  }
  return bytes;
}

function createStorage() {
  const map = new Map();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key) {
      return map.has(String(key)) ? map.get(String(key)) : null;
    },
    setItem(key, value) {
      map.set(String(key), String(value));
    },
    removeItem(key) {
      map.delete(String(key));
    },
  };
}

function createElement(tagName) {
  const tag = String(tagName || "div").toLowerCase();
  return {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    nodeName: tag.toUpperCase(),
    style: {},
    children: [],
    src: "",
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((x) => x !== child);
      return child;
    },
    setAttribute() {},
    getAttribute() {
      return null;
    },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() {
      return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 };
    },
  };
}

function installRuntime(payload) {
  const nativeObjectKeys = Object.keys.bind(Object);
  const uniqueStrings = (values, fallback) => {
    const out = [];
    const source = Array.isArray(values) && values.length ? values : fallback;
    for (const value of source || []) {
      const text = String(value || "");
      if (text && !out.includes(text)) out.push(text);
    }
    return out;
  };
  const nativeFunction = (name) => {
    const fn = function () {};
    Object.defineProperty(fn, "name", { value: name, configurable: true });
    fn.toString = () => `function ${name}() {\n    [native code]\n}`;
    return fn;
  };
  for (const key of [
    "global",
    "__filename",
    "__dirname",
    "module",
    "exports",
    "require",
    "clearImmediate",
    "setImmediate",
    "__payload_json",
    "__sdk_source",
    "__vm_done",
    "__vm_output_json",
    "__vm_error",
  ]) {
    try {
      if (Object.prototype.propertyIsEnumerable.call(globalThis, key)) {
        Object.defineProperty(globalThis, key, { enumerable: false, configurable: true });
      }
    } catch (_) {}
  }
  const isFirefox = /Firefox\//.test(String(payload.user_agent || ""));
  const screen = {
    width: Number(payload.screen_width || 1366),
    height: Number(payload.screen_height || 768),
    availWidth: Number(payload.screen_width || 1366),
    availHeight: Number(payload.screen_height || 768),
    colorDepth: 24,
    pixelDepth: 24,
  };
  Object.assign(screen, {
    top: 0,
    left: 0,
    availTop: 0,
    availLeft: 0,
  });
  const scriptUrls =
    Array.isArray(payload.script_urls) && payload.script_urls.length
      ? payload.script_urls.map((src) => String(src || "")).filter((src) => src)
      : [
          "https://sentinel.openai.com/backend-api/sentinel/sdk.js",
          "https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js",
        ];
  const scripts = scriptUrls.map((src) => {
    const el = createElement("script");
    el.src = src;
    return el;
  });
  const locationSearchRaw = String(payload.location_search || "");
  const locationSearch =
    locationSearchRaw && !locationSearchRaw.startsWith("?")
      ? `?${locationSearchRaw}`
      : locationSearchRaw;
  const locationObject = {
    href: `https://auth.openai.com/${locationSearch}`,
    origin: "https://auth.openai.com",
    pathname: "/",
    search: locationSearch,
  };
  const documentKeyCandidates = uniqueStrings(payload.document_keys, [
    "location",
    "__reactContainer$sentinel",
    "_reactListening",
  ]);
  const windowKeyCandidates = uniqueStrings(payload.window_keys, [
    "screenTop",
    "onmousedown",
    "onpagehide",
    "$RC",
    "resizeTo",
    "onanimationcancel",
    "resizeBy",
    "__reactRouterRouteModules",
  ]);
  const navigatorProtoKeys = uniqueStrings(payload.navigator_proto_keys, [
    "serviceWorker",
    "languages",
    "geolocation",
    "globalPrivacyControl",
    "language",
    "taintEnabled",
    "sendBeacon",
    "appCodeName",
    "productSub",
  ]);
  const documentElement = createElement("html");
  documentElement.clientWidth = screen.width;
  documentElement.clientHeight = screen.height;
  const document = {
    readyState: "complete",
    hidden: false,
    visibilityState: "visible",
    referrer: String(payload.referrer || "https://auth.openai.com/"),
    URL: locationObject.href,
    cookie: `oai-did=${encodeURIComponent(payload.device_id || "")}`,
    scripts,
    currentScript: scripts[scripts.length - 1] || { src: "", getAttribute() { return null; } },
    location: locationObject,
    documentElement,
    body: createElement("body"),
    head: createElement("head"),
    createElement(tag) {
      const el = createElement(tag);
      if (String(tag).toLowerCase() === "script") scripts.push(el);
      return el;
    },
    createElementNS(_ns, tag) {
      return this.createElement(tag);
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    getElementById() {
      return null;
    },
    getElementsByTagName(tag) {
      if (String(tag || "").toLowerCase() === "script") return scripts;
      if (String(tag || "").toLowerCase() === "head") return [this.head];
      if (String(tag || "").toLowerCase() === "body") return [this.body];
      if (String(tag || "").toLowerCase() === "html") return [this.documentElement];
      return [];
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return true;
    },
  };

  let performanceNow = Number(payload.performance_now || 12345.67);
  const performanceStep = Math.max(0.25, Number(payload.performance_step || 8.5));
  const performance = {
    now: () => {
      performanceNow += performanceStep;
      return Math.round(performanceNow);
    },
    timeOrigin: Number(payload.time_origin || 1710000000000),
  };
  if (payload.js_heap_size_limit !== null && payload.js_heap_size_limit !== undefined) {
    performance.memory = { jsHeapSizeLimit: Number(payload.js_heap_size_limit || 4294967296) };
  }

  class TextEncoderPoly {
    encode(text) {
      const str = String(text || "");
      const out = new Uint8Array(str.length);
      for (let i = 0; i < str.length; i += 1) out[i] = str.charCodeAt(i) & 255;
      return out;
    }
  }

  class TextDecoderPoly {
    decode(input) {
      if (!input) return "";
      let out = "";
      for (let i = 0; i < input.length; i += 1) {
        out += String.fromCharCode(input[i]);
      }
      return out;
    }
  }

  class URLSearchParamsPoly {
    constructor(search) {
      this._pairs = [];
      const s = String(search || "").replace(/^\?/, "");
      if (!s) return;
      const parts = s.split("&");
      for (const p of parts) {
        if (!p) continue;
        const i = p.indexOf("=");
        if (i < 0) {
          this._pairs.push([decodeURIComponent(p), ""]);
        } else {
          this._pairs.push([
            decodeURIComponent(p.slice(0, i)),
            decodeURIComponent(p.slice(i + 1)),
          ]);
        }
      }
    }
    keys() {
      return this._pairs.map((x) => x[0])[Symbol.iterator]();
    }
  }

  class URLPoly {
    constructor(input, base) {
      const raw = String(input || "");
      if (/^https?:\/\//i.test(raw)) {
        this.href = raw;
      } else {
        const b = String(base || "https://auth.openai.com/").replace(/\/$/, "");
        this.href = `${b}/${raw.replace(/^\//, "")}`;
      }
      const m = this.href.match(/^(https?:)\/\/([^\/]+)(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i);
      this.protocol = m ? m[1] : "https:";
      this.host = m ? m[2] : "auth.openai.com";
      this.hostname = this.host;
      this.pathname = m && m[3] ? m[3] : "/";
      this.search = m && m[4] ? m[4] : "";
      this.hash = m && m[5] ? m[5] : "";
      this.origin = `${this.protocol}//${this.host}`;
    }
    toString() {
      return this.href;
    }
  }

  globalThis.window = globalThis;
  globalThis.self = globalThis;
  globalThis.top = globalThis;
  globalThis.parent = globalThis;
  globalThis.document = document;
  const navigatorPrototype = {};
  Object.assign(navigatorPrototype, {
    serviceWorker: { toString: () => "[object ServiceWorkerContainer]" },
    geolocation: { toString: () => "[object Geolocation]" },
    appCodeName: "Mozilla",
    appName: "Netscape",
    product: "Gecko",
    productSub: "20030107",
    vendorSub: "",
    globalPrivacyControl: true,
    taintEnabled: nativeFunction("taintEnabled"),
    sendBeacon: nativeFunction("sendBeacon"),
    registerProtocolHandler: nativeFunction("registerProtocolHandler"),
  });
  const navigatorObject = Object.create(navigatorPrototype);
  Object.defineProperty(globalThis, "navigator", {
    value: navigatorObject,
    writable: true,
    configurable: true,
  });
  Object.assign(navigatorObject, {
    userAgent: String(payload.user_agent || "Mozilla/5.0"),
    language: String(payload.language || "en-US"),
    languages: Array.isArray(payload.languages) ? payload.languages : ["en-US", "en"],
    hardwareConcurrency: Number(payload.hardware_concurrency || 12),
    platform: String(payload.navigator_platform || "Win32"),
    vendor: String(payload.navigator_vendor || ""),
    webdriver: false,
    cookieEnabled: true,
    onLine: true,
    pdfViewerEnabled: true,
  });
  globalThis.location = locationObject;
  globalThis.screen = screen;
  globalThis.performance = performance;
  globalThis.localStorage = createStorage();
  globalThis.sessionStorage = createStorage();
  Object.assign(globalThis, {
    screenTop: 0,
    screenLeft: 0,
    innerWidth: screen.width,
    innerHeight: screen.height,
    outerWidth: screen.width,
    outerHeight: screen.height,
    onmousedown: null,
    onpagehide: null,
    onanimationcancel: null,
    __reactRouterRouteModules: {},
    $RC: nativeFunction("$RC"),
    resizeTo: nativeFunction("resizeTo"),
    resizeBy: nativeFunction("resizeBy"),
  });
  for (const key of documentKeyCandidates) {
    if (key.startsWith("__reactContainer$") || key.startsWith("_reactListening")) {
      document[key] = key.startsWith("__reactContainer$") ? {} : true;
    }
  }
  Object.assign(document, {
    onformdata: null,
    onstorage: null,
  });
  Object.defineProperty(globalThis, "__sentinel_init_pending", {
    value: [],
    writable: true,
    configurable: true,
  });
  Object.defineProperty(globalThis, "__sentinel_token_pending", {
    value: [],
    writable: true,
    configurable: true,
  });

  globalThis.setTimeout = (cb, delay, ...args) => {
    if (typeof cb !== "function") return 0;
    if (nativeSetTimeout) {
      return nativeSetTimeout(() => cb(...args), Math.max(0, Number(delay) || 0));
    }
    cb(...args);
    return 1;
  };
  globalThis.clearTimeout = (id) => {
    if (nativeClearTimeout && id) nativeClearTimeout(id);
  };
  globalThis.setInterval = (cb, delay, ...args) => {
    if (typeof cb !== "function") return 0;
    if (nativeSetInterval) {
      return nativeSetInterval(() => cb(...args), Math.max(1, Number(delay) || 1));
    }
    return 1;
  };
  globalThis.clearInterval = (id) => {
    if (nativeClearInterval && id) nativeClearInterval(id);
  };
  globalThis.requestIdleCallback = (cb) => {
    if (typeof cb !== "function") return 0;
    return globalThis.setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 50 }), 1);
  };
  globalThis.cancelIdleCallback = (id) => globalThis.clearTimeout(id);
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
  globalThis.dispatchEvent = () => true;
  globalThis.postMessage = () => {};

  globalThis.atob = (input) => String.fromCharCode(...base64ToBytes(input));
  globalThis.btoa = (input) => {
    const str = String(input || "");
    const bytes = [];
    for (let i = 0; i < str.length; i += 1) bytes.push(str.charCodeAt(i) & 255);
    return bytesToBase64(bytes);
  };
  globalThis.TextEncoder = globalThis.TextEncoder || TextEncoderPoly;
  globalThis.TextDecoder = globalThis.TextDecoder || TextDecoderPoly;
  globalThis.URL = globalThis.URL || URLPoly;
  globalThis.URLSearchParams = globalThis.URLSearchParams || URLSearchParamsPoly;
  globalThis.Event =
    globalThis.Event ||
    class Event {
      constructor(type) {
        this.type = type;
      }
    };
  globalThis.CustomEvent =
    globalThis.CustomEvent ||
    class CustomEvent extends globalThis.Event {
      constructor(type, init) {
        super(type);
        this.detail = init && Object.prototype.hasOwnProperty.call(init, "detail") ? init.detail : null;
      }
    };
  const NativeDate = globalThis.Date;
  if (payload.date_string) {
    class DatePoly extends NativeDate {
      constructor(...args) {
        super(...args);
        this.__sentinel_no_args = args.length === 0;
      }
      toString() {
        if (this.__sentinel_no_args) return String(payload.date_string);
        return super.toString();
      }
    }
    DatePoly.now = NativeDate.now.bind(NativeDate);
    DatePoly.parse = NativeDate.parse.bind(NativeDate);
    DatePoly.UTC = NativeDate.UTC.bind(NativeDate);
    globalThis.Date = DatePoly;
  }
  globalThis.MessageChannel =
    globalThis.MessageChannel ||
    class MessageChannel {
      constructor() {
        const makePort = () => ({
          onmessage: null,
          _listeners: [],
          _peer: null,
          postMessage(message) {
            const peer = this._peer;
            if (!peer) return;
            globalThis.setTimeout(() => {
              const event = { data: message };
              if (typeof peer.onmessage === "function") peer.onmessage(event);
              for (const listener of peer._listeners) listener(event);
            }, 0);
          },
          addEventListener(type, listener) {
            if (type === "message" && typeof listener === "function") {
              this._listeners.push(listener);
            }
          },
          removeEventListener(type, listener) {
            if (type === "message") {
              this._listeners = this._listeners.filter((x) => x !== listener);
            }
          },
          start() {},
          close() {
            this._listeners = [];
            this.onmessage = null;
            this._peer = null;
          },
        });
        this.port1 = makePort();
        this.port2 = makePort();
        this.port1._peer = this.port2;
        this.port2._peer = this.port1;
      }
    };
  globalThis.matchMedia =
    globalThis.matchMedia ||
    ((query) => ({
      media: String(query || ""),
      matches: false,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    }));
  globalThis.getComputedStyle =
    globalThis.getComputedStyle ||
    (() => ({
      getPropertyValue() {
        return "";
      },
    }));
  globalThis.history = globalThis.history || { length: 1, state: null, back() {}, forward() {}, go() {}, pushState() {}, replaceState() {} };
  if (isFirefox) {
    Object.defineProperty(globalThis, "dump", { value: () => {}, writable: true, configurable: true });
    Object.defineProperty(globalThis, "InstallTrigger", { value: {}, writable: true, configurable: true });
    try { delete globalThis.chrome; } catch (_) {}
  } else {
    globalThis.chrome = globalThis.chrome || { runtime: {}, app: {} };
  }
  globalThis.CSS = globalThis.CSS || { supports() { return true; } };
  globalThis.indexedDB =
    globalThis.indexedDB ||
    {
      open() {
        return { onerror: null, onsuccess: null, onupgradeneeded: null, result: {}, error: null };
      },
      deleteDatabase() {
        return {};
      },
    };
  globalThis.fetch = async () => {
    throw new Error("fetch should not be called");
  };
  Object.keys = (target) => {
    if (target === document) return documentKeyCandidates.slice();
    if (target === globalThis || target === globalThis.window) return windowKeyCandidates.slice();
    if (target === navigatorPrototype || target === Object.getPrototypeOf(navigatorObject)) return navigatorProtoKeys.slice();
    return nativeObjectKeys(target);
  };

  const randomFill = (arr) => {
    for (let i = 0; i < arr.length; i += 1) {
      arr[i] = Math.floor(Math.random() * 256);
    }
    return arr;
  };
  globalThis.crypto = {
    randomUUID: globalThis.crypto && typeof globalThis.crypto.randomUUID === "function"
      ? globalThis.crypto.randomUUID.bind(globalThis.crypto)
      : undefined,
    getRandomValues: randomFill,
  };
}

function loadPatchedSdk(sdkSource) {
  let sdk = String(sdkSource || "");
  sdk = sdk.replace(SDK_GLOBAL_PATCH, SDK_GLOBAL_REPLACEMENT);
  sdk = sdk.replace(INSTANCE_PATCH, INSTANCE_REPLACEMENT);
  sdk = sdk.replace(EXPOSE_PATCH, EXPOSE_REPLACEMENT);
  sdk = sdk.replace(NE_PATCH, NE_REPLACEMENT);
  Object.defineProperty(globalThis, "__patched_sdk_source", {
    value: sdk,
    writable: true,
    configurable: true,
  });
  eval(sdk);
}

async function run(payload, sdkSource) {
  installRuntime(payload);
  loadPatchedSdk(sdkSource);

  if (payload.action === "requirements") {
    const requestP = await globalThis.__debugP.getRequirementsToken();
    return { request_p: requestP };
  }

  if (payload.action === "solve") {
    const challenge = payload.challenge || {};
    const requestP = String(payload.request_p || "").trim();
    if (!requestP) throw new Error("missing request_p");
    const finalP = await globalThis.__debugP.getEnforcementToken(challenge);
    globalThis.SentinelSDK.__debug_bindProof(challenge, requestP);
    const dx = challenge && challenge.turnstile ? challenge.turnstile.dx : null;
    const tValue = dx ? await globalThis.SentinelSDK.__debug_n(challenge, dx) : null;

    // so 来自 sessionObserverToken，它读取 ne Map 里的 cachedSOChatReq，
    // 然后跑 Nt(challenge.so.collector_dx) 得到加密的 sensor data。
    let so = globalThis.__debug_so || null;
    if (!so && globalThis.__debug_ne) {
      try {
        const flowKey = String(payload.flow || "authorize_continue");
        let ctx = globalThis.__debug_ne.get(flowKey);
        if (!ctx) {
          ctx = {};
          globalThis.__debug_ne.set(flowKey, ctx);
        }
        // 注入 challenge 到 sessionObserver 需要的缓存槽位
        ctx.cachedSOChatReq = challenge;
        ctx.sessionObserverCollectorActive = false;

        if (
          challenge &&
          challenge.so &&
          challenge.so.required &&
          typeof challenge.so.collector_dx === "string"
        ) {
          const soToken = await globalThis.SentinelSDK.sessionObserverToken(flowKey);
          if (soToken) {
            const obj =
              typeof soToken === "string" ? JSON.parse(soToken) : soToken;
            so = (obj && obj.so) || null;
          }
        }
      } catch (_) {}
    }
    return { final_p: finalP, t: tValue, so: so };
  }

  throw new Error(`unsupported action: ${payload.action}`);
}

(async () => {
  try {
    const payload = JSON.parse(String(globalThis.__payload_json || "{}"));
    const sdkSource = String(globalThis.__sdk_source || "");
    const result = await run(payload, sdkSource);
    globalThis.__vm_output_json = JSON.stringify(result);
  } catch (error) {
    const detail = {
      name: error && error.name ? String(error.name) : "Error",
      message: error && error.message ? String(error.message) : String(error),
      stack: error && error.stack ? String(error.stack) : String(error),
    };
    const message = `${detail.name}: ${detail.message}\n${detail.stack}`;
    globalThis.__vm_error = message;
  } finally {
    globalThis.__vm_done = true;
  }
})();
