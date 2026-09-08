"""Licensing: entitlement tokens, machine fingerprint, offline activation.

Tier 1. Standard library only. Never imports engine.

Bind the ruleset, not the engine. The engine is a JSON interpreter anyone can
reproduce from the spec; the authored ruleset is the asset. It ships sealed to
a per-install key with an entitlement expiry, so a shared copy goes stale on
its own.

What is real here and what is not, because the gate depends on the difference:

  REAL      The entitlement set. parse_token(), entitlements() against an
            explicit as_of, expiry, seat, tier, and the capability set the
            facade passes to engine.run_ruleset. Fingerprint composition and
            N-of-M matching. The offline challenge file.
  STUBBED   Every cryptographic operation. verify_signature() and
            verify_ruleset_signature() raise NotImplementedError. A token
            produced by issue_unsigned_token() is marked "unsigned" on its
            face and the facade must surface that as a placeholder. Nothing
            in this draft may be mistaken for a working license check.

Ruleset signatures are verified before load, always, once the crypto exists.
That is a safety requirement independent of the commercial one: an agent that
will run an unsigned ruleset can be fed one that passes everything.

No clock. as_of is passed in.
"""

import base64
import hashlib
import json
from datetime import date, datetime

MODULE_VERSION = "2026.09.08"

TIERS = ("one_off", "maintained", "cyso_as_a_service")

# Which capabilities each tier may be entitled to. The token still lists its
# own capabilities explicitly; this is the ceiling, not the grant.
TIER_CEILING = {
    "one_off": {"kev", "criticality"},
    "maintained": {"kev", "criticality", "surveillance"},
    "cyso_as_a_service": {"kev", "criticality", "surveillance", "custody", "asset_alerts"},
}

# Stable identifiers a fingerprint may be composed from. On Windows: the
# machine GUID at HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid, the SMBIOS
# UUID, and a volume serial. Collection is the caller's job (it reads the
# machine); composition and matching are pure and live here.
FINGERPRINT_COMPONENTS = ("machine_guid", "smbios_uuid", "volume_serial")
MATCH_THRESHOLD = 2   # N of M: a NIC swap or a reimage must not lock a customer out


class LicenseError(Exception):
    """Malformed token, wrong shape, or an operation the draft cannot perform."""


# --------------------------------------------------------------------------
# fingerprint
# --------------------------------------------------------------------------

def fingerprint(identifiers):
    """Hash each identifier separately so N-of-M can match component-wise.

    Raw identifiers never leave the machine; only their hashes travel in the
    activation challenge.
    """
    comps = {}
    for name in FINGERPRINT_COMPONENTS:
        value = identifiers.get(name)
        comps[name] = None if not value else hashlib.sha256(
            ("%s:%s" % (name, str(value).strip().lower())).encode("utf-8")).hexdigest()
    present = [c for c in comps.values() if c]
    if len(present) < MATCH_THRESHOLD:
        raise LicenseError("need at least %d of %d identifiers, got %d"
                           % (MATCH_THRESHOLD, len(FINGERPRINT_COMPONENTS), len(present)))
    return {"components": comps,
            "composite": hashlib.sha256("|".join(sorted(present)).encode("ascii")).hexdigest()}


def match(stored, current, threshold=MATCH_THRESHOLD):
    """N-of-M component match between a stored and a current fingerprint."""
    agree = [n for n in FINGERPRINT_COMPONENTS
             if stored["components"].get(n) and stored["components"][n] == current["components"].get(n)]
    return {"matched": len(agree), "required": threshold,
            "ok": len(agree) >= threshold, "components": agree}


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------

TOKEN_FIELDS = ("tenant", "tier", "seat", "install_id", "capabilities",
                "ruleset_entitlement", "expires", "fingerprint_composite")


def encode_token(payload, signature):
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
    return "%s.%s" % (body, signature)


def parse_token(token):
    """Split and decode. Does not verify. Verification is a separate, explicit step."""
    try:
        body, signature = token.split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise LicenseError("malformed token (%s)" % exc)
    missing = [f for f in TOKEN_FIELDS if f not in payload]
    if missing:
        raise LicenseError("token is missing %s" % ", ".join(missing))
    if payload["tier"] not in TIERS:
        raise LicenseError("unknown tier %r" % payload["tier"])
    over = set(payload["capabilities"]) - TIER_CEILING[payload["tier"]]
    if over:
        raise LicenseError("tier %r may not carry %s" % (payload["tier"], sorted(over)))
    return {"payload": payload, "signature": signature}


