# Open questions from the GUI and skeleton build

Written during the unattended build described in GUI-HANDOFF.md. Each entry has
enough context to answer cold, states the assumption the build proceeded under,
and names where the assumption lives in code. None of these were resolved
silently. Questions already recorded under "Still open" in CLAUDE.md are not
repeated here.

## 1. The question module is a new content file

CLAUDE.md says to ask before creating a new file when an existing one could hold
the content. `plan_version` pins a question module version as one of its four
pins, which makes the question module a separately versioned artifact rather
than part of the content instance or the ruleset.

Assumption: `content/csp-questions.json` is that artifact, with its own
`question_module_version`. `validate.py` section 7d checks it. If it should
instead live inside `csp-navigator.content.json`, the schema needs a
`questions` definition and the pin becomes the content schema version.

## 2. Two presence-only text types added to Answer typing

The ruleset reads `cyso.name`, `cyso.title`, `org.personnel[].duties`,
`cyso.contact.oncall_protocol` and similar with `answered`. Those are single-line
text, not enums, but a presence check on a name is not regex hunting.

Assumption: `identifier` and `short_text` are legitimate answer types on the
condition that a predicate may only ever read them with `answered`.
`validate.py` fails the build if any other operator reads one, and fails if any
operator reads a `narrative` field at all. If the intent was stricter, that is,
every rule-read path must be an enum or boolean without exception, then names
need to become entity references into a personnel store.

## 3. Recordkeeping sections for vessels and OCS facilities are not in the registry

`records.py` transcribes field lists and retention from 104.235, 105.225 and
106.230, all verbatim in `reference/`. Only `cfr:105.225` is a registry entry.
The UI therefore cites `cfr:101.640` (which names all three) for every record
category and shows the paragraph the fields came from as "verified against
reference text, not a registry entry".

Question: should `cfr:104.235` and `cfr:106.230` be added to `authorities`, and
should the individual paragraphs, for example `cfr:105.225(b)(1)`, be entries so
a record category can cite its exact paragraph? Same question for
`cfr:101.625(d)(11)`, the CySO records duty, which exists only as the parent
`cfr:101.625(d)`. The registry is content ground truth, so it was not edited.

## 4. The TSI scope question has no TSI-specific citation

Stage 1 asks whether compromise could cause a Transportation Security Incident.
The registry has no entry defining TSI, and the definition was not cited from
memory. `criticality.SCOPE_QUESTIONS` cites `cfr:101.650(b)`, the critical
system designation duty, for both stage 1 questions.

Question: which registry entry should the TSI question cite? The definition of
Transportation Security Incident sits outside Subpart F and would need to be
verified against the eCFR before it is added.

## 5. Consensus rule for attributed workshop answers

CLAUDE.md requires attribution but does not say how several participants'
answers combine.

Assumption, in `criticality.effective_answers`: the latest answer from every
source must agree. Any disagreement is `disputed`, which is not a yes and
never scopes an asset in. There is no tie-break, no facilitator override, and no
role weighting. The facilitator resolves a dispute by having the room record a
further answer.

## 6. Signature primitive for tokens and ruleset bundles

The standard library has no Ed25519 or ECDSA verification. `license.py` raises
`NotImplementedError` from `verify_signature` and `verify_ruleset_signature`,
and every screen that depends on them carries a PLACEHOLDER badge.

Question for the infrastructure owners: vendor a pure-Python Ed25519
implementation inside the trust boundary, or call platform crypto (Windows CNG
through ctypes) and accept a Windows-only agent? Either answer keeps the no
third-party dependency rule; the second one loses portability.

## 7. Entitlement names that are tiers rather than modules

The licensing screen toggles `kev`, `criticality`, `surveillance`, `custody`,
`asset_alerts`. Only the first two have a module and gate rules. The other three
are tier features named in CLAUDE.md with no module behind them yet.

Assumption: they are carried in the token and in `app.TOGGLEABLE` so the UI can
say what an install may do, and they change no engine behaviour. If a tier
feature should gate anything, it needs a module declaring `CAPABILITY`.

## 8. A tenth screen for records

GUI-HANDOFF.md lists nine screens. The 101.640 records live nowhere on that
list, and `records.py` is a module the brief asks for.

Assumption: a tenth screen, Records, is additive and does not change any of the
nine. It could fold into Facilities and plans if a tenth screen is unwanted.

## 9. Seeded demo entities

The workspace is seeded with two tenants and two facilities so the transfer
path and the shared-CySO derivation can be exercised. The second facility,
MEG-1 platform, is taken from the content instance profile and carries no
assessment data. The multi-facility plan copies the organisation answers from
the fixture and empties the inventory.

Every seeded document carries `seed: true` or a `seed` provenance string and
the GUI shows it. The fixture file itself is never edited. Question: is any of
this seed shape worth keeping as a second fixture, or should a fresh install
seed nothing at all?

## 10. Trigger 1 needs the pinned ruleset body, not just its version

`plan_version` pins the ruleset version string. To diff a frozen plan against
the ruleset it was frozen under, the agent needs that older ruleset body.
Today only the current ruleset exists on disk, so trigger 1 reports "no change"
unless the simulate toggle applies the labelled hypothetical amendment.

Assumption: sealed ruleset bundles will be retained by version on the install,
and the pin resolves to a file. Until then the surveillance screen states that
the simulation is the only placeholder and the diff code is real.

## 11. Workshop answers are ordered, not timestamped

Nothing below the facade reads the clock. `Session.criticality_answer` records
`recorded_at` as `as_of#ordinal` rather than a wall-clock time.

Question: does attribution need a real timestamp for a workshop record to be
defensible? If so the facade reads the clock per answer and passes it down,
which is allowed by the brief, but it makes the session document
non-reproducible across two identical workshops.

## 12. SMBIOS UUID collection spawns PowerShell

`app.collect_identifiers` reads the machine GUID from the registry, the volume
serial through the Win32 API, and the SMBIOS UUID by running PowerShell
`Get-CimInstance`. The third is a subprocess on a box that may sit beside an OT
network.

Assumption: acceptable for a draft; each component reports collected or
unavailable and the fingerprint needs 2 of 3. Question: is a subprocess
acceptable on a hardened install, or should the SMBIOS component be read
through WMI COM or dropped for a fourth stable identifier?

## 13. Register fields the answer model does not carry

Appendices K, L, N and O need fields (vendor, firmware version, remote access
method, which CVE a compensating control covers, risk owner and review date)
that the device entity does not have. The register views list these under
"fields not in the answer model" rather than rendering empty columns.

This is the same gap CLAUDE.md records under KEV "Known gap": the fix is an
observation entity keyed (asset, cve) plus a compensating-control and
risk-acceptance entity, which is build step 04. No entity was invented here.

## 14. Appendix M scope

The KEV register (Appendix M) here lists every device carrying an open KEV,
with the critical flag as a column. The crosswalk restricts to critical assets
because 101.650(e)(3)(i) reaches critical systems. Question: should Appendix M
also restrict to critical assets, or is a KEV on a non-critical workstation
still a register entry?
