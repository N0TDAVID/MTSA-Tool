# Build brief: GUI and program skeleton

Instructions for an unattended session. Read `CLAUDE.md` first and treat it as
authoritative; this file is the build order, not a restatement of the design.

Your job is a working rough draft of the whole program: a facade, a set of modules
with real interfaces, and a GUI that exercises every feature discussed so far. The
engine and the KEV module are already real. Most of the rest is not, and the point
of this pass is to make the shape visible without faking anything.

## The one rule that outranks the others

**Real where real, visibly stubbed where stubbed, never fake.**

This is compliance software for a regulated facility. A screen that renders
plausible-looking gap output from placeholder logic is worse than a blank screen,
because someone will screenshot it and believe it. Anything not backed by a real
computation must say so on its face, in the UI, not just in a code comment. Use a
visible `PLACEHOLDER` badge and keep it in the rendered output.

Do not invent compliance verdicts, citations, findings, or remediation text.

## Verify before and after every change

```bash
python validate.py
python demo_engine.py
python demo_kev.py
```

All three must exit zero when you finish. `validate.py` gates schema conformance,
the seven regression fixtures, the bindingness cap, capability declarations, the
KEV snapshot, and the module boundaries described below. If one of them goes red,
fix your change; do not edit the check to make it pass, and do not edit the
fixtures.

## Module architecture

The user's requirement is that components stay independently changeable. The
dependency direction is enforced mechanically by `validate.py` section 7c, which
parses imports and fails the build on a violation. Do not weaken it.

```
Tier 0   engine.py                stdlib only. Knows nothing about any capability.
Tier 1   capability modules       stdlib only. Never import engine. Never import
         infrastructure modules   each other. Independently droppable from a build.
Tier 2   composites               may import tier 1.
Tier 3   app.py facade, GUI,      may import anything.
         demos, validate.py
```

Tier 1 modules never import `engine` because the entitlement gate has to be able to
drop them from a build entirely. A capability module that the engine depended on
could not be dropped, and a capability that cannot be dropped is not a capability.

To register a new tier-1 module, add it to `LAYERS` in `validate.py` with an empty
allowed set, and add it to `CAPABILITIES` if it gates rules.

### How a capability module reaches the engine

One way only, and `kev.py` is the worked example. Read it before writing another.

1. The module exposes `CAPABILITY = "<name>"` and a pure `enrich(answers, ...)` that
   returns a **new** answer set with derived fields added. It never mutates input.
2. A rule in `content/csp-ruleset.json` declares `"requires_capability": "<name>"`.
3. The caller passes `capabilities={...}` to `engine.run_ruleset`.
4. An unlicensed rule comes back `unavailable`, which blocks export for a binding
   rule. Never `pass`.

Never add engine operators for a capability. The operator set is closed, and an
operator present for one tenant and absent for another is not a deterministic
engine.

### Modules to create

Create these as tier 1 with honest interfaces. Implement what is cheap and
mechanical; raise `NotImplementedError` with a one-line reason for what is not, and
have the GUI surface that as a `PLACEHOLDER` badge rather than hiding the screen.

| Module | Purpose | Real now? |
|---|---|---|
| `store.py` | The answer store behind an interface the engine never reaches around. An abstract `AnswerStore` plus a `JsonFileStore` implementation. This is the seam that lets custody move between customer workstation and our cloud without a rewrite. | Yes, build it properly. |
| `criticality.py` | Two-stage narrowing. Stage 1 scope: per asset, is it critical and could compromise cause a TSI. Stage 2 rank: order survivors by consequence. Multi-participant session with answer attribution, because stage 1 is a facilitated workshop, not a single respondent. | Interfaces and stage 1 data model real; the CFDD weighted scoring is a stub, it lives in SAM. |
| `records.py` | The 101.640 record categories and their retention clocks. Retention is **per category**, read from the category, never a hardcoded constant. The six categories are training, drills, exercises, cybersecurity threats, reportable cyber incidents, and audits of the Plan. Custodian (VSO/FSO) and accountable officer (CySO) are different fields. | Yes, the CFR gives the field list outright. See CLAUDE.md "Still open". |
| `plan_version.py` | Freeze an answer set, content-hash it, pin ruleset + question module + clause library + plan versions. The KEV snapshot rides inside the ruleset bundle version as `ruleset@X+kev.Y`, so it stays four pins. | Yes, it is hashing and serialisation. |
| `render.py` | Deterministic prose. Clause library with conditional slots plus human-written narrative fields. | Interface real, clause library empty. **No generative model, ever.** |
| `license.py` | Entitlement token verification against a public key, machine fingerprint with N-of-M matching, offline challenge-response activation. | Stub the crypto, make the entitlement set real, because the gate depends on it. |
| `audit.py` | Section-scoped audit runs. A run carries its scope and the report states it. Deficiency and recommendation are different objects with different severity. Findings are keyed by `authority_id`. | Interfaces real, finding templates empty. |

Leave `kev.crosswalk()` where it is for now. When `criticality.py` lands, the
crosswalk moves to a tier-2 `crosswalk.py` that imports both, and the version in
`kev.py` is deleted rather than left behind.

## The facade

`app.py`, tier 3. **The GUI imports this and nothing else.** No GUI file imports
`engine`, `kev`, or any tier-1 module directly. That is what keeps a module change
from breaking the UI.

