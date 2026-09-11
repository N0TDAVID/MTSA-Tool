"""The facade. Tier 3. The GUI imports this and nothing else.

Owns the three things the modules deliberately do not: the entitlement set,
the answer store instance, and as_of. Wires enrichment: for each licensed
capability module, call its enrich(), then call engine.run_ruleset with the
entitlement set. An unlicensed module is not called and its rules come back
`unavailable`.

The facade may read the clock once, at session construction, and passes the
value down. Nothing below it reads the clock.

Every citation the GUI shows comes from the authorities registry in the content
instance, resolved here by authority_id. An id that is not in the registry is
returned as `missing` so the GUI can show that rather than a guessed cite.

Placeholders. Anything not backed by a real computation is returned with
`placeholder: {reason}` so the GUI can render a PLACEHOLDER badge. The GUI
never decides what is real; this file does.
"""

import copy
import io
import json
import os
from datetime import date

import audit
import criticality
import engine
import ingest
import kev
import license as licensing
import plan_version
import records
import render
import store

CONTENT = "content/csp-navigator.content.json"
RULESET = "content/csp-ruleset.json"
QUESTIONS = "content/csp-questions.json"
KEV_SNAPSHOT = "content/kev-snapshot.json"
FIXTURE = "fixtures/meg-westport-original.answers.json"

# Capability modules this build ships. Same set validate.py checks rules against.
CAPABILITY_MODULES = {kev.CAPABILITY: kev, criticality.CAPABILITY: criticality}

# Entitlements the licensing screen may toggle. `surveillance`, `custody` and
# `asset_alerts` are tier features with no module behind them yet; toggling
# them changes what the UI says it may do, not what the engine evaluates.
TOGGLEABLE = ("kev", "criticality", "surveillance", "custody", "asset_alerts")

REGISTER_APPENDIX = {
    "critical_it_ot": "I", "hardware_software": "K", "remote_access": "L", "kev": "M",
    "compensating_control": "N", "risk_acceptance": "O", "backup": "P",
}

DEV_PUBLIC_KEY = None   # no issuer key exists yet; see license.verify_signature


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def placeholder(reason):
    return {"placeholder": True, "reason": reason}


