# MTSA Cybersecurity Plan generator

Interview-driven web app that generates 33 CFR Part 101 Subpart F Cybersecurity Plans.
Primary customer is a consultant managing many facilities. Multi-tenant SaaS.
Later phases add other MTSA documents (FSP, Cyber Incident Response Plan) on the same spine.

## Layout

```
content/        versioned authoring data (schema, instance, ruleset, KEV snapshot)
reference/      regulation and source materials, read-only, never edited
fixtures/       answer sets for regression-testing the engine, not product content
engine.py       the deterministic rule engine, tier 0, stdlib only
kev.py          CISA KEV capability module, tier 1, entitlement-gated
store.py        tier 1: the answer store interface, JSON file and in-memory backends
criticality.py  tier 1: two-stage narrowing as a facilitated workshop session
records.py      tier 1: 101.640 record categories, retention read per category
plan_version.py tier 1: freeze, content hash, the four pins, amendment diff
render.py       tier 1: deterministic prose from a clause library, empty so far
license.py      tier 1: entitlements, fingerprint N-of-M, offline challenge; crypto stubbed
audit.py        tier 1: section-scoped audit runs, deficiency vs recommendation
app.py          tier 3: the facade. The GUI imports this and nothing else.
serve.py        tier 3: 127.0.0.1 HTTP server and JSON API for gui/
gui/            vanilla HTML, CSS, JS; nothing loaded from anywhere else
validate.py     schema conformance, the invariants JSON Schema cannot express,
                the question module, GUI hygiene, and the module dependency direction
demo_engine.py  feasibility run: engine vs the seven known-truth fixtures
demo_kev.py     feasibility run: the KEV join, the entitlement gate, surveillance
docs/           human-facing briefings, not gated by validate.py
GUI-HANDOFF.md  build brief for the GUI and program skeleton, written for an
                unattended session
QUESTIONS.md    design questions the GUI build could not answer from this file,
                each with the assumption it proceeded under
```

## Ground truth

The regulation PDF in `reference/` is authoritative. Nothing else is.

Every source so far has miscited it. Verify citations against the PDF before writing or
repeating them. Do not cite from memory. Corrections already found and applied:

- 24/7 CySO accessibility is 101.620(b)(3). It is NOT 101.625(b), which is "Serving as
  CySO for Multiple Vessels."
- The alternate CySO comes from the 101.615 definition of Cybersecurity Officer.
  It is NOT 101.625(d), which is the 15-item Responsibilities list.
- 101.625(e) lists 12 specific knowledge areas. Use the real list, not a plausible one.
- 101.650(g) is Resilience. Reporting to the NRC without delay is (g)(1), documented in
  Sections 3 and 9, not Section 5.
- Lesson 3 miscites KEV patching as 101.650(e)(3)(iv). Correct is (e)(3)(i).

Frequently needed and frequently gotten wrong:

- 101.630(c) lists **14** plan sections, not 12.
- Plan validity is 5 years from approval, 101.630(d)(3).
- General amendments go to the Coast Guard 30 days before effective date, 101.630(e)(2).
- A CySO change is the exception: 96 hours to notify and 96 hours to submit the amended
  portion, 101.630(e)(4). The 30-day rule does not apply to it.
- Submission goes to the COTP or OCMI for facilities and OCS facilities, and to the Marine
  Safety Center for U.S.-flagged vessels. See 101.630(d) and 101.625(d)(13).
- 101.630(a) allows four delivery modes: inside the VSP/FSP/OCS FSP, as an annex, as part
  of an approved Alternative Security Program, or as a separate submission.
- 101.640 sets no retention period but is not only a deferral. It names six activities that
  records must be created for, at a minimum: training, drills, exercises, cybersecurity
  threats, reportable cyber incidents, and audits of the Cybersecurity Plan. Note that it
  splits drills from exercises, while 104.235/105.225/106.230 carry them as one category.
  Creation and maintenance then follow 33 CFR 104.235 for U.S.-flagged vessels, 105.225 for
  facilities, and 106.230 for OCS facilities, all outside Subpart F and all now in
  `reference/`, verbatim from the eCFR versioner API.
