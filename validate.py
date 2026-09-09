"""Validate content/ against the schema, plus the invariants JSON Schema cannot express.

Run from the project root:  python validate.py
Exits non-zero on any failure so it can gate a build.
"""
import ast
import io
import json
import os
import sys

from jsonschema import Draft202012Validator

SCHEMA = "content/csp-navigator.schema.json"
CONTENT = "content/csp-navigator.content.json"
RULESET = "content/csp-ruleset.json"
KEV_SNAPSHOT = "content/kev-snapshot.json"

# Capability modules this build ships. A rule naming a capability outside this set
# would be gated off at every install, so a typo here is silent loss of coverage.
import criticality
import kev

CAPABILITIES = {kev.CAPABILITY: kev, criticality.CAPABILITY: criticality}

QUESTIONS = "content/csp-questions.json"

# The seven checklist items across Sections 1 and 5 with known-correct ground truth.
# These are the gap engine's regression suite. Do not edit to make a check pass.
FIXTURES = {
    "struct-defined": True,
    "persons-identified": True,
    "cyso-designated": True,
    "cyso-24-7": False,
    "personnel-notification": False,
    "authority-comms": False,
    "vessel-comms": False,
}

failures = []


def fail(check, detail):
    failures.append("%s: %s" % (check, detail))


schema = json.load(io.open(SCHEMA, encoding="utf-8"))
doc = json.load(io.open(CONTENT, encoding="utf-8"))

# 1. Schema conformance.
for e in sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda x: list(x.path)):
    fail("schema", "%s %s" % (list(e.path), e.message[:200]))

authorities = doc.get("authorities", {})
sections = doc.get("sections", [])


def walk_refs(node, path=""):
    """Yield (json_path, authority_id) for every citation reference in the document."""
    if isinstance(node, list):
        for i, v in enumerate(node):
            for r in walk_refs(v, "%s[%d]" % (path, i)):
                yield r
    elif isinstance(node, dict):
        for k, v in node.items():
            p = "%s/%s" % (path, k)
            if k in ("authority_ids", "connected_authority_ids") and isinstance(v, list):
                for i, a in enumerate(v):
                    yield ("%s[%d]" % (p, i), a)
            else:
                for r in walk_refs(v, p):
                    yield r


# 2. No dangling citations. These validate fine but silently break the gap engine.
for path, aid in walk_refs(doc):
    if aid not in authorities:
        fail("dangling-authority", "%s -> %s not in registry" % (path, aid))

# 3. Bindingness cap. A gap may be binding only if it cites at least one binding
#    authority. This is what stops a policy letter from blocking an export.
for s in sections:
    ga = s.get("gap_analysis") or {}
    for bucket in ("regulatory_gaps", "best_practice_improvements"):
        for g in ga.get(bucket, []):
            if not g.get("binding"):
                continue
            cited = [authorities[a] for a in g["authority_ids"] if a in authorities]
            if cited and not any(a.get("binding") for a in cited):
                fail("bindingness-cap",
                     "section %s gap '%s' is binding but cites only non-binding authorities %s"
                     % (s["number"], g["id"], g["authority_ids"]))

# 4. Regression fixtures intact.
seen = {}
for s in sections:
    for c in s.get("checklist", []):
        if c["id"] in FIXTURES:
            seen[c["id"]] = c["satisfied_by_original"]
for fid, expected in FIXTURES.items():
    if fid not in seen:
        fail("fixture-missing", "checklist item '%s' is gone" % fid)
    elif seen[fid] is not expected:
        fail("fixture-changed",
             "checklist item '%s' satisfied_by_original is %r, expected %r"
             % (fid, seen[fid], expected))

# 5. Spine shape. 101.630(c) fixes 14 sections; holding the order is what lets the
#    product never generate the index that a departure from that order would require.
nums = [s["number"] for s in sections]
if len(sections) != 14:
    fail("spine", "expected 14 sections, found %d" % len(sections))
if sorted(set(nums)) != list(range(1, 15)):
    fail("spine", "section numbers are not exactly 1..14: %s" % nums)
if nums != sorted(nums):
    fail("spine", "sections are not in ascending order: %s" % nums)

# 6. Appendix shape. Skipped until the outline supplies them.
appendices = doc.get("appendices")
if appendices is None:
    print("note: no appendices yet (blocked on reference/MTSA_Cybersecurity_Plan_Outline.docx)")