It owns three things the modules deliberately do not: the entitlement set, the
answer store instance, and `as_of`. Sketch:

```python
class Session:
    def __init__(self, store, ruleset, entitlements, as_of): ...

    def capabilities(self): ...           # what this install may use
    def answers(self, plan_id): ...       # via store, never a direct file read
    def evaluate(self, plan_id): ...      # enrich through licensed modules, then run
    def crosswalk(self, plan_id): ...
    def registers(self): ...              # the seven register views
    def freeze(self, plan_id): ...        # -> plan_version
    def surveillance(self, plan_id, previous): ...
```

`evaluate` is where enrichment is wired: for each licensed capability module, call
its `enrich`, then call `engine.run_ruleset(..., capabilities=self.entitlements)`.
An unlicensed module is simply not called, and its rules come back `unavailable`.

`as_of` is an explicit input everywhere. Never call `datetime.now()` below the
facade. The facade may read the clock once, at session construction, and pass the
value down.

## GUI

**Stack: Python stdlib `http.server` bound to `127.0.0.1`, serving vanilla
HTML/CSS/JS.** Decided rather than left open, for these reasons:

- Zero third-party dependencies, matching the engine's existing constraint.
- Installs on a locked-down Windows workstation on or beside an OT network with no
  package manager and no internet.
- Most of this UI is tables (registers, crosswalk, gap report), which render far
  better in HTML than in Tkinter.

Constraints that follow from SSI and are not negotiable:

- Bind `127.0.0.1` only. Never `0.0.0.0`.
- No CDN, no external fonts, no analytics, no outbound request of any kind. All CSS
  and JS is local and inline or served from `gui/`.
- Mark SSI-bearing screens visibly, per 49 CFR 1520.
- No file upload path that leaves the machine.

Put it in `gui/`, served by `serve.py` at root. Screens, each exercising something
already discussed:

1. **Facilities and plans.** Tenant is the consulting org, facility is a child
   entity, plan-to-facility is many-to-many. Show the transfer path exists.
2. **Interview.** Question nodes with `applies_to` predicates against the asset type
   discriminator. Constrained answer types only: enum, boolean, number, date, entity
   reference. Tri-state where applicability varies: `yes` / `no` / `not_applicable`.
   Free text only in narrative fields no predicate reads.
3. **Criticality workshop.** Multi-participant, answers attributed, a facilitator may
   be driving while the client answers. Stage 1 then stage 2, visibly two stages.
4. **Registers.** The seven register views, rendered from the entity store. All are
   controlled attachments and version separately from the plan body.
5. **Crosswalk.** Real, backed by `kev.crosswalk`. Every row resolves to mitigated,
   planned with owner/date/cost, accepted with a reason, or `unresolved`. Do not
   hide `unresolved`; it is the honest state given the current entity shape.
6. **Gap report.** Real, backed by `engine.run_ruleset`. Show status, severity,
   citation, and the evidence paths. Binding failures block export, best-practice
   failures warn, and the two must never be conflated in the UI.
7. **Licensing.** Show the entitlement set and let it be toggled in the draft. Toggling
   `kev` off must visibly turn `kev-without-delay` to `unavailable` and block export.
   This screen is the clearest demonstration of the module boundary.
8. **Surveillance.** Ruleset diff (trigger 1) and KEV catalog diff (trigger 2) as
   separate paths, because they are. Show the outbound telemetry shape next to the
   agent-local finding, so the redaction is visible.
9. **Audit mode.** Section-scoped, scope stated on the report.

## Do not

- Do not edit `content/csp-navigator.content.json` ground truth, `fixtures/`, or
  anything in `reference/`.
- Do not add a third-party dependency. `jsonschema` is the only one and it is used
  by `validate.py` alone, never by shipped code.
- Do not call any model API, and do not add a generative step at the document layer.
- Do not cite the CFR from memory. Every citation in UI copy comes from the
  `authorities` registry in the content instance, by `authority_id`. If you need a
  citation that is not in the registry, stop and write it down rather than guessing;
  every source so far has miscited this rule.
- Do not conflate `authorities[id].binding` with `gap.binding`. See CLAUDE.md.
- Do not use CISA's KEV `dueDate` as a remediation deadline. It is a federal civilian
  agency deadline under BOD 26-04 with no application to an MTSA facility, and it is
  more permissive than the actual "without delay" standard at 101.650(e)(3)(i).
- Do not relitigate anything under "Settled decisions" in CLAUDE.md.
- No emojis. No em-dashes. Technical register.

## When you are unsure

Write the question into `QUESTIONS.md` at root, with enough context to answer it
cold, and keep building around it under a stated assumption. Do not stop the whole
build on one open question, and do not resolve a design question by guessing and
moving on silently.

Known open questions already recorded, do not re-derive: where the hosted half runs,
tier boundaries, ingest of an existing plan, whether the 101.640 records are an
entity type or a register view, client user role scoping. See CLAUDE.md "Still open".

## Definition of done

- `python validate.py`, `python demo_engine.py`, `python demo_kev.py` all exit zero.
- `python serve.py` starts and every screen above renders without a traceback.
- No GUI file imports a tier-0 or tier-1 module directly.
- Every placeholder is visibly labelled in the rendered UI.
- `QUESTIONS.md` lists anything you had to assume.
