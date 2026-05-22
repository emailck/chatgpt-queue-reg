# PayPal HAR → pure-protocol implementation notes

This note records the verified mapping between the captured PayPal HAR at `C:\Users\i\project\har-gpt-reg\pp.har\har.har`, the CTF-pay pure-protocol implementation at `C:\Users\i\project\ggpt\gap\CTF-reg\paypal_plus\signup.py`, and the current queue implementation under `backend/integrations/chatgpt/paypal/`.

The useful conclusion is: a PayPal pure-protocol flow is not just the three checkoutweb GraphQL mutations. The stable flow is a browser state machine replay split into canonical URL handling, GraphQL business calls, risk/telemetry side effects, authchallenge handling, and Hermes authorize finalization.

## Verified HAR main chain

The key HAR rows from `pp.har` form this order:

```text
#0127 GET  https://www.paypal.com/agreements/approve?ba_token=BA-2881255548113043Y
      Referer: https://pay.openai.com/

#0340 GET  https://www.paypal.com/agreements/approve?ssrt=1779120130812&ul=1&modxo_redirect_reason=guest_user&ulOnboardRedirect=true&ba_token=BA-2881255548113043Y&locale.x=en_US&country.x=US
      Status: 302
      Referer: https://www.paypal.com/pay/?ssrt=1779120130812&token=BA-2881255548113043Y&ul=1&paypal_client_cfci=modxo_vaulted_not_recurring-Pay_With_Card&ctxId=05b6ecd2-29c7-44da-b66a-82fa538ed5c2

#0344 GET  https://www.paypal.com/checkoutweb/signup?ssrt=1779120130812&ul=1&modxo_redirect_reason=guest_user&ba_token=BA-2881255548113043Y&locale.x=en_US&country.x=US&token=EC-7YT72177BJ8974616&rcache=1&cookieBannerVariant=hidden
      Status: 200
      Referer: https://www.paypal.com/pay/?ssrt=1779120130812&token=BA-2881255548113043Y&ul=1&paypal_client_cfci=modxo_vaulted_not_recurring-Pay_With_Card&ctxId=05b6ecd2-29c7-44da-b66a-82fa538ed5c2

#0362 POST https://www.paypal.com/graphql?DeferredFeature
#0369 POST https://www.paypal.com/graphql?GriffinMetadataQuery
#0370 POST https://www.paypal.com/graphql?CheckoutSessionDataQuery
#0386 POST https://www.paypal.com/graphql?AddressAutocompleteQuery
#0387 POST https://www.paypal.com/graphql?AddressAutocompleteQuery
#0389 POST https://www.paypal.com/graphql?AddressFromAutocompletePlaceIdQuery
#0391 POST https://www.paypal.com/graphql?InitiateRiskBasedTwoFactorPhoneConfirmationMutation
#0392 POST https://www.paypal.com/graphql?ConfirmRiskBasedTwoFactorPhoneConfirmationMutation
#0393 POST https://www.paypal.com/graphql?SignUpNewMemberMutation
      Common checkoutweb GraphQL headers:
      Referer: https://www.paypal.com/checkoutweb/signup?ssrt=1779120130812&ul=1&modxo_redirect_reason=guest_user&ba_token=BA-2881255548113043Y&locale.x=en_US&country.x=US&token=EC-7YT72177BJ8974616&rcache=1&cookieBannerVariant=hidden
      Origin: https://www.paypal.com
      X-App-Name: checkoutuinodeweb_weasley
      PayPal-Client-Context: EC-7YT72177BJ8974616
      PayPal-Client-Metadata-Id: EC-7YT72177BJ8974616
      X-Country: US
      X-Locale: en_US
      Content-Type: application/json

#0394 GET https://www.paypal.com/checkoutweb/drop
#0395 GET https://www.paypal.com/checkoutweb/drop
      Referer: canonical /checkoutweb/signup URL
      X-PayPal-Internal-EUAT: present

#0396 GET https://www.paypal.com/webapps/hermes?ssrt=1779120130812&ul=1&modxo_redirect_reason=guest_user&ba_token=BA-2881255548113043Y&locale.x=en_US&country.x=US&token=EC-7YT72177BJ8974616&rcache=1&cookieBannerVariant=hidden&fromSignupLite=true&addFIContingency=noretry&redirectToHermes=true&fallback=1&reason=Q0FSRF9HRU5FUklDX0VSUk9S
#0412 GET same Hermes URL
#0413 GET same Hermes URL

#0429 POST https://www.paypal.com/graphql/
      Referer: same Hermes URL plus billingLite=1
      Origin: https://www.paypal.com
      X-App-Name: checkoutuinodeweb
      PayPal-Client-Metadata-Id: 10643ff1-2bd7-409b-9eb5-19059c2a080f
      X-PayPal-Internal-EUAT: present
      Body operation: authorize
```

