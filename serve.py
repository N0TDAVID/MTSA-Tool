"""Localhost GUI server. Tier 3. Imports app and nothing else from the project.

    python serve.py              serve on 127.0.0.1:8765, workspace ./workspace
    python serve.py --port 9000
    python serve.py --as-of 2026-09-03
    python serve.py --check      exercise every screen's data path in memory and exit

Binds 127.0.0.1 only. Serves gui/ and a JSON API under /api/. No CDN, no
external font, no outbound request of any kind: the plan is SSI under
49 CFR 1520 and this process must never be the thing that leaks it.
"""

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import app

BIND = "127.0.0.1"
GUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui")
ID = r"[A-Za-z0-9._@+-]+"

SSI_NOTICE = ("SENSITIVE SECURITY INFORMATION. This screen renders plan content that is "
              "SSI under 49 CFR part 1520 once populated for a regulated facility. "
              "It is served to this machine only.")


# --------------------------------------------------------------------------
# routes: (method, pattern, handler). Handlers take (session, groups, query, body).
# --------------------------------------------------------------------------

def r_state(s, g, q, b):
    return {"as_of": s.as_of, "versions": s.versions(), "capabilities": s.capabilities(),
            "tenants": s.tenants(), "facilities": s.facilities(), "plans": s.plans(),
            "spine": s.spine(), "appendices": s.appendices(),
            "shared_cyso": s.shared_cyso_coverage(), "ssi_notice": SSI_NOTICE}


def r_registry(s, g, q, b):
    return s.registry()


def r_authority(s, g, q, b):
    return s.authority_text(g[0])


def r_plan(s, g, q, b):
    p = s.plan(g[0])
    return {k: v for k, v in p.items() if k != "answers"} | {"versions": s.plan_versions(g[0])}


def r_answers(s, g, q, b):
    return s.answers(g[0])


def r_save_answers(s, g, q, b):
    return s.save_answers(g[0], b["answers"])


def r_set_answer(s, g, q, b):
    return s.set_answer(g[0], b["path"], b.get("value"))


def r_questions(s, g, q, b):
    return s.questions_for(g[0])


def r_evaluate(s, g, q, b):
    return s.evaluate(g[0])


def r_crosswalk(s, g, q, b):
    return s.crosswalk(g[0])


def r_registers(s, g, q, b):
    return s.registers(g[0])


def r_freeze(s, g, q, b):
    return s.freeze(g[0], b.get("frozen_by") or "gui")


def r_versions(s, g, q, b):
    return s.plan_versions(g[0])


def r_diff(s, g, q, b):
    return s.amendment_diff(g[0], q["from"][0], q["to"][0])


def r_surveillance(s, g, q, b):
    return s.surveillance(g[0], simulate=q.get("simulate", ["0"])[0] == "1")


def r_criticality(s, g, q, b):
    return s.criticality(g[0])


def r_crit_participant(s, g, q, b):
    return s.criticality_add_participant(g[0], {"id": b["id"], "name": b["name"], "role": b["role"]})


def r_crit_answer(s, g, q, b):
    return s.criticality_answer(g[0], b["asset_id"], b["question_id"], b["value"],
                                b["participant_id"], b.get("on_behalf_of") or None, b.get("note") or None)


def r_render(s, g, q, b):
    return s.render_section(g[0], int(g[1]))


def r_audits(s, g, q, b):
    return s.audit_runs(g[0])


def r_audit_start(s, g, q, b):
    return s.audit_start(g[0], [int(x) for x in b["scope"]], b.get("auditor") or "auditor",
                         b.get("client_representative") or None)


def r_audit(s, g, q, b):
    return s.audit_view(g[0])


def r_audit_accept(s, g, q, b):
    return s.audit_accept(g[0], set(b.get("candidate_ids", [])))


def r_audit_rec(s, g, q, b):
    return s.audit_add_recommendation(g[0], int(b["section"]), b["text"], b.get("authority_ids") or [])


def r_audit_close(s, g, q, b):
    return s.audit_close(g[0])


def r_records(s, g, q, b):
    return s.records_for(g[0])


def r_record_add(s, g, q, b):
    return s.add_record(g[0], b["category"], b["fields"], b.get("custodian"),
                        b.get("accountable_officer"), b.get("created_by") or "gui")


def r_transfer(s, g, q, b):
    return s.transfer_facility(g[0], b["to_tenant_id"], b.get("reason") or "")


def r_license(s, g, q, b):
    return s.license_status()


def r_entitlements(s, g, q, b):
    s.set_capabilities(b.get("capabilities", []))
    return s.license_status()