class Session:
    def __init__(self, answer_store, content, ruleset, questions, kev_snapshot,
                 entitlements, as_of, install_id="dev-install", clause_library=None,
                 token=None, fingerprint=None):
        self.store = answer_store
        self.content = content
        self.ruleset = ruleset
        self.questions = questions
        self.kev_snapshot = kev_snapshot
        self.entitlements = set(entitlements)
        self.as_of = str(as_of)
        self.install_id = install_id
        self.clause_library = clause_library or render.EMPTY_LIBRARY
        self.token = token
        self.fingerprint = fingerprint
        self.authorities = content["authorities"]
        self.section_titles = {s["number"]: s["title"] for s in content["sections"]}

    # ------------------------------------------------------------------ open

    @classmethod
    def open(cls, workspace, as_of=None, entitlements=None, memory=False):
        """Build a session from the content directory and a workspace store.

        This is the single clock read in the program when as_of is None.
        """
        as_of = str(as_of or date.today())
        content = _load(CONTENT)
        ruleset = _load(RULESET)
        questions = _load(QUESTIONS)
        snap = kev.load(KEV_SNAPSHOT)
        problems = kev.verify(snap)
        if problems:
            raise RuntimeError("KEV snapshot failed verification: %s" % "; ".join(problems))
        answer_store = store.InMemoryStore() if memory else store.JsonFileStore(workspace)
        if not answer_store.ids("tenants"):
            seed(answer_store, content, as_of)

        identifiers = collect_identifiers()
        try:
            fp = licensing.fingerprint(identifiers["values"])
        except licensing.LicenseError:
            fp = None
        caps = set(entitlements) if entitlements is not None else {"kev", "criticality", "surveillance"}
        token = licensing.issue_unsigned_token(
            tenant="harbor-cyber-consulting", tier="maintained", seat=1,
            install_id="dev-install", capabilities=caps,
            ruleset_entitlement=plan_version.ruleset_pin(ruleset["ruleset_version"], snap["catalog_version"]),
            expires="2027-09-08", fingerprint_composite=fp["composite"] if fp else "none")
        session = cls(answer_store, content, ruleset, questions, snap, caps, as_of,
                      token=token, fingerprint={"identifiers": identifiers, "fingerprint": fp})
        return session

    # ------------------------------------------------------------- versions

    def versions(self):
        return {
            "as_of": self.as_of,
            "ruleset": self.ruleset["ruleset_version"],
            "kev_catalog": self.kev_snapshot["catalog_version"],
            "ruleset_pin": plan_version.ruleset_pin(self.ruleset["ruleset_version"],
                                                    self.kev_snapshot["catalog_version"]),
            "question_module": self.questions["question_module_version"],
            "clause_library": self.clause_library["clause_library_version"],
            "content_schema": self.content["schema_version"],
            "ruleset_signature": placeholder(
                "ruleset bundle signature verification is not implemented; "
                "the loaded ruleset is unsigned (license.verify_ruleset_signature)"),
        }

    # --------------------------------------------------------- capabilities

    def capabilities(self):
        return sorted(self.entitlements)

    def set_capabilities(self, caps):
        bad = set(caps) - set(TOGGLEABLE)
        if bad:
            raise ValueError("unknown entitlements %s" % sorted(bad))
        self.entitlements = set(caps)
        return self.capabilities()

    def license_status(self):
        ent = licensing.entitlements(self.token, self.as_of,
                                     self.fingerprint["fingerprint"], DEV_PUBLIC_KEY)
        modules = {name: {"module_version": mod.MODULE_VERSION,
                          "licensed": name in self.entitlements,
                          "rules": sorted(r["id"] for r in self.ruleset["rules"]
                                          if r.get("requires_capability") == name)}
                   for name, mod in CAPABILITY_MODULES.items()}
        return {
            "token": ent,
            "active_entitlements": self.capabilities(),
            "toggleable": list(TOGGLEABLE),
            "tier_ceiling": {t: sorted(c) for t, c in licensing.TIER_CEILING.items()},
            "modules": modules,
            "fingerprint": {
                "identifiers": self.fingerprint["identifiers"]["status"],
                "composite": (self.fingerprint["fingerprint"] or {}).get("composite"),
                "threshold": "%d of %d" % (licensing.MATCH_THRESHOLD,
                                           len(licensing.FINGERPRINT_COMPONENTS)),
            },
            "offline_challenge": (licensing.challenge(self.fingerprint["fingerprint"],
                                                      self.install_id, ent["tenant"], "nonce-dev")
                                  if self.fingerprint["fingerprint"] else None),
            "placeholders": {
                "signature": placeholder("token signature is not verified; entitlements are "
                                         "taken from an unsigned development token"),
                "activation": placeholder("offline activation response verification is not "
                                          "implemented (license.activate_offline)"),
                "override": placeholder("the active entitlement set is a draft toggle on this "
                                        "screen, not the token"),
            },
        }

    # ----------------------------------------------------------- citations

    def cite(self, authority_id):
        a = self.authorities.get(authority_id)
        if a is None:
            return {"id": authority_id, "missing": True,
                    "placeholder": placeholder("%s is not in the authorities registry; "
                                               "no citation text is shown" % authority_id)}
        return {"id": authority_id, "cite": a["cite"], "title": a["title"],
                "authority_type": a["authority_type"], "binding": a["binding"]}

    def cites(self, ids):
        return [self.cite(a) for a in ids]

    def authority_text(self, authority_id):
        a = self.authorities.get(authority_id)
        if a is None:
            return self.cite(authority_id)
        out = self.cite(authority_id)
        out["text"] = a["text"]
        out["description"] = a.get("description")
        return out

    def registry(self):
        return [self.cite(a) for a in sorted(self.authorities)]

    # ------------------------------------------------------------ tenancy

    def tenants(self):
        return self.store.all("tenants")

    def facilities(self):
        out = []
        for f in self.store.all("facilities"):
            f["plan_ids"] = [p["id"] for p in self.store.all("plans")
                             if f["id"] in p.get("facility_ids", [])]
            out.append(f)
        return out

    def plans(self):
        out = []
        for p in self.store.all("plans"):
            meta = {k: v for k, v in p.items() if k != "answers"}
            meta["versions"] = len(self.store.where("plan_versions", plan_id=p["id"]))
            meta["asset_type"] = (p.get("answers", {}).get("asset") or {}).get("type")
            out.append(meta)
        # Creation order, not id order, so the plan with the assessment data is first.
        out.sort(key=lambda m: (m.get("sequence", 0), m["id"]))
        return out

    def plan(self, plan_id):
        return self.store.get("plans", plan_id)

    def transfer_facility(self, facility_id, to_tenant_id, reason):
        return self.store.transfer_facility(facility_id, to_tenant_id, self.as_of, reason)

    def shared_cyso_coverage(self):
        """101.625(b): every facility a shared CySO covers appears in each of their plans.

        Derived across facilities, never answered. Keyed on the CySO name as
        answered in each plan.
        """
        by_cyso = {}
        for p in self.store.all("plans"):
            name = ((p.get("answers") or {}).get("cyso") or {}).get("name")
            if not name:
                continue
            for fid in p.get("facility_ids", []):
                by_cyso.setdefault(name, set()).add(fid)
        names = {f["id"]: f["name"] for f in self.store.all("facilities")}
        return [{"cyso": c, "facility_ids": sorted(f), "facilities": [names.get(i, i) for i in sorted(f)],
                 "must_list_in_every_plan": len(f) > 1,
                 "authority_ids": ["cfr:101.625(b)"], "citations": self.cites(["cfr:101.625(b)"])}
                for c, f in sorted(by_cyso.items())]

    # ----------------------------------------------------------- answers

    def answers(self, plan_id):
        return self.store.answers(plan_id)

    def save_answers(self, plan_id, answers):
        self.store.save_answers(plan_id, answers)
        return self.answers(plan_id)

    def set_answer(self, plan_id, path, value):
        answers = self.answers(plan_id)
        _set_path(answers, path, value)
        return self.save_answers(plan_id, answers)

    def patch_answers(self, plan_id, patches):
        """Apply several answers in one write. Each patch is {path, value} or
        {device, field, value}. This is what Continue and Back call so that
        nothing typed on a step is lost by moving off it."""
        answers = self.answers(plan_id)
        allowed = {f["key"] for f in self.device_fields()}
        applied = 0
        for p in patches:
            if "device" in p:
                if p.get("field") not in allowed:
                    raise ValueError("field %r is not an asset field" % p.get("field"))
                dev = next((d for d in answers.get("devices", [])
                            if (d.get("nickname") or "").lower() == str(p["device"]).lower()), None)
                if dev is None:
                    raise ValueError("no asset named %r" % p["device"])
                dev[p["field"]] = p.get("value")
            else:
                _set_path(answers, p["path"], p.get("value"))
            applied += 1
        self.save_answers(plan_id, answers)
        return {"applied": applied}

    def questions_for(self, plan_id):
        """Question nodes with applicability decided by the engine and current values."""
        answers = self.answers(plan_id)
        ctx = engine.Context(answers, self.as_of, None)
        readers = _rule_readers(self.ruleset)
        out = []
        for q in self.questions["questions"]:
            node = copy.deepcopy(q)
            applies = q.get("applies_to")
            node["applicable"] = True if applies is None else engine.evaluate(applies, ctx, answers, [])
            value = engine.resolve(q["path"], answers, answers)
            node["value"] = None if value is engine.MISSING else value
            node["read_by_rules"] = sorted(readers.get(q["path"], set()))
            node["citations"] = self.cites(q.get("authority_ids", []))
            out.append(node)
        return {"question_module_version": self.questions["question_module_version"],
                "questions": out}

    # ---------------------------------------------------------- evaluate

    def enrich(self, plan_id, answers):
        """Project derived fields through every licensed module, in a fixed order."""
        out = answers
        applied = []
        if "kev" in self.entitlements:
            out = kev.enrich(out, self.kev_snapshot)
            applied.append("kev")
        if "criticality" in self.entitlements:
            session = self._criticality_session(plan_id, create=False)
            if session is not None:
                out = criticality.enrich(out, session)
                applied.append("criticality")
        return out, applied

    def evaluate(self, plan_id, answers=None, ruleset=None):
        answers = self.answers(plan_id) if answers is None else answers
        enriched, applied = self.enrich(plan_id, answers)
        run = engine.run_ruleset(ruleset or self.ruleset, enriched, self.as_of,
                                 capabilities=self.entitlements)
        for v in run["verdicts"]:
            v["citations"] = self.cites(v["authority_ids"])
            v["section_title"] = self.section_titles.get(v.get("section"))
        run["enrichment_applied"] = applied
        return run

    # ---------------------------------------------------------- crosswalk

    def crosswalk(self, plan_id):
        answers = self.answers(plan_id)
        if "kev" not in self.entitlements:
            return {"unavailable": True, "reason": "requires the kev capability, which this "
                                                   "install is not licensed for", "rows": []}
        rows = kev.crosswalk(answers, self.kev_snapshot)
        critical = [d for d in answers.get("devices", []) if d.get("is_critical_system")]
        with_rows = {r["asset"] for r in rows}
        return {
            "unavailable": False,
            "rows": rows,
            "critical_assets": len(critical),
            "critical_assets_without_rows": sorted(d.get("nickname") for d in critical
                                                   if d.get("nickname") not in with_rows),
            "ot_critical_assets": sum(1 for d in critical if d.get("is_ot")),
            "dispositions": sorted({r["disposition"] for r in rows}),
            "citations": self.cites(["cfr:101.650(e)(3)(i)", "cfr:101.630(c)(11)", "cfr:101.630(c)(12)"]),
            "source_appendices": ["I", "M", "N", "O"],
            "catalog": {"version": self.kev_snapshot["catalog_version"],
                        "count": self.kev_snapshot["count"]},
            "disposition_note": "The three evidence fields hang off the device, not off an "
                                "(asset, cve) pair, so a row cannot say which CVE they cover; "
                                "every row therefore reads unresolved until an observation is "
                                "its own entity. The evidence collected is shown beside it "
                                "unattributed rather than read as a resolution",
        }

    # ---------------------------------------------------------- registers

    def registers(self, plan_id):
        answers = self.answers(plan_id)
        devices = answers.get("devices", [])
        appendix = {a["register"]: a for a in self.content["appendices"] if a.get("register")}

        def dom(d):
            return "OT" if d.get("is_ot") else "IT"

        views = {}
        views["critical_it_ot"] = _view(
            ["nickname", "domain", "component_type", "system_function", "public_facing"],
            [{"nickname": d.get("nickname"), "domain": dom(d), "component_type": d.get("component_type"),
              "system_function": d.get("system_function"), "public_facing": d.get("public_facing")}
             for d in devices if d.get("is_critical_system")])
        views["hardware_software"] = _view(
            ["nickname", "domain", "component_type", "system_function"],
            [{"nickname": d.get("nickname"), "domain": dom(d), "component_type": d.get("component_type"),
              "system_function": d.get("system_function")} for d in devices],
            not_captured=["vendor", "model", "firmware_version", "software_version", "approval_status"])
        views["remote_access"] = _view(
            ["nickname", "domain", "public_facing", "internet_justification"],
            [{"nickname": d.get("nickname"), "domain": dom(d), "public_facing": True,
              "internet_justification": d.get("internet_justification")}
             for d in devices if d.get("public_facing")],
            not_captured=["remote_access_method", "authorized_users", "mfa"])
        if "kev" in self.entitlements:
            enriched = kev.enrich(answers, self.kev_snapshot)
            rows = []
            for d in enriched["devices"]:
                for cve in d["open_kevs"]:
                    entry = self.kev_snapshot["kevs"][cve]
                    rows.append({"nickname": d.get("nickname"), "domain": dom(d),
                                 "is_critical_system": d.get("is_critical_system"), "cve": cve,
                                 "vendor_project": entry["vendor_project"], "product": entry["product"],
                                 "date_added": entry["date_added"], "ransomware": entry["ransomware"]})
            views["kev"] = _view(["nickname", "domain", "is_critical_system", "cve", "vendor_project",
                                  "product", "date_added", "ransomware"], rows)
            views["kev"]["catalog_version"] = self.kev_snapshot["catalog_version"]
        else:
            views["kev"] = _view([], [], unavailable="requires the kev capability")
        views["compensating_control"] = _view(
            ["nickname", "domain", "compensating_control"],
            [{"nickname": d.get("nickname"), "domain": dom(d), "compensating_control": d["compensating_control"]}
             for d in devices if d.get("compensating_control")],
            not_captured=["cve_covered", "owner", "review_date"])
        views["risk_acceptance"] = _view(
            ["nickname", "domain", "risk_acceptance"],
            [{"nickname": d.get("nickname"), "domain": dom(d), "risk_acceptance": d["risk_acceptance"]}
             for d in devices if d.get("risk_acceptance")],
            not_captured=["owner", "justification", "compensating_control", "review_date"])
        views["backup"] = _view(
            ["system", "frequency", "last_tested", "protected"],
            list(answers.get("backups", [])))

        out = []
        for name, view in views.items():
            a = appendix[name]
            view.update({
                "register": name, "designation": a["designation"], "title": a["title"],
                "controlled_attachment": bool(a.get("controlled_attachment")),
                "citations": self.cites(a.get("authority_ids", [])),
                "attachment_version": "%s@%s" % (name, view["sha256"][:12]),
            })
            out.append(view)
        return out

    def attachment_refs(self, plan_id):
        return {"register:%s" % v["register"]: {"version": v["attachment_version"], "sha256": v["sha256"]}
                for v in self.registers(plan_id)}

    # ------------------------------------------------------------- freeze

    def pins(self):
        return {
            "ruleset": plan_version.ruleset_pin(self.ruleset["ruleset_version"],
                                                self.kev_snapshot["catalog_version"]),
            "question_module": self.questions["question_module_version"],
            "clause_library": self.clause_library["clause_library_version"],
            "plan": None,   # filled per plan
        }

    def freeze(self, plan_id, frozen_by):
        answers = self.answers(plan_id)
        existing = self.store.where("plan_versions", plan_id=plan_id)
        sequence = len(existing) + 1
        pins = self.pins()
        pins["plan"] = "%s@%d" % (plan_id, sequence)
        version = plan_version.freeze(plan_id, answers, pins, self.as_of, frozen_by,
                                      attachments=self.attachment_refs(plan_id), sequence=sequence)
        self.store.put("plan_versions", version["version_id"], version)
        return self.version_summary(version)

    def version_summary(self, v):
        return {k: v[k] for k in ("version_id", "plan_id", "sequence", "as_of", "frozen_by",
                                  "pins", "attachments", "answers_sha256")}

    def plan_versions(self, plan_id):
        vs = sorted(self.store.where("plan_versions", plan_id=plan_id), key=lambda v: v["sequence"])
        return [dict(self.version_summary(v), verify=plan_version.verify(v)) for v in vs]

    def latest_version(self, plan_id):
        vs = self.store.where("plan_versions", plan_id=plan_id)
        return max(vs, key=lambda v: v["sequence"]) if vs else None

    def amendment_diff(self, plan_id, from_id, to_id):
        a, b = self.store.get("plan_versions", from_id), self.store.get("plan_versions", to_id)
        return {"answers": plan_version.diff_answers(a["answers"], b["answers"]),
                "pins": plan_version.diff_pins(a, b),
                "citations": self.cites(["cfr:101.630(e)(2)", "cfr:101.630(e)(4)"])}

    # ------------------------------------------------------- surveillance

    def surveillance(self, plan_id, simulate=False):
        """Trigger 1 (ruleset change) and trigger 2 (catalog change), as separate paths.

        Both run over the latest frozen version, never live answers. With no
        real ruleset or catalog change on disk, `simulate` applies a labelled
        hypothetical so the shape of each path is visible. The simulation is
        the only placeholder; the diffing is the real code.
        """
        frozen = self.latest_version(plan_id)
        if frozen is None:
            return {"frozen_version": None, "note": "freeze a plan_version first; surveillance "
                                                    "runs over frozen answers only"}
        answers = frozen["answers"]
        pinned = plan_version.parse_ruleset_pin(frozen["pins"]["ruleset"])

        # trigger 1
        before = self.evaluate(plan_id, answers)
        ruleset_after = copy.deepcopy(self.ruleset)
        sim1 = None
        if simulate:
            ruleset_after["ruleset_version"] = self.ruleset["ruleset_version"] + "-sim"
            for rule in ruleset_after["rules"]:
                if rule["id"] == "alternate-cyso-designated":
                    rule["severity"] = "binding"
                    rule["authority_ids"] = ["cfr:101.620(b)(3)"]
            sim1 = placeholder("simulated amendment: alternate-cyso-designated escalated to binding. "
                               "Not a real Federal Register change.")
        after = self.evaluate(plan_id, answers, ruleset=ruleset_after)
        changes = engine.diff_runs(before, after)
        trigger1 = {
            "ruleset_pinned": pinned["ruleset"],
            "ruleset_current": ruleset_after["ruleset_version"],
            "changed": pinned["ruleset"] != ruleset_after["ruleset_version"],
            "simulation": sim1,
            "local_finding": [dict(c, citations=self.cites(c["authority_ids"])) for c in changes],
            "outbound_telemetry": {
                "install_id": self.install_id, "trigger": "ruleset",
                "from": pinned["ruleset"], "to": ruleset_after["ruleset_version"],
                "changes": [{"rule_id": c["rule_id"], "change": c["change"],
                             "from": c["from"], "to": c["to"], "severity": c["severity"]}
                            for c in changes],
            },
            "redacted": "answer values, asset names, facility identifiers",
        }

        # trigger 2
        trigger2 = {"catalog_pinned": pinned["kev"], "catalog_current": self.kev_snapshot["catalog_version"]}
        if "kev" not in self.entitlements:
            trigger2.update({"unavailable": True, "reason": "requires the kev capability"})
        else:
            snap_before = copy.deepcopy(self.kev_snapshot)
            sim2 = None
            if simulate:
                for cve in ("CVE-2021-44228", "CVE-2024-38112"):
                    snap_before["kevs"].pop(cve, None)
                snap_before["catalog_version"] = pinned["kev"] + "-sim"
                snap_before["count"] = len(snap_before["kevs"])
                sim2 = placeholder("simulated prior catalog: two entries removed so the "
                                   "additions are visible. Not a real CISA release.")
            local = kev.newly_affected(answers, snap_before, self.kev_snapshot)
            run_b = engine.run_ruleset(self.ruleset, kev.enrich(answers, snap_before), self.as_of,
                                       capabilities=self.entitlements)
            run_a = engine.run_ruleset(self.ruleset, kev.enrich(answers, self.kev_snapshot), self.as_of,
                                       capabilities=self.entitlements)
            trigger2.update({
                "unavailable": False,
                "simulation": sim2,
                "catalog_diff": kev.diff_snapshots(snap_before, self.kev_snapshot),
                "local_finding": local,
                "outbound_telemetry": kev.telemetry(local, self.install_id),
                "verdict_diff_changes": len(engine.diff_runs(run_b, run_a)),
                "note": "a catalog change is a data change and usually moves no verdict; "
                        "it reports through kev.telemetry, not engine.diff_runs",
                "redacted": "asset names, CVE ids, facility identifiers",
            })

        trigger3 = placeholder("trigger 3, a new vulnerability on an asset already in a plan "
                               "prompting a criticality re-check, has no data path until an "
                               "observation is its own entity keyed (asset, cve)")
        return {"frozen_version": self.version_summary(frozen), "trigger1": trigger1,
                "trigger2": trigger2, "trigger3": trigger3,
                "asset_level_alerts": "custody tier only" if "asset_alerts" not in self.entitlements
                else "entitled (asset_alerts)"}

    # ------------------------------------------------------- criticality

    def _criticality_session(self, plan_id, create):
        sid = "crit-%s" % plan_id
        if self.store.exists("sessions", sid):
            return self.store.get("sessions", sid)
        if not create:
            return None
        session = criticality.new_session(
            sid, plan_id, facilitator="facilitator",
            participants=[{"id": "facilitator", "name": "Consultant (facilitator)", "role": "facilitator"}],
            opened_at=self.as_of)
        self.store.put("sessions", sid, session)
        return session

    def criticality(self, plan_id):
        session = self._criticality_session(plan_id, create=True)
        assets = self.answers(plan_id).get("devices", [])
        scoped = criticality.scope(session, assets)
        try:
            criticality.rank(scoped["in_scope"], session)
            rank = None
        except NotImplementedError as exc:
            rank = placeholder(str(exc))
        return {
            "session": session,
            "questions": [dict(q, citations=self.cites(q["authority_ids"]))
                          for q in criticality.SCOPE_QUESTIONS],
            "roles": list(criticality.PARTICIPANT_ROLES),
            "assets": [{"nickname": a.get("nickname"), "is_ot": a.get("is_ot"),
                        "sam_is_critical_system": a.get("is_critical_system"),
                        "effective": criticality.effective_answers(session, a.get("nickname"))}
                       for a in assets],
            "stage1": scoped,
            "stage2": {"rank": rank, "survivors": scoped["in_scope"]},
            "licensed": "criticality" in self.entitlements,
        }

    def criticality_add_participant(self, plan_id, participant):
        session = self._criticality_session(plan_id, create=True)
        session = criticality.add_participant(session, participant)
        self.store.put("sessions", session["id"], session)
        return self.criticality(plan_id)

    def criticality_answer(self, plan_id, asset_id, question_id, value, participant_id,
                           on_behalf_of=None, note=None):
        session = self._criticality_session(plan_id, create=True)
        recorded_at = "%s#%d" % (self.as_of, len(session["answers"]) + 1)   # ordinal, no clock
        session = criticality.record_answer(session, asset_id, question_id, value, participant_id,
                                            recorded_at, on_behalf_of=on_behalf_of, note=note)
        self.store.put("sessions", session["id"], session)
        # The sort takes effect as soon as an asset is decided, so the steps that
        # depend on it (follow-ups, registers, crosswalk, gap report) move with it.
        self.apply_sort(plan_id)
        return self.criticality(plan_id)

    def apply_sort(self, plan_id):
        """Write the stage 1 outcome onto each decided device as is_critical_system.

        In scope becomes true, out of scope becomes false, pending is left as
        whatever SAM or the inventory supplied. The source is recorded on the
        device so a SAM value and a workshop value are distinguishable.
        """
        session = self._criticality_session(plan_id, create=False)
        answers = self.answers(plan_id)
        if session is None:
            return {"applied": 0, "pending": len(answers.get("devices", []))}
        scoped = criticality.scope(session, answers.get("devices", []))
        outcome = {}
        for row in scoped["in_scope"]:
            outcome[row["asset_id"]] = True
        for row in scoped["out_of_scope"]:
            outcome[row["asset_id"]] = False
        applied = 0
        for d in answers.get("devices", []):
            name = d.get("nickname")
            if name in outcome:
                if d.get("is_critical_system") != outcome[name] or d.get("criticality_source") != session["id"]:
                    applied += 1
                d["is_critical_system"] = outcome[name]
                d["criticality_source"] = session["id"]
        self.save_answers(plan_id, answers)
        return {"applied": applied, "in_scope": len(scoped["in_scope"]),
                "out_of_scope": len(scoped["out_of_scope"]), "pending": len(scoped["pending"])}

    # ------------------------------------------------------------ records

    def records_for(self, facility_id):
        facility = self.store.get("facilities", facility_id)
        rows = self.store.where("records", facility_id=facility_id)
        return {
            "facility": facility,
            "asset_type": facility["asset_type"],
            "custodian_role": records.CUSTODIAN_ROLE[facility["asset_type"]],
            "accountable_role": records.ACCOUNTABLE_ROLE,
            "recordkeeping_authority": self.cite(records.RECORDKEEPING_AUTHORITY[facility["asset_type"]])
            if records.RECORDKEEPING_AUTHORITY[facility["asset_type"]] else
            placeholder("the recordkeeping section for %s is not in the authorities registry"
                        % facility["asset_type"]),
            "categories": [dict(cat, id=cid, citations=self.cites(cat["authority_ids"]),
                                source_paragraph=cat["source_paragraph"][facility["asset_type"]])
                           for cid, cat in records.categories_for(facility["asset_type"]).items()],
            "records": [dict(r, retention=records.status(r, self.as_of),
                             citations=self.cites(r["authority_ids"])) for r in rows],
            "cadence": records.cadence(rows, self.as_of),
            "cadence_citations": self.cites(["cfr:101.635"]),
        }

    def add_record(self, facility_id, category, fields, custodian, accountable_officer, created_by):
        facility = self.store.get("facilities", facility_id)
        rid = "rec-%s-%d" % (facility_id, len(self.store.ids("records")) + 1)
        rec = records.new_record(rid, category, facility["asset_type"], facility_id, fields,
                                 custodian, accountable_officer, created_by)
        self.store.put("records", rid, rec)
        return self.records_for(facility_id)

    # -------------------------------------------------------------- audit

    def audit_start(self, plan_id, scope, auditor, client_representative=None):
        frozen = self.latest_version(plan_id)
        if frozen is None:
            raise ValueError("freeze a plan_version before auditing; audits run over frozen answers")
        run_id = "audit-%s-%d" % (plan_id, len(self.store.ids("audit_runs")) + 1)
        run = audit.new_run(run_id, frozen["version_id"], scope, auditor, self.as_of, client_representative)
        self.store.put("audit_runs", run_id, run)
        return self.audit_view(run_id)

    def audit_runs(self, plan_id=None):
        runs = self.store.all("audit_runs")
        if plan_id:
            ids = {v["version_id"] for v in self.store.where("plan_versions", plan_id=plan_id)}
            runs = [r for r in runs if r["plan_version_id"] in ids]
        return [{k: r[k] for k in ("id", "plan_version_id", "scope", "auditor", "as_of", "status")}
                | {"findings": len(r["findings"])} for r in runs]

    def audit_view(self, run_id):
        run = self.store.get("audit_runs", run_id)
        frozen = self.store.get("plan_versions", run["plan_version_id"])
        verdicts = self.evaluate(frozen["plan_id"], frozen["answers"])["verdicts"]
        cands = audit.from_verdicts(run, verdicts)
        binding = {a: v["binding"] for a, v in self.authorities.items()}
        candidates = []
        for c in cands["candidates"]:
            c["citations"] = self.cites(c["authority_ids"])
            c["template"] = placeholder("no finding template is authored for %s; deficiency text "
                                        "is the rule message, remediation is absent"
                                        % ", ".join(c["authority_ids"]))
            c["already_added"] = any(f["id"] == c["id"] for f in run["findings"])
            c["cites_binding_authority"] = any(binding.get(a) for a in c["authority_ids"])
            candidates.append(c)
        rep = audit.report(run, self.section_titles)
        for f in rep["deficiencies"] + rep["recommendations"]:
            f["citations"] = self.cites(f["authority_ids"])
            if f["kind"] == "deficiency" and not f["remediation"]:
                f["remediation_placeholder"] = placeholder("remediation text not authored")
        return {"run": run, "report": rep, "candidates": candidates,
                "out_of_scope_verdicts": cands["out_of_scope_verdicts"],
                "frozen_version": self.version_summary(frozen),
                "templates_version": audit.TEMPLATES["template_version"]}

    def audit_accept(self, run_id, candidate_ids):
        run = self.store.get("audit_runs", run_id)
        view = self.audit_view(run_id)
        binding = {a: v["binding"] for a, v in self.authorities.items()}
        for c in view["candidates"]:
            if c["id"] in candidate_ids and not c["already_added"]:
                finding = {k: v for k, v in c.items()
                           if k not in ("citations", "template", "already_added", "cites_binding_authority")}
                if finding["kind"] == "deficiency":
                    finding = audit.deficiency(finding["id"], finding["section"], finding["authority_ids"],
                                               finding["text"], finding["remediation"],
                                               finding["checklist_item_id"], finding["evidence"],
                                               authority_is_binding=binding)
                run = audit.add_finding(run, finding)
        self.store.put("audit_runs", run_id, run)
        return self.audit_view(run_id)

    def audit_add_recommendation(self, run_id, section, text, authority_ids=None):
        run = self.store.get("audit_runs", run_id)
        fid = "%s:rec-%d" % (run_id, len(run["findings"]) + 1)
        run = audit.add_finding(run, audit.recommendation(fid, section, text, authority_ids))
        self.store.put("audit_runs", run_id, run)
        return self.audit_view(run_id)

    def audit_close(self, run_id):
        run = audit.close(self.store.get("audit_runs", run_id))
        self.store.put("audit_runs", run_id, run)
        return self.audit_view(run_id)

    # ------------------------------------------------------------- render

    def render_section(self, plan_id, section_number):
        out = render.render_section(self.clause_library, section_number, self.answers(plan_id))
        out["title"] = self.section_titles.get(section_number)
        for b in out["blocks"]:
            if b["kind"] == "placeholder":
                b["placeholder"] = placeholder(b["reason"])
        return out

    # ------------------------------------------------------------- wizard
    #
    # The guided walk-through. Steps come from the question module. Percent
    # complete is answered applicable items over applicable items, and "what is
    # missing" is unanswered prompts plus the authored message of every failed
    # rule. Nothing here is estimated or generated.

    def _question_map(self, plan_id):
        return {q["id"]: q for q in self.questions_for(plan_id)["questions"]}

    def device_fields(self):
        q = next(q for q in self.questions["questions"] if q["path"] == "devices")
        return q["fields"]

    def _rule_step_map(self):
        """Which step each rule belongs to, by the answer paths it reads.

        A rule that reads only the inventory and the critical flag belongs to
        the sort step; any other rule over devices belongs to the follow-ups;
        everything else belongs to the first questions step that carries a
        path the rule reads. Unmapped rules count in their section only.
        """
        # Predicate paths only: applies_to says when a rule applies, not what
        # it checks, and it usually reads the asset type on the first step.
        readers = _rule_readers(self.ruleset, include_applies_to=False)
        rule_paths = {}
        for path, rids in readers.items():
            for rid in rids:
                rule_paths.setdefault(rid, set()).add(path)
        step_ids = {st["kind"]: st["id"] for st in self.questions["steps"] if st["kind"] in ("sort", "followups")}
        qpaths = {q["id"]: q["path"] for q in self.questions["questions"]}
        mapping = {}
        for rule in self.ruleset["rules"]:
            paths = rule_paths.get(rule["id"], set())
            dev = {p for p in paths if p == "devices" or p.startswith("devices.")}
            if dev and dev <= {"devices", "devices.is_critical_system"}:
                mapping[rule["id"]] = step_ids.get("sort")
                continue
            if dev:
                mapping[rule["id"]] = step_ids.get("followups")
                continue
            mapping[rule["id"]] = None
            for st in self.questions["steps"]:
                if st["kind"] != "questions":
                    continue
                mine = [qpaths[q] for q in st["questions"]]
                if any(p == qp or p.startswith(qp + ".") for p in paths for qp in mine):
                    mapping[rule["id"]] = st["id"]
                    break
        return mapping

    def _vacuous(self, verdict, answers, rule_paths):
        """A pass that checked nothing: the rule reads a collection that is empty.

        for_each and every succeed over an empty list, so with no inventory the
        KEV and internet-exposure rules pass without looking at anything. An
        obligation over zero assets is not demonstrated, so such a pass is not
        counted as done. A collection that is present but filtered to nothing
        (an inventory with no public-facing OT) is a real pass and does count.
        """
        if verdict["status"] != "pass":
            return False
        for path in rule_paths.get(verdict["rule_id"], ()):
            value = engine.resolve(path, answers, answers)
            if isinstance(value, list) and not value:
                return True
        return False

    def _fold_verdicts(self, verdicts, answers):
        """Count required checks into a total/done pair and list what failed.

        Binding rules count: pass is done, fail, unavailable, or a vacuous pass
        is not. Best practice rules never count toward percent but are listed
        as recommended. Not applicable rules are ignored.
        """
        readers = _rule_readers(self.ruleset, include_applies_to=False)
        rule_paths = {}
        for path, rids in readers.items():
            for rid in rids:
                rule_paths.setdefault(rid, set()).add(path)
        total, done, missing = 0, 0, []
        for v in verdicts:
            if v["status"] == "not_applicable":
                continue
            if v["severity"] == "binding":
                total += 1
                if v["status"] == "pass" and self._vacuous(v, answers, rule_paths):
                    # A passing verdict carries no message, so use the rule's own.
                    authored = next((r.get("message") for r in self.ruleset["rules"]
                                     if r["id"] == v["rule_id"]), None)
                    missing.append({"kind": "asset", "text": "%s (nothing to check yet: no assets)"
                                    % (authored or v["rule_id"])})
                elif v["status"] == "pass":
                    done += 1
                elif v["status"] == "fail":
                    missing.append({"kind": "required", "text": v.get("message") or v["rule_id"]})
                else:
                    missing.append({"kind": "unavailable", "text": v.get("message")})
            elif v["status"] == "fail":
                missing.append({"kind": "recommended", "text": v.get("message") or v["rule_id"]})
        return total, done, missing

    def wizard(self, plan_id):
        plan = self.plan(plan_id)
        qmap = self._question_map(plan_id)
        answers = self.answers(plan_id)
        devices = answers.get("devices", [])
        run = self.evaluate(plan_id)
        step_of = self._rule_step_map()
        by_step = {}
        for v in run["verdicts"]:
            by_step.setdefault(step_of.get(v["rule_id"]), []).append(v)
        steps, done_all, total_all = [], 0, 0
        for i, st in enumerate(self.questions["steps"]):
            kind = st["kind"]
            note = None
            missing = []
            if kind == "questions":
                applicable = [qmap[q] for q in st["questions"]
                              if qmap[q]["applicable"] and not qmap[q].get("optional")]
                total = len(applicable)
                done = sum(1 for q in applicable if _has_value(q["value"]))
                missing = [{"kind": "question", "text": q["prompt"]} for q in applicable if not _has_value(q["value"])]
            elif kind == "inventory":
                total, done = 1, (1 if devices else 0)
                note = "%d assets" % len(devices)
                if not devices:
                    missing = [{"kind": "question", "text": "Add at least one asset"}]
            elif kind == "sort":
                scoped = self._scope(plan_id, devices)
                total = len(devices)
                done = len(scoped["in_scope"]) + len(scoped["out_of_scope"])
                note = None if devices else "add assets first"
                missing = [{"kind": "asset", "text": "Sort %s" % r["asset_id"]} for r in scoped["pending"]]
            elif kind == "followups":
                f = self.followups(plan_id)
                total, done = f["total"], f["total"] - f["outstanding"]
                note = None if total else "nothing to ask until assets are sorted in"
                missing = [{"kind": "asset", "text": it["prompt"]} for it in f["items"] if not it["done"]]
            else:
                total, done = 0, 0
            r_total, r_done, r_missing = self._fold_verdicts(by_step.get(st["id"], []), answers)
            total += r_total
            done += r_done
            missing += r_missing
            percent = int(round(100.0 * done / total)) if total else 0
            steps.append({
                "index": i + 1, "id": st["id"], "title": st["title"], "blurb": st.get("blurb", ""),
                "kind": kind, "total": total, "done": done, "percent": percent, "note": note,
                "missing": missing,
                "status": ("complete" if total and done == total else "in_progress" if done else "not_started"),
            })
            if kind != "review":
                done_all += done
                total_all += total
        overall = int(round(100.0 * done_all / total_all)) if total_all else 0
        for s in steps:
            if s["kind"] == "review":
                s["percent"] = overall
                s["status"] = "complete" if overall == 100 else "in_progress" if overall else "not_started"
        return {"plan": {k: v for k, v in plan.items() if k != "answers"},
                "steps": steps, "overall": {"done": done_all, "total": total_all, "percent": overall},
                "as_of": self.as_of}

    def step(self, plan_id, step_id):
        st = next((s for s in self.questions["steps"] if s["id"] == step_id), None)
        if st is None:
            raise ValueError("no step %r" % step_id)
        wiz = self.wizard(plan_id)
        me = next(s for s in wiz["steps"] if s["id"] == step_id)
        idx = wiz["steps"].index(me)
        out = dict(me)
        out["previous"] = wiz["steps"][idx - 1]["id"] if idx > 0 else None
        out["next"] = wiz["steps"][idx + 1]["id"] if idx + 1 < len(wiz["steps"]) else None
        out["count"] = len(wiz["steps"])
        out["overall"] = wiz["overall"]
        kind = st["kind"]
        if kind == "questions":
            qmap = self._question_map(plan_id)
            out["questions"] = [qmap[q] for q in st["questions"]]
        elif kind == "inventory":
            out["inventory"] = self.inventory(plan_id)
        elif kind == "sort":
            out["sort"] = self.criticality(plan_id)
            out["sort"]["applied"] = self.apply_sort(plan_id)
        elif kind == "followups":
            out["followups"] = self.followups(plan_id)
        else:
            out["progress"] = self.progress(plan_id)
            out["versions"] = self.plan_versions(plan_id)
        return out

    def _scope(self, plan_id, devices):
        session = self._criticality_session(plan_id, create=False)
        if session is None:
            return {"in_scope": [], "out_of_scope": [],
                    "pending": [{"asset_id": d.get("nickname")} for d in devices]}
        return criticality.scope(session, devices)

    def inventory(self, plan_id):
        answers = self.answers(plan_id)
        devices = answers.get("devices", [])
        scoped = self._scope(plan_id, devices)
        outcome = {}
        for bucket in ("in_scope", "out_of_scope", "pending"):
            for row in scoped[bucket]:
                outcome[row["asset_id"]] = bucket
        rows = []
        for d in devices:
            rows.append(dict(d, sort=outcome.get(d.get("nickname"), "pending"),
                             observed_cve_count=len(d.get("observed_cves") or d.get("open_kevs") or [])))
        return {
            "devices": rows,
            "fields": self.device_fields(),
            "imports": answers.get("scan_imports", []),
            "template_url": "/gui/samples/inventory-template.csv",
            "example_scan_url": "/gui/samples/example-scan.nessus",
            "formats": {
                "inventory": "CSV with a header row. Recognised columns: " + ", ".join(
                    "%s (%s)" % (k, "/".join(v[:3])) for k, v in ingest.INVENTORY_COLUMNS.items()),
                "scan": "Nessus .nessus export, or any CSV with a host column and a CVE column",
            },
        }

    def add_device(self, plan_id, device):
        if not device.get("nickname"):
            raise ValueError("an asset needs a name")
        allowed = {f["key"] for f in self.device_fields()}
        clean = {k: v for k, v in device.items() if k in allowed and v not in (None, "")}
        answers, report = ingest.apply_inventory(self.answers(plan_id), [clean])
        self.save_answers(plan_id, answers)
        return dict(report, inventory=self.inventory(plan_id))

    def remove_device(self, plan_id, nickname):
        answers = self.answers(plan_id)
        before = len(answers.get("devices", []))
        answers["devices"] = [d for d in answers.get("devices", [])
                              if (d.get("nickname") or "").lower() != nickname.lower()]
        if len(answers["devices"]) == before:
            raise ValueError("no asset named %r" % nickname)
        self.save_answers(plan_id, answers)
        return self.inventory(plan_id)

    def set_device_field(self, plan_id, nickname, field, value):
        allowed = {f["key"] for f in self.device_fields()}
        if field not in allowed:
            raise ValueError("field %r is not an asset field" % field)
        answers = self.answers(plan_id)
        dev = next((d for d in answers.get("devices", [])
                    if (d.get("nickname") or "").lower() == nickname.lower()), None)
        if dev is None:
            raise ValueError("no asset named %r" % nickname)
        dev[field] = value
        self.save_answers(plan_id, answers)
        return dev

    def import_inventory_preview(self, plan_id, text):
        parsed = ingest.parse_inventory_csv(text)
        existing = {(d.get("nickname") or "").lower() for d in self.answers(plan_id).get("devices", [])}
        for d in parsed["devices"]:
            d["_action"] = "update" if d["nickname"].lower() in existing else "add"
        return parsed

    def import_inventory_apply(self, plan_id, devices):
        clean = [{k: v for k, v in d.items() if not k.startswith("_")} for d in devices]
        answers, report = ingest.apply_inventory(self.answers(plan_id), clean)
        self.save_answers(plan_id, answers)
        return dict(report, inventory=self.inventory(plan_id))

    def import_scan_preview(self, plan_id, text, filename=""):
        parsed = ingest.parse_scan(text, filename)
        match = ingest.match_hosts(parsed["hosts"], self.answers(plan_id).get("devices", []))
        catalog = self.kev_snapshot["kevs"] if "kev" in self.entitlements else None
        for row in match["matched"] + match["unmatched"]:
            row["kev_count"] = (len([c for c in row["cves"] if c in catalog])
                                if catalog is not None else None)
        return {"format": parsed["format"], "filename": filename, "hosts": len(parsed["hosts"]),
                "matched": match["matched"], "unmatched": match["unmatched"],
                "devices": [d.get("nickname") for d in self.answers(plan_id).get("devices", [])],
                "kev_licensed": catalog is not None}

    def import_scan_apply(self, plan_id, assignments, source):
        answers, report = ingest.apply_scan(self.answers(plan_id), assignments, source or "scan")
        self.save_answers(plan_id, answers)
        return dict(report, inventory=self.inventory(plan_id))

    def followups(self, plan_id):
        """Per-asset questions the rules need, derived from the sorted inventory."""
        answers = self.answers(plan_id)
        enriched, applied = self.enrich(plan_id, answers)
        items = []
        for d in enriched.get("devices", []):
            name = d.get("nickname")
            if d.get("is_ot") and d.get("public_facing"):
                items.append({
                    "id": "%s:internet_justification" % name, "device": name, "kind": "field",
                    "field": "internet_justification", "type": "short_text",
                    "prompt": "%s is an OT system reachable from the public internet. Why is that necessary?" % name,
                    "value": d.get("internet_justification"),
                    "done": bool(d.get("internet_justification")),
                })
            if not d.get("is_critical_system"):
                continue
            if "kev" in applied:
                kevs = d.get("open_kevs", [])
                if kevs:
                    values = {f: d.get(f) for f in ("compensating_control", "remediation_plan", "risk_acceptance")}
                    items.append({
                        "id": "%s:kev_disposition" % name, "device": name, "kind": "kev_disposition",
                        "prompt": "%s has %d known exploited vulnerabilit%s: %s. What has been done about %s?"
                                  % (name, len(kevs), "y" if len(kevs) == 1 else "ies", ", ".join(kevs),
                                     "it" if len(kevs) == 1 else "them"),
                        "fields": [
                            {"key": "compensating_control", "prompt": "A compensating control is in place (describe it)"},
                            {"key": "remediation_plan", "prompt": "Remediation is planned (owner, date, cost)"},
                            {"key": "risk_acceptance", "prompt": "The risk is accepted (reference and reason)"},
                        ],
                        "values": values, "cves": kevs,
                        "done": any(values.values()),
                    })
                elif not (d.get("observed_cves") or d.get("open_kevs")):
                    items.append({
                        "id": "%s:cve_source_note" % name, "device": name, "kind": "field",
                        "field": "cve_source_note", "type": "short_text",
                        "prompt": "%s is a critical system but no scan or advisory data has been imported for it. "
                                  "Import scanner output on the inventory step, or describe how its vulnerabilities are identified." % name,
                        "value": d.get("cve_source_note"),
                        "done": bool(d.get("cve_source_note")),
                    })
        out = {"items": items, "total": len(items), "outstanding": sum(1 for i in items if not i["done"]),
               "kev_licensed": "kev" in self.entitlements}
        if "kev" not in self.entitlements:
            out["placeholder"] = placeholder("the kev capability is not licensed, so known exploited "
                                             "vulnerabilities on critical assets cannot be identified here")
        return out

    def progress(self, plan_id):
        """Percent complete and what is missing, per section, with no citations."""
        run = self.evaluate(plan_id)
        answers = self.answers(plan_id)
        qs = self.questions_for(plan_id)["questions"]
        follow = self.followups(plan_id)
        sections, done_all, total_all = [], 0, 0
        for s in self.content["sections"]:
            n = s["number"]
            mine = [q for q in qs if q["section"] == n and q["applicable"] and not q.get("optional")]
            answered = [q for q in mine if _has_value(q["value"])]
            missing = [{"kind": "question", "text": q["prompt"]} for q in mine if not _has_value(q["value"])]
            total, done = len(mine), len(answered)
            if n == 6:
                total += follow["total"]
                done += follow["total"] - follow["outstanding"]
                missing += [{"kind": "asset", "text": i["prompt"]} for i in follow["items"] if not i["done"]]
            # Required checks count toward the section, so a section with a
            # failing required item can never read 100%.
            r_total, r_done, r_missing = self._fold_verdicts(
                [v for v in run["verdicts"] if v.get("section") == n], answers)
            total += r_total
            done += r_done
            missing += r_missing
            sections.append({
                "number": n, "title": s["title"], "total": total, "done": done,
                "percent": (int(round(100.0 * done / total)) if total else None),
                "in_interview": total > 0 or any(v.get("section") == n for v in run["verdicts"]),
                "missing": missing,
            })
            done_all += done
            total_all += total
        summary = run["summary"]
        # One denominator everywhere: the overall figure is the walk-through's,
        # which also counts the inventory and sort steps. The per-section rows
        # above count questions and follow-ups only.
        return {
            "sections": sections,
            "overall": self.wizard(plan_id)["overall"],
            "questions": {"done": done_all, "total": total_all},
            "export": {"allowed": summary["export_allowed"], "blocking": summary["blocking"],
                       "warnings": summary["warnings"], "unavailable": summary["unavailable"]},
            "as_of": self.as_of,
        }

    # ------------------------------------------------------------ content

    def spine(self):
        return [{"number": s["number"], "title": s["title"], "status": s["status"],
                 "requirements": len(s.get("requirements", [])),
                 "citations": self.cites(s["connected_authority_ids"])}
                for s in self.content["sections"]]

    def appendices(self):
        return [{"designation": a["designation"], "title": a["title"], "kind": a["kind"],
                 "register": a.get("register"), "controlled_attachment": bool(a.get("controlled_attachment")),
                 "status": a["status"], "citations": self.cites(a.get("authority_ids", []))}
                for a in self.content["appendices"]]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _has_value(v):
    return v not in (None, "", [], {})


