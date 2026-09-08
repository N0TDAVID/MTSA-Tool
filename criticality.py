"""Criticality: a two-stage narrowing, run as a facilitated workshop.

Tier 1. Standard library only. Never imports engine.

Stage 1, scope. For each asset: is it critical, and could its compromise cause
a Transportation Security Incident? Both answered yes narrows a general asset
inventory down to the assets actually inside the MTSA footprint. Most of an
inventory falls out here, and the plan covers what survives.

Stage 2, rank. Among the survivors, order by consequence. This never says one
critical asset does not matter. It sets tracking priority for KEV and CVE
monitoring. The scoring instrument is the CFDD questionnaire in SAM, extended
with TSI questions and more questions overall so it stops producing ties. It is
NOT reimplemented here: rank() raises, and the GUI shows that as a placeholder.

Stage 1 is a group activity. Operations, security, cyber, and IT answer it
together because no one of them knows both what an asset does and what breaks
when it stops. So a session has participants, every answer is attributed to
one, and a facilitator may be driving the tool while the client answers. That
is a product requirement, not a packaging idea.

Answer typing. Both scope questions are tri-state: yes, no, not_applicable. A
requirement that fails and one that does not apply are different findings.

This module reaches the engine one way only, through enrich(): it projects the
stage 1 outcome onto each device as plain JSON. No clock anywhere; the caller
supplies recorded_at.
"""

import copy

CAPABILITY = "criticality"
MODULE_VERSION = "2026.09.08"

TRI_STATE = ("yes", "no", "not_applicable")

# The two stage 1 questions. Authority ids name registry entries; the facade
# resolves them to display text. 101.650(b) is where critical IT and OT systems
# are designated; the TSI question has no CFR text of its own in the registry
# and cites the same designation duty (see QUESTIONS.md).
SCOPE_QUESTIONS = (
    {"id": "is_critical", "type": "tri_state",
     "prompt": "Is this asset a critical IT or OT system?",
     "authority_ids": ["cfr:101.650(b)"]},
    {"id": "tsi_possible", "type": "tri_state",
     "prompt": "Could compromise of this asset cause a Transportation Security Incident?",
     "authority_ids": ["cfr:101.650(b)"]},
)

PARTICIPANT_ROLES = ("operations", "security", "cyber", "it", "facilitator", "owner_operator", "other")


class CriticalityError(Exception):
    """Bad session, unknown participant, or a value outside the tri-state."""


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------

def new_session(session_id, plan_id, facilitator, participants, opened_at):
    """A workshop session. Participants carry id, name, role. Facilitator is one of them."""
    seen = set()
    for p in participants:
        for key in ("id", "name", "role"):
            if not p.get(key):
                raise CriticalityError("participant needs id, name, role: %r" % (p,))
        if p["role"] not in PARTICIPANT_ROLES:
            raise CriticalityError("unknown participant role %r" % p["role"])
        if p["id"] in seen:
            raise CriticalityError("duplicate participant id %r" % p["id"])
        seen.add(p["id"])
    if facilitator not in seen:
        raise CriticalityError("facilitator %r is not a participant" % facilitator)
    return {
        "id": session_id,
        "plan_id": plan_id,
        "module_version": MODULE_VERSION,
        "opened_at": opened_at,
        "facilitator": facilitator,
        "participants": copy.deepcopy(participants),
        "answers": [],          # attributed, append-only within the session
        "stage": 1,
    }


def add_participant(session, participant):
    out = copy.deepcopy(session)
    if any(p["id"] == participant["id"] for p in out["participants"]):
        raise CriticalityError("participant %r already present" % participant["id"])
    if participant.get("role") not in PARTICIPANT_ROLES:
        raise CriticalityError("unknown participant role %r" % participant.get("role"))
    out["participants"].append(copy.deepcopy(participant))
    return out


def record_answer(session, asset_id, question_id, value, participant_id, recorded_at,
                  on_behalf_of=None, note=None):
    """Append one attributed answer. Returns a new session; never mutates.

    on_behalf_of covers the facilitator-drives-while-client-answers case: the
    facilitator's id is the recorder, the client participant is the source.
    """
    if question_id not in {q["id"] for q in SCOPE_QUESTIONS}:
        raise CriticalityError("unknown stage 1 question %r" % question_id)
    if value not in TRI_STATE:
        raise CriticalityError("value must be one of %s, got %r" % (TRI_STATE, value))
    ids = {p["id"] for p in session["participants"]}
    if participant_id not in ids:
        raise CriticalityError("unknown participant %r" % participant_id)
    if on_behalf_of is not None and on_behalf_of not in ids:
        raise CriticalityError("unknown participant %r" % on_behalf_of)
    out = copy.deepcopy(session)
    out["answers"].append({
        "asset_id": asset_id,
        "question_id": question_id,
        "value": value,
        "recorded_by": participant_id,
        "source": on_behalf_of or participant_id,
        "recorded_at": recorded_at,
        "note": note,
    })
    return out


