"""Stand up a checkout: interpreter check, virtual environment, the one
third-party dependency, and a verification pass over every entry point.

    python bootstrap.py                  venv at .venv, install, verify
    python bootstrap.py --offline        no network call of any kind
    python bootstrap.py --no-venv        use the current interpreter
    python bootstrap.py --check          verify only, change nothing
    python bootstrap.py --venv PATH      somewhere other than .venv

The only outbound request this script can make is pip fetching jsonschema, and
only when neither --offline nor --check is passed. Nothing else in the project
reaches the network except `python kev.py refresh`, which is deliberate and
manual. Shipped code has no third-party dependency at all: jsonschema is used
by validate.py alone, and validate.py fails the build if any other module
imports it. An install that cannot reach PyPI is therefore fully functional
apart from the validator, which is why a pip failure here is a warning rather
than an abort.
"""

import argparse
import os
import subprocess
import sys

MIN_PYTHON = (3, 9)
# A partial checkout is a real failure mode and produces confusing errors much
# later, so name the files the entry points cannot start without.
REQUIRED = (
    "engine.py", "app.py", "serve.py", "validate.py",
    "content/csp-navigator.schema.json",
    "content/csp-navigator.content.json",
    "content/csp-ruleset.json",
    "content/csp-questions.json",
    "content/kev-snapshot.json",
    "gui/index.html",
)
# Ordered cheapest first so a broken checkout fails before anything slow runs.
# validate.py is the only one that needs jsonschema; the rest are stdlib only,
# which is what makes an offline install still worth verifying.
CHECKS = (
    ("validate.py", ("validate.py",), True),
    ("demo_engine.py", ("demo_engine.py",), False),
    ("demo_kev.py", ("demo_kev.py",), False),
    ("serve.py --check", ("serve.py", "--check"), False),
)
ROOT = os.path.dirname(os.path.abspath(__file__))


def say(line=""):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def show(path):
    """Repo-relative when the path is inside the checkout, absolute otherwise.
    A venv interpreter reads better as .venv/bin/python; a system one does not
    read at all as ../../../usr/local/bin/python3."""
    rel = os.path.relpath(path, ROOT)
    return rel if not rel.startswith(os.pardir) else path


def venv_python(venv):
    """The interpreter inside a venv, on either platform layout."""
    if os.name == "nt":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def activate_hint(venv):
    if os.name == "nt":
        return "%s\\Scripts\\activate" % show(venv)
    return "source %s/bin/activate" % show(venv)


def run(argv, capture=True):
    """Run a subprocess from the repo root. Returns (returncode, output)."""
    proc = subprocess.Popen(
        argv, cwd=ROOT,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None)
    out, _ = proc.communicate()
    return proc.returncode, (out or b"").decode("utf-8", "replace")


def tail(text, lines=12):
    kept = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join("    " + ln for ln in kept[-lines:])


def check_interpreter():
    if sys.version_info < MIN_PYTHON:
        say("FAIL  Python %d.%d or newer required, running %s"
            % (MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0]))
        return False
    say("ok    Python %s" % sys.version.split()[0])
    return True


def check_checkout():
    missing = [p for p in REQUIRED if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        say("FAIL  incomplete checkout, missing: %s" % ", ".join(missing))
        return False
    say("ok    checkout complete, %d required paths present" % len(REQUIRED))
    return True


def make_venv(venv):
    """Create the venv if it is not already there. Returns its interpreter."""
    target = venv_python(venv)
    if os.path.exists(target):
        say("ok    virtual environment already at %s" % show(venv))
        return target
    say("      creating virtual environment at %s" % show(venv))
    code, out = run([sys.executable, "-m", "venv", venv])
    if code != 0 or not os.path.exists(target):
        say("FAIL  could not create the virtual environment")
        say(tail(out))
        say("      on Debian and Ubuntu this usually means python3-venv is not installed")
        return None
    say("ok    virtual environment created")
    return target


def has_jsonschema(python):
    code, _ = run([python, "-c", "import jsonschema"])
    return code == 0


def install_jsonschema(python, offline):
    """The single dependency, and only validate.py uses it."""
    if has_jsonschema(python):
        say("ok    jsonschema present")
        return True
    if offline:
        say("warn  jsonschema absent and --offline was passed; validate.py will not run")
        say("      from a local wheel: %s -m pip install --no-index --find-links DIR jsonschema"
            % show(python))
        return False
    say("      installing jsonschema (the only outbound request this script makes)")
    code, out = run([python, "-m", "pip", "install", "--disable-pip-version-check", "jsonschema"])
    if code != 0 or not has_jsonschema(python):
        say("warn  jsonschema install failed; validate.py will not run, everything else will")
        say(tail(out))
        say("      offline alternative: %s -m pip install --no-index --find-links DIR jsonschema"
            % show(python))
        return False
    say("ok    jsonschema installed")
    return True


def verify(python, have_jsonschema):
    """Run every entry point. This is the part that proves the checkout works."""
    results = []
    for label, argv, needs_jsonschema in CHECKS:
        if needs_jsonschema and not have_jsonschema:
            say("skip  %s (jsonschema not installed)" % label)
            results.append((label, "skipped"))
            continue
        code, out = run([python] + list(argv))
        if code == 0:
            say("ok    %s" % label)
            results.append((label, "passed"))
        else:
            say("FAIL  %s exited %d" % (label, code))
            say(tail(out))
            results.append((label, "failed"))
    return results


def main(argv):
    parser = argparse.ArgumentParser(
        description="Stand up and verify an MTSA Cybersecurity Plan tool checkout.")
    parser.add_argument("--venv", default=".venv",
                        help="virtual environment path, default .venv")
    parser.add_argument("--no-venv", action="store_true",
                        help="use the current interpreter instead of creating one")
    parser.add_argument("--offline", action="store_true",
                        help="make no network call; skip the jsonschema install")
    parser.add_argument("--check", action="store_true",
                        help="verify only: no venv, no install, no writes")
    args = parser.parse_args(argv)

    say("MTSA Cybersecurity Plan tool: bootstrap")
    say()

    if not check_interpreter() or not check_checkout():
        return 1

    if args.check:
        python = sys.executable
        have = has_jsonschema(python)
        say("ok    jsonschema present" if have else "warn  jsonschema absent")
    elif args.no_venv:
        python = sys.executable
        have = install_jsonschema(python, args.offline)
    else:
        venv = os.path.join(ROOT, args.venv)
        python = make_venv(venv)
        if python is None:
            return 1
        have = install_jsonschema(python, args.offline)

    say()
    results = verify(python, have)
    say()

    failed = [label for label, state in results if state == "failed"]
    skipped = [label for label, state in results if state == "skipped"]
    if failed:
        say("FAILED: %s" % ", ".join(failed))
        return 1

    say("Ready." + (" Skipped: %s." % ", ".join(skipped) if skipped else ""))
    say()
    if not args.check and not args.no_venv:
        say("  %s" % activate_hint(os.path.join(ROOT, args.venv)))
    say("  python serve.py")
    say()
    say("Then open http://127.0.0.1:8765/. The first run creates ./workspace and")
    say("seeds a demo tenant. Delete that directory to start from empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
