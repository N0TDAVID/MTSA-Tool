"""CISA Known Exploited Vulnerabilities: an entitlement-gated capability module.

This is not part of the engine. `engine.py` never imports it, and it never reaches
into the engine's operator set. The two meet at exactly one point: this module
projects derived facts onto an answer set before evaluation, and the engine then
reads them as plain JSON like anything else. That is deliberate. Adding KEV as new
operators would have made the closed operator set open, and an operator that is
present for one tenant and absent for another is not a deterministic engine.

Three layers, and the line between the first and the other two is the important one.

    fetch()       The only network call in the codebase. Runs when a bundle is
                  built or an agent updates, never during evaluation. An offline
                  install never calls it at all: it sideloads a snapshot file.
    snapshot()    Pure reduction of the feed to pinned, content-addressed data.
                  Everything a verdict can depend on is in here and nothing else.
    enrich()      Pure projection. Derives `open_kevs` per device from observed
                  CVEs intersected with the pinned catalog. No clock, no network.

Licensing. This module declares CAPABILITY, and rules in the ruleset declare
`requires_capability`. The engine masks a rule whose capability was not supplied
to `unavailable`, which for a binding rule blocks export rather than passing. That
last part is not a sales mechanism, it is the honest answer: 101.650(e)(3)(i)
reaches KEVs on critical IT and OT systems, and a plan with no KEV identification
source cannot demonstrate compliance with it. See the authored gap
`no-kev-identification` in Section 12.

SSI. The catalog itself is public and carries no SSI. The join result does: it
names assets. `telemetry()` is the only shape that may leave the agent under the
default posture, and it names rule ids and counts, never an asset.
"""

import hashlib
import json
import re

CAPABILITY = "kev"
MODULE_VERSION = "2026.09.08"

FEED_URL = ("https://www.cisa.gov/sites/default/files/feeds/"
            "known_exploited_vulnerabilities.json")

# Verified against the live feed on 2026-09-08: all 1699 entries carry a unique
# cveID and every one matches this pattern. It is the only sound join key the feed
# offers. vendorProject and product are editorial labels, not identifiers: 283
# distinct vendor strings over 718 vendor/product pairs, with Microsoft alone
# appearing under 72 product spellings. Never match on those.
CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,}$")


class KevError(Exception):
    """Malformed feed, malformed snapshot, or malformed CVE. Never caught internally."""


# --------------------------------------------------------------------------
# layer 1: acquisition. Network. Never reachable from an evaluation.
# --------------------------------------------------------------------------

def fetch(url=FEED_URL, opener=None, timeout=30):
    """Retrieve the raw catalog bytes.

    `opener` is injected so the caller owns the network, not this module: an agent
    behind a proxy, a test with a canned body, and an offline install that never
    calls this at all are then all the same code path. urllib is imported inside
    the function so the pure half of this module has no network reachability at
    import time, which is what an SSI review will look for.
    """
    if opener is None:
        from urllib.request import urlopen

        def opener(u):
            return urlopen(u, timeout=timeout)

    with opener(url) as response:
        return response.read()


# --------------------------------------------------------------------------
# layer 2: the snapshot. Pure, content-addressed, pinnable.
# --------------------------------------------------------------------------