Important observations from the HAR:

- The first merchant handoff only has `ba_token`.
- The real guest signup page is the canonical `/checkoutweb/signup?...token=EC-...&rcache=1&cookieBannerVariant=hidden` URL.
- Every checkoutweb GraphQL request keeps that signup URL as `Referer`.
- Checkoutweb GraphQL uses `X-App-Name: checkoutuinodeweb_weasley` and both PayPal client context headers set to the EC token.
- The final Hermes authorize uses `/graphql/`, an array body with operation `authorize`, `X-App-Name: checkoutuinodeweb`, and EUAT.
- The HAR uses a fallback/billingLite Hermes URL after signup (`fallback=1`, `reason=Q0FSRF9HRU5FUklDX0VSUk9S`, then `billingLite=1` on the authorize referer).

## Verified HAR risk/telemetry side effects

The HAR also contains many non-business PayPal requests before and around signup:

```text
c.paypal.com/da/r/fb_fp.js
c.paypal.com/v1/r/d/b/p1
c.paypal.com/v1/r/d/b/p2
c.paypal.com/v1/r/d/b/w
ddbm2.paypal.com/tags.js
ddbm2.paypal.com/js/
www.paypal.com/identity/di/log
www.paypal.com/auth/logclientdata
www.paypal.com/auth/verifyhcaptchapassive
www.paypal.com/pay/api/trpc/observability.handleClientEmit
```

Verified examples:

```text
#0180 POST c.paypal.com/v1/r/d/b/p1
#0181 POST c.paypal.com/v1/r/d/b/p2
#0182 POST c.paypal.com/v1/r/d/b/w
#0201 GET  ddbm2.paypal.com/tags.js
#0209 POST ddbm2.paypal.com/js/
#0212 POST www.paypal.com/identity/di/log
#0227 POST www.paypal.com/auth/logclientdata
#0371 GET  ddbm2.paypal.com/tags.js
#0375 POST ddbm2.paypal.com/js/
#0376 GET  c.paypal.com/da/r/fb_fp.js
#0380 POST c.paypal.com/v1/r/d/b/p1
#0381 POST c.paypal.com/v1/r/d/b/p2
#0383 POST c.paypal.com/v1/r/d/b/w
#0423 POST c.paypal.com/v1/r/d/b/p1
#0424 POST c.paypal.com/v1/r/d/b/p2
#0426 POST c.paypal.com/v1/r/d/b/w
#0427 POST c.paypal.com/v1/r/d/b/pa
#0428 POST ddbm2.paypal.com/js/
```

These are not the direct signup API, but they are the browser side-effect envelope. CTF-pay treats them as best-effort but important because missing FraudNet/DataDome/Weasley-style signals correlates with `OAS_ERROR`, authchallenge, or `SignUpNewMemberMutation` returning no useful EUAT.

## CTF-pay implementation mapping

Reference file: `C:\Users\i\project\ggpt\gap\CTF-reg\paypal_plus\signup.py`.

### Canonical onboarding URL

CTF-pay implements this with:

- `_build_onboard_url(...)`
- `_coerce_onboard_url(...)`

Verified behavior from code:

```python
params.extend([
    ("ul", "1"),
    ("country.x", locale_country),
    ("locale.x", f"{locale_lang}_{locale_country}"),
    ("modxo_redirect_reason", "guest_user"),
    ("ulOnboardRedirect", "true"),
    ("ba_token", ba_token),
])
```

This matches HAR row `#0340` and avoids using a later `/webapps/hermes?...ulOnboardRedirect=true` URL as if it were the signup entry. The code explicitly warns that capturing a Hermes URL here can poison later GraphQL referers.

### Canonical signup URL and referer preservation

CTF-pay implements this with:

