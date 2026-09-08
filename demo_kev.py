"""Feasibility demonstration for the KEV capability module.

Ships with the module, not with the engine. Everything here runs against the
pinned snapshot in content/, so it works on an offline install and produces
identical output on every run.

    python demo_kev.py
"""

import copy
import json
import sys

import engine
import kev

RULESET = "content/csp-ruleset.json"
ANSWERS = "fixtures/meg-westport-original.answers.json"
SNAPSHOT = "content/kev-snapshot.json"
AS_OF = "2026-09-03"

BAR = "-" * 74


def main():
    ruleset = engine.load(RULESET)
    answers = engine.load(ANSWERS)
    snap = kev.load(SNAPSHOT)

    print(BAR)
    print("SNAPSHOT  pinned, content-addressed, no network at evaluation time")
    print(BAR)
    print("  catalog %s   entries %d" % (snap["catalog_version"], snap["count"]))
    print("  sha256  %s" % snap["sha256"])

    # ------------------------------------------------------------------ join
    print("\n" + BAR)
    print("JOIN  observed CVEs intersected with the catalog")
    print(BAR)

    # One synthetic device, so the narrowing is visible: both CVEs the fixture
    # carries are in the catalog, so an intersection over it alone looks like a
    # no-op. The fixture itself is a regression asset and is never edited.
    probe = copy.deepcopy(answers)
    probe["devices"].append({
        "nickname": "RTU-BERTH-03",
        "is_ot": True,
        "is_critical_system": True,
        "observed_cves": ["CVE-2024-38112", "cve-2021-44228", "CVE-2019-0708",
                          "CVE-2016-2183", "CVE-2022-40684"],
    })

    enriched = kev.enrich(probe, snap)
    for device in enriched["devices"]:
        seen, open_kevs = device["observed_cves"], device["open_kevs"]
        if not seen:
            continue
        print("  %-14s observed %d -> KEV %d   critical=%s"
              % (device["nickname"], len(seen), len(open_kevs),
                 bool(device.get("is_critical_system"))))
        for cve in seen:
            if cve in open_kevs:
                entry = snap["kevs"][cve]
                print("      KEV %-16s %s %s (added %s)"
                      % (cve, entry["vendor_project"], entry["product"],
                         entry["date_added"]))
            else:
                print("          %-16s not in the catalog" % cve)

    # ------------------------------------------------------------- licensing
    print("\n" + BAR)
    print("LICENSING  the same ruleset, the same answers, two entitlements")
    print(BAR)

    licensed = engine.run_ruleset(ruleset, enriched, AS_OF, capabilities={"kev"})
    unlicensed = engine.run_ruleset(ruleset, enriched, AS_OF, capabilities=set())

    for label, run in (("licensed {kev}", licensed), ("unlicensed {}", unlicensed)):
        verdict = _verdict(run, "kev-without-delay")
        summary = run["summary"]
        print("  %-16s kev-without-delay: %-12s blocking=%d unavailable=%d export=%s"
              % (label, verdict["status"], summary["blocking"],
                 summary["unavailable"], summary["export_allowed"]))

    print("\n  Unlicensed does not pass. Without the gate it would: an absent")
    print("  open_kevs makes the for_each `where` clause match nothing, so the rule")
    print("  succeeds having checked zero devices. Conservative missing-data")
    print("  semantics do not help, because no comparison is ever reached.")

    stripped = copy.deepcopy(enriched)
    for device in stripped["devices"]:
        device.pop("open_kevs", None)
        device.pop("observed_cves", None)
    ungated = engine.run_ruleset(ruleset, stripped, AS_OF)

    print("\n     no module, no gate  -> kev-without-delay: %-12s export=%s"
          % (_verdict(ungated, "kev-without-delay")["status"],
             ungated["summary"]["export_allowed"]))
    print("     no module, gated    -> kev-without-delay: %-12s export=%s"
          % (_verdict(unlicensed, "kev-without-delay")["status"],
             unlicensed["summary"]["export_allowed"]))

    # ------------------------------------------------------------- crosswalk
    print("\n" + BAR)
    print("CROSSWALK  composite over appendices I, M, N, O. Critical assets only.")
    print(BAR)

    rows = kev.crosswalk(enriched, snap)
    print("  %-14s %-4s %-16s %-27s %s"
          % ("ASSET", "DOM", "CVE", "PRODUCT", "DISPOSITION"))
    for row in rows:
        print("  %-14s %-4s %-16s %-27s %s"
              % (row["asset"], row["domain"], row["cve"],
                 ("%s %s" % (row["vendor_project"], row["product"]))[:27],
                 row["disposition"]))

    print("\n  %d rows. WS-ACCT-14 carries a KEV and is absent, because it is not" % len(rows))
    print("  critical and 101.650(e)(3)(i) does not reach it.")
    print("  Every row reads 'unresolved': compensating_control is one field on the")
    print("  device and cannot say 'CVE A compensated, CVE B not'. The module")
    print("  refuses to invent a disposition. Fixing that means promoting an")
    print("  observation to its own entity keyed (asset, cve).")

    # ---------------------------------------------------------- surveillance
    print("\n" + BAR)
    print("SURVEILLANCE  trigger 2: a new KEV lands on an old scan result")
    print(BAR)

    before = copy.deepcopy(snap)
    for cve in ("CVE-2021-44228", "CVE-2024-38112"):
        before["kevs"].pop(cve, None)
    before["catalog_version"] = "2026.09.05"
    before["count"] = len(before["kevs"])

    delta = kev.diff_snapshots(before, snap)
    print("  catalog %s -> %s: %d added, %d withdrawn"
          % (delta["from"], delta["to"], len(delta["added"]), len(delta["removed"])))

    local = kev.newly_affected(enriched, before, snap)
    print("\n  agent-local finding (SSI, never leaves the trust boundary):")
    for row in local:
        print("    %-14s %-16s %-10s critical=%s"
              % (row["asset"], row["cve"], row["change"], row["critical"]))

    print("\n  outbound telemetry (the only sendable shape):")
    print("    " + json.dumps(kev.telemetry(local, install_id="inst-7f3a"),
                              sort_keys=True))

    run_before = engine.run_ruleset(ruleset, kev.enrich(probe, before), AS_OF,
                                    capabilities={"kev"})
    changes = engine.diff_runs(run_before, licensed)
    print("\n  verdict diff across the catalog change: %d changes" % len(changes))
    for change in changes:
        print("    %-24s %-8s %s -> %s   blocks export: %s"
              % (change["rule_id"], change["change"], change["from"],
                 change["to"], change["blocks_export"]))
    print("\n  Zero, and that is correct rather than a bug. kev-without-delay was")
    print("  already failing on other KEVs, so two more landing on critical assets")
    print("  moved no verdict. engine.diff_runs is scoped to ruleset-attributable")
    print("  change, which is trigger 1. A catalog change is a data change, so")
    print("  trigger 2 reports through kev.telemetry above. Routing trigger 2")
    print("  through the verdict diff would be silent.")

    # ----------------------------------------------------------- determinism
    print("\n" + BAR)
    print("DETERMINISM AND FAIL-LOUD")
    print(BAR)

    once = json.dumps(kev.enrich(probe, snap), sort_keys=True)
    twice = json.dumps(kev.enrich(kev.enrich(probe, snap), snap), sort_keys=True)
    print("  enrich is idempotent: %s" % (once == twice))

    a = json.dumps(engine.run_ruleset(ruleset, enriched, AS_OF, capabilities={"kev"}),
                   sort_keys=True)
    b = json.dumps(engine.run_ruleset(ruleset, enriched, AS_OF, capabilities={"kev"}),
                   sort_keys=True)
    print("  two gated runs byte-identical: %s" % (a == b))

    failures = 0
    for label, thunk in (
        ("empty catalog", lambda: kev.enrich(probe, {
            "catalog_version": "x", "date_released": "x", "sha256": "x",
            "count": 0, "kevs": {}})),
        ("malformed CVE", lambda: kev.normalize("CVE-24-38112")),
        ("non-list observations", lambda: kev.observed({"open_kevs": "CVE-2021-44228"})),
    ):
        try:
            thunk()
            print("  %s ACCEPTED: this is a bug" % label)
            failures += 1
        except kev.KevError as exc:
            print("  %-22s refused: %s" % (label, str(exc)[:44]))

    print("\n" + BAR)
    return 1 if failures else 0


def _verdict(run, rule_id):
    return next(v for v in run["verdicts"] if v["rule_id"] == rule_id)


if __name__ == "__main__":
    sys.exit(main())
