"""Deterministic prose: a clause library with conditional slots, plus narrative.

Tier 1. Standard library only. Never imports engine.

The same answers must always produce the same document. There is no generative
model at the document layer, ever. A section is rendered from two sources:

1. Clauses. Authored templates with named slots filled from the answer set.
   A clause carries a `when` condition over the answers; when it does not hold
   the clause is omitted. Conditions are a tiny equality/presence check on a
   dotted path, deliberately not the engine's operator set: prose selection
   and compliance evaluation are different jobs and must not share a
   vocabulary that would tempt someone to evaluate compliance in a template.
2. Narrative fields. Human-written free text stored under `narrative` in the
   answer set. Never read by a predicate. Rendered verbatim.

The clause library is versioned and pinned by plan_version. It is EMPTY in this
draft. render_section() therefore returns a placeholder block for every section
with no clauses, and the GUI shows that as a PLACEHOLDER. No prose is invented.

A slot with no answer raises rather than rendering blank: a plan with a silent
hole in it is exactly the failure this layer exists to prevent.
"""

import copy
import hashlib
import json
import re

MODULE_VERSION = "2026.09.08"

# The library that ships today. Authoring it is content work, section by
# section, and Sections 1 through 4 arrive as templates from Julio rather
# than being written here.
EMPTY_LIBRARY = {"clause_library_version": "0.0.0", "clauses": []}

_SLOT = re.compile(r"\{([a-zA-Z0-9_.\[\]]+)\}")


class RenderError(Exception):
    """Bad clause, missing slot value, or a library that fails verification."""


def library_hash(library):
    return hashlib.sha256(json.dumps(library, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_library(library):
    """Shape check. A clause needs id, section, text; when is optional."""
    problems = []
    if not isinstance(library, dict) or "clause_library_version" not in library:
        return ["library has no clause_library_version"]
    seen = set()
    for i, clause in enumerate(library.get("clauses", [])):
        where = "clauses[%d]" % i
        for key in ("id", "section", "text"):
            if key not in clause:
                problems.append("%s: missing %r" % (where, key))
        if clause.get("id") in seen:
            problems.append("%s: duplicate id %r" % (where, clause.get("id")))
        seen.add(clause.get("id"))
        if not isinstance(clause.get("section"), int) or not 1 <= clause.get("section", 0) <= 14:
            problems.append("%s: section must be 1..14" % where)
        for cond in clause.get("when", []):
            if "path" not in cond or not ({"eq", "answered"} & set(cond)):
                problems.append("%s: when needs path and one of eq/answered" % where)
    return problems


def _resolve(path, answers):
    cur = answers
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _holds(cond, answers):
    value = _resolve(cond["path"], answers)
    if "eq" in cond:
        return value == cond["eq"]
    present = value not in (None, "", [], {})
    return present if cond["answered"] else not present


def fill(text, answers):
    """Substitute {dotted.path} slots. A missing slot raises."""
    def sub(match):
        value = _resolve(match.group(1), answers)
        if value in (None, "", [], {}):
            raise RenderError("slot %r has no answer" % match.group(1))
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)
    return _SLOT.sub(sub, text)


def render_section(library, section_number, answers, narrative_keys=None):
    """Render one section deterministically.

    Returns a block list. Kinds: `clause` (filled template), `narrative`
    (verbatim human text), `placeholder` (nothing authored for this section).
    """
    problems = verify_library(library)
    if problems:
        raise RenderError("refusing to render from a bad library: %s" % "; ".join(problems))

    blocks = []
    clauses = [c for c in library["clauses"] if c["section"] == section_number]
    for clause in clauses:
        if all(_holds(cond, answers) for cond in clause.get("when", [])):
            blocks.append({"kind": "clause", "id": clause["id"],
                           "text": fill(clause["text"], answers)})

    narrative = (answers.get("narrative") or {}).get("section_%d" % section_number) or {}
    for key in sorted(narrative_keys or narrative.keys()):
        text = narrative.get(key)
        if text:
            blocks.append({"kind": "narrative", "field": key, "text": text})

    if not clauses:
        blocks.insert(0, {
            "kind": "placeholder",
            "reason": "clause library %s carries no clauses for section %d; "
                      "no prose is generated in its place"
                      % (library["clause_library_version"], section_number),
        })

    return {
        "section": section_number,
        "clause_library_version": library["clause_library_version"],
        "clause_library_sha256": library_hash(library),
        "blocks": blocks,
        "deterministic": True,
    }


def render_plan(library, answers, sections=range(1, 15)):
    return [render_section(library, n, answers) for n in sections]


def add_clause(library, clause):
    """Authoring helper. Returns a new library; the version must be bumped by the author."""
    out = copy.deepcopy(library)
    out["clauses"].append(copy.deepcopy(clause))
    problems = verify_library(out)
    if problems:
        raise RenderError("; ".join(problems))
    return out