def snapshot(raw):
    """Reduce the CISA feed to the pinned form a verdict may depend on.

    The sha256 is over the raw bytes, not over the reduction, so the snapshot
    attests to exactly what CISA served. catalogVersion alone is not enough: it is
    a date string, and a same-day re-release would reuse it.
    """
    if not isinstance(raw, bytes):
        raise KevError("snapshot() takes raw bytes, got %s" % type(raw).__name__)
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise KevError("feed is not JSON (%s)" % exc)

    for key in ("catalogVersion", "dateReleased", "count", "vulnerabilities"):
        if key not in doc:
            raise KevError("feed has no %r; the format changed, do not guess" % key)

    kevs = {}
    for entry in doc["vulnerabilities"]:
        cve = normalize(entry.get("cveID", ""))
        if cve in kevs:
            raise KevError("feed lists %s twice; the join key is not unique" % cve)
        kevs[cve] = {
            "date_added": entry["dateAdded"],
            "vendor_project": entry["vendorProject"],
            "product": entry["product"],
            "ransomware": entry["knownRansomwareCampaignUse"] == "Known",
        }

    if len(kevs) != doc["count"]:
        raise KevError("feed says count=%d but carries %d entries"
                       % (doc["count"], len(kevs)))

    return {
        "module_version": MODULE_VERSION,
        "catalog_version": doc["catalogVersion"],
        "date_released": doc["dateReleased"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "count": len(kevs),
        "kevs": kevs,
    }


def verify(snap):
    """Check a snapshot before it is used. Fail loud beats a silent empty catalog.

    An empty or truncated catalog makes every KEV rule pass vacuously, which is the
    worst failure mode this module has: it looks exactly like compliance.
    """
    problems = []
    if not isinstance(snap, dict):
        return ["snapshot is not an object"]
    for key in ("catalog_version", "date_released", "sha256", "count", "kevs"):
        if key not in snap:
            problems.append("snapshot has no %r" % key)
    kevs = snap.get("kevs")
    if not isinstance(kevs, dict):
        problems.append("snapshot kevs is not an object")
    elif not kevs:
        problems.append("snapshot catalog is empty; every KEV rule would pass vacuously")
    else:
        if len(kevs) != snap.get("count"):
            problems.append("snapshot count is %r but carries %d entries"
                            % (snap.get("count"), len(kevs)))
        bad = [c for c in kevs if not CVE_ID.match(c)]
        if bad:
            problems.append("snapshot has %d malformed cve ids, e.g. %s"
                            % (len(bad), bad[:3]))
    return problems


def normalize(cve):
    """Canonical CVE id, or raise.

    This is the `matches` discipline from Answer typing: a format check on an
    identifier, which is what pattern matching is legitimately for.
    """
    if not isinstance(cve, str):
        raise KevError("not a CVE identifier: %r" % (cve,))
    canon = cve.strip().upper()
    if not CVE_ID.match(canon):
        raise KevError("not a CVE identifier: %r" % (cve,))
    return canon


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(snap, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# layer 3: the join. Pure. This is the whole matching algorithm.
# --------------------------------------------------------------------------

def observed(device):
    """Every CVE seen on a device, from whichever source supplied it.

    `observed_cves` is the raw upstream signal: scanner output for IT, vendor
    advisory mapping for OT, which usually cannot be actively scanned. Older answer
    sets wrote the already-narrowed list into `open_kevs` directly; those are read
    as observations and re-narrowed, so re-enriching is idempotent.
    """
    raw = device.get("observed_cves")
    if raw is None:
        raw = device.get("open_kevs", [])
    if not isinstance(raw, list):
        raise KevError("observed CVEs must be a list, got %r" % (raw,))
    return {normalize(c) for c in raw}


def enrich(answers, snap, device_path="devices"):
    """Return a copy of `answers` with `open_kevs` derived per device.

    The join is a set intersection on CVE id. That is the entire algorithm, and it
    is exact: there is no fuzzy matching anywhere in this module, by design.

    Does not mutate the input. The engine then evaluates the result exactly as it
    evaluates any other answer set, which is what keeps a KEV verdict reproducible
    from a frozen plan_version.
    """
    problems = verify(snap)
    if problems:
        raise KevError("refusing to enrich from a bad snapshot: %s" % "; ".join(problems))

    catalog = snap["kevs"]
    out = dict(answers)
    devices = []
    for device in answers.get(device_path, []):
        seen = observed(device)
        enriched = dict(device)
        enriched["observed_cves"] = sorted(seen)
        enriched["open_kevs"] = sorted(seen & catalog.keys())
        devices.append(enriched)
    out[device_path] = devices

    # Provenance travels with the answers, so a verdict can be traced to the exact
    # catalog that produced it, and so a rule can assert a source exists at all.
    out["kev_snapshot"] = {
        "catalog_version": snap["catalog_version"],
        "date_released": snap["date_released"],
        "sha256": snap["sha256"],
        "count": snap["count"],
    }
    return out


def crosswalk(answers, snap, device_path="devices"):
    """The mitigation crosswalk: one row per (critical asset, KEV).

    Coast Guard guidance asks for one table: critical asset, known vulnerabilities,
    and what was done about each. This is the composite view over appendices I, M,
    N and O described in CLAUDE.md, not an eighth register.

    Disposition is honest about what the current entity shape can express.
    `compensating_control` is a single field on the device, so it cannot say
    "CVE A compensated, CVE B not". Until an observation becomes its own entity
    keyed (asset, cve), rows resolve to `unresolved` rather than being invented.
    """
    catalog = snap["kevs"]
    rows = []
    for device in answers.get(device_path, []):
        if not device.get("is_critical_system"):
            continue  # 101.650(e)(3)(i) reaches critical IT and OT systems only.
        for cve in sorted(observed(device) & catalog.keys()):
            entry = catalog[cve]
            rows.append({
                "asset": device.get("nickname"),
                "domain": "OT" if device.get("is_ot") else "IT",
                "cve": cve,
                "vendor_project": entry["vendor_project"],
                "product": entry["product"],
                "date_added": entry["date_added"],
                "ransomware": entry["ransomware"],
                "disposition": _disposition(device),
                "compensating_control": device.get("compensating_control"),
                "remediation_plan": device.get("remediation_plan"),
                "risk_acceptance": device.get("risk_acceptance"),
            })
    rows.sort(key=lambda r: (r["asset"] or "", r["cve"]))
    return rows


def _disposition(device):
    """Always unresolved, on purpose. See the crosswalk docstring.

    The three evidence fields hang off the device, not off an (asset, cve)
    pair, so a device carrying one compensating control and three KEVs cannot
    say which of the three it covers. Reading the presence of that field as
    "mitigated" for every CVE on the device is an invented disposition, and an
    invented disposition in a compliance table is the failure mode this module
    exists to avoid. The evidence travels with the row so a reviewer can see
    what was collected; only the resolution claim is withheld.
    """
    del device  # deliberately unread until the observation entity exists
    return "unresolved"


# --------------------------------------------------------------------------
# surveillance: trigger 2, a new KEV landing on an old scan result
# --------------------------------------------------------------------------

def diff_snapshots(before, after):
    """What changed between two catalogs.

    Removals matter as much as additions: CISA does withdraw entries, and a rule
    flipping to pass because a KEV was withdrawn is a real verdict the diff must
    carry rather than drop.
    """
    old, new = set(before["kevs"]), set(after["kevs"])
    return {
        "from": before["catalog_version"],
        "to": after["catalog_version"],
        "added": sorted(new - old),
        "removed": sorted(old - new),
    }


def newly_affected(answers, before, after, device_path="devices"):
    """Assets hit by a catalog change. AGENT-LOCAL ONLY.

    Every row names an asset, so this is SSI and must not leave the trust boundary
    under the default posture. Pass it through `telemetry()` before anything goes out.
    """
    delta = diff_snapshots(before, after)
    added, removed = set(delta["added"]), set(delta["removed"])
    rows = []
    for device in answers.get(device_path, []):
        seen = observed(device)
        critical = bool(device.get("is_critical_system"))
        for cve in sorted(seen & added):
            rows.append({"asset": device.get("nickname"), "cve": cve,
                         "change": "added", "critical": critical})
        for cve in sorted(seen & removed):
            rows.append({"asset": device.get("nickname"), "cve": cve,
                         "change": "withdrawn", "critical": critical})
    return rows


def telemetry(rows, install_id):
    """Reduce agent-local findings to the shape that may be sent outbound.

    Counts and a flag. No asset name, no CVE, no facility identifier. "A KEV now
    affects the asset you flagged critical" is not sendable from telemetry alone;
    that is a custody-tier alert. This function is what enforces the difference.
    """
    return {
        "install_id": install_id,
        "capability": CAPABILITY,
        "critical_assets_newly_affected": len({r["asset"] for r in rows
                                               if r["critical"] and r["change"] == "added"}),
        "critical_assets_relieved": len({r["asset"] for r in rows
                                         if r["critical"] and r["change"] == "withdrawn"}),
        "review_recommended": any(r["critical"] and r["change"] == "added" for r in rows),
    }


# --------------------------------------------------------------------------
# CLI: refresh the pinned snapshot. Not used at evaluation time.
# --------------------------------------------------------------------------

def _main(argv):
    command = argv[1] if len(argv) > 1 else "show"
    path = argv[2] if len(argv) > 2 else "content/kev-snapshot.json"

    if command == "refresh":
        save(snapshot(fetch()), path)
        print("wrote %s" % path)
    elif command != "show":
        print("usage: python kev.py [refresh|show] [snapshot-path]")
        return 2

    snap = load(path)
    problems = verify(snap)
    print("catalog %s  released %s" % (snap["catalog_version"], snap["date_released"]))
    print("entries %d  sha256 %s" % (snap["count"], snap["sha256"]))
    print("verify: %s" % ("OK" if not problems else "; ".join(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
