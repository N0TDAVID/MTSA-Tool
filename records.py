"""101.640 record categories and their retention clocks.

Tier 1. Standard library only. Never imports engine.

101.640 names six activities that records must be created for at a minimum:
training, drills, exercises, cybersecurity threats, reportable cyber incidents,
and audits of the Cybersecurity Plan. It sets no retention period itself. It
defers creation and maintenance to 33 CFR 104.235 for U.S.-flagged vessels,
105.225 for facilities, and 106.230 for OCS facilities. Those three are in
reference/ verbatim, and every field list and retention figure below was
transcribed from that file, not from memory.

Retention is a property of the category, never a constant. Each of the three
sections opens "unless otherwise specified in this section", and the Declaration
of Security is specified otherwise at 90 days. The DoS is not one of the six
101.640 categories, so it does not appear here, but the shape is per-category so
that it could, and so that nothing ever reads a hardcoded 2.

Two roles touch a record and they are different fields. The VSO or FSO keeps it
(104.235(a), 105.225(a), 106.230(a)). The CySO ensures it is maintained
(101.625(d), item 11). Custodian and accountable officer are not the same person
and the model does not let them collapse.

Citations. authority_ids name entries in the content registry and are what the
UI renders. source_paragraph is the recordkeeping paragraph the field list was
transcribed from; those paragraphs are not registry entries yet (see
QUESTIONS.md) so the UI shows them as verified-against-reference text, not as a
registry citation.
"""

import copy
from datetime import date, datetime

ASSET_TYPES = ("vessel", "facility", "ocs_facility")

# Retention read from 104.235(a) / 105.225(a) / 106.230(a): "for at least 2 years".
_TWO_YEARS = {"years": 2, "basis": "for at least 2 years"}