- `_build_signup_url(...)`
- `_prime_checkout_signup(...)`

Verified behavior from code:

```python
params.extend([
    ("ul", "1"),
    ("country.x", locale_country),
    ("locale.x", f"{locale_lang}_{locale_country}"),
    ("modxo_redirect_reason", "guest_user"),
    ("ba_token", ba_token),
    ("token", ec_token),
    ("rcache", "1"),
    ("cookieBannerVariant", "hidden"),
])
```

This matches HAR row `#0344`. `_prime_checkout_signup(...)` uses `allow_redirects=False` and keeps the canonical signup URL if PayPal redirects away, because the HAR shows checkoutweb GraphQL referer remains `/checkoutweb/signup?...` through `#0362` to `#0393`.

### Checkoutweb GraphQL wrapper

CTF-pay implements this with `_gql(...)`.

Verified code headers:

```python
"Origin": PP_ORIGIN,
"Referer": signup_url,
"X-Requested-With": "fetch",
"X-App-Name": "checkoutuinodeweb_weasley",
"PayPal-Client-Context": token,
"PayPal-Client-Metadata-Id": token,
"X-Country": str(country),
"X-Locale": "en_US" if str(country).upper() == "US" else f"en_{str(country).upper()}",
"Sec-Fetch-Site": "same-origin",
"Sec-Fetch-Mode": "cors",
"Sec-Fetch-Dest": "empty",
```

This matches the HAR GraphQL rows `#0362` through `#0393`. CTF-pay additionally includes detailed `Sec-CH-UA*` headers and uses `curl_cffi` Chrome impersonation where available.

### GraphQL business sequence

CTF-pay top-level `signup_no_card(...)` runs:

1. `DeferredFeature`
2. `GriffinMetadataQuery`
3. `CheckoutSessionDataQuery`
4. optional idapps/authchallenge logic
5. `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
6. SMS poll
7. `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
8. `SignUpNewMemberMutation`
9. `/checkoutweb/drop`
10. `/webapps/hermes`
11. `/graphql/` `authorize`

This is the same business chain visible in the HAR, except CTF-pay may skip address autocomplete by using manual address variables. That is consistent: the HAR has `AddressAutocompleteQuery` and `AddressFromAutocompletePlaceIdQuery`, but CTF-pay notes its userscript path uses manual address fields, so those requests are not strictly required if the final signup variables represent a manual address.

### PayPal risk side effects

CTF-pay implements the HAR side effects with these functions:

- `_paypal_pay_pre_onboard_warmup(...)`
- `_paypal_identity_di_log(...)`
- `_paypal_fraudnet_warmup(...)`
- `_paypal_ddbm2_node_warmup(...)`
- `_paypal_fraudnet_field_events(...)`
- `_paypal_weasley_log(...)`

Verified mapping:

- HAR `/pay/api/trpc/observability.handleClientEmit` → `_paypal_pay_pre_onboard_warmup(...)` / `_paypal_pay_observability_emit(...)`
- HAR `/identity/di/log` → `_paypal_identity_di_log(...)`
- HAR `c.paypal.com/v1/r/d/b/p1,p2,w` → `_paypal_fraudnet_warmup(...)`
- HAR `ddbm2.paypal.com/tags.js` + `/js/` → `_paypal_ddbm2_node_warmup(...)`
- Browser field typing FraudNet beacons → `_paypal_fraudnet_field_events(...)`
- Weasley client events → `_paypal_weasley_log(...)`

Important distinction: the HAR proves these side effects occur, but not all payload-generation logic is directly derivable from one HAR. CTF-pay contains extra reconstruction logic, especially for ddbm2 `jspl`, FraudNet browser environment payloads, and field timing beacons.

### Authchallenge / captcha branch

CTF-pay handles GraphQL returning HTML/captcha via:

- `CaptchaRequired`
- `_validate_paypal_authchallenge(...)`
- `_validate_paypal_hcaptcha_passive(...)`
- `_validate_paypal_recaptcha(...)`

This is not the happy-path GraphQL chain, but it is necessary for pure protocol because a PayPal node may return `authchallenge` HTML instead of JSON. The code catches this in `_gql(...)` when JSON parse fails and the response contains `authchallenge`, `recaptcha`, or `captcha`.

### Signup partial success and billingLite fallback