ROUTES = [
    ("GET", r"/api/state", r_state),
    ("GET", r"/api/registry", r_registry),
    ("GET", r"/api/authority/([A-Za-z0-9._:()+-]+)", r_authority),
    ("GET", r"/api/plan/(%s)" % ID, r_plan),
    ("GET", r"/api/plan/(%s)/answers" % ID, r_answers),
    ("POST", r"/api/plan/(%s)/answers" % ID, r_save_answers),
    ("POST", r"/api/plan/(%s)/answer" % ID, r_set_answer),
    ("GET", r"/api/plan/(%s)/questions" % ID, r_questions),
    ("GET", r"/api/plan/(%s)/evaluate" % ID, r_evaluate),
    ("GET", r"/api/plan/(%s)/crosswalk" % ID, r_crosswalk),
    ("GET", r"/api/plan/(%s)/registers" % ID, r_registers),
    ("POST", r"/api/plan/(%s)/freeze" % ID, r_freeze),
    ("GET", r"/api/plan/(%s)/versions" % ID, r_versions),
    ("GET", r"/api/plan/(%s)/diff" % ID, r_diff),
    ("GET", r"/api/plan/(%s)/surveillance" % ID, r_surveillance),
    ("GET", r"/api/plan/(%s)/criticality" % ID, r_criticality),
    ("POST", r"/api/plan/(%s)/criticality/participant" % ID, r_crit_participant),
    ("POST", r"/api/plan/(%s)/criticality/answer" % ID, r_crit_answer),
    ("GET", r"/api/plan/(%s)/render/(\d+)" % ID, r_render),
    ("GET", r"/api/plan/(%s)/audits" % ID, r_audits),
    ("POST", r"/api/plan/(%s)/audit" % ID, r_audit_start),
    ("GET", r"/api/audit/(%s)" % ID, r_audit),
    ("POST", r"/api/audit/(%s)/accept" % ID, r_audit_accept),
    ("POST", r"/api/audit/(%s)/recommendation" % ID, r_audit_rec),
    ("POST", r"/api/audit/(%s)/close" % ID, r_audit_close),
    ("GET", r"/api/facility/(%s)/records" % ID, r_records),
    ("POST", r"/api/facility/(%s)/record" % ID, r_record_add),
    ("POST", r"/api/facility/(%s)/transfer" % ID, r_transfer),
    ("GET", r"/api/license", r_license),
    ("POST", r"/api/license/entitlements", r_entitlements),
]
COMPILED = [(m, re.compile("^" + p + "$"), h) for m, p, h in ROUTES]

# The server is threaded so idle browser connections cannot stall it, but the
# facade does read-modify-write on the answer set, so requests are serialised.
# Two answers saved in the same instant would otherwise overwrite each other.
_SESSION_LOCK = threading.Lock()


def dispatch(session, method, path, query, body):
    for m, pattern, handler in COMPILED:
        match = pattern.match(path)
        if match and m == method:
            with _SESSION_LOCK:
                return handler(session, match.groups(), query, body)
    raise LookupError("no route for %s %s" % (method, path))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    session = None
    server_version = "csp-gui/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, status, payload):
        data = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-SSI", "49 CFR 1520")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # the browser navigated away mid-response; nothing to recover

    def _api(self, method):
        url = urlparse(self.path)
        body = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        try:
            result = dispatch(self.session, method, url.path, parse_qs(url.query), body)
            self._json(200, result)
        except LookupError as exc:
            self._json(404, {"error": str(exc), "type": "not_found"})
        except NotImplementedError as exc:
            self._json(501, {"error": str(exc), "type": "not_implemented", "placeholder": True})
        except Exception as exc:  # noqa: BLE001 - the GUI shows the error rather than hanging
            traceback.print_exc()
            self._json(400, {"error": str(exc), "type": type(exc).__name__})

    def _static(self):
        url = urlparse(self.path)
        rel = url.path.lstrip("/") or "index.html"
        if rel.startswith("gui/"):
            rel = rel[4:]
        full = os.path.normpath(os.path.join(GUI_DIR, rel))
        if not full.startswith(GUI_DIR) or not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._api("GET")
        else:
            self._static()

    def do_HEAD(self):
        self.send_response(200 if not self.path.startswith("/api/") else 405)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._api("POST")
        else:
            self._json(405, {"error": "method not allowed"})


# --------------------------------------------------------------------------
# self-check: every screen's data path, in memory, no socket
# --------------------------------------------------------------------------

