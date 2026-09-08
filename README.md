# MTSA Cybersecurity Plan tool

Interview-driven generator and gap engine for 33 CFR Part 101 Subpart F
Cybersecurity Plans. Rough draft: a facade, tier-1 modules with real interfaces,
and a localhost GUI. `CLAUDE.md` is authoritative on the domain and the settled
decisions; `GUI-HANDOFF.md` is the build brief this draft was built to;
`QUESTIONS.md` lists what had to be assumed.

## Run

```bash
python validate.py        # schema, invariants, fixtures, module boundaries
python demo_engine.py     # engine vs the seven known-truth fixtures
python demo_kev.py        # KEV join, entitlement gate, surveillance
python serve.py --check   # every GUI data path, in memory, no socket
python serve.py           # GUI on http://127.0.0.1:8765/
```

Python 3 standard library only. `jsonschema` is used by `validate.py` alone and
is never imported by shipped code; `validate.py` fails the build if it is.

## Layout

```
engine.py         tier 0   deterministic rule engine
kev.py            tier 1   CISA KEV capability module (the worked example)
store.py          tier 1   answer store interface, JSON file and in-memory backends
criticality.py    tier 1   two-stage narrowing, facilitated workshop session
records.py        tier 1   101.640 record categories with per-category retention
plan_version.py   tier 1   freeze, content hash, four pins, amendment diff
render.py         tier 1   deterministic prose from a clause library (empty)
license.py        tier 1   entitlements, fingerprint, offline challenge (crypto stubbed)
audit.py          tier 1   section-scoped runs, deficiency vs recommendation
app.py            tier 3   the facade; the GUI imports this and nothing else
serve.py          tier 3   127.0.0.1 HTTP server and JSON API
gui/              tier 3   vanilla HTML, CSS and JS, nothing loaded externally
content/          versioned authoring data: schema, instance, ruleset, questions, KEV snapshot
fixtures/         regression answer sets
reference/        regulation and source material, read-only
```

## Real versus stubbed

| Area | State |
|---|---|
| Rule engine, KEV join, entitlement gate, surveillance diffs | Real |
| Answer store, facility transfer, shared-CySO derivation | Real |
| Record categories, retention clocks, drill cadence | Real |
| plan_version freeze, hashing, pins, amendment diff | Real |
| Criticality stage 1 scope with attributed answers | Real |
| Criticality stage 2 rank (CFDD instrument in SAM) | Placeholder |
| Clause library | Interface real, library empty, placeholder per section |
| Token parsing, expiry, tier ceilings, fingerprint N-of-M | Real |
| Signature verification, offline activation, ruleset seal | Placeholder |
| Audit runs, scope statement, finding objects | Real |
| Finding templates and remediation text | Placeholder |

Every placeholder carries a visible PLACEHOLDER badge in the rendered GUI.
Nothing in the GUI invents a verdict, a citation, a finding, or remediation
text. All citations come from the authorities registry in
`content/csp-navigator.content.json` by `authority_id`.

## SSI

The plan is Sensitive Security Information under 49 CFR 1520 once populated.
The GUI binds `127.0.0.1` only, loads no external resource, and makes no
outbound request. `validate.py` section 7e checks the GUI files for any
outbound marker.