else:
    if len(appendices) != 22:
        fail("appendices", "expected 22, found %d" % len(appendices))
    desigs = [a["designation"] for a in appendices]
    expected = [chr(c) for c in range(ord("A"), ord("V") + 1)]
    if sorted(desigs) != expected:
        fail("appendices", "designations are not exactly A..V: %s" % "".join(sorted(desigs)))
    if desigs != sorted(desigs):
        fail("appendices", "appendices are not in A..V order: %s" % "".join(desigs))
    # Every entity-store view is claimed exactly once. A duplicated register would
    # render the same table under two appendix letters.
    used = [a["register"] for a in appendices if a.get("register")]
    if len(used) != len(set(used)):
        fail("appendices", "a register is claimed by more than one appendix: %s" % used)
    for a in appendices:
        if a.get("kind") == "register_view":
            if not a.get("register"):
                fail("appendices", "register_view '%s' has no register" % a["id"])
            if a.get("blocks"):
                fail("appendices", "register_view '%s' must not carry authored blocks" % a["id"])
            if not a.get("controlled_attachment"):
                fail("appendices",
                     "register_view '%s' must be a controlled attachment: a register rendered "
                     "from the entity store cannot version with the plan body" % a["id"])

# 7. Ruleset. Optional until it exists, gated hard once it does.
ruleset = None
if os.path.exists(RULESET):
    import engine

    ruleset = json.load(io.open(RULESET, encoding="utf-8"))
    seen_ids = set()
    sections_with_rules = set()

    for rule in ruleset.get("rules", []):
        rid = rule.get("id", "(unnamed)")

        if rid in seen_ids:
            fail("rule-duplicate", "two rules share the id '%s'" % rid)
        seen_ids.add(rid)

        for key in ("authority_ids", "severity", "predicate"):
            if key not in rule:
                fail("rule-shape", "rule '%s' has no %s" % (rid, key))

        if rule.get("severity") not in ("binding", "best_practice"):
            fail("rule-shape", "rule '%s' has severity %r" % (rid, rule.get("severity")))

        # Dangling citations break the gap engine silently, same as for gaps.
        for aid in rule.get("authority_ids", []):
            if aid not in authorities:
                fail("rule-dangling-authority", "rule '%s' cites %s" % (rid, aid))

        # The cap, identical in force to the one applied to authored gaps.
        cited = [authorities[a] for a in rule.get("authority_ids", []) if a in authorities]
        if rule.get("severity") == "binding" and cited and not any(a["binding"] for a in cited):
            fail("rule-bindingness-cap",
                 "rule '%s' is binding but cites only guidance %s"
                 % (rid, rule["authority_ids"]))

        # A gated rule must name a capability this build actually supplies.
        need = rule.get("requires_capability")
        if need is not None and need not in CAPABILITIES:
            fail("rule-capability",
                 "rule '%s' requires capability %r, which no module supplies" % (rid, need))

        sec = rule.get("section")
        if sec is not None:
            if sec not in range(1, 15):
                fail("rule-section", "rule '%s' names section %r" % (rid, sec))
            else:
                sections_with_rules.add(sec)

        # A quantifier over an absent collection never reaches its own body, so
        # predicates have to be checked statically rather than by running them.
        for problem in engine.lint(rule.get("predicate", {})):
            fail("rule-predicate", "rule '%s' %s" % (rid, problem))
        if "applies_to" in rule:
            for problem in engine.lint(rule["applies_to"], "applies_to"):
                fail("rule-predicate", "rule '%s' %s" % (rid, problem))

        # requirement_id, when present, must name a real outline bullet.
        req = rule.get("requirement_id")
        if req:
            owner = next((s for s in sections if s["number"] == sec), None)
            known = {r["id"] for r in (owner or {}).get("requirements", [])}
            if owner is None or req not in known:
                fail("rule-requirement",
                     "rule '%s' cites requirement '%s', absent from section %s"
                     % (rid, req, sec))

# 7b. Pinned capability data. A truncated or empty KEV snapshot is the dangerous
#     failure: every KEV rule would pass vacuously, which looks like compliance.
if os.path.exists(KEV_SNAPSHOT):
    for problem in kev.verify(kev.load(KEV_SNAPSHOT)):
        fail("kev-snapshot", problem)