def verify_signature(payload, signature, public_key):
    """Check the issuer signature. NOT IMPLEMENTED in this draft.

    The intended scheme is an asymmetric signature over the canonical payload
    bytes verified against a public key embedded in the agent. The standard
    library has no Ed25519 or ECDSA primitive, and vendoring one is a decision
    for the infrastructure owners (see QUESTIONS.md). Raising here is the
    honest state: no token in this draft is cryptographically trusted.
    """
    raise NotImplementedError("signature verification is not implemented; "
                              "no token is cryptographically verified in this draft")


def issue_unsigned_token(tenant, tier, seat, install_id, capabilities, ruleset_entitlement,
                         expires, fingerprint_composite):
    """A development token. The signature field says exactly what it is."""
    payload = {
        "tenant": tenant, "tier": tier, "seat": seat, "install_id": install_id,
        "capabilities": sorted(capabilities), "ruleset_entitlement": ruleset_entitlement,
        "expires": expires, "fingerprint_composite": fingerprint_composite,
        "issued_by": "issue_unsigned_token", "module_version": MODULE_VERSION,
    }
    parse_token(encode_token(payload, "unsigned"))   # shape check
    return encode_token(payload, "unsigned")


def entitlements(token, as_of, current_fingerprint=None, public_key=None):
    """Resolve a token to the capability set the facade may pass to the engine.

    Every reason a token is not honoured is listed, and the returned set is
    empty unless every check passes. Signature verification is attempted and
    its NotImplementedError is recorded as `signature: unverified` so the UI
    can show it, rather than being swallowed.
    """
    parsed = parse_token(token)
    payload = parsed["payload"]
    reasons = []
    checks = {}

    when = _as_date(as_of)
    expiry = _as_date(payload["expires"])
    if when is None or expiry is None:
        raise LicenseError("as_of and expires must be ISO dates")
    checks["expiry"] = "valid" if when <= expiry else "expired"
    if when > expiry:
        reasons.append("entitlement expired %s" % payload["expires"])

    if current_fingerprint is not None:
        ok = current_fingerprint["composite"] == payload["fingerprint_composite"]
        checks["fingerprint"] = "match" if ok else "mismatch"
        if not ok:
            reasons.append("token was issued to a different machine fingerprint")
    else:
        checks["fingerprint"] = "not_checked"

    try:
        verify_signature(payload, parsed["signature"], public_key)
        checks["signature"] = "verified"
    except NotImplementedError as exc:
        checks["signature"] = "unverified"
        reasons.append(str(exc))

    return {
        "tenant": payload["tenant"],
        "tier": payload["tier"],
        "seat": payload["seat"],
        "install_id": payload["install_id"],
        "expires": payload["expires"],
        "ruleset_entitlement": payload["ruleset_entitlement"],
        "capabilities": sorted(payload["capabilities"]),
        "checks": checks,
        "honoured": not reasons,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------
# offline activation: challenge-response by file exchange
# --------------------------------------------------------------------------

def challenge(fp, install_id, tenant, nonce):
    """The file a customer carries out of an air-gapped network to activate.

    Carries hashes only. No raw identifier, no answer data, no facility name.
    """
    return {
        "kind": "activation_challenge",
        "module_version": MODULE_VERSION,
        "install_id": install_id,
        "tenant": tenant,
        "nonce": nonce,
        "fingerprint_composite": fp["composite"],
        "fingerprint_components": fp["components"],
    }


def activate_offline(challenge_doc, response_doc):
    """Consume the response file that came back. NOT IMPLEMENTED: needs the crypto."""
    if challenge_doc.get("nonce") != response_doc.get("nonce"):
        raise LicenseError("response nonce does not match the challenge")
    raise NotImplementedError("offline activation response verification needs the "
                              "signature primitive; see verify_signature")


def verify_ruleset_signature(bundle, public_key):
    """Verify a sealed ruleset bundle before load. NOT IMPLEMENTED.

    Never relaxed for a tier once it exists. Until then the facade must state on
    every screen that the loaded ruleset is unsigned.
    """
    raise NotImplementedError("ruleset bundle signature verification is not implemented; "
                              "the loaded ruleset is unsigned")


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