- **Retention is 2 years, not 3.** All three sections read "for at least 2 years". Verified
  2026-09-04 against `reference/33 CFR 104.235 105.225 106.230 Recordkeeping...txt`. An
  earlier note in this file said 3 on the strength of a spoken correction in the 2026-09
  brainstorm; the correction went the wrong way and the original 2 was right.
- Retention is per category, not a single constant. Each section opens "unless otherwise
  specified in this section", and the Declaration of Security is specified otherwise at 90
  days after the end of its effective period. 104.235(b)(7) adds a count rule on top for
  manned vessels: the last 10 DoSs kept on board. Any predicate reads the record category,
  never a hardcoded 2.
- The three recordkeeping sections are not parallel. 106.230 has eight categories where the
  others have nine: no reader/PACS item and no paragraph (c) at all. 104.235(c) and 105.225(c)
  declare reader and PACS records to be SSI under 49 CFR 1520 by name; 106.230 carries no such
  clause, though its (b) preamble demands protection against unauthorized access and
  disclosure that the other two omit. A `record_category` enum is therefore not uniform across
  the asset type discriminator, which is what `applies_to` is for.
- Two roles touch a record. The VSO or FSO **keeps** it, per 104.235(a)/105.225(a)/106.230(a).
  The CySO **ensures** it is maintained, per 101.625(d)(11). Custodian and accountable officer
  are different fields.
- The Coast Guard inspects the **current** plan only. It does not ask for superseded plans.
  An inspection compares the plan in force against what the facility actually does. Retention
  attaches to the six record categories above, never to prior plan versions.

## Settled decisions

Do not relitigate these without being asked.

- **Tenant is the consulting org.** Facility is a child entity. Build the facility transfer
  path early: the owner or operator is the responsible party under 101.620(a), not the
  consultant, and clients change consultants.
- **Plan-to-facility is many-to-many.** Required by 101.630(d)(2) multi-facility plans and
  by 101.625(b), which makes every facility a shared CySO covers appear in every one of
  their plans. That last one is a derived field computed across facilities, not answered.
- **One asset model, not three apps.** `covered_asset` carries a type discriminator for
  vessel, facility, and OCS facility. Question nodes carry an `applies_to` predicate. The
  14-section spine is identical across all three.
- **Every export writes an immutable `plan_version`** that freezes the answer set and pins
  four versions: ruleset, question module, clause library, plan. Never render a document
  from live mutable state. This is what makes amendment diff, 5-year renewal, and later
  submission tracking possible, and it is what makes surveillance attributable: because the
  frozen answers cannot move, a verdict that flips between two runs is attributable to the
  ruleset rather than to the customer. Freezing is ours, not the Coast Guard's. They never
  ask for an old plan, so do not justify it as regulatory record-keeping. Lifecycle tracking
  is out of scope for now but must stay additive.
- **Prose is deterministic.** Clause library with conditional slots, plus human-written
  narrative fields. No generative model at the document layer, ever. Same answers must
  always produce the same document.
- **SSI never leaves our infrastructure, and by default it never enters it.** The plan is
  SSI under 101.630(b) and 49 CFR 1520. No third-party model API, no third-party OCR for the
  later ingest phase. Any model assistance must be self-hosted inside the trust boundary.
  The default posture is stronger than that: the customer holds the answer set and we do not.
  See Deployment posture below. Full custody exists, but as an opt-in tier, not the default.
- **The gap-check is rule-based and ships as a product feature.** Rules are predicates over
  the answer set, keyed to registry entries, with severity split between binding and best
  practice. A binding failure blocks export; a best-practice failure warns. Never conflate
  them.
- **Registers are entity-store views, not authored prose.** Roughly a third of the 22
  appendices (critical IT/OT, hardware and software, remote access, KEV, compensating
  control, risk acceptance, backup) are rendered tables.
- **Attachments version separately from the plan body.** Inventories and network maps are
  controlled attachments so a switch replacement does not trigger a 30-day amendment
  filing. `plan_version` pins them by reference rather than embedding them.

## Current state of content/

`csp-navigator.content.json` validates against `csp-navigator.schema.json` under JSON Schema
draft 2020-12. Re-validate after every edit:

```bash
python validate.py
```