# 7c. Module boundaries. Components must stay independently changeable, so the
#     dependency direction is checked mechanically rather than by convention.
#
#     Tier 0  engine.py            stdlib only. Knows nothing about any capability.
#     Tier 1  capability modules   stdlib only. Never import engine, never import
#                                  each other. They project facts onto an answer
#                                  set; the engine reads the result as plain JSON.
#     Tier 2  composites           may import tier 1.
#     Tier 3  app facade, demos,   may import anything.
#             validate
#
#     A capability module that imported engine could not be dropped from a build,
#     which is exactly what the entitlement gate has to be able to do.
TIER_1 = ("kev.py", "store.py", "criticality.py", "records.py", "plan_version.py",
          "render.py", "license.py", "audit.py", "ingest.py")
LAYERS = {"engine.py": set()}
LAYERS.update({name: set() for name in TIER_1})
# The GUI imports the facade and nothing else. That is what keeps a module
# change from breaking the UI.
LAYERS["serve.py"] = {"app"}
# Reaching the network at import time defeats the SSI argument for these modules:
# an offline install must be able to load them and never open a socket.
NO_AMBIENT_NETWORK = ("engine.py",) + TIER_1
NETWORK_MODULES = ("urllib", "http", "socket", "ssl", "ftplib", "requests", "asyncio")
# jsonschema belongs to this file alone. Shipped code has no third-party dependency.
THIRD_PARTY = ("jsonschema",)

project_modules = {f[:-3] for f in os.listdir(".") if f.endswith(".py")}


def imports_in(tree, top_level_only):
    """Yield (module_name, is_top_level) for every import in a parsed module."""
    top = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top.add(node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node in top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0], node in top


for filename, allowed in sorted(LAYERS.items()):
    if not os.path.exists(filename):
        continue
    tree = ast.parse(io.open(filename, encoding="utf-8").read(), filename)
    for name, top_level in imports_in(tree, True):
        if name in project_modules and name != filename[:-3] and name not in allowed:
            fail("module-boundary",
                 "%s imports project module '%s'; its tier may import %s"
                 % (filename, name, sorted(allowed) or "nothing"))
        if filename in NO_AMBIENT_NETWORK and top_level and name in NETWORK_MODULES:
            fail("module-boundary",
                 "%s imports '%s' at module scope; a network module must be "
                 "imported inside the function that uses it, so the pure half "
                 "of the module has no network reachability at import time"
                 % (filename, name))

for filename in sorted(f for f in os.listdir(".") if f.endswith(".py") and f != "validate.py"):
    tree = ast.parse(io.open(filename, encoding="utf-8").read(), filename)
    for name, _ in imports_in(tree, True):
        if name in THIRD_PARTY:
            fail("third-party", "%s imports %s; shipped code has no third-party dependency"
                 % (filename, name))

# 7d. The question module. Every path a rule reads must be a constrained type.
#     Free text pushes rule authoring into regex hunting, so a predicate may only
#     presence-check an identifier or short_text with `answered`, and may never
#     read a narrative field at all.
CONSTRAINED = {"enum", "enum_multi", "boolean", "number", "date", "tri_state", "entity_ref"}
PRESENCE_ONLY = {"identifier", "short_text", "identifier_list"}
CONTAINER_OPS = {"answered", "count_gte", "count_eq", "every", "some", "none", "for_each"}


def rule_reads(node, rid, prefix=""):
    """Yield (path, op) for every answer path a predicate reads."""
    if not isinstance(node, dict):
        return
    for op, arg in node.items():
        if op in ("all", "any"):
            for c in arg:
                yield from rule_reads(c, rid, prefix)
        elif op == "not":
            yield from rule_reads(arg, rid, prefix)
        elif op == "answered":
            yield _join(prefix, arg), op
        elif op in ("every", "some", "none"):
            yield _join(prefix, arg[0]), op
            yield from rule_reads(arg[1], rid, _join(prefix, arg[0]))
        elif op == "for_each":
            yield _join(prefix, arg["path"]), op
            yield from rule_reads(arg.get("where"), rid, _join(prefix, arg["path"]))
            yield from rule_reads(arg["must"], rid, _join(prefix, arg["path"]))
        elif isinstance(arg, list) and arg and isinstance(arg[0], str):
            yield _join(prefix, arg[0]), op


