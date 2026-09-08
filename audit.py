"""Section-scoped audit runs, with deficiencies and recommendations kept apart.

Tier 1. Standard library only. Never imports engine.

An auditor samples a few sections, chosen with the client, not the whole plan.
A run carries its scope and the report states it. A scoped run that reads as
though it covered the whole plan is a liability, so the report names both the
sections examined and the sections not examined.

Two finding objects, never conflated:

  deficiency       A failed binding requirement. Cited to an authority_id.
                   Carries remediation obligations. Coast Guard practice cites
                   every deficiency to the CFR; an uncited one is not defensible.
  recommendation   An enhancement or best practice with no failure behind it.
                   Different severity, different part of the report, no
                   remediation obligation. Free text belongs here.

Finding templates are authored content keyed by authority_id, versioned with
the ruleset and pinned by plan_version. They are EMPTY in this draft.
template_for() says so rather than inventing deficiency or remediation text.

This module does not evaluate anything. from_verdicts() takes engine output
the facade already produced and turns in-scope failures into candidate
findings, carrying the rule's authored message and nothing more.
"""

import copy

MODULE_VERSION = "2026.09.08"

SECTIONS = tuple(range(1, 15))
FINDING_KINDS = ("deficiency", "recommendation")

# Authored templates: {authority_id: {"deficiency": str, "remediation": str}}.
# None exist yet. Authoring them is content work against the PDF.
TEMPLATES = {"template_version": "0.0.0", "by_authority": {}}


class AuditError(Exception):
    """Bad scope, an uncited deficiency, or a finding of the wrong shape."""


def new_run(run_id, plan_version_id, scope, auditor, as_of, client_representative=None):
    scope = sorted(set(scope))
    if not scope or any(s not in SECTIONS for s in scope):
        raise AuditError("scope must be a non-empty subset of sections 1..14, got %r" % (scope,))
    return {
        "id": run_id,
        "module_version": MODULE_VERSION,
        "plan_version_id": plan_version_id,
        "scope": scope,
        "not_examined": [s for s in SECTIONS if s not in scope],
        "auditor": auditor,
        "client_representative": client_representative,
        "as_of": str(as_of),
        "findings": [],
        "status": "open",
    }


def template_for(authority_id, templates=TEMPLATES):
    """The authored finding template behind a citation, or None if none exists."""
    return templates["by_authority"].get(authority_id)


def deficiency(finding_id, section, authority_ids, text, remediation, checklist_item_id=None,
               evidence=None, authority_is_binding=None):
    """A failed binding requirement. Must cite; must carry remediation.

    authority_is_binding is a map the facade fills from the registry. A
    deficiency citing only non-binding guidance is refused: that is the
    bindingness cap applied to audit output.
    """
    if not authority_ids:
        raise AuditError("a deficiency must cite at least one authority_id")
    if authority_is_binding is not None and not any(authority_is_binding.get(a) for a in authority_ids):
        raise AuditError("deficiency %r cites only non-binding authorities %s; "
                         "record it as a recommendation instead" % (finding_id, authority_ids))
    if not text:
        raise AuditError("deficiency %r has no deficiency text" % finding_id)
    return {
        "id": finding_id, "kind": "deficiency", "section": section,
        "authority_ids": list(authority_ids), "checklist_item_id": checklist_item_id,
        "text": text,
        "remediation": remediation,      # None is allowed and is shown as a placeholder
        "evidence": copy.deepcopy(evidence or []),
        "severity": "binding",
    }


def recommendation(finding_id, section, text, authority_ids=None, checklist_item_id=None):
    """An enhancement. No failure behind it, no remediation obligation."""
    if not text:
        raise AuditError("recommendation %r has no text" % finding_id)
    return {
        "id": finding_id, "kind": "recommendation", "section": section,
        "authority_ids": list(authority_ids or []), "checklist_item_id": checklist_item_id,
        "text": text, "remediation": None, "evidence": [], "severity": "best_practice",
    }


def add_finding(run, finding):
    if run["status"] != "open":
        raise AuditError("run %r is %s" % (run["id"], run["status"]))
    if finding.get("kind") not in FINDING_KINDS:
        raise AuditError("finding kind must be one of %s" % (FINDING_KINDS,))
    if finding["section"] not in run["scope"]:
        raise AuditError("finding %r is in section %s, outside this run's scope %s"
                         % (finding["id"], finding["section"], run["scope"]))
    if any(f["id"] == finding["id"] for f in run["findings"]):
        raise AuditError("duplicate finding id %r" % finding["id"])
    out = copy.deepcopy(run)
    out["findings"].append(copy.deepcopy(finding))
    return out


def from_verdicts(run, verdicts, templates=TEMPLATES):
    """Candidate findings from engine verdicts, restricted to the run's scope.

    A binding fail becomes a deficiency candidate carrying the rule's authored
    message; remediation comes from a template if one exists, else None. A
    best-practice fail becomes a recommendation candidate. An `unavailable`
    binding verdict becomes a deficiency candidate too: the obligation was not
    demonstrated. Out-of-scope verdicts are dropped, and counted, so the report
    can say how much it did not look at.
    """
    candidates, dropped = [], 0
    for v in verdicts:
        if v.get("section") not in run["scope"]:
            dropped += 1
            continue
        if v["status"] not in ("fail", "unavailable"):
            continue
        fid = "%s:%s" % (run["id"], v["rule_id"])
        if v["severity"] == "binding":
            tmpl = next((template_for(a, templates) for a in v["authority_ids"]
                         if template_for(a, templates)), None)
            candidates.append(deficiency(
                fid, v["section"], v["authority_ids"],
                text=(tmpl or {}).get("deficiency") or v.get("message") or
                     "rule %s: %s" % (v["rule_id"], v["status"]),
                remediation=(tmpl or {}).get("remediation"),
                checklist_item_id=v["rule_id"], evidence=v.get("evidence", [])))
        else:
            candidates.append(recommendation(
                fid, v["section"], v.get("message") or v["rule_id"],
                authority_ids=v["authority_ids"], checklist_item_id=v["rule_id"]))
    return {"candidates": candidates, "out_of_scope_verdicts": dropped}


def close(run):
    out = copy.deepcopy(run)
    out["status"] = "closed"
    return out


def report(run, section_titles):
    """The report, with its scope stated on its face."""
    defs = [f for f in run["findings"] if f["kind"] == "deficiency"]
    recs = [f for f in run["findings"] if f["kind"] == "recommendation"]
    return {
        "run_id": run["id"],
        "plan_version_id": run["plan_version_id"],
        "as_of": run["as_of"],
        "auditor": run["auditor"],
        "client_representative": run["client_representative"],
        "scope_statement": (
            "This audit examined sections %s only. Sections %s were not examined "
            "and nothing in this report should be read as a finding about them."
            % (", ".join(str(s) for s in run["scope"]),
               ", ".join(str(s) for s in run["not_examined"]))),
        "sections_examined": [{"number": s, "title": section_titles.get(s)} for s in run["scope"]],
        "sections_not_examined": [{"number": s, "title": section_titles.get(s)}
                                  for s in run["not_examined"]],
        "deficiencies": defs,
        "recommendations": recs,
        "counts": {"deficiencies": len(defs), "recommendations": len(recs),
                   "deficiencies_without_remediation": sum(1 for f in defs if not f["remediation"])},
        "status": run["status"],
    }
