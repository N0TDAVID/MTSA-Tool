"""The answer store, behind an interface the engine never reaches around.

Tier 1. Standard library only. Never imports engine, never imports another
project module.

This is the seam that lets custody move. Under the default posture the customer
holds their answer set on their own workstation; under the custody tier we hold
it. Both are the same code: the engine receives a plain answer set as an
argument and never knows where it came from. The store is the only thing that
changes between the two deployments, and it changes by swapping the
implementation behind AnswerStore, not by forking anything above it.

Collections are fixed and named here so that a misspelled collection is a loud
error rather than a silently empty directory. plan_versions is append-only: a
frozen version can be read and never rewritten, which is what makes an amendment
diff and a surveillance diff attributable.

Nothing in this module reads the clock. Timestamps are supplied by the caller.
"""

import copy
import json
import os
import tempfile

COLLECTIONS = (
    "tenants",          # the consulting org. Tenant is the org, not the facility.
    "clients",          # the owner or operator, the responsible party under 101.620(a);
                        # a child of the tenant and the parent of its facilities
    "facilities",       # child entity of a tenant; client_id names its owner or operator
    "plans",            # a Cybersecurity Plan and its live, mutable answer set
    "plan_versions",    # frozen answer sets, append-only
    "records",          # 101.640 record entries with their retention clocks
    "sessions",         # criticality workshop sessions
    "audit_runs",       # section-scoped audit runs
)

APPEND_ONLY = ("plan_versions",)


class StoreError(Exception):
    """Unknown collection, missing document, or an attempt to rewrite frozen data."""


class AnswerStore:
    """Abstract document store. Subclasses implement the four primitives.

    Everything else on this class is written in terms of them, so a new backend
    (SQLite, a cloud table, an encrypted archive) implements four methods and
    inherits the rest.
    """

    def _read(self, collection, doc_id):
        raise NotImplementedError

    def _write(self, collection, doc_id, doc):
        raise NotImplementedError

    def _remove(self, collection, doc_id):
        raise NotImplementedError

    def _ids(self, collection):
        raise NotImplementedError

    # ---- public primitives ----------------------------------------------

    def get(self, collection, doc_id):
        _check_collection(collection)
        doc = self._read(collection, doc_id)
        if doc is None:
            raise StoreError("no %s with id %r" % (collection, doc_id))
        return copy.deepcopy(doc)

    def exists(self, collection, doc_id):
        _check_collection(collection)
        return self._read(collection, doc_id) is not None

    def put(self, collection, doc_id, doc):
        _check_collection(collection)
        if not isinstance(doc, dict):
            raise StoreError("a document must be an object, got %s" % type(doc).__name__)
        if collection in APPEND_ONLY and self._read(collection, doc_id) is not None:
            raise StoreError("%s is append-only; %r already exists and cannot be "
                             "rewritten" % (collection, doc_id))
        stored = copy.deepcopy(doc)
        stored["id"] = doc_id
        self._write(collection, doc_id, stored)
        return stored

    def delete(self, collection, doc_id):
        _check_collection(collection)
        if collection in APPEND_ONLY:
            raise StoreError("%s is append-only; nothing in it may be deleted" % collection)
        if self._read(collection, doc_id) is None:
            raise StoreError("no %s with id %r" % (collection, doc_id))
        self._remove(collection, doc_id)

    def ids(self, collection):
        _check_collection(collection)
        return sorted(self._ids(collection))

    def all(self, collection):
        return [self.get(collection, i) for i in self.ids(collection)]

    def where(self, collection, **equals):
        return [d for d in self.all(collection)
                if all(d.get(k) == v for k, v in equals.items())]

    # ---- answer-set helpers, the only thing the facade calls for evaluation ---

    def answers(self, plan_id):
        """The live answer set for a plan. A copy: mutating it changes nothing."""
        return self.get("plans", plan_id).get("answers", {})

    def save_answers(self, plan_id, answers):
        plan = self.get("plans", plan_id)
        plan["answers"] = copy.deepcopy(answers)
        self.put("plans", plan_id, plan)
        return plan

    # ---- tenancy ---------------------------------------------------------

    def transfer_facility(self, facility_id, to_tenant_id, effective_date, reason):
        """Move a facility to another tenant. Clients change consultants.

        The owner or operator is the responsible party, not the consultant, so
        the facility and its plans travel together. A plan that also covers a
        facility staying behind is a multi-facility plan under 101.630(d)(2)
        and is not moved; it is reported instead so a human can split it.
        """
        facility = self.get("facilities", facility_id)
        if not self.exists("tenants", to_tenant_id):
            raise StoreError("no tenant %r to transfer to" % to_tenant_id)
        from_tenant = facility["tenant_id"]
        if from_tenant == to_tenant_id:
            raise StoreError("facility %r already belongs to %r" % (facility_id, to_tenant_id))

        moved, held = [], []
        for plan in self.where("plans", tenant_id=from_tenant):
            if facility_id not in plan.get("facility_ids", []):
                continue
            others = [f for f in plan["facility_ids"] if f != facility_id]
            if others:
                held.append({"plan_id": plan["id"], "also_covers": others})
                continue
            plan["tenant_id"] = to_tenant_id
            self.put("plans", plan["id"], plan)
            moved.append(plan["id"])

        # The client travels with the facility, so no facility ever points at a
        # client under another tenant. If the destination already holds a
        # client of the same name, the facility joins it. Otherwise, if every
        # facility the client owns is leaving, the client itself changed
        # consultants and moves; if some stay, it is cloned under the
        # destination and the origin keeps its own record of the same owner.
        client_note = None
        client_id = facility.get("client_id")
        if client_id and self.exists("clients", client_id):
            client = self.get("clients", client_id)
            staying = [f["id"] for f in self.where("facilities", client_id=client_id)
                       if f["id"] != facility_id]
            twin = next((c for c in self.where("clients", tenant_id=to_tenant_id)
                         if c["name"] == client["name"]), None)
            if twin:
                facility["client_id"] = twin["id"]
                client_note = {"client_id": twin["id"], "action": "joined"}
                if not staying:
                    # nothing references the origin record any more; it was a
                    # duplicate of the destination one by construction
                    self.delete("clients", client_id)
                    client_note["origin_removed"] = client_id
            elif staying:
                clone_id = unique_id(self, "clients", client_id)
                client["tenant_id"] = to_tenant_id
                client["cloned_from"] = client_id
                self.put("clients", clone_id, client)
                facility["client_id"] = clone_id
                client_note = {"client_id": clone_id, "action": "cloned",
                               "origin_keeps": client_id, "origin_facilities": staying}
            else:
                client["tenant_id"] = to_tenant_id
                self.put("clients", client_id, client)
                client_note = {"client_id": client_id, "action": "moved"}

        facility["tenant_id"] = to_tenant_id
        history = facility.setdefault("transfer_history", [])
        history.append({"from": from_tenant, "to": to_tenant_id,
                        "effective_date": effective_date, "reason": reason})
        self.put("facilities", facility_id, facility)
        return {"facility_id": facility_id, "from": from_tenant, "to": to_tenant_id,
                "plans_moved": moved, "plans_held_multi_facility": held, "client": client_note}


