from __future__ import annotations

import random
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .paypal_graphql import graphql_checkoutweb
from .paypal_login import fetch_paypal_otp
from .runtime import (
    LogFn,
    PayPalHttpError,
    card_type,
    emit,
    find_key_recursive,
    first_match,
    gen_paypal_password,
    generate_fn_sync_data,
    luhn_check_digit,
    phone_country_code,
    query_value,
    short_random_id,
    strip_phone_country_code,
    utc_year,
)


SIGNUP_TERMS_CONTENT_ID = "US:en:f411614ea3eaac38abc54763fcfca00e:compliance.signupTerms"


def paypal_signup_url(http: Any, approve_url: str, ba_token: str, log: LogFn | None) -> str:
    parsed = urlparse(approve_url)
    query = parse_qs(parsed.query)
    if "ssrt" not in query:
        query["ssrt"] = [str(int(time.time() * 1000))]
    query.setdefault("ul", ["1"])
    query.setdefault("modxo_redirect_reason", ["guest_user"])
    query.setdefault("ulOnboardRedirect", ["true"])
    query["ba_token"] = [ba_token]
    query.setdefault("locale.x", ["en_US"])
    query.setdefault("country.x", ["US"])
    url = "https://www.paypal.com/agreements/approve?" + urlencode({k: v[-1] for k, v in query.items()})
    resp = http.get(url, allow_redirects=False, timeout=30)
    location = resp.headers.get("Location") or resp.headers.get("location") or ""
    emit(log, f"paypal_http: paypal guest approve redirect status={resp.status_code}")
    if location:
        return urljoin("https://www.paypal.com", location)
    return str(getattr(resp, "url", "") or url)