CATEGORIES = {
    "training": {
        "label": "Training",
        "section": 2,
        "clock_field": "date",
        "fields": ["date", "duration", "description", "attendees"],
        "authority_ids": ["cfr:101.640"],
        "source_paragraph": {"vessel": "104.235(b)(1)", "facility": "105.225(b)(1)",
                             "ocs_facility": "106.230(b)(1)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
    "drill": {
        "label": "Drill",
        "section": 3,
        "clock_field": "date_held",
        "fields": ["date_held", "description", "participants", "lessons_learned"],
        "authority_ids": ["cfr:101.640", "cfr:101.635"],
        "source_paragraph": {"vessel": "104.235(b)(2)", "facility": "105.225(b)(2)",
                             "ocs_facility": "106.230(b)(2)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
    "exercise": {
        # 101.640 splits drills from exercises; the recordkeeping sections carry
        # them as one paragraph. Two categories here, one source paragraph.
        "label": "Exercise",
        "section": 3,
        "clock_field": "date_held",
        "fields": ["date_held", "description", "participants", "lessons_learned"],
        "authority_ids": ["cfr:101.640", "cfr:101.635"],
        "source_paragraph": {"vessel": "104.235(b)(2)", "facility": "105.225(b)(2)",
                             "ocs_facility": "106.230(b)(2)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
    "cybersecurity_threat": {
        "label": "Cybersecurity threat",
        "section": 4,
        "clock_field": "date_time",
        "fields": ["date_time", "how_communicated", "received_by", "description",
                   "reported_to", "response"],
        "authority_ids": ["cfr:101.640"],
        "source_paragraph": {"vessel": "104.235(b)(6)", "facility": "105.225(b)(6)",
                             "ocs_facility": "106.230(b)(6)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
    "reportable_cyber_incident": {
        "label": "Reportable cyber incident",
        "section": 4,
        "clock_field": "date_time",
        "fields": ["date_time", "location", "description", "reported_to", "response"],
        "authority_ids": ["cfr:101.640", "cfr:101.620(b)(7)"],
        "source_paragraph": {"vessel": "104.235(b)(3)", "facility": "105.225(b)(3)",
                             "ocs_facility": "106.230(b)(3)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
    "plan_audit": {
        "label": "Audit of the Cybersecurity Plan",
        "section": 4,
        "clock_field": "completion_date",
        "fields": ["completion_date", "certified_by", "certification_letter_ref"],
        "authority_ids": ["cfr:101.640", "cfr:101.630(f)"],
        "source_paragraph": {"vessel": "104.235(b)(8)", "facility": "105.225(b)(8)",
                             "ocs_facility": "106.230(b)(8)"},
        "retention": _TWO_YEARS,
        "applies_to": ASSET_TYPES,
    },
}

# Who keeps the record, per 104.235(a) / 105.225(a) / 106.230(a).
CUSTODIAN_ROLE = {"vessel": "VSO", "facility": "FSO", "ocs_facility": "FSO"}
# Who ensures it is maintained, 101.625(d) item 11.
ACCOUNTABLE_ROLE = "CySO"
# The registry entry behind the recordkeeping section for each asset type.
# Only the facility section is in the registry today; see QUESTIONS.md.
RECORDKEEPING_AUTHORITY = {"vessel": None, "facility": "cfr:105.225", "ocs_facility": None}


class RecordError(Exception):
    """Unknown category, wrong asset type, or a record missing a required field."""


def categories_for(asset_type):
    if asset_type not in ASSET_TYPES:
        raise RecordError("unknown asset type %r" % (asset_type,))
    return {k: v for k, v in CATEGORIES.items() if asset_type in v["applies_to"]}


def new_record(record_id, category, asset_type, facility_id, fields, custodian,
               accountable_officer, created_by):
    """Build a record. Validates shape; never invents a value."""
    if category not in CATEGORIES:
        raise RecordError("unknown record category %r" % (category,))
    spec = CATEGORIES[category]
    if asset_type not in spec["applies_to"]:
        raise RecordError("category %r does not apply to %r" % (category, asset_type))
    missing = [f for f in spec["fields"] if f not in fields or fields[f] in (None, "", [])]
    if missing:
        raise RecordError("record %r (%s) is missing required fields: %s"
                          % (record_id, category, ", ".join(missing)))
    if _as_date(fields[spec["clock_field"]]) is None:
        raise RecordError("%s must be an ISO date, got %r"
                          % (spec["clock_field"], fields[spec["clock_field"]]))
    if not custodian or not accountable_officer:
        raise RecordError("custodian and accountable officer are both required and distinct fields")
    return {
        "id": record_id,
        "category": category,
        "asset_type": asset_type,
        "facility_id": facility_id,
        "fields": copy.deepcopy({k: fields[k] for k in spec["fields"]}),
        "custodian": {"role": CUSTODIAN_ROLE[asset_type], "name": custodian},
        "accountable_officer": {"role": ACCOUNTABLE_ROLE, "name": accountable_officer},
        "created_by": created_by,
        "authority_ids": list(spec["authority_ids"]),
        "source_paragraph": spec["source_paragraph"][asset_type],
    }


def retention_until(record):
    """The date this record may first be disposed of. Read from its category."""
    spec = CATEGORIES[record["category"]]
    start = _as_date(record["fields"][spec["clock_field"]])
    years = spec["retention"]["years"]
    return _add_years(start, years)


def status(record, as_of):
    """retain | eligible_for_disposal, against an explicit as_of. No clock."""
    when = _as_date(as_of)
    if when is None:
        raise RecordError("as_of must be an ISO date, got %r" % (as_of,))
    until = retention_until(record)
    return {
        "record_id": record["id"],
        "category": record["category"],
        "retain_until": until.isoformat(),
        "days_remaining": (until - when).days,
        "status": "retain" if when < until else "eligible_for_disposal",
        "basis": CATEGORIES[record["category"]]["retention"]["basis"],
        "source_paragraph": record["source_paragraph"],
    }


def cadence(records, as_of):
    """Drill and exercise cadence against 101.635, over an explicit as_of.

    Drills at least twice each calendar year; exercises at least once each
    calendar year with no more than 18 months between them. Reports counts and
    the gap; does not decide compliance, which is the ruleset's job.
    """
    when = _as_date(as_of)
    year = when.year
    drills = sorted(_as_date(r["fields"]["date_held"]) for r in records if r["category"] == "drill")
    exercises = sorted(_as_date(r["fields"]["date_held"]) for r in records if r["category"] == "exercise")
    last_ex = exercises[-1] if exercises else None
    return {
        "as_of": when.isoformat(),
        "calendar_year": year,
        "drills_this_year": sum(1 for d in drills if d.year == year),
        "drills_required_per_year": 2,
        "exercises_this_year": sum(1 for d in exercises if d.year == year),
        "exercises_required_per_year": 1,
        "months_since_last_exercise": _months_between(last_ex, when) if last_ex else None,
        "max_months_between_exercises": 18,
        "authority_ids": ["cfr:101.635"],
    }


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _add_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def _months_between(earlier, later):
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)