def _set_path(answers, path, value):
    cur = answers
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _view(columns, rows, not_captured=None, unavailable=None):
    return {
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "sha256": plan_version.content_hash(rows),
        "fields_not_in_answer_model": not_captured or [],
        "unavailable": unavailable,
    }


def _rule_readers(ruleset, include_applies_to=True):
    """Map answer path -> rule ids whose predicate reads it (top-level paths only)."""
    readers = {}

    def walk(node, rid, prefix=""):
        if not isinstance(node, dict):
            return
        for op, arg in node.items():
            if op in ("all", "any"):
                for c in arg:
                    walk(c, rid, prefix)
            elif op == "not":
                walk(arg, rid, prefix)
            elif op == "answered":
                readers.setdefault(_join(prefix, arg), set()).add(rid)
            elif op in ("every", "some", "none"):
                readers.setdefault(_join(prefix, arg[0]), set()).add(rid)
                walk(arg[1], rid, _join(prefix, arg[0]))
            elif op == "for_each":
                readers.setdefault(_join(prefix, arg["path"]), set()).add(rid)
                walk(arg.get("where"), rid, _join(prefix, arg["path"]))
                walk(arg["must"], rid, _join(prefix, arg["path"]))
            elif isinstance(arg, list) and arg and isinstance(arg[0], str):
                readers.setdefault(_join(prefix, arg[0]), set()).add(rid)

    for rule in ruleset["rules"]:
        walk(rule.get("predicate"), rule["id"])
        if include_applies_to:
            walk(rule.get("applies_to"), rule["id"])
    return readers


