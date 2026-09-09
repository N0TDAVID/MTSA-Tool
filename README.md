# MTSA Cybersecurity Plan tool

Interview-driven generator and gap engine for 33 CFR Part 101 Subpart F
Cybersecurity Plans. Rough draft: a facade, tier-1 modules with real interfaces,
and a localhost GUI. `CLAUDE.md` is authoritative on the domain and the settled
decisions; `GUI-HANDOFF.md` is the build brief this draft was built to;
`QUESTIONS.md` lists what had to be assumed.

## Setup

```bash
git clone <repo> && cd MTSA-Tool
python bootstrap.py
```

`bootstrap.py` checks the interpreter and the checkout, creates `.venv`,
installs the one dependency, then runs every entry point to prove the checkout
works. Re-running it is safe.

| Flag | Effect |
|---|---|
| `--offline` | make no network call at all; skip the install |
| `--no-venv` | use the current interpreter instead of creating one |
| `--check` | verify only: no venv, no install, no writes |
| `--venv PATH` | somewhere other than `.venv` |

Python 3.9 or newer, developed on 3.11. Standard library only, with one
exception: `jsonschema` is the single third-party package, `validate.py` is the
only file that imports it, and `validate.py` fails the build if anything else
does. An install with no route to PyPI is therefore complete apart from the
validator, which is why a failed install is a warning here and not an abort. To
supply it offline:

```bash
.venv/bin/python -m pip install --no-index --find-links DIR jsonschema
```

There is nothing to compile and no configuration file to write.

## Run

Activate first with `source .venv/bin/activate`, or `.venv\Scripts\activate`
on Windows, or call `.venv/bin/python` directly.

```bash
python validate.py        # schema, invariants, fixtures, module boundaries
python demo_engine.py     # engine vs the seven known-truth fixtures
python demo_kev.py        # KEV join, entitlement gate, surveillance
python serve.py --check   # every GUI data path, in memory, no socket
python serve.py           # GUI on http://127.0.0.1:8765/
```

`serve.py` also takes `--port` (default 8765), `--workspace` (default
`./workspace`), and `--as-of DATE`. The workspace is the answer store on disk:
it is created and seeded with a demo tenant on first run, and it is gitignored,
so deleting it starts from empty. `--as-of` pins the single clock read in the
program, which is what makes a run reproducible.

`python kev.py refresh` re-fetches the CISA catalog into
`content/kev-snapshot.json`. It is the only command in the project that reaches
the network, apart from the one install above. Evaluation never does.

## The two areas of the GUI

**Plan builder** is the client walk-through: a few questions at a time, answers
saved as they are changed, percent complete and "what is missing" per step and
per section, no regulation text. It collects the asset inventory (by hand, by
CSV upload, or by importing a Nessus export or scanner CSV), runs the two-question
criticality sort as a group activity, and then asks only the per-asset follow-up
questions the sorted inventory needs. Later steps, the registers, the crosswalk,
and the gap report all change as assets are sorted.

**Consultant tools** are the working screens with citations and evidence:
facilities and plans, the full interview, the workshop, the seven registers, the
crosswalk, the gap report, licensing, surveillance, audit mode, and records.

The theme follows the ABS MTSA training portal. Its three fonts are bundled
locally under `gui/fonts/` under the SIL Open Font License.

## Layout

```
bootstrap.py      tooling  interpreter check, venv, dependency, verification pass
engine.py         tier 0   deterministic rule engine
kev.py            tier 1   CISA KEV capability module (the worked example)
ingest.py         tier 1   inventory CSV and scanner import: parse, exact match, propose
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