# --------------------------------------------------------------------------
# stage 1: scope
# --------------------------------------------------------------------------

def effective_answers(session, asset_id):
    """Per question: the latest answer from each source, and whether they agree.

    Consensus rule (assumed, see QUESTIONS.md): every source's latest answer
    must agree. Disagreement is `disputed`, which is not a yes and does not
    scope the asset in. A dispute is surfaced for the facilitator to resolve
    by recording a further answer, not resolved by any tie-break here.
    """
    out = {}
    for q in SCOPE_QUESTIONS:
        latest = {}
        for a in session["answers"]:
            if a["asset_id"] == asset_id and a["question_id"] == q["id"]:
                latest[a["source"]] = a
        values = {a["value"] for a in latest.values()}
        if not values:
            state = "unanswered"
            value = None
        elif len(values) == 1:
            state = "agreed"
            value = values.pop()
        else:
            state = "disputed"
            value = None
        out[q["id"]] = {"value": value, "state": state,
                        "by": {src: a["value"] for src, a in sorted(latest.items())}}
    return out


def scope(session, assets, asset_key="nickname"):
    """Stage 1 outcome over an asset list. Survivors are in the MTSA footprint."""
    survivors, excluded, pending = [], [], []
    for asset in assets:
        aid = asset.get(asset_key)
        eff = effective_answers(session, aid)
        crit, tsi = eff["is_critical"], eff["tsi_possible"]
        row = {"asset_id": aid, "is_critical": crit, "tsi_possible": tsi}
        if crit["state"] != "agreed" or tsi["state"] != "agreed":
            row["outcome"] = "pending"
            row["reason"] = "; ".join("%s %s" % (q, eff[q]["state"])
                                      for q in ("is_critical", "tsi_possible")
                                      if eff[q]["state"] != "agreed")
            pending.append(row)
        elif crit["value"] == "yes" and tsi["value"] == "yes":
            row["outcome"] = "in_scope"
            survivors.append(row)
        else:
            row["outcome"] = "out_of_scope"
            row["reason"] = "is_critical=%s, tsi_possible=%s" % (crit["value"], tsi["value"])
            excluded.append(row)
    return {"session_id": session["id"], "stage": 1,
            "in_scope": survivors, "out_of_scope": excluded, "pending": pending}


# --------------------------------------------------------------------------
# stage 2: rank. Not implemented here by design.
# --------------------------------------------------------------------------

def rank(survivors, session=None):
    """Order survivors by consequence.

    The CFDD weighted scoring instrument lives in SAM and is to be extended
    there, not duplicated here. Until that lands, this refuses rather than
    inventing an ordering that would set KEV tracking priority on a guess.
    """
    raise NotImplementedError(
        "stage 2 ranking uses the CFDD instrument in SAM (build step 04); "
        "no ordering is computed here")


# --------------------------------------------------------------------------
# projection onto the answer set
# --------------------------------------------------------------------------

def enrich(answers, session, device_path="devices", asset_key="nickname"):
    """Return a copy of answers with the stage 1 outcome on every device.

    Adds `criticality` = {scope, is_critical, tsi_possible} per device and a
    `criticality_session` provenance block. Does not touch is_critical_system,
    which SAM supplies; a rule may compare the two.
    """
    result = scope(session, answers.get(device_path, []), asset_key)
    by_id = {}
    for bucket in ("in_scope", "out_of_scope", "pending"):
        for row in result[bucket]:
            by_id[row["asset_id"]] = row
    out = dict(answers)
    devices = []
    for device in answers.get(device_path, []):
        row = by_id.get(device.get(asset_key))
        enriched = dict(device)
        enriched["criticality"] = {
            "scope": row["outcome"],
            "is_critical": row["is_critical"]["value"],
            "tsi_possible": row["tsi_possible"]["value"],
        }
        devices.append(enriched)
    out[device_path] = devices
    out["criticality_session"] = {
        "session_id": session["id"],
        "module_version": MODULE_VERSION,
        "participants": len(session["participants"]),
        "answers_recorded": len(session["answers"]),
    }
    return out
