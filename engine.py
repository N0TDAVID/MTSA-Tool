"""Deterministic rule engine for the CSP gap check.

A rule is a predicate over an answer set. Evaluation is a recursive walk of a closed
set of operators over plain JSON. There is no model, no inference, and no network
call anywhere in this file, which is what lets it run entirely inside the SSI
boundary and produce byte-identical verdicts for identical inputs.

Three properties the product depends on, all enforced here:

1. Determinism. The same (ruleset, answers, as_of) always yields the same verdicts.
   Nothing reads the clock; `as_of` is an explicit pinned input. An audit run in
   2029 against a 2026 plan_version must reproduce the 2026 report exactly.
2. Evidence. Every verdict carries the paths the predicate actually read and what
   it found. That is what makes an auditor's report defensible and what makes a
   flipped verdict explainable in a surveillance alert.
3. Fail loud. An unknown operator or a malformed rule raises. A compliance engine
   that silently passes an unparseable rule is worse than no engine.

Conservative missing-data semantics: a path that is absent makes its comparison
False. An obligation nobody answered is not demonstrated, so it does not pass.
"""

import json
import re
from datetime import date, datetime

MISSING = object()


class RuleError(Exception):
    """Malformed rule or unknown operator. Never caught internally."""


# --------------------------------------------------------------------------
# path resolution
# --------------------------------------------------------------------------

def resolve(path, scope, root):
    """Resolve a dotted path. A leading '$.' escapes an item scope back to root."""
    if not isinstance(path, str):
        raise RuleError("path must be a string, got %r" % (path,))
    cur = root if path.startswith("$.") else scope
    parts = (path[2:] if path.startswith("$.") else path).split(".")
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return MISSING
    return cur


def _empty(value):
    return value is MISSING or value is None or value == "" or value == [] or value == {}


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------

class Context:
    """Everything a predicate may read. Nothing else is reachable."""

    def __init__(self, answers, as_of, capabilities=None):
        self.answers = answers
        self.as_of = _as_date(as_of)
        if self.as_of is None:
            raise RuleError("as_of must be an ISO date, got %r" % (as_of,))
        # None means ungated: evaluate every rule regardless of what it requires.
        # That is the authoring and test path. A licensed deployment always passes
        # an explicit set, so an unlicensed capability cannot be reached by default.
        self.capabilities = None if capabilities is None else frozenset(capabilities)

    def supplies(self, capability):
        return self.capabilities is None or capability in self.capabilities