def _join(prefix, path):
    if path.startswith("$."):
        return path[2:]
    return "%s.%s" % (prefix, path) if prefix else path


questions = None
if os.path.exists(QUESTIONS) and ruleset is not None:
    import re

    import engine

    questions = json.load(io.open(QUESTIONS, encoding="utf-8"))
    known_types = set(questions.get("types", []))
    reads = {}
    for rule in ruleset.get("rules", []):
        for path, op in list(rule_reads(rule.get("predicate"), rule["id"])) + \
                list(rule_reads(rule.get("applies_to"), rule["id"])):
            reads.setdefault(path, set()).add(op)

    def check_typed(qid, path, qtype):
        ops = reads.get(path, set())
        if qtype == "narrative" and ops:
            fail("question-typing", "%s: narrative field %s is read by a predicate (%s)"
                 % (qid, path, sorted(ops)))
        elif qtype in PRESENCE_ONLY and ops - {"answered"}:
            fail("question-typing", "%s: %s field %s is read by %s; only `answered` may read it"
                 % (qid, qtype, path, sorted(ops - {"answered"})))
        elif qtype == "entity_list" and ops - CONTAINER_OPS:
            fail("question-typing", "%s: entity_list %s is read by scalar operators %s"
                 % (qid, path, sorted(ops - CONTAINER_OPS)))
        elif qtype not in CONSTRAINED | PRESENCE_ONLY | {"entity_list", "narrative"} and ops:
            fail("question-typing", "%s: %s has type %r, which no predicate may read"
                 % (qid, path, qtype))

    seen_q = set()
    q_paths = set()
    for q in questions.get("questions", []):
        qid = q.get("id", "(unnamed)")
        if qid in seen_q:
            fail("question-duplicate", "two questions share the id %r" % qid)
        seen_q.add(qid)
        for key in ("path", "type", "prompt", "authority_ids", "section"):
            if key not in q:
                fail("question-shape", "%s has no %s" % (qid, key))
        if q.get("type") not in known_types:
            fail("question-shape", "%s has type %r, not in the module type list" % (qid, q.get("type")))
        if q.get("section") not in range(1, 15):
            fail("question-shape", "%s names section %r" % (qid, q.get("section")))
        if not q.get("authority_ids"):
            fail("question-citation", "%s cites nothing; every question carries a registry citation" % qid)
        for aid in q.get("authority_ids", []):
            if aid not in authorities:
                fail("question-dangling-authority", "%s cites %s, not in registry" % (qid, aid))
        if q.get("type") in ("enum", "tri_state") and not q.get("options"):
            fail("question-shape", "%s is %s but has no options" % (qid, q["type"]))
        if q.get("type") == "tri_state" and q.get("options") != ["yes", "no", "not_applicable"]:
            fail("question-shape", "%s tri_state options must be yes/no/not_applicable" % qid)
        if "format" in q:
            try:
                re.compile(q["format"])
            except re.error as exc:
                fail("question-shape", "%s has a bad format regex (%s)" % (qid, exc))
        if "applies_to" in q:
            for problem in engine.lint(q["applies_to"], "applies_to"):
                fail("question-predicate", "%s %s" % (qid, problem))
        check_typed(qid, q.get("path", ""), q.get("type"))
        q_paths.add(q.get("path"))
        if q.get("type") == "entity_list":
            for f in q.get("fields", []):
                if "key" not in f or "type" not in f:
                    fail("question-shape", "%s field %r needs key and type" % (qid, f))
                    continue
                if f["type"] not in known_types and f["type"] != "identifier_list":
                    fail("question-shape", "%s field %s has type %r" % (qid, f["key"], f["type"]))
                if f["type"] in ("enum", "enum_multi") and not f.get("options"):
                    fail("question-shape", "%s field %s is %s but has no options" % (qid, f["key"], f["type"]))
                sub = "%s.%s" % (q["path"], f["key"])
                check_typed(qid, sub, f["type"])
                q_paths.add(sub)

    # The guided steps. Every listed question must exist and appear in one step
    # only; a computed step names no questions. The special kinds are closed.
    STEP_KINDS = {"questions", "inventory", "sort", "followups", "review"}
    placed = set()
    for st in questions.get("steps", []):
        sid = st.get("id", "(unnamed)")
        for key in ("id", "title", "kind"):
            if key not in st:
                fail("step-shape", "step %s has no %s" % (sid, key))
        if st.get("kind") not in STEP_KINDS:
            fail("step-shape", "step %s has kind %r" % (sid, st.get("kind")))
        if st.get("kind") == "questions" and not st.get("questions"):
            fail("step-shape", "step %s is a questions step with no questions" % sid)
        if st.get("kind") != "questions" and st.get("questions"):
            fail("step-shape", "step %s is a %s step but lists questions" % (sid, st.get("kind")))
        for qid in st.get("questions", []):
            if qid not in seen_q:
                fail("step-question", "step %s lists unknown question %r" % (sid, qid))
            if qid in placed:
                fail("step-question", "question %r appears in more than one step" % qid)
            placed.add(qid)