That runs schema conformance plus the things the schema cannot express: no dangling
`authority_ids` or `connected_authority_ids` (dangling citations validate fine but break the
gap engine), the bindingness cap, the seven regression fixtures, the shape of the 14-section
spine, and the appendix set. It exits non-zero on failure, so it can gate a build. It also
prints the current unreferenced-authority list, which is informational rather than a failure.

The content instance holds 14 sections and 22 appendices. Three sections are authored,
carried over from a training tool called the CSP Navigator: Sections 1 and 5 are complete
with known ground truth, and Section 12 is truncated mid-sentence in its original draft with
no strengthened draft, text not recoverable from anything in `reference/`. The other 11 are
`stub`: real titles and citations from the PDF, outline requirement bullets loaded, no
authored drafts or checklists yet. All 22 appendices are `stub`.

**Sections 1, 5, and 12 are test fixtures for the gap engine, not product content.** Seven
checklist items across Sections 1 and 5 have known-correct `satisfied_by_original` values.
Treat them as a regression suite. Do not delete them when restructuring. `validate.py` pins
all seven by id and value and fails if any is removed or changed.

## Conventions

- Prefer editing existing files. Do not leave abandoned files behind; delete what a change
  supersedes.
- Ask before creating a new file when an existing one could hold the content.
- No emojis. No em-dashes.
- Technical register. Skip preamble.

## Opening task

All four changes are applied. `reference/MTSA Cybersecurity Plan Outline.docx` is the plan
spine, replacing the 12-section structure inherited from the Navigator.

1. Spine widened to the 14 sections of 101.630(c). The 11 new sections are `stub` status,
   titled and cited from the PDF, each carrying the outline's requirement bullets verbatim
   in `requirements` (172 across the 14). Turning a bullet into a checklist item with
   authored ground truth is the next authoring step, section by section.
2. The 22 appendices, A through V, are a first-class artifact type in `appendices`, keyed
   by `$defs/appendix`. Deliberately not sections: no drafts, no checklist, no gap
   analysis.
3. `regulation_id` replaced by a namespaced `authority_id` and `regulations` by
   `authorities`, each entry carrying `authority_type` and `binding`. Namespaces are `cfr:`,
   `pl:`, `nvic:`, `fr:`, `wi:`, and `guide:`. The last one was not in the original plan; the
   outline's reference list carries four Coast Guard guides and job aids that fit none of
   the others.
4. Section 1 subsection 1.4 now states the two 96-hour clocks of 101.630(e)(4) and
   explicitly disclaims the 30-day rule at 101.630(e)(2) for that case.

The outline follows the exact order of 101.630(c) on purpose. 101.630(c) requires an index
only when a plan departs from that order, so holding the order means the app never needs to
generate one. Keep it. The schema enforces exactly 14 sections and `validate.py` enforces
ascending order.

## Appendices

`kind` and `controlled_attachment` are orthogonal and both matter.

- `kind` is how the content is produced. `register_view` is a rendered table computed from
  the entity store and may carry no authored body at all. There are exactly seven, and the
  `register` enum names them: I critical IT/OT, K hardware and software, L remote access,
  M KEV, N compensating control, O risk acceptance, P backup. The other 15 are
  `authored_prose`.
- `controlled_attachment` is how it versions. True means it versions separately from the
  plan body, so a switch replacement or a new scan result does not trigger a 30-day
  amendment filing under 101.630(e)(2); `plan_version` pins it by reference instead of
  embedding it. All seven registers are attachments, and so are J network diagrams, T
  penetration test certification, and U Coast Guard correspondence.

J is why the two are separate: it is authored rather than rendered, because a diagram is
uploaded, but it must still version independently.

## Criticality and the crosswalk

This is the part clients cannot do on their own, and per the 2026-09 brainstorm it is the
reason the tool gets bought. It outranks the 14-section spine in build priority.

**Criticality is a two-stage narrowing, not a single score.**

1. Scope. For each asset: is it critical, and could its compromise cause a Transportation
   Security Incident? Answering both narrows a general asset inventory down to the assets
   actually inside the MTSA footprint. Most of an inventory falls out here, and the plan
   covers what survives.
2. Rank. Among the survivors, order by consequence. This never says one critical asset does
   not matter. It says which failures hurt most, and it sets tracking priority for KEV and
   CVE monitoring.