def evaluate(node, ctx, scope=None, evidence=None):
    """Evaluate a predicate node. Returns bool, appending to `evidence`."""
    if evidence is None:
        evidence = []
    if scope is None:
        scope = ctx.answers
    if not isinstance(node, dict) or len(node) != 1:
        raise RuleError("predicate must be a single-key object, got %r" % (node,))

    (op, arg), = node.items()

    # --- boolean combinators ---
    # Neither combinator short-circuits. An auditor asking "what did you check?"
    # is entitled to the whole list, not just the clause that failed first.
    if op == "all":
        _need_list(op, arg)
        return all([evaluate(c, ctx, scope, evidence) for c in arg])
    if op == "any":
        _need_list(op, arg)
        return any([evaluate(c, ctx, scope, evidence) for c in arg])
    if op == "not":
        return not evaluate(arg, ctx, scope, evidence)

    # --- presence ---
    if op == "answered":
        value = resolve(arg, scope, ctx.answers)
        ok = not _empty(value)
        _record(evidence, arg, value, ok)
        return ok

    # --- comparisons ---
    if op in ("eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "matches"):
        _need_pair(op, arg)
        path, operand = arg
        value = resolve(path, scope, ctx.answers)
        ok = _compare(op, value, operand)
        _record(evidence, path, value, ok)
        return ok

    # --- collections ---
    if op == "includes":
        _need_pair(op, arg)
        path, member = arg
        value = resolve(path, scope, ctx.answers)
        ok = isinstance(value, list) and member in value
        _record(evidence, path, "(absent)" if value is MISSING else value, ok)
        return ok

    if op in ("count_gte", "count_eq"):
        _need_pair(op, arg)
        path, n = arg
        value = resolve(path, scope, ctx.answers)
        size = len(value) if isinstance(value, (list, dict)) else 0
        ok = size >= n if op == "count_gte" else size == n
        _record(evidence, path, size, ok)
        return ok

    if op in ("every", "some", "none"):
        _need_pair(op, arg)
        path, sub = arg
        value = resolve(path, scope, ctx.answers)
        items = value if isinstance(value, list) else []
        if value is MISSING:
            _record(evidence, path, MISSING, False)
            return False
        results = [evaluate(sub, ctx, item, evidence) for item in items]
        if op == "every":
            ok = all(results)
        elif op == "some":
            ok = any(results)
        else:
            ok = not any(results)
        _record(evidence, path + "[]", "%d/%d matched" % (sum(results), len(results)), ok)
        return ok

    # --- filtered collection: every item matching `where` must satisfy `must` ---
    if op == "for_each":
        if not isinstance(arg, dict) or "path" not in arg or "must" not in arg:
            raise RuleError("for_each needs path/must (where optional), got %r" % (arg,))
        value = resolve(arg["path"], scope, ctx.answers)
        items = value if isinstance(value, list) else []
        where = arg.get("where")
        checked = 0
        failed = 0
        for item in items:
            if where is not None and not evaluate(where, ctx, item, []):
                continue
            checked += 1
            if not evaluate(arg["must"], ctx, item, evidence):
                failed += 1
        ok = failed == 0
        _record(evidence, arg["path"] + "[]",
                "%d in scope, %d failed" % (checked, failed), ok)
        return ok

    # --- temporal, against the pinned as_of only ---
    if op == "within_days":
        _need_pair(op, arg)
        path, days = arg
        when = _as_date(resolve(path, scope, ctx.answers))
        ok = when is not None and 0 <= (ctx.as_of - when).days <= days
        age = (ctx.as_of - when).days if when else None
        _record(evidence, path, "%s (%s days ago)" % (when, age), ok)
        return ok

    raise RuleError("unknown operator %r" % (op,))


def _compare(op, value, operand):
    if op == "matches":
        return isinstance(value, str) and re.search(operand, value) is not None
    if op == "in":
        return value is not MISSING and value in operand
    if op == "not_in":
        return value is not MISSING and value not in operand
    if value is MISSING:
        return False
    if op == "eq":
        return value == operand
    if op == "ne":
        return value != operand
    try:
        if op == "gt":
            return value > operand
        if op == "gte":
            return value >= operand
        if op == "lt":
            return value < operand
        if op == "lte":
            return value <= operand
    except TypeError:
        return False
    raise RuleError("unhandled comparison %r" % (op,))


def _need_list(op, arg):
    if not isinstance(arg, list):
        raise RuleError("%s needs a list, got %r" % (op, arg))


def _need_pair(op, arg):
    if not isinstance(arg, list) or len(arg) != 2:
        raise RuleError("%s needs [path, operand], got %r" % (op, arg))


def _record(evidence, path, value, ok):
    evidence.append({
        "path": path,
        "value": "(absent)" if value is MISSING else value,
        "ok": ok,
    })


# --------------------------------------------------------------------------
# rules and verdicts
# --------------------------------------------------------------------------

def run_rule(rule, ctx):
    """Evaluate one rule. Returns a verdict dict."""
    # Capability gating, decided in the same place as applicability and before it.
    # A rule whose data source was not licensed is `unavailable`, never `pass`.
    # Missing-data semantics would otherwise pass it vacuously: an absent
    # collection makes `where` match nothing, so `for_each` succeeds having
    # checked nothing. Silently certifying an obligation nobody could evaluate is
    # the one outcome a compliance engine must never produce.
    need = rule.get("requires_capability")
    if need is not None and not ctx.supplies(need):
        return {
            "rule_id": rule["id"],
            "status": "unavailable",
            "severity": rule["severity"],
            "section": rule.get("section"),
            "authority_ids": rule["authority_ids"],
            "requires_capability": need,
            "message": "Requires the '%s' capability, which this install is not "
                       "licensed for. The obligation is not evaluated and is "
                       "therefore not demonstrated." % need,
            "evidence": [],
        }

    applies = rule.get("applies_to")
    if applies is not None and not evaluate(applies, ctx, ctx.answers, []):
        return {
            "rule_id": rule["id"],
            "status": "not_applicable",
            "severity": rule["severity"],
            "section": rule.get("section"),
            "authority_ids": rule["authority_ids"],
            "evidence": [],
        }

    evidence = []
    passed = evaluate(rule["predicate"], ctx, ctx.answers, evidence)
    return {
        "rule_id": rule["id"],
        "status": "pass" if passed else "fail",
        "severity": rule["severity"],
        "section": rule.get("section"),
        "authority_ids": rule["authority_ids"],
        "message": None if passed else rule.get("message", ""),
        "evidence": evidence,
    }


def run_ruleset(ruleset, answers, as_of, capabilities=None):
    """Evaluate every rule. Verdicts come back sorted by rule id for stable diffing.

    `capabilities` is the set of capability modules this install is licensed for and
    has actually loaded. None means ungated. See Context.
    """
    ctx = Context(answers, as_of, capabilities)
    verdicts = [run_rule(r, ctx) for r in ruleset["rules"]]
    verdicts.sort(key=lambda v: v["rule_id"])
    return {
        "ruleset_version": ruleset["ruleset_version"],
        "as_of": str(ctx.as_of),
        "capabilities": None if ctx.capabilities is None else sorted(ctx.capabilities),
        "verdicts": verdicts,
        "summary": summarize(verdicts),
    }


def summarize(verdicts):
    blocking = [v for v in verdicts if v["status"] == "fail" and v["severity"] == "binding"]
    warning = [v for v in verdicts if v["status"] == "fail" and v["severity"] == "best_practice"]
    # A binding rule that could not be evaluated blocks export for the same reason a
    # failed one does: the obligation is not demonstrated. Unlicensed is not a pass.
    ungated = [v for v in verdicts
               if v["status"] == "unavailable" and v["severity"] == "binding"]
    return {
        "evaluated": sum(1 for v in verdicts
                         if v["status"] not in ("not_applicable", "unavailable")),
        "passed": sum(1 for v in verdicts if v["status"] == "pass"),
        "blocking": len(blocking),
        "warnings": len(warning),
        "not_applicable": sum(1 for v in verdicts if v["status"] == "not_applicable"),
        "unavailable": sum(1 for v in verdicts if v["status"] == "unavailable"),
        "export_allowed": not blocking and not ungated,
    }


def diff_runs(before, after):
    """What surveillance actually reports: verdicts that changed between two runs.

    Both runs must be over the same frozen answer set. Any change is then
    attributable to the ruleset, not to the customer.
    """
    prior = {v["rule_id"]: v for v in before["verdicts"]}
    changes = []
    for v in after["verdicts"]:
        was = prior.get(v["rule_id"])
        rid = v["rule_id"]

        if was is None:
            if v["status"] == "fail":
                changes.append(_change(rid, "new_rule", "(none)",
                                       "%s %s" % (v["severity"], v["status"]), v))
            continue

        # A status flip is the obvious signal.
        if was["status"] != v["status"]:
            changes.append(_change(rid, "status", was["status"], v["status"], v))

        # A severity escalation matters just as much and is easy to miss: a rule
        # that was a warning yesterday and is binding today now blocks an export,
        # even though its pass/fail status never moved.
        if was["severity"] != v["severity"] and v["status"] == "fail":
            kind = ("escalated" if v["severity"] == "binding" else "relaxed")
            changes.append(_change(rid, kind, was["severity"], v["severity"], v))

    changes.sort(key=lambda c: (c["rule_id"], c["change"]))
    return changes


def _change(rule_id, kind, before, after, verdict):
    return {
        "rule_id": rule_id,
        "change": kind,
        "from": before,
        "to": after,
        "severity": verdict["severity"],
        "blocks_export": verdict["severity"] == "binding" and verdict["status"] == "fail",
        "authority_ids": verdict["authority_ids"],
    }


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# static linting
# --------------------------------------------------------------------------

_PAIR_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "matches",
             "includes", "count_gte", "count_eq", "within_days"}