def slug(text):
    """A document id from a display name: lowercase, hyphenated, ASCII only."""
    out = "".join(c if c.isalnum() else "-" for c in str(text).lower().encode("ascii", "ignore").decode())
    out = "-".join(part for part in out.split("-") if part)
    if not out:
        raise StoreError("cannot derive an id from %r" % (text,))
    return out


def unique_id(answer_store, collection, base):
    """`base`, or `base-2`, `base-3`, ... until one is free in the collection."""
    candidate, n = base, 1
    while answer_store.exists(collection, candidate):
        n += 1
        candidate = "%s-%d" % (base, n)
    return candidate


def _check_collection(collection):
    if collection not in COLLECTIONS:
        raise StoreError("unknown collection %r; known: %s" % (collection, ", ".join(COLLECTIONS)))


# --------------------------------------------------------------------------
# implementations
# --------------------------------------------------------------------------

class InMemoryStore(AnswerStore):
    """Volatile. For tests and for a workshop laptop that must leave nothing behind."""

    def __init__(self):
        self._data = {c: {} for c in COLLECTIONS}

    def _read(self, collection, doc_id):
        return self._data[collection].get(doc_id)

    def _write(self, collection, doc_id, doc):
        self._data[collection][doc_id] = doc

    def _remove(self, collection, doc_id):
        del self._data[collection][doc_id]

    def _ids(self, collection):
        return list(self._data[collection])


class JsonFileStore(AnswerStore):
    """One JSON file per document under <root>/<collection>/<id>.json.

    Writes are atomic (temp file then os.replace), so a crash mid-write leaves
    the previous document intact rather than a truncated one. This is the
    default-posture backend: the directory lives on the customer's machine and
    nothing here ever transmits it.
    """

    def __init__(self, root):
        self.root = root
        for c in COLLECTIONS:
            os.makedirs(os.path.join(root, c), exist_ok=True)

    def _path(self, collection, doc_id):
        if not doc_id or "/" in doc_id or "\\" in doc_id or doc_id in (".", ".."):
            raise StoreError("bad document id %r" % (doc_id,))
        return os.path.join(self.root, collection, doc_id + ".json")

    def _read(self, collection, doc_id):
        path = self._path(collection, doc_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, collection, doc_id, doc):
        path = self._path(collection, doc_id)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _remove(self, collection, doc_id):
        os.remove(self._path(collection, doc_id))

    def _ids(self, collection):
        folder = os.path.join(self.root, collection)
        return [f[:-5] for f in os.listdir(folder) if f.endswith(".json")]