SAM already holds the scoring instrument: the CFDD questionnaire, six to nine questions
scoring exposure and integrity through a weighted model into a qualitative composite. Extend
it rather than building a second one. Two changes are needed. TSI questions, which RMF has no
equivalent of, and more questions overall, because the current instrument produces ties and
stage 2 needs a usable ordering.

Stage 1 is a group activity. Operations, security, cyber, and IT answer it together, because
no one of them knows both what an asset does and what breaks when it stops. See Service model.

**The crosswalk is the deliverable.** Coast Guard guidance asks for one table: critical asset,
known vulnerabilities, and what was done about each. Every row resolves to mitigated, planned
with an owner and a date and a cost, or accepted with no mitigation and a stated reason. It is
the hardest artifact for a client to produce by hand and the clearest demonstration of value.

It is a composite view over four registers already in the appendix set: I critical IT/OT, M
KEV, N compensating control, O risk acceptance. It is deliberately **not** an eighth register
view. The `register` enum is closed at seven and `validate.py` enforces that. If the Coast
Guard turns out to require the crosswalk as a standalone appendix rather than as a view, that
is a schema change and a deliberate one, not a quiet addition.

The accepted branch is not a dismissal button. A declined finding is a risk acceptance record
feeding Section 12 and Appendix O, so it requires an owner, a justification, a compensating
control where one exists, and a review date. Sections 11 and 12 are, read literally, a record
of what the compliance checks turned up: 11 holds what was resolved, 12 holds what was not.
Wired this way the validator produces two of the fourteen required sections rather than
sitting beside the document as a separate feature.

## Deployment posture

The customer holds their answer set. We hold the logic. This is the default, and full custody
is an opt-in tier for clients already piping data to the SOC under CySO-as-a-service.

The invariant that has to be true in the code from day one, because it is what lets custody
move later without a rewrite:

- **The engine is a pure library with no ambient state, and the answer store sits behind an
  interface the engine never reaches around.** No global config, no implicit file paths, no
  environment lookups, no clock. Everything a run needs is passed in. The engine is already
  built this way for SSI reasons; the consequence is that the same code runs on a customer
  workstation and in our cloud with no fork, so the deployment question is a packaging
  decision rather than an architectural one.

How surveillance survives the customer holding the data:

- The agent re-evaluates locally when a new ruleset arrives and reports back the **diff shape
  only**: rule ids, old and new status, old and new severity. Never answer values, never asset
  names, never facility identifiers beyond the tenant and install the license already names.
  Rule ids and severities are not SSI. Detail is viewed in the portal against data the client
  holds.
- Surveillance has three triggers, not one. A ruleset change is the original. A newly
  published KEV matching an old scan result breaks compliance with no law changing at all.
  A new vulnerability landing on an asset already in a plan is a prompt to re-examine whether
  that asset is still rated correctly, which is a criticality question rather than a patching
  one. The second and third read asset data, so under the default posture they run inside the
  agent and surface as a local finding. Only the custody tier can name an asset in an outbound
  alert.
- This constrains alert copy. "Rule `kev-critical-compensating` flipped to fail" is sendable;
  "a KEV now affects the asset you flagged critical" is not, from telemetry alone. Alerts that
  need asset-level specificity are a custody-tier feature. Do not write copy that quietly
  assumes data we do not have.

Licensing, because an agent on customer hardware will get shared:

- **Bind the ruleset, not the engine.** The engine is a JSON interpreter and is reproducible
  by anyone who reads the spec. The authored ruleset, maintained against the Federal Register
  by staff, is the asset the subscription pays for. Ship it sealed to a per-install key with
  an entitlement expiry, so a shared copy goes stale on its own.
- Per-machine activation, not a shared key. Compose the fingerprint from several stable
  identifiers, on Windows the machine GUID at `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`
  plus the SMBIOS UUID plus a volume serial, hashed together. Match N-of-M so a NIC swap or a
  reimage does not lock a customer out. Activation returns a license token signed by us,
  carrying tenant, tier, seat, ruleset entitlement, and expiry, verified against a public key
  in the agent. Seats are per-tenant with self-service deactivate and transfer.
- **Offline activation is a requirement, not an edge case.** Installs will sit on or beside OT
  networks with no outbound path. Challenge-response file exchange has to work from the start.