CTF-pay implements this with:

- `_signup_response_parts(...)`
- `_is_retryable_create_member_account_error(...)`
- the Hermes fallback parameter construction in `signup_no_card(...)`

Verified behavior:

- If signup returns errors but also EUAT, CTF-pay continues via billingLite.
- If signup returns no EUAT and matches narrow createMember/OAS retry conditions, it can rotate persona/address and retry.
- If EUAT is present, the final path is:
  - GET `/checkoutweb/drop` with `X-PayPal-Internal-EUAT`
  - GET `/webapps/hermes?...fromSignupLite=true...`
  - POST `/graphql/` operation `authorize` with `X-App-Name: checkoutuinodeweb` and `X-PayPal-Internal-EUAT`

This matches HAR rows `#0394` through `#0429`, including fallback/billingLite behavior.

## Current queue implementation comparison

Current queue pure-protocol file: `backend/integrations/chatgpt/paypal/paypal_guest_signup.py`.

Verified current behavior:

- `paypal_guest_signup_authorize(...)` builds/loads a signup URL, extracts EC token, then calls:
  - `DeferredFeature`
  - `GriffinMetadataQuery`
  - `CheckoutSessionDataQuery`
  - optional `_otp_challenge_check(...)`
  - `InitiateRiskBasedTwoFactorPhoneConfirmationMutation`
  - `ConfirmRiskBasedTwoFactorPhoneConfirmationMutation`
  - `SignUpNewMemberMutation`
  - `/checkoutweb/drop`
- It extracts `buyer.auth.accessToken` and stores it as `x-paypal-internal-euat` on the HTTP session.
- It currently does not complete HTTP authorize in that function; it logs `skipping HTTP authorize (non-browser sessions always ANONYMOUS), using browser` and calls `browser_authorize_from_hermes(...)`.

Current queue GraphQL wrapper: `backend/integrations/chatgpt/paypal/paypal_graphql.py`.

Verified current checkoutweb headers:

```python
"x-app-name": "checkoutuinodeweb_weasley",
"paypal-client-context": ec_token,
"paypal-client-metadata-id": ec_token,
"x-country": country,
"x-locale": locale,
"Origin": "https://www.paypal.com",
"Referer": referer,
```

This matches the core HAR GraphQL requirements, but the queue wrapper is simpler than CTF-pay: it does not include the fuller Chrome/Sec-CH/Sec-Fetch header envelope, ddbm2, FraudNet, Weasley logger, or authchallenge replay logic.

Current browser fallback file: `backend/integrations/chatgpt/paypal/paypal_browser_authorize.py`.

Verified functions now present:

- `_goto_paypal_onboard_redirect(...)`
- `_build_paypal_onboard_redirect_url(...)`
- `_install_otp_graphql_watch(...)`
- `_complete_signup_with_graphql_fallback(...)`
- `_browser_graphql_checkoutweb(...)`
- `_browser_checkoutweb_drop(...)`
- `_wait_for_post_signup_progress(...)`
- `_paypal_authorize_from_billing_runtime(...)`

This means the browser path now has pieces of the CTF-pay insight: it can recover `/pay` stalls into onboard redirect, watch OTP GraphQL, run direct browser-session GraphQL fallback, treat Hermes as progress, and authorize from the billing runtime.

Remaining gap versus CTF-pay: the queue still mostly relies on real browser execution for PayPal JS/risk side effects, while CTF-pay tries to reproduce those side effects in pure HTTP. If we want queue pure protocol to be similarly self-contained, the missing pieces are the risk side-effect layer, authchallenge replay, billingLite partial-success handling in pure HTTP, and canonical signup URL preservation before every GraphQL call.

## How to update protocol from a new HAR

Use this repeatable process.

### 1. Extract the main chain

Filter the HAR by these endpoint families:

```text
/pay
/pay/api/trpc/observability.handleClientEmit
/agreements/approve
/checkoutweb/signup
/graphql
/idapps/graphql
/checkoutweb/drop
/webapps/hermes
/graphql/
/xoplatform/logger
/auth/logclientdata
/auth/validatecaptcha
/auth/verifyhcaptchapassive
c.paypal.com/v1/r/d/b/*
ddbm2.paypal.com/*
```

Record, in order:

