"""Freeze an answer set into an immutable, content-addressed plan_version.

Tier 1. Standard library only. Never imports engine.

Every export writes a plan_version that freezes the answer set and pins four
versions: ruleset, question module, clause library, and plan. The KEV snapshot
rides inside the ruleset pin as `ruleset@X+kev.Y`, so it stays four pins.
Controlled attachments (the seven registers, network diagrams, and the like)
are pinned by reference, as a content hash each, rather than embedded.

Freezing is ours, not the Coast Guard's. It is what makes an amendment diff,
a 5-year renewal, and a surveillance diff possible: because the frozen answers
cannot move, a verdict that differs between two runs over the same version is
attributable to the ruleset rather than to the customer.

Nothing here reads the clock. `as_of` and `frozen_at` are supplied by the caller.
"""

import copy
import hashlib
import json
import re

PINS = ("ruleset", "question_module", "clause_library", "plan")

_RULESET_PIN = re.compile(r"^(?P<ruleset>[^+@\s]+)(\+kev\.(?P<kev>[^+\s]+))?$")


class VersionError(Exception):
    """Bad pins, a malformed version, or a hash that does not verify."""


def canonical(obj):
    """Canonical bytes for hashing: sorted keys, no whitespace, ASCII only."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def content_hash(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def ruleset_pin(ruleset_version, kev_catalog_version=None):
    """`2026.09.03` or `2026.09.03+kev.2026.09.08`. One pin, two versions."""
    if kev_catalog_version is None:
        return ruleset_version
    return "%s+kev.%s" % (ruleset_version, kev_catalog_version)


def parse_ruleset_pin(pin):
    m = _RULESET_PIN.match(pin or "")
    if not m:
        raise VersionError("malformed ruleset pin %r" % (pin,))
    return {"ruleset": m.group("ruleset"), "kev": m.group("kev")}


def freeze(plan_id, answers, pins, as_of, frozen_by, attachments=None, sequence=None):
    """Return a new plan_version. The caller stores it in an append-only collection.

    `pins` must carry exactly the four names in PINS. `attachments` is a map of
    attachment id to {"version": ..., "sha256": ...}; the plan_version carries
    the reference and never the attachment body.
    """
    if not isinstance(pins, dict) or set(pins) != set(PINS):
        raise VersionError("pins must be exactly %s, got %s" % (list(PINS), sorted(pins or [])))
    for name in PINS:
        if not isinstance(pins[name], str) or not pins[name]:
            raise VersionError("pin %r must be a non-empty string" % name)
    parse_ruleset_pin(pins["ruleset"])

    frozen = copy.deepcopy(answers)
    refs = {}
    for aid, ref in sorted((attachments or {}).items()):
        if not isinstance(ref, dict) or "sha256" not in ref:
            raise VersionError("attachment %r must carry a sha256" % aid)
        refs[aid] = {"version": ref.get("version"), "sha256": ref["sha256"]}

    body = {
        "plan_id": plan_id,
        "sequence": sequence,
        "as_of": str(as_of),
        "frozen_by": frozen_by,
        "pins": {k: pins[k] for k in PINS},
        "attachments": refs,
        "answers_sha256": content_hash(frozen),
        "answers": frozen,
    }
    version_id = content_hash(body)
    body["version_id"] = version_id
    return body


def verify(plan_version):
    """Recompute both hashes. A version whose body has drifted is not a version."""
    problems = []
    if not isinstance(plan_version, dict):
        return ["plan_version is not an object"]
    for key in ("plan_id", "as_of", "pins", "attachments", "answers_sha256", "answers", "version_id"):
        if key not in plan_version:
            problems.append("missing %r" % key)
    if problems:
        return problems
    if content_hash(plan_version["answers"]) != plan_version["answers_sha256"]:
        problems.append("answers_sha256 does not match the frozen answers")
    body = {k: v for k, v in plan_version.items() if k not in ("version_id", "id")}
    if content_hash(body) != plan_version["version_id"]:
        problems.append("version_id does not match the body")
    if set(plan_version["pins"]) != set(PINS):
        problems.append("pins are not exactly %s" % list(PINS))
    return problems


def diff_answers(before, after, path=""):
    """Leaf-level differences between two answer sets. The amendment diff."""
    changes = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            sub = "%s.%s" % (path, key) if path else key
            if key not in before:
                changes.append({"path": sub, "change": "added", "from": None, "to": after[key]})
            elif key not in after:
                changes.append({"path": sub, "change": "removed", "from": before[key], "to": None})
            else:
                changes += diff_answers(before[key], after[key], sub)
    elif isinstance(before, list) and isinstance(after, list):
        n = max(len(before), len(after))
        for i in range(n):
            sub = "%s[%d]" % (path, i)
            if i >= len(before):
                changes.append({"path": sub, "change": "added", "from": None, "to": after[i]})
            elif i >= len(after):
                changes.append({"path": sub, "change": "removed", "from": before[i], "to": None})
            else:
                changes += diff_answers(before[i], after[i], sub)
    elif before != after:
        changes.append({"path": path, "change": "changed", "from": before, "to": after})
    return changes


def diff_pins(before, after):
    return [{"pin": k, "from": before["pins"][k], "to": after["pins"][k]}
            for k in PINS if before["pins"][k] != after["pins"][k]]