- **Ruleset signatures are verified before load, always.** An agent that will run an unsigned
  ruleset can be fed one that passes everything, which is worse than shipping nothing. This is
  a safety requirement independent of the commercial one and does not get relaxed for a tier.

## Service model

Not signed off. Recorded because the tier boundaries determine what has to be separable in
the code, and building a monolith and then breaking it apart is the expensive order.

- **One-off.** We build the plan, they keep it. Lightweight local agent, ruleset current at
  delivery, no monitoring. They maintain it themselves or they do not.
- **Maintained.** Agent plus ruleset subscription plus surveillance alerts. The recurring
  half, and the thing originally pitched.
- **CySO as a service.** We hold the role clients are reluctant to name, data pipes to the
  SOC, incident response runs through us. This is the tier where full custody applies and the
  only one where asset-level alerting is possible at all.

Pricing follows the oil spill response model, which MTSA-regulated facilities already
understand: a modest annual retainer covering limited plan maintenance and recurrent training,
with separate published rates for response, additional training, and incident work. Keep the
retainer accessible and make the margin on response.

Two service decisions that change the product rather than just the invoice:

- **The first audit is bundled into the CSP engagement.** The engine validates the document,
  not the facility, and no amount of software closes that gap. Selling plan development with
  the first audit attached does close it: we build the plan, we verify the facility matches
  it, and the client reaches their first Coast Guard inspection already checked. This is the
  answer to a risk we could not engineer away. It does not remove the disclosure obligation:
  certification output states on its face that it covers the plan as written, and separately
  what was physically verified and when.
- **The criticality stage ships as a facilitated workshop.** Stage 1 needs several roles in a
  room at once. The interview therefore cannot assume a single authenticated respondent
  working alone. A session has multiple participants, answers may need attribution, and a
  facilitator may be driving the tool while the client answers. That is a product requirement,
  not a packaging idea.

## Answer typing

Rules are only as good as the shape of what they read. Free text pushes rule authoring into
regex hunting, which is unmaintainable and silently misses things.

- Any answer path a rule reads is a constrained type: enum, boolean, number, date, or a
  reference to an entity in the store. Never free text. If a rule needs a field, that field
  gets an enum first.
- Free text is allowed only in narrative fields that feed the clause library and are never
  evaluated by a predicate.
- Where applicability varies, the enum is tri-state: `yes`, `no`, `not_applicable`. A
  requirement that fails and one that does not apply are different findings, and collapsing
  them into a boolean produces wrong gap output.
- `matches` is for format checks: phone numbers, identifiers, dates. It is not for detecting
  whether prose says the right thing. Prose is reviewed by a human, not pattern-matched.

## Rule engine

`engine.py` evaluates predicates over an answer set. It is a closed set of operators
walking plain JSON. No model, no inference, no network call, no import beyond the standard
library. That is not a preference, it is what lets the whole thing run inside the SSI
boundary, and it is the mechanical form of the settled "prose is deterministic" decision.

Three properties the product depends on:

- **Determinism.** Same `(ruleset, answers, as_of)` always yields byte-identical verdicts.
  Nothing reads the clock; `as_of` is an explicit pinned input, so re-running a pinned
  `plan_version` reproduces its report exactly. That is what makes a surveillance diff mean
  something: without it, a second run differs for reasons nobody can separate. Never call
  `datetime.now()` inside a predicate.
- **Evidence.** Every verdict carries the paths the predicate read and what it found.
  `all` and `any` deliberately do not short-circuit: an auditor asking what was checked is
  entitled to the whole list, not just the clause that failed first.
- **Fail loud.** An unknown operator raises. Missing data makes a comparison false, never
  true: an obligation nobody answered is not demonstrated, so it does not pass.

`engine.lint()` statically checks a predicate without running it. This is not optional
belt-and-braces: a quantifier over an absent or empty collection never reaches its own
body, so a typo inside `for_each.where` would otherwise ship undetected. `validate.py`
lints every rule and every `applies_to`.

Surveillance diffing must compare **severity as well as status**. A rule that goes from
best practice to binding turns a compliant plan into a non-compliant one without its
pass/fail status ever moving. Missing that was a real bug caught by running the demo.