def paypal_guest_signup_authorize(
    http: Any,
    approve_url: str,
    approve_html: str,
    ba_token: str,
    paypal_cfg: dict[str, Any],
    log: LogFn | None,
    authorize_from_hermes_fn,
) -> dict[str, Any]:
    phone_raw = str(paypal_cfg.get("phone") or paypal_cfg.get("phone_number") or "").strip()
    smsurl = str(paypal_cfg.get("smsurl") or paypal_cfg.get("sms_url") or "").strip()
    if not phone_raw or not smsurl:
        raise PayPalHttpError("PayPal guest signup 缺少 phone/smsurl")
    phone_country = str(paypal_cfg.get("phone_country") or paypal_cfg.get("country") or "US").upper()
    phone_country_code_value = str(paypal_cfg.get("phone_country_code") or phone_country_code(phone_country))
    phone_number = strip_phone_country_code(phone_raw, phone_country_code_value)
    country = str(paypal_cfg.get("country") or "US").upper()
    lang = str(paypal_cfg.get("lang") or "en")
    runtime_ctx = paypal_cfg.get("_runtime") if isinstance(paypal_cfg.get("_runtime"), dict) else {}

    signup_url = paypal_signup_url(http, approve_url, ba_token, log)
    signup_resp = http.get(signup_url, allow_redirects=True, timeout=30)
    signup_url = str(signup_resp.url)
    html = signup_resp.text or approve_html
    ec_token = query_value(signup_url, "token") or first_match([r"(EC-[A-Z0-9]{17,})", r"(EC-[A-Z0-9-]{17,})"], html)
    if not ec_token:
        raise PayPalHttpError("PayPal checkoutweb signup 未返回 EC token")
    emit(log, f"paypal_http: paypal guest signup ec={bool(ec_token)}")

    ctx_id = first_match([r'"ctxId"\s*:\s*"([^"]+)"', r'"ctx_id"\s*:\s*"([^"]+)"'], html)

    graphql_checkoutweb(http, _deferred_feature_payload(ec_token, country), referer=signup_url, ec_token=ec_token, country=country, label="paypal DeferredFeature")
    graphql_checkoutweb(http, _griffin_metadata_payload(country, lang), referer=signup_url, ec_token=ec_token, country=country, label="paypal GriffinMetadataQuery")
    graphql_checkoutweb(http, _checkout_session_payload(ec_token), referer=signup_url, ec_token=ec_token, country=country, label="paypal CheckoutSessionDataQuery")

    signup_email = _signup_email(paypal_cfg)
    _otp_challenge_check(http, signup_url=signup_url, signup_html=html, email=signup_email, ec_token=ec_token, ctx_id=ctx_id, country=country, log=log)

    if runtime_ctx.get("paypal_address_autocomplete"):
        _paypal_address_autocomplete(http, signup_url, paypal_cfg, runtime_ctx, country, lang, ec_token)

    init_data = graphql_checkoutweb(
        http,
        _initiate_phone_payload(ec_token, phone_number, phone_country, country, lang),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    phone_state = _extract_phone_confirmation(init_data, require_auth_ids=True)
    otp = fetch_paypal_otp({**paypal_cfg, "otp_file": paypal_cfg.get("otp_file") or "", "smsurl": smsurl}, timeout=int(paypal_cfg.get("otp_timeout") or 90), log=log)
    if not otp:
        raise PayPalHttpError("PayPal phone OTP 获取失败")
    confirm_data = graphql_checkoutweb(
        http,
        _confirm_phone_payload(ec_token, phone_state["authId"], phone_state["challengeId"], otp),
        referer=signup_url,
        ec_token=ec_token,
        country=country,
        label="paypal ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
    )
    confirm_state = _extract_phone_confirmation(confirm_data, require_auth_ids=False)
    if confirm_state["state"].upper() != "CONFIRMED":
        raise PayPalHttpError(f"PayPal phone confirmation 未通过 state={confirm_state['state']!r}: {confirm_data}")

    signup_payload = _signup_payload(ec_token, paypal_cfg, phone_number, phone_country_code_value, country, signup_email)
    signup_data = graphql_checkoutweb(http, signup_payload, referer=signup_url, ec_token=ec_token, country=country, label="paypal SignUpNewMemberMutation")
    access_token = _extract_buyer_access_token(signup_data)
    if access_token:
        http.headers.update({"Authorization": f"Bearer {access_token}"})

    drop_resp = http.get("https://www.paypal.com/checkoutweb/drop", headers={"Referer": signup_url}, allow_redirects=True, timeout=30)
    hermes_url = str(drop_resp.url)
    if "/webapps/hermes" not in hermes_url:
        hermes_url = _hermes_url(signup_url, ba_token, ec_token)
    return authorize_from_hermes_fn(http, hermes_url, ba_token, log)


def _otp_challenge_check(
    http: Any,
    *,
    signup_url: str,
    signup_html: str,
    email: str,
    ec_token: str,
    ctx_id: str,
    country: str,
    log: LogFn | None,
) -> None:
    """Best-effort idapps/graphql getOtpChallengeOperation probe.

    Matches the HAR flow: PayPal checks whether the email is already registered
    before the phone OTP step. Failure here is non-fatal; we log and continue.
    """
    csrf = first_match([
        r'"csrfNonce"\s*:\s*"([^"]+)"',
        r'name="_csrfNonce"\s+value="([^"]+)"',
        r'"csrf_nonce"\s*:\s*"([^"]+)"',
    ], signup_html)
    if not csrf or not email:
        emit(log, "paypal_http: idapps otp_challenge skipped (no csrfNonce or email)")
        return
    payload = {
        "operationName": "getOtpChallengeOperation",
        "query": "",
        "csrfNonce": csrf,
        "variables": {
            "clientInfo": {
                "fnId": ec_token,
                "ctxId": ctx_id or "",
                "rData": "%7B%7D",
            },
            "credentials": {
                "credentialValue": email,
                "credentialType": "EMAIL",
            },
            "challengeInfo": {"autoSmsOtp": False},
        },
        "fn_sync_data": generate_fn_sync_data(email),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "fetch",
        "x-app-name": "checkoutuinodeweb_weasley",
        "paypal-client-context": ec_token,
        "paypal-client-metadata-id": ec_token,
        "x-country": country,
        "x-locale": "en_US",
        "Origin": "https://www.paypal.com",
        "Referer": signup_url,
    }
    try:
        resp = http.post("https://www.paypal.com/idapps/graphql", json=payload, headers=headers, timeout=30)
        emit(log, f"paypal_http: idapps otp_challenge status={resp.status_code}")
    except Exception as exc:
        emit(log, f"paypal_http: idapps otp_challenge skipped: {exc}", level="warning")


def _signup_email(paypal_cfg: dict[str, Any]) -> str:
    return str(
        paypal_cfg.get("signup_email")
        or paypal_cfg.get("guest_email")
        or paypal_cfg.get("email")
        or f"ctf{uuid.uuid4().hex[:10]}@example.com"
    )


def _paypal_address_autocomplete(
    http: Any,
    signup_url: str,
    paypal_cfg: dict[str, Any],
    runtime_ctx: dict[str, Any],
    country: str,
    lang: str,
    ec_token: str,
) -> None:
    address = _signup_address(paypal_cfg, str(paypal_cfg.get("first_name") or "Jealous"), str(paypal_cfg.get("last_name") or "Lane"), country)
    line1 = str(address.get("line1") or "")
    session_id = str(paypal_cfg.get("address_session_id") or runtime_ctx.get("paypal_address_session_id") or short_random_id())
    location = str(paypal_cfg.get("address_location") or runtime_ctx.get("paypal_address_location") or "43.110,-88.070")
    place_id = str(paypal_cfg.get("address_place_id") or runtime_ctx.get("paypal_address_place_id") or "")
    if len(line1) > 1:
        graphql_checkoutweb(http, _address_autocomplete_payload(line1[:-1], country, lang, session_id, location), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressAutocompleteQuery")
    if line1:
        suggestions = graphql_checkoutweb(http, _address_autocomplete_payload(line1, country, lang, session_id, location), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressAutocompleteQuery")
        if not place_id:
            place_id = str(find_key_recursive(suggestions, "placeId") or "")
    if place_id:
        graphql_checkoutweb(http, _address_place_payload(place_id, lang, session_id), referer=signup_url, ec_token=ec_token, country=country, label="paypal AddressFromAutocompletePlaceIdQuery")


def _deferred_feature_payload(ec_token: str, country: str) -> dict[str, Any]:
    return {
        "operationName": "DeferredFeature",
        "variables": {
            "channel": "WEB",
            "countryCodeAsString": country,
            "integrationType": "XoSignupAuth",
            "isBaslAsString": "false",
            "isForcedGuest": "false",
            "token": ec_token,
        },
        "query": (
            "query DeferredFeature($channel: String!, $countryCodeAsString: String!, "
            "$isBaslAsString: String!, $isForcedGuest: String!, $token: String!, "
            "$integrationType: String!) { otpLoginContext(token: $token, integrationType: $integrationType) "
            "{ __typename context } elmoExperiment(app: \"checkoutuinodeweb\" filters: "
            "[{key: \"Country\", value: $countryCodeAsString}, {key: \"Channel\", value: $channel}, "
            "{key: \"IsBasl\", value: $isBaslAsString}, {key: \"IsGuestOnly\", value: $isForcedGuest}] "
            "res: \"weasley:deferredFeature:memberAsDefault\") { __typename treatments { __typename "
            "experimentId experimentName factors { __typename key value } treatmentId treatmentName } } }"
        ),
    }


def _griffin_metadata_payload(country: str, lang: str) -> dict[str, Any]:
    return {
        "operationName": "GriffinMetadataQuery",
        "variables": {"countryCode": country, "languageCode": lang, "shippingCountryCode": country},
        "query": (
            "query GriffinMetadataQuery($countryCode: CountryCodes!, $languageCode: CheckoutContentLanguageCode!, "
            "$shippingCountryCode: CountryCodes!) { localeMetadata { address(countryCode: $countryCode, "
            "languageCode: $languageCode) { ...AddressMetadata __typename } shippingAddress: address("
            "countryCode: $shippingCountryCode languageCode: $languageCode) { ...AddressMetadata __typename } "
            "currencyCode(countryCode: $countryCode) date(countryCode: $countryCode, languageCode: $languageCode) "
            "{ displayFormat datePattern __typename } phone(countryCode: $countryCode) { masks { mobile "
            "__typename } patterns { default __typename } __typename } territories(countryCode: $countryCode, "
            "languageCode: $languageCode) { code internationalDialingCode name region suggestedDefaultLanguage "
            "__typename } __typename } } fragment AddressMetadata on LocaleAddressMetadata { layout { maxLength "
            "minLength isRequired name regex __typename } strings { cityLabel line1Label line2Label optionalLabel "
            "postcodeLabel stateLabel stateList { displayText value __typename } __typename } __typename }"
        ),
    }


def _checkout_session_payload(ec_token: str) -> dict[str, Any]:
    return {
        "operationName": "CheckoutSessionDataQuery",
        "variables": {"token": ec_token},
        "query": (
            "query CheckoutSessionDataQuery($token: String!) { checkoutSession(token: $token) { "
            "cart { billingAddress { city country line1 line2 postalCode state formattedFullAddress __typename } "
            "email { stringValue __typename } payer { name { familyName givenName __typename } __typename } "
            "formattedPhoneNumber(shouldValidate: true, useInternationalFormat: true) "
            "phoneNumber(shouldValidate: true, stripDialingCode: true) __typename } "
            "checkoutSessionType merchant { country merchantId name __typename } __typename } }"
        ),
    }


def _address_autocomplete_payload(line1: str, country: str, lang: str, session_id: str, location: str) -> dict[str, Any]:
    return {
        "operationName": "AddressAutocompleteQuery",
        "variables": {
            "count": 4,
            "countries": [country],
            "input": line1,
            "language": lang,
            "radius": 1500,
            "sessionId": session_id,
            "location": location,
        },
        "query": (
            "query AddressAutocompleteQuery($count: Int, $countries: [CountryCodes], $input: String!, "
            "$language: CheckoutContentLanguageCode, $location: GeoLocation, $radius: Int, $sessionId: String!) "
            "{ addressAutoComplete(count: $count countries: $countries input: $input language: $language "
            "location: $location radius: $radius sessionId: $sessionId) { suggestions { addressText mainText "
            "placeId secondaryText __typename } __typename } }"
        ),
    }


def _address_place_payload(place_id: str, lang: str, session_id: str) -> dict[str, Any]:
    return {
        "operationName": "AddressFromAutocompletePlaceIdQuery",
        "variables": {"language": lang, "placeId": place_id, "sessionId": session_id},
        "query": (
            "query AddressFromAutocompletePlaceIdQuery($language: CheckoutContentLanguageCode, $placeId: ID!, "
            "$sessionId: String!) { addressFromAutoCompletePlaceId(language: $language placeId: $placeId "
            "sessionId: $sessionId) { address { line1 line2 city state postalCode country __typename } __typename } }"
        ),
    }


def _initiate_phone_payload(ec_token: str, phone: str, phone_country: str, country: str, lang: str) -> dict[str, Any]:
    return {
        "operationName": "InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
        "variables": {
            "locale": {"country": country, "lang": lang},
            "phoneCountry": phone_country,
            "phoneNumber": phone,
            "token": ec_token,
        },
        "query": (
            "mutation InitiateRiskBasedTwoFactorPhoneConfirmationMutation($phoneNumber: String!, "
            "$locale: LocaleInput!, $phoneCountry: CountryCodes!, $token: String!) { "
            "initiateRiskBasedTwoFactorPhoneConfirmation(locale: $locale phoneCountry: $phoneCountry "
            "phoneNumber: $phoneNumber token: $token) { authId challengeId state __typename } }"
        ),
    }


def _confirm_phone_payload(ec_token: str, auth_id: str, challenge_id: str, otp: str) -> dict[str, Any]:
    return {
        "operationName": "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
        "variables": {"authId": auth_id, "challengeId": challenge_id, "pin": otp, "token": ec_token},
        "query": (
            "mutation ConfirmRiskBasedTwoFactorPhoneConfirmationMutation($pin: String!, $authId: String!, "
            "$challengeId: String!, $token: String!) { confirmRiskBasedTwoFactorPhoneConfirmation("
            "pin: $pin authId: $authId challengeId: $challengeId token: $token) "
            "{ authId challengeId state __typename } }"
        ),
    }


_SIGNUP_QUERY = (
    "mutation SignUpNewMemberMutation($bank: BankAccountInput, $billingAddress: AddressInput, "
    "$card: CardInput, $contentIdentifier: String, $country: CountryCodes, "
    "$countrySpecificFirstName: String, $countrySpecificLastName: String, "
    "$crsData: CommonReportingStandardsInput, $currencyConversionType: CheckoutCurrencyConversionType, "
    "$dateOfBirth: DateOfBirth, $email: String!, $firstName: String!, $gender: Gender, "
    "$identityDocument: IdentityDocumentInput, $lastName: String!, $middleName: String, "
    "$marketingOptOut: Boolean, $nationality: CountryCodes, $occupation: Occupation, "
    "$password: String, $phone: PhoneInput!, $placeOfBirth: CountryCodes, "
    "$secondaryIdentityDocument: IdentityDocumentInput, $selectedInstallmentOption: InstallmentsInput, "
    "$shareAddressWithDonatee: Boolean, $shippingAddress: AddressInput, "
    "$supportedThreeDsExperiences: [ThreeDSPaymentExperience], $token: String!, "
    "$residentialAddress: AddressInput, $isSignupIncentiveOptIn: Boolean, "
    "$isSignupIncentiveOptInStretch: Boolean, $legalAgreements: LegalAgreementsInput, "
    "$collectedConsents: [CollectedConsent]) { "
    "onboardAccount: signUpNewMember(bank: $bank billingAddress: $billingAddress card: $card "
    "contentIdentifier: $contentIdentifier countrySpecificFirstName: $countrySpecificFirstName "
    "countrySpecificLastName: $countrySpecificLastName country: $country crsData: $crsData "
    "currencyConversionType: $currencyConversionType dateOfBirth: $dateOfBirth email: $email "
    "firstName: $firstName gender: $gender identityDocument: $identityDocument lastName: $lastName "
    "middleName: $middleName marketingOptOut: $marketingOptOut nationality: $nationality "
    "occupation: $occupation password: $password phone: $phone placeOfBirth: $placeOfBirth "
    "secondaryIdentityDocument: $secondaryIdentityDocument selectedInstallmentOption: $selectedInstallmentOption "
    "shareAddressWithDonatee: $shareAddressWithDonatee shippingAddress: $shippingAddress token: $token "
    "residentialAddress: $residentialAddress isSignupIncentiveOptIn: $isSignupIncentiveOptIn "
    "isSignupIncentiveOptInStretch: $isSignupIncentiveOptInStretch legalAgreements: $legalAgreements "
    "collectedConsents: $collectedConsents supportedThreeDsExperiences: $supportedThreeDsExperiences) "
    "{ ...buyer flags { is3DSecureRequired __typename } ...fundingOptions "
    "paymentContingencies { ...threeDomainSecure ...threeDSContingencyData __typename } __typename } } "
    "fragment buyer on CheckoutSession { buyer { auth { accessToken __typename } userId __typename } __typename } "
    "fragment fundingOptions on CheckoutSession { fundingOptions { allPlans { fundingSources { fundingInstrument "
    "{ id __typename } amount { currencyCode currencyValue __typename } __typename } fundingContingencies "
    "{ ... on OpenBankingContingency { encryptedId contingencyReasons contingencyType __typename } __typename } "
    "__typename } fundingInstrument { id lastDigits name nameDescription type __typename } __typename } __typename } "
    "fragment threeDomainSecure on PaymentContingencies { threeDomainSecure(experiences: $supportedThreeDsExperiences) "
    "{ status redirectUrl { href __typename } method parameter experience requestParams { key value __typename } "
    "__typename } __typename } "
    "fragment threeDSContingencyData on PaymentContingencies { threeDSContingencyData { name causeName __typename } "
    "__typename }"
)


def _signup_payload(
    ec_token: str,
    paypal_cfg: dict[str, Any],
    phone_number: str,
    phone_country_code_value: str,
    country: str,
    email: str,
) -> dict[str, Any]:
    first_name = str(paypal_cfg.get("first_name") or "Jealous")
    last_name = str(paypal_cfg.get("last_name") or "Lane")
    password = str(paypal_cfg.get("signup_password") or paypal_cfg.get("guest_password") or paypal_cfg.get("password") or gen_paypal_password())
    card = _signup_card(paypal_cfg)
    address = _signup_address(paypal_cfg, first_name, last_name, country)
    return {
        "operationName": "SignUpNewMemberMutation",
        "variables": {
            "card": card,
            "country": country,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "phone": {"countryCode": phone_country_code_value, "number": phone_number, "type": "MOBILE"},
            "supportedThreeDsExperiences": ["IFRAME"],
            "token": ec_token,
            "billingAddress": address,
            "shippingAddress": {
                "line1": "",
                "city": "",
                "state": "",
                "postalCode": "",
                "accountQuality": {"autoCompleteType": "MANUAL", "isUserModified": False},
                "country": country,
                "familyName": last_name,
                "givenName": first_name,
            },
            "contentIdentifier": str(paypal_cfg.get("content_identifier") or SIGNUP_TERMS_CONTENT_ID),
            "marketingOptOut": bool(paypal_cfg.get("marketing_opt_out") or False),
            "password": password,
            "crsData": None,
            "legalAgreements": {},
        },
        "query": _SIGNUP_QUERY,
        "fn_sync_data": generate_fn_sync_data(email, password),
    }


def _signup_card(paypal_cfg: dict[str, Any]) -> dict[str, str]:
    card = paypal_cfg.get("card") if isinstance(paypal_cfg.get("card"), dict) else {}
    number = str(card.get("number") or paypal_cfg.get("card_number") or "").replace(" ", "")
    exp_month = str(card.get("exp_month") or paypal_cfg.get("card_exp_month") or "").zfill(2)
    exp_year = str(card.get("exp_year") or paypal_cfg.get("card_exp_year") or "")
    cvv = str(card.get("cvv") or paypal_cfg.get("card_cvv") or "")
    if not number or not exp_month.strip("0") or not exp_year or not cvv:
        generated = _generate_signup_card()
        number = generated["number"]
        exp_month = generated["exp_month"]
        exp_year = generated["exp_year"]
        cvv = generated["cvv"]
    if len(exp_year) == 2:
        exp_year = "20" + exp_year
    return {
        "cardNumber": number,
        "expirationDate": f"{exp_month}/{exp_year}",
        "securityCode": cvv,
        "type": str(card.get("type") or paypal_cfg.get("card_type") or card_type(number)),
    }


def _generate_signup_card() -> dict[str, str]:
    base = "4147"
    while len(base) < 15:
        base += str(random.randint(0, 9))
    year = utc_year() + 2 + random.randint(0, 3)
    return {
        "number": base + luhn_check_digit(base),
        "exp_month": str(random.randint(1, 12)).zfill(2),
        "exp_year": str(year),
        "cvv": str(random.randint(100, 999)),
    }


def _signup_address(paypal_cfg: dict[str, Any], first_name: str, last_name: str, country: str) -> dict[str, Any]:
    billing = paypal_cfg.get("billing") if isinstance(paypal_cfg.get("billing"), dict) else {}
    return {
        "line1": str(billing.get("line1") or paypal_cfg.get("billing_line1") or "Driftwood Court"),
        "city": str(billing.get("city") or paypal_cfg.get("billing_city") or "Brookfield"),
        "state": str(billing.get("state") or paypal_cfg.get("billing_state") or "WI"),
        "postalCode": str(billing.get("postal_code") or billing.get("postalCode") or paypal_cfg.get("billing_postal") or "53005"),
        "accountQuality": {"autoCompleteType": "GOOGLE", "isUserModified": False},
        "country": str(billing.get("country") or country),
        "familyName": last_name,
        "givenName": first_name,
    }


def _extract_phone_confirmation(payload: Any, *, require_auth_ids: bool) -> dict[str, str]:
    """Pull authId/challengeId/state from initiate or confirm responses.

    `require_auth_ids` should be True for the initiate response (we need both
    ids to call confirm next), False for the confirm response (server returns
    them as null on success; only `state` is meaningful there).
    """
    found = find_key_recursive(payload, "initiateRiskBasedTwoFactorPhoneConfirmation") or find_key_recursive(payload, "confirmRiskBasedTwoFactorPhoneConfirmation")
    if not isinstance(found, dict):
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少确认状态: {payload}")
    auth_id = str(found.get("authId") or "")
    challenge_id = str(found.get("challengeId") or "")
    state = str(found.get("state") or "")
    if require_auth_ids and (not auth_id or not challenge_id):
        raise PayPalHttpError(f"PayPal phone confirmation 响应缺少 authId/challengeId: {payload}")
    return {"authId": auth_id, "challengeId": challenge_id, "state": state}


def _extract_buyer_access_token(payload: Any) -> str:
    value = find_key_recursive(payload, "accessToken")
    return str(value or "")


def _hermes_url(signup_url: str, ba_token: str, ec_token: str) -> str:
    parsed = urlparse(signup_url)
    q = parse_qs(parsed.query)
    q.update({
        "ba_token": [ba_token],
        "token": [ec_token],
        "fromSignupLite": ["true"],
        "addFIContingency": ["noretry"],
        "redirectToHermes": ["true"],
        "fallback": ["1"],
        "reason": ["Q0FSRF9HRU5FUklDX0VSUk9S"],
    })
    return "https://www.paypal.com/webapps/hermes?" + urlencode({k: v[-1] for k, v in q.items()})