_QUANT_OPS = {"every", "some", "none"}
_LIST_OPS = {"all", "any"}


def lint(node, path="predicate"):
    """Statically check a predicate without evaluating it.

    Evaluation alone is not enough: a quantifier over an absent collection never
    reaches its sub-predicate, so a typo inside it would ship undetected. Returns
    a list of human-readable problems.
    """
    problems = []
    if not isinstance(node, dict) or len(node) != 1:
        return ["%s: must be a single-key object, got %r" % (path, node)]

    (op, arg), = node.items()
    where = "%s.%s" % (path, op)

    if op in _LIST_OPS:
        if not isinstance(arg, list) or not arg:
            problems.append("%s: needs a non-empty list" % where)
        else:
            for i, child in enumerate(arg):
                problems += lint(child, "%s[%d]" % (where, i))
    elif op == "not":
        problems += lint(arg, where)
    elif op == "answered":
        if not isinstance(arg, str):
            problems.append("%s: needs a path string" % where)
    elif op in _PAIR_OPS:
        if not isinstance(arg, list) or len(arg) != 2:
            problems.append("%s: needs [path, operand]" % where)
        elif not isinstance(arg[0], str):
            problems.append("%s: first element must be a path string" % where)
        elif op == "matches":
            try:
                re.compile(arg[1])
            except re.error as exc:
                problems.append("%s: bad regex (%s)" % (where, exc))
    elif op in _QUANT_OPS:
        if not isinstance(arg, list) or len(arg) != 2 or not isinstance(arg[0], str):
            problems.append("%s: needs [path, predicate]" % where)
        else:
            problems += lint(arg[1], where)
    elif op == "for_each":
        if not isinstance(arg, dict) or "path" not in arg or "must" not in arg:
            problems.append("%s: needs an object with path and must" % where)
        else:
            problems += lint(arg["must"], where + ".must")
            if "where" in arg:
                problems += lint(arg["where"], where + ".where")
            extra = set(arg) - {"path", "where", "must"}
            if extra:
                problems.append("%s: unknown keys %s" % (where, sorted(extra)))
    else:
        problems.append("%s: unknown operator %r" % (path, op))

    return problems