def check(as_of):
    session = app.Session.open(workspace=None, as_of=as_of, memory=True)
    plan = "meg-westport-csp"
    steps = [
        ("GET", "/api/state", {}),
        ("GET", "/api/registry", {}),
        ("GET", "/api/authority/cfr:101.620(b)(3)", {}),
        ("GET", "/api/plan/%s" % plan, {}),
        ("GET", "/api/plan/%s/questions" % plan, {}),
        ("POST", "/api/plan/%s/answer" % plan, {"path": "backup.critical_systems_backup_tested", "value": "not_applicable"}),
        ("GET", "/api/plan/%s/evaluate" % plan, {}),
        ("GET", "/api/plan/%s/crosswalk" % plan, {}),
        ("GET", "/api/plan/%s/registers" % plan, {}),
        ("GET", "/api/plan/%s/criticality" % plan, {}),
        ("POST", "/api/plan/%s/criticality/participant" % plan, {"id": "ops", "name": "Ops lead", "role": "operations"}),
        ("POST", "/api/plan/%s/criticality/answer" % plan,
         {"asset_id": "OCC-HMI-01", "question_id": "is_critical", "value": "yes",
          "participant_id": "facilitator", "on_behalf_of": "ops"}),
        ("POST", "/api/plan/%s/freeze" % plan, {"frozen_by": "check"}),
        ("GET", "/api/plan/%s/versions" % plan, {}),
        ("GET", "/api/plan/%s/surveillance?simulate=1" % plan, {}),
        ("GET", "/api/plan/%s/surveillance" % plan, {}),
        ("GET", "/api/plan/%s/render/1" % plan, {}),
        ("POST", "/api/plan/%s/audit" % plan, {"scope": [1, 5], "auditor": "check"}),
        ("GET", "/api/plan/%s/audits" % plan, {}),
        ("GET", "/api/facility/meg-westport/records", {}),
        ("POST", "/api/facility/meg-westport/record",
         {"category": "exercise", "fields": {"date_held": "2026-05-01", "description": "check",
                                               "participants": ["a"], "lessons_learned": "none"},
          "custodian": "Robert Williams", "accountable_officer": "Jordan Kim"}),
        ("GET", "/api/license", {}),
        ("POST", "/api/license/entitlements", {"capabilities": ["criticality"]}),
        ("GET", "/api/plan/%s/evaluate" % plan, {}),
        ("GET", "/api/plan/%s/crosswalk" % plan, {}),
        ("POST", "/api/license/entitlements", {"capabilities": ["kev", "criticality", "surveillance"]}),
        ("POST", "/api/facility/meg-1-platform/transfer", {"to_tenant_id": "successor-consulting", "reason": "check"}),
    ]
    failures = 0
    for method, path, body in steps:
        url = urlparse(path)
        try:
            result = dispatch(session, method, url.path, parse_qs(url.query), body)
            note = ""
            if method == "GET" and url.path.endswith("/evaluate"):
                kv = next(v for v in result["verdicts"] if v["rule_id"] == "kev-without-delay")
                note = "kev-without-delay=%s export_allowed=%s" % (kv["status"], result["summary"]["export_allowed"])
            elif url.path.endswith("/crosswalk"):
                note = "unavailable" if result.get("unavailable") else "%d rows" % len(result["rows"])
            print("  ok   %-4s %-58s %s" % (method, path, note))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("  FAIL %-4s %-58s %s: %s" % (method, path, type(exc).__name__, exc))
            traceback.print_exc()
    # audit follow-through needs the run id
    run_id = session.audit_runs(plan)[0]["id"]
    for method, path, body in [
        ("GET", "/api/audit/%s" % run_id, {}),
        ("POST", "/api/audit/%s/accept" % run_id, {"candidate_ids": ["%s:cyso-24-7" % run_id]}),
        ("POST", "/api/audit/%s/recommendation" % run_id, {"section": 1, "text": "check"}),
        ("POST", "/api/audit/%s/close" % run_id, {}),
    ]:
        try:
            dispatch(session, method, path, {}, body)
            print("  ok   %-4s %s" % (method, path))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("  FAIL %-4s %s %s: %s" % (method, path, type(exc).__name__, exc))
    print("%d failures" % failures)
    return 1 if failures else 0


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default="workspace")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        return check(args.as_of)

    Handler.session = app.Session.open(workspace=args.workspace, as_of=args.as_of)
    # Threading, because a browser holds speculative idle connections open and a
    # single-threaded server would block on one of them while the page waits.
    httpd = ThreadingHTTPServer((BIND, args.port), Handler)
    httpd.daemon_threads = True
    print("serving on http://127.0.0.1:%d/  workspace=%s  as_of=%s"
          % (args.port, args.workspace, Handler.session.as_of))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