Current state: 13 rules covering 11 of the 172 outline bullets. `python demo_engine.py`
reproduces all seven regression fixtures from the answer set in `fixtures/`, and measures
throughput at roughly 0.2 ms per plan, so re-evaluating 10,000 held plans after a ruleset
change is a couple of seconds on one core.

## Capability modules

Optional features are modules, and the module boundary is the licensing boundary. A
tenant gets what they are entitled to and nothing else, without a forked build.

- **Rules declare what they need, modules supply it.** A rule carries
  `requires_capability`; a module declares `CAPABILITY`; the caller passes
  `capabilities` to `run_ruleset`. Adding a gated rule is an authoring change, not an
  engineering one. `validate.py` fails the build on a capability no module supplies.
- **An unlicensed rule is `unavailable`, never `pass`, and a binding one blocks
  export.** This is not a commercial lever. Without the gate the KEV rule *passes*
  unlicensed: an absent `open_kevs` makes the `for_each` `where` clause match nothing,
  so the rule succeeds having checked zero devices. Conservative missing-data
  semantics do not help, because no comparison is ever reached. `demo_kev.py`
  demonstrates it both ways. The same trap waits for every future quantified rule.
- **Capability modules never import `engine`, and `engine` never imports them.** They
  meet at one point: a pure `enrich(answers, ...)` projects derived fields onto a copy
  of the answer set before evaluation, and the engine reads the result as plain JSON.
  Adding operators for a capability would open the closed operator set, and an
  operator present for one tenant and absent for another is not a deterministic
  engine. A module the engine depended on could not be dropped from a build, and a
  capability that cannot be dropped is not a capability.
- **`validate.py` enforces the dependency direction by parsing imports.** Tier 0 is
  `engine.py`, tier 1 is capability and infrastructure modules, tier 2 composites,
  tier 3 the facade and GUI. Tier 1 also may not import a network module at module
  scope, so an offline install can load it without a socket ever being reachable.

`kev.py` is the worked example. Read it before writing another.

## KEV

`content/kev-snapshot.json` pins the CISA catalog, content-addressed by sha256 over
the raw feed bytes. `python kev.py refresh` re-fetches; nothing else in the codebase
makes a network call, and evaluation never does.

- **The join key is `cveID` and nothing else.** Verified against the live feed:
  1699 entries, all unique, all matching `^CVE-\d{4}-\d{4,}$`. `vendorProject` and
  `product` are editorial labels, not identifiers, with 283 vendor strings over 718
  vendor/product pairs and Microsoft alone spelled 72 ways. There are no CPEs in the
  feed. Never match on a product name; it is regex hunting and it violates Answer
  typing. An asset with no CVE source is the `no-kev-identification` gap, not a guess.
- **Getting an asset to a CVE is the hard half and it is not the engine's job.**
  Scanner output for IT, vendor advisory mapping for OT, version inference off
  Appendix K as a last resort. The last two produce candidates needing human
  confirmation, same posture as ingest. The engine only ever sees the confirmed list.
- **KEV is effectively an IT catalog.** Siemens 1 entry, Rockwell 1, Schneider 1, out
  of 1699. Microsoft is 388. The duty at 101.650(e)(3)(i) reaches critical IT *and* OT,
  so for an OT-heavy facility the join returns empty, and empty is absence of coverage
  rather than compliance. Do not render an empty join as a clean crosswalk. The real
  OT signal is ICS-CERT and vendor ProductCERT advisories, which are not KEVs.
- **CISA's `dueDate` is not the compliance clock.** It is a federal civilian agency
  deadline under BOD 26-04, most commonly 3 days, with no application to an MTSA
  facility and more permissive than "without delay". Never surface it as a deadline.
- **A KEV update is a ruleset version bump**, `ruleset@1.4.2+kev.2026-09-08`, so the
  snapshot rides inside the sealed bundle and `plan_version` still pins four things.
  The catalog is public and carries no SSI, so it ships down the same signed channel
  as the ruleset and reaches an offline install by the same file exchange.