- method
- status
- full URL and query keys
- `operationName`
- `Referer`
- `Origin`
- `X-App-Name`
- `PayPal-Client-Context`
- `PayPal-Client-Metadata-Id`
- `X-PayPal-Internal-EUAT`
- `Set-Cookie` changes
- whether body is object or array

### 2. Track token lifecycle

For each new HAR, identify where these appear:

```text
BA token       merchant / PayPal approve-pay context
EC token       checkoutweb signup and GraphQL token
ctxId          /pay runtime state
ssrt           PayPal page/session timestamp
datadome       DataDome cookie
sc_f / ddi     FraudNet cookies/signals
authId         OTP initiate result
challengeId    OTP initiate result
EUAT           signup result / x-paypal-internal-euat
returnURL      authorize result
```

If token location or naming changes, update the protocol layer that owns that token rather than patching random call sites.

### 3. Diff GraphQL operations

For every GraphQL operation, diff:

- path: `/graphql?OperationName` vs `/graphql/`
- body: object vs array
- `operationName`
- variables shape
- query string
- extra top-level fields, especially `fn_sync_data`
- `X-App-Name`
- `Referer`
- PayPal client context headers
- country/locale headers

Rules verified by the current HAR:

- checkoutweb signup GraphQL uses `checkoutuinodeweb_weasley` and canonical signup referer.
- Hermes authorize uses `checkoutuinodeweb`, `/graphql/`, EUAT, and Hermes referer.

### 4. Classify requests before porting

Do not blindly copy every HAR row.

Required state-changing requests:

```text
/agreements/approve
/checkoutweb/signup
DeferredFeature
GriffinMetadataQuery
CheckoutSessionDataQuery
InitiateRiskBasedTwoFactorPhoneConfirmationMutation
ConfirmRiskBasedTwoFactorPhoneConfirmationMutation
SignUpNewMemberMutation
/checkoutweb/drop
/webapps/hermes
/graphql/ authorize
```

High-priority best-effort risk requests:

```text
/pay/api/trpc/observability.handleClientEmit
/identity/di/log
/auth/logclientdata
/auth/verifyhcaptchapassive
c.paypal.com/v1/r/d/b/p1,p2,w,pa
ddbm2.paypal.com/tags.js
ddbm2.paypal.com/js/
xoplatform/logger/api/logger/
```

Usually skippable unless a new HAR proves otherwise:

```text
fonts
images
static JS/CSS chunks
address autocomplete if final signup variables use manual address
```

### 5. Keep protocol code layered

The maintainable shape should be:

```text
bootstrap_paypal_checkout()
  -> build/normalize /agreements/approve onboarding URL
  -> /pay warmup side effects
  -> extract EC

prime_signup_context()
  -> build canonical /checkoutweb/signup URL
  -> GET signup without poisoning GraphQL referer

warm_risk_context()
  -> DeferredFeature / GriffinMetadata / CheckoutSessionData
  -> Weasley logger
  -> FraudNet
  -> ddbm2
  -> idapps/authchallenge if present

run_phone_confirmation()
  -> InitiateRiskBased...
  -> poll OTP
  -> ConfirmRiskBased...

run_signup()
  -> SignUpNewMemberMutation
  -> parse EUAT
  -> continue on partial error with EUAT
  -> retry persona/address only for no-EUAT createMember/OAS bucket

finish_authorization()
  -> /checkoutweb/drop
  -> /webapps/hermes
  -> /graphql/ authorize
  -> extract returnURL
```

## Why HAR is enough to start but not enough to finish

A single successful HAR is enough to derive:

- endpoint order
- token propagation
- GraphQL operation names and variables
- required core headers
- canonical referer behavior
- final drop/Hermes/authorize sequence

A single HAR is not enough to derive all stable pure-protocol logic:

- ddbm2 `jspl` generation
- FraudNet browser environment payload generation
- field timing beacon generation
- hcaptcha/recaptcha authchallenge replay
- which side effects are mandatory versus merely correlated
- retry strategy for OAS/createMemberAccount failures
- whether a signup error with EUAT can continue through billingLite
- behavior under different proxy/country/risk buckets

CTF-pay fills those gaps with implementation experience beyond the HAR. Therefore future work should use HAR as the protocol trace source of truth, but validate each ported step against runtime outcomes and keep risk side effects best-effort until proven mandatory.