def _join(prefix, path):
    if path.startswith("$."):
        return path[2:]
    return "%s.%s" % (prefix, path) if prefix else path


def collect_identifiers():
    """Best-effort machine identifiers for the fingerprint. Facade-level, ambient by design.

    Each component reports collected or unavailable. Nothing is invented: a
    component that cannot be read is None and the fingerprint needs 2 of 3.
    """
    values, status = {}, {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            values["machine_guid"] = winreg.QueryValueEx(key, "MachineGuid")[0]
        status["machine_guid"] = "collected"
    except Exception as exc:  # noqa: BLE001 - any failure means unavailable
        status["machine_guid"] = "unavailable (%s)" % type(exc).__name__
    try:
        import ctypes
        serial = ctypes.c_uint32(0)
        root = os.path.splitdrive(os.path.abspath(os.sep))[0] + "\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, ctypes.byref(serial),
                                                          None, None, None, 0)
        if ok:
            values["volume_serial"] = "%08X" % serial.value
            status["volume_serial"] = "collected"
        else:
            status["volume_serial"] = "unavailable (GetVolumeInformationW failed)"
    except Exception as exc:  # noqa: BLE001
        status["volume_serial"] = "unavailable (%s)" % type(exc).__name__
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
            capture_output=True, text=True, timeout=8)
        uuid = out.stdout.strip()
        if out.returncode == 0 and uuid:
            values["smbios_uuid"] = uuid
            status["smbios_uuid"] = "collected"
        else:
            status["smbios_uuid"] = "unavailable (no output)"
    except Exception as exc:  # noqa: BLE001
        status["smbios_uuid"] = "unavailable (%s)" % type(exc).__name__
    return {"values": values, "status": status}