- **Trigger 2 does not go through `engine.diff_runs`.** A catalog change is a data
  change, not a ruleset change. Worse, it is usually invisible to a verdict diff: if
  the rule was already failing on one KEV, two more landing on critical assets move no
  verdict at all. `demo_kev.py` shows exactly that, zero changes reported. Trigger 2
  reports through `kev.newly_affected` locally and `kev.telemetry` outbound.
- **Known gap.** `compensating_control` is one field on the device, so it cannot say
  "CVE A compensated, CVE B not", and every crosswalk row therefore reads `unresolved`.
  The module refuses to invent a disposition. Fixing it means promoting an observation
  to its own entity keyed (asset, cve), which is a prerequisite for a real crosswalk at
  build step 04.

## Audit mode

Audit mode is not merely the engine run with a pinned ruleset over frozen answers. The auditor
is drafting a report in the field, and the point is that the report is finished when they
leave rather than rewritten back at the office.

- **Audits are section-scoped.** An auditor samples a few sections, chosen with the client,
  not the whole plan. A run carries its scope and the report states it. A scoped run that
  reads as though it covered the whole plan is a liability.
- **Findings are authored against a citation.** Selecting a checklist item shows the authority
  behind it and offers a templated finding: deficiency text plus remediation. Templates are
  keyed by `authority_id`, so the citation trail is structural rather than typed by hand.
  Coast Guard practice cites every deficiency to the CFR, and an uncited finding is not
  defensible.
- **Deficiency and recommendation are different objects.** A deficiency is a failed binding
  requirement. A recommendation is an enhancement or best practice with no failure behind it.
  They carry different severity, render in different parts of the report, and only the first
  has remediation obligations attached. Free text belongs on the recommendation.
- Finding templates are authored content. They version with the ruleset and are pinned by
  `plan_version` like everything else.

The facility inspector cyber job aid is now in `reference/`, and reading it downgrades the
claim made for it in the brainstorm. It is useful, but not as the questions a Cybersecurity
Plan will be graded against.

- **It predates the rule.** Revision 2 is January 2023. Subpart F was published at 90 FR 6447
  in January 2025. Every reference in the job aid is to Part 105 physical security (105.400,
  105.405, 105.305, 105.220, 105.205/210/215, 105.255, 105.260, 105.275, 105.250, 105.240/245).
  It cites Subpart F nowhere, because Subpart F did not exist. It reads cyber into the FSP
  framework rather than checking a CSP.
- **It disclaims regulatory force in its own text.** Not a rule, creates no requirements, a NO
  box is not a discrepancy, and an inspector may never issue a Notice of Violation on it alone.
  It stays `guide:` with `binding` false, so the bindingness cap already prevents a rule citing
  only this from being binding. That is the correct outcome, not a limitation to work around.
- **It is facility-only.** No vessel or OCS equivalent, so it does not cover the discriminator.
- **It is SSI once filled out**, marked on every page. Audit output carrying these answers
  inherits that, which is another reason audit mode runs inside the agent.

What it is genuinely good for: the question shape. Ten top-level questions, each with
supporting factors underneath, answered Y/N/N-A. That tri-state is the pattern already settled
in Answer typing, now confirmed against how inspectors actually work. Use it to calibrate
granularity and tone for audit checklist items, and use its crossover mapping where a client
must keep FSP and CSP aligned. Do not map CSP requirement bullets onto it.

WATCH FOR: a Subpart F era successor. Julio called this the latest the Coast Guard has put out
and expected it to change. A revision citing 101.6xx would be the document worth mapping to,
and it should be checked for periodically.

## Bindingness

Two separate flags, and they must not be conflated:

- `authorities[id].binding` is a property of the instrument. True only for codified CFR
  text. Policy letters, NVICs, work instructions, and Federal Register preamble are always
  false; the codified text is the binding form of whatever they discuss.
- `gap.binding` is a property of the finding, and authors set it explicitly.

The rule is one-directional: a gap may be binding only if at least one authority it cites is
binding. The converse does not hold. `no-severity-tiers` in Section 5 is correctly
non-binding while citing 101.645(a), a binding regulation. Authority bindingness caps gap
severity; it never sets it. `validate.py` enforces the cap.

## Build priority

Steps 00 through 03 are work inside SAM and are gated on the RMF package work finishing
first. That dependency is external to this project and bounds when anything here starts.