# 7e. GUI hygiene. The plan is SSI under 49 CFR 1520: the GUI binds 127.0.0.1
#     only, and loads nothing from anywhere else.
GUI_FILES = ["serve.py"]
for folder, _, names in os.walk("gui"):
    GUI_FILES += [os.path.join(folder, n) for n in names
                  if n.rsplit(".", 1)[-1] in ("html", "css", "js", "json", "svg", "txt", "csv", "nessus")]
OUTBOUND = ("https://", "http://", "@import", "fonts.googleapis", "cdn.", "//unpkg", "0.0.0.0")
for path in GUI_FILES:
    if not os.path.isfile(path):
        continue
    text = io.open(path, encoding="utf-8").read()
    for marker in OUTBOUND:
        for line_no, line in enumerate(text.splitlines(), 1):
            if marker in line and "127.0.0.1" not in line:
                fail("gui-outbound", "%s:%d contains %r; no CDN, no external font, no outbound "
                     "request, never 0.0.0.0" % (path, line_no, marker))

# 8. Advisory: registry entries nothing references yet.
referenced = {aid for _, aid in walk_refs(doc)}
if ruleset:
    for rule in ruleset.get("rules", []):
        referenced |= set(rule.get("authority_ids", []))
orphans = sorted(set(authorities) - referenced)

if failures:
    print("FAIL (%d)" % len(failures))
    for f in failures:
        print("  " + f)
    sys.exit(1)

print("PASS  %d authorities, %d sections, %d fixtures verified"
      % (len(authorities), len(sections), len(FIXTURES)))

if ruleset:
    rules = ruleset["rules"]
    total_reqs = sum(len(s.get("requirements", [])) for s in sections)
    covered = {r["requirement_id"] for r in rules if r.get("requirement_id")}
    print("ruleset %s: %d rules (%d binding, %d best practice) over %d sections"
          % (ruleset["ruleset_version"], len(rules),
             sum(1 for r in rules if r["severity"] == "binding"),
             sum(1 for r in rules if r["severity"] == "best_practice"),
             len({r["section"] for r in rules if r.get("section")})))
    print("requirement coverage: %d of %d outline bullets have a rule (%.0f%%)"
          % (len(covered), total_reqs, 100.0 * len(covered) / total_reqs))

    # Per-section, because the aggregate implies a uniform target and there is not one.
    # Sections 1-4 are standard blurb authored outside the tool; the assessment sections
    # are where coverage is worth driving up. See "Build priority" in CLAUDE.md.
    by_section = {}
    for r in rules:
        if r.get("requirement_id") and r.get("section"):
            by_section.setdefault(r["section"], set()).add(r["requirement_id"])
    print("  by section:")
    for sec in sections:
        reqs = sec.get("requirements", [])
        if not reqs:
            continue
        hit = len(by_section.get(sec["number"], set()) & {r["id"] for r in reqs})
        print("    %2s  %2d/%-3d  %s"
              % (sec["number"], hit, len(reqs), sec.get("title", "")))

if questions:
    read_paths = set(reads)
    uncovered = sorted(p for p in read_paths if p not in q_paths)
    print("question module %s: %d questions; %d of %d rule-read paths have a question"
          % (questions["question_module_version"], len(questions["questions"]),
             len(read_paths & q_paths), len(read_paths)))
    if uncovered:
        print("  rule-read paths with no question node (derived by enrichment or unauthored): %s"
              % ", ".join(uncovered))

if orphans:
    print("unreferenced authorities (%d): %s" % (len(orphans), ", ".join(orphans)))