def seed(answer_store, content, as_of):
    """Seed a fresh workspace from the fixture. Demo data, labelled as such."""
    fixture = _load(FIXTURE)
    answers = {k: v for k, v in fixture.items() if not k.startswith("_")}
    answer_store.put("tenants", "harbor-cyber-consulting",
                     {"name": "Harbor Cyber Consulting", "kind": "consulting_org", "seed": True})
    answer_store.put("tenants", "successor-consulting",
                     {"name": "Successor Consulting LLC", "kind": "consulting_org", "seed": True,
                      "note": "exists so the facility transfer path can be exercised"})
    answer_store.put("facilities", "meg-westport", {
        "tenant_id": "harbor-cyber-consulting", "name": answers["asset"]["name"],
        "asset_type": "facility", "owner_operator": content["facility"]["name"],
        "cognizant_cotp": answers["asset"]["cognizant_cotp"], "seed": True})
    answer_store.put("facilities", "meg-1-platform", {
        "tenant_id": "harbor-cyber-consulting", "name": content["facility"]["profile"]["offshore_asset"],
        "asset_type": "ocs_facility", "owner_operator": content["facility"]["name"],
        "cognizant_cotp": None, "seed": True,
        "note": "seeded from the content instance profile so a multi-facility plan and a shared "
                "CySO can be shown; carries no assessment data of its own"})
    answer_store.put("plans", "meg-westport-csp", {
        "tenant_id": "harbor-cyber-consulting", "facility_ids": ["meg-westport"],
        "title": "MEG Westport Cybersecurity Plan", "delivery_mode": "separate_submission", "sequence": 1,
        "seed": "fixtures/meg-westport-original.answers.json", "answers": answers})
    multi = copy.deepcopy(answers)
    multi["asset"] = {"type": "ocs_facility", "name": content["facility"]["profile"]["offshore_asset"],
                      "cognizant_cotp": None}
    multi["devices"] = []
    answer_store.put("plans", "meg-group-multi-facility-csp", {
        "tenant_id": "harbor-cyber-consulting", "facility_ids": ["meg-westport", "meg-1-platform"],
        "title": "MEG multi-facility Cybersecurity Plan (101.630(d)(2))",
        "delivery_mode": "annex", "sequence": 2,
        "seed": "derived from the fixture: organisation and CySO answers copied, inventory emptied",
        "answers": multi})
    for i, (cat, fields) in enumerate([
        ("training", {"date": "2026-02-10", "duration": "2 hours",
                      "description": "Annual cybersecurity awareness training (seed)",
                      "attendees": ["Sarah Chen", "James Thompson", "Alex Rivera", "Lisa Wong"]}),
        ("drill", {"date_held": "2026-03-18", "description": "Phishing response drill (seed)",
                   "participants": ["James Thompson", "Lisa Wong"],
                   "lessons_learned": "Help desk escalation path needed a named backup"}),
        ("drill", {"date_held": "2024-06-05", "description": "Ransomware isolation drill (seed)",
                   "participants": ["James Thompson", "Alex Rivera"],
                   "lessons_learned": "Segment isolation runbook was out of date"}),
    ], start=1):
        rec = records.new_record("rec-meg-westport-%d" % i, cat, "facility", "meg-westport", fields,
                                 custodian="Robert Williams", accountable_officer="Jordan Kim",
                                 created_by="seed:demo")
        answer_store.put("records", rec["id"], rec)