```
00  Golden-output harness. Regenerate the two existing vessel assessments and capture them
    as fixtures. Nothing else starts until we can prove we have not broken anything.
01  Profile mechanism. The compliance dropdown, with the RMF profile as an exact no-op.
02  Asset type discriminator. Vessel, facility, OCS facility, defaulting to vessel.
03  The MTSA profile. Field sets, taxonomy, SSI marking, register export.
04  Criticality instrument and the mitigation crosswalk.
05  KEV feed and join, scoped to critical systems, feeding the crosswalk.
06  Rule authoring at depth, Sections 5 through 14.
07  Freeze, then audit mode.
08  Surveillance and alerting.
```

Rule authoring sits behind the criticality work deliberately. The engine mechanism is built
and holds up, so what remains under that heading is authoring volume, and authoring volume is
not the bottleneck. Criticality is: it is where clients get stuck, and the assessment sections
depend on its output.

**Sections 1 through 4 are not our authoring work.** They are standard blurb across facilities
and arrive as templates from Julio, section by section, as separate documents. Do not spend
engineering effort there, and do not treat the 172 requirement bullets as a uniform coverage
target.

One caveat on that. Boilerplate prose does not mean no data model. Section 2 Personnel
Training, Section 3 Drills and Exercises, and Section 4 Records and Documentation are where
the training, drill, and exercise records live, and those records carry the only real
retention clock in the whole plan and are the one thing an inspection looks back at. The
narrative is Julio's. The underlying entity is ours, and it is currently missing. Coverage in the assessment sections is worth driving high. Coverage in Sections 1
through 4 is worth close to nothing, which is why `validate.py` reports coverage per section
rather than as a single number.

## Still open

- Where the hosted half runs, and what the answer store is under the custody tier. Needs the
  infrastructure owners. The agent half is settled; see Deployment posture.
- Tier boundaries need sign-off before implementation, not after. See Service model. The seams
  that have to be separable: engine and ruleset delivery, surveillance telemetry, custody of
  the answer store, and SOC integration.
- Ingest of a plan somebody else already wrote is unsolved and stays manual. Reading free
  prose is genuine text understanding and it is the one place a model will look reasonable to
  somebody, which is exactly why the decision gets settled now rather than in front of a
  working prototype: structured extraction only, self-hosted, mandatory human confirmation of
  every extracted field, never generation. Whether a local model clears the SSI bar at all is
  unresolved. Until it is, ingest is a person retyping into the interview.
- Whether the eight unreferenced registry entries stay seeded or get pruned. Two are CFR
  leftovers from the Navigator (101.625(d), 101.650(e)(3)(vi)); six are guidance the
  outline's reference list names without tying to a section (90 FR 6298, NVIC 01-20,
  MCP-WI-003, and the three Coast Guard guides). The original four-entry question partly
  resolved itself: 101.630(c)(11) now anchors Section 11, and 101.630(e) was replaced by the
  (e)(2)/(e)(4) split. `validate.py` reports the current orphan list on every run.
  `guide:facility-inspector-job-aid` is now a deliberate orphan rather than an open one: the
  document is in `reference/` and has been read, and the decision is that no requirement
  bullet maps to it, for the reasons under Audit mode. Keep it seeded so a Subpart F era
  revision has somewhere to land.
- The 101.640 record categories are absent from the content model. They are the only artifact
  with a real retention clock and the only thing an inspection looks back at. They land across
  Section 2 Personnel Training, Section 3 Drills and Exercises, and Section 4 Records and
  Documentation. This is no longer a design question so much as unbuilt work: the CFR gives
  the field list outright. Training carries date, duration, description, and attendees. Drills
  and exercises carry date held, description, participants, and any best practices or lessons
  learned. Threats and incidents carry date and time, location, description, who it was
  reported to, and the response. The plan audit carries a certified letter naming the
  completion date. Open parts that remain: whether this is an entity type or a register view
  (a retention clock argues entity, since a view over nothing retains nothing), and how the
  category enum varies by asset type given that 106.230 is short two items.
- Client user role scoping. Owner/operator e-sign is in scope: the signature binds to a
  `plan_version` content hash, with signer identity, timestamp, and the exact statement text
  shown. Client users see only their own facilities, which is a second isolation boundary
  nested inside the tenant boundary.
