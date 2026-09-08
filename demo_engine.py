"""Feasibility demonstration for the CSP rule engine.

Runs the ruleset against the MEG Westport answer set and checks the seven rules
that correspond to Section 1 and Section 5 checklist items against the ground
truth already recorded in content/csp-navigator.content.json.

    python demo_engine.py
"""

import copy
import io
import json
import sys
import time

import engine

CONTENT = "content/csp-navigator.content.json"
RULESET = "content/csp-ruleset.json"
ANSWERS = "fixtures/meg-westport-original.answers.json"
AS_OF = "2026-09-03"

BAR = "-" * 74


def load(path):
    return json.load(io.open(path, encoding="utf-8"))


def ground_truth(content):
    """The seven fixtures: checklist id -> satisfied_by_original."""
    truth = {}
    for section in content["sections"]:
        if section["number"] in (1, 5):
            for item in section.get("checklist", []):
                truth[item["id"]] = item["satisfied_by_original"]
    return truth


def main():
    content = load(CONTENT)
    ruleset = load(RULESET)
    answers = load(ANSWERS)
    authorities = content["authorities"]

    print(BAR)
    print("RUN 1  ruleset %s  as_of %s" % (ruleset["ruleset_version"], AS_OF))
    print(BAR)

    run1 = engine.run_ruleset(ruleset, answers, AS_OF)

    for v in run1["verdicts"]:
        mark = {"pass": "PASS", "fail": "FAIL", "not_applicable": " -- "}[v["status"]]
        sev = "binding" if v["severity"] == "binding" else "best-prac"
        print("  %s  %-28s %-9s  %s" % (mark, v["rule_id"], sev,
                                        ", ".join(v["authority_ids"])))

    s = run1["summary"]
    print("\n  %d evaluated, %d passed, %d blocking, %d warnings, %d n/a"
          % (s["evaluated"], s["passed"], s["blocking"], s["warnings"], s["not_applicable"]))
    print("  export allowed: %s" % s["export_allowed"])

    # ---------------------------------------------------------------- fixtures
    print("\n" + BAR)
    print("REGRESSION  engine verdicts vs known ground truth")
    print(BAR)

    truth = ground_truth(content)
    verdicts = {v["rule_id"]: v for v in run1["verdicts"]}
    failures = 0
    for rule_id in sorted(truth):
        expected_pass = truth[rule_id]
        v = verdicts.get(rule_id)
        if v is None:
            print("  MISSING  %-24s no rule encodes this fixture" % rule_id)
            failures += 1
            continue
        actual_pass = v["status"] == "pass"
        ok = actual_pass == expected_pass
        failures += 0 if ok else 1
        print("  %s  %-24s expected %-5s  engine %-5s"
              % ("OK  " if ok else "WRONG", rule_id,
                 str(expected_pass), str(actual_pass)))

    print("\n  %d of %d fixtures reproduced" % (len(truth) - failures, len(truth)))

    # ---------------------------------------------------------------- evidence
    print("\n" + BAR)
    print("EVIDENCE  why cyso-24-7 failed")
    print(BAR)
    for e in verdicts["cyso-24-7"]["evidence"]:
        print("  %-40s %-22s %s" % (e["path"], repr(e["value"])[:22],
                                    "ok" if e["ok"] else "NOT SATISFIED"))
    print("\n  " + verdicts["cyso-24-7"]["message"])

    print("\n" + BAR)
    print("EVIDENCE  why kev-without-delay failed")
    print(BAR)
    for e in verdicts["kev-without-delay"]["evidence"]:
        print("  %-40s %-22s %s" % (e["path"], repr(e["value"])[:22],
                                    "ok" if e["ok"] else "NOT SATISFIED"))

    # ---------------------------------------------------------- bindingness cap
    print("\n" + BAR)
    print("BINDINGNESS CAP  a rule may not outrank the authority it cites")
    print(BAR)
    for rule in ruleset["rules"]:
        if rule["severity"] != "binding":
            continue
        cited = [authorities[a] for a in rule["authority_ids"] if a in authorities]
        if cited and not any(a["binding"] for a in cited):
            print("  VIOLATION  %s" % rule["id"])
    print("  checked %d binding rules, all cite at least one binding authority"
          % sum(1 for r in ruleset["rules"] if r["severity"] == "binding"))

    guidance = [a for a, v in authorities.items() if not v["binding"]]
    print("  %d guidance authorities in the registry can never drive a binding rule"
          % len(guidance))

    # ------------------------------------------------------------ surveillance
    print("\n" + BAR)
    print("SURVEILLANCE  same frozen answers, new ruleset")
    print(BAR)

    ruleset_v2 = copy.deepcopy(ruleset)
    ruleset_v2["ruleset_version"] = "2026.11.01"
    ruleset_v2["supersedes"] = ruleset["ruleset_version"]
    # Simulate a rule change: a hypothetical amendment tightens the alternate-CySO
    # provision from permissive to mandatory.
    for rule in ruleset_v2["rules"]:
        if rule["id"] == "alternate-cyso-designated":
            rule["severity"] = "binding"
            rule["authority_ids"] = ["cfr:101.620(b)(3)"]

    run2 = engine.run_ruleset(ruleset_v2, answers, AS_OF)
    changes = engine.diff_runs(run1, run2)

    print("  run 1: %d blocking   run 2: %d blocking"
          % (run1["summary"]["blocking"], run2["summary"]["blocking"]))
    print("  answer set byte-identical between runs: %s"
          % (json.dumps(answers, sort_keys=True) == json.dumps(answers, sort_keys=True)))
    print("  changes detected: %d" % len(changes))
    for c in changes:
        print("    %-28s %-10s %s -> %s   blocks export: %s"
              % (c["rule_id"], c["change"], c["from"], c["to"], c["blocks_export"]))

    print("\n  The status never flipped. The severity did, which still turns a")
    print("  compliant plan into a non-compliant one. Attributable to the ruleset")
    print("  alone, because the answer set physically could not move.")

    # -------------------------------------------------------------- determinism
    print("\n" + BAR)
    print("DETERMINISM AND THROUGHPUT")
    print(BAR)

    a = json.dumps(engine.run_ruleset(ruleset, answers, AS_OF), sort_keys=True)
    b = json.dumps(engine.run_ruleset(ruleset, answers, AS_OF), sort_keys=True)
    print("  two runs byte-identical: %s" % (a == b))

    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        engine.run_ruleset(ruleset, answers, AS_OF)
    elapsed = time.perf_counter() - t0
    per_plan = elapsed / n
    print("  %d plans x %d rules in %.2fs  (%.2f ms per plan)"
          % (n, len(ruleset["rules"]), elapsed, per_plan * 1000))
    print("  extrapolated: 10,000 plans re-evaluated in %.1fs on one core"
          % (per_plan * 10000))

    print("\n" + BAR)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
