"""Asset inventory and scanner ingest: parse, match, propose. Never guess.

Tier 1. Standard library only. Never imports engine.

Getting an asset to a CVE is the hard half and it is not the engine's job. This
module reads what a scanner or a spreadsheet says, matches it to the devices
already in the answer set by exact identifiers, and returns a proposal that a
human confirms before anything is applied. Unmatched hosts stay unmatched.
Unrecognised enum values stay unset and are reported. No fuzzy matching.

Formats:

  inventory CSV     one row per asset; column names are matched case-insensitively
                    against a synonym list. See inventory_template().
  .nessus XML       Nessus v2 export. One ReportHost per scanned host, identified
                    by IP and by NetBIOS name, hostname, and FQDN tags. CVE ids come
                    from <cve> children of each ReportItem.
  scanner CSV       any CSV with a host column and a CVE column, one finding per row.

Everything here is pure. The caller supplies text and devices and receives new
objects; no file is read, no clock, no network.
"""

import csv
import io
import re
import xml.etree.ElementTree as ET

MODULE_VERSION = "2026.09.09"

CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,}$")
CVE_SPLIT = re.compile(r"[,;\s]+")

COMPONENT_TYPES = ("HMI", "PLC", "RTU", "Server", "Workstation", "Switch", "Firewall", "Other")
SYSTEM_FUNCTIONS = ("Automation", "Safety", "Navigation", "Cargo", "Business", "Uncategorized")

# Canonical device field -> accepted column headings, all compared lower-case
# with spaces and dashes folded to underscores.
INVENTORY_COLUMNS = {
    "nickname": ("nickname", "name", "asset", "asset_name", "hostname", "host", "device", "device_name"),
    "ip": ("ip", "ip_address", "ipv4", "address"),
    "component_type": ("component_type", "type", "component", "device_type"),
    "system_function": ("system_function", "function", "role"),
    "is_ot": ("is_ot", "ot", "domain", "zone"),
    "public_facing": ("public_facing", "internet_facing", "internet", "exposed", "public"),
    "is_critical_system": ("is_critical_system", "critical"),
    "operating_system": ("operating_system", "os"),
    "vendor": ("vendor", "manufacturer", "make"),
    "model": ("model",),
}

_COMPONENT_SYNONYMS = {
    "pc": "Workstation", "laptop": "Workstation", "desktop": "Workstation", "ws": "Workstation",
    "srv": "Server", "sw": "Switch", "fw": "Firewall", "router": "Other", "printer": "Other",
}
_TRUE = {"yes", "y", "true", "t", "1", "x", "ot", "public", "critical"}
_FALSE = {"no", "n", "false", "f", "0", "", "it", "private", "internal"}


class IngestError(Exception):
    """Unparseable input. Never caught internally."""


# --------------------------------------------------------------------------
# inventory CSV
# --------------------------------------------------------------------------

def inventory_template():
    """A CSV a client can fill in. The two rows are examples, not data."""
    return (
        "nickname,ip,component_type,system_function,is_ot,public_facing,operating_system,vendor,model\n"
        "OCC-HMI-01,10.50.1.21,HMI,Automation,yes,no,Windows 10 LTSC,Example Vendor,Panel 900\n"
        "CORP-FS-01,10.50.0.5,Server,Business,no,no,Windows Server 2019,Example Vendor,R740\n"
    )


def _fold(name):
    return re.sub(r"[\s\-]+", "_", (name or "").strip().lower())


def _bool(value, field, row_no, problems):
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False if v else None
    problems.append("row %d: %s value %r not understood; left unset" % (row_no, field, value))
    return None


def _enum(value, options, synonyms, field, row_no, problems):
    v = (value or "").strip()
    if not v:
        return None
    for opt in options:
        if v.lower() == opt.lower():
            return opt
    if v.lower() in synonyms:
        return synonyms[v.lower()]
    problems.append("row %d: %s %r is not one of %s; left unset"
                    % (row_no, field, v, "/".join(options)))
    return None


def parse_inventory_csv(text):
    """Parse an inventory CSV into device dicts plus a problem list.

    A row without a nickname is skipped and reported. Unknown columns are
    ignored and reported once. Nothing is invented for a blank cell.
    """
    if not isinstance(text, str):
        raise IngestError("inventory must be text")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise IngestError("inventory CSV is empty")
    mapping, ignored = {}, []
    for i, raw in enumerate(header):
        key = _fold(raw)
        hit = next((canon for canon, names in INVENTORY_COLUMNS.items() if key in names), None)
        if hit and hit not in mapping:
            mapping[hit] = i
        else:
            ignored.append(raw)
    if "nickname" not in mapping:
        raise IngestError("no asset name column found; expected one of %s"
                          % ", ".join(INVENTORY_COLUMNS["nickname"]))

    problems = ["ignored columns: %s" % ", ".join(ignored)] if ignored else []
    devices, seen = [], set()
    for row_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(field):
            idx = mapping.get(field)
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        nickname = cell("nickname")
        if not nickname:
            problems.append("row %d: no asset name; skipped" % row_no)
            continue
        if nickname.lower() in seen:
            problems.append("row %d: duplicate asset name %r; skipped" % (row_no, nickname))
            continue
        seen.add(nickname.lower())
        device = {"nickname": nickname}
        if cell("ip"):
            device["ip"] = cell("ip")
        ct = _enum(cell("component_type"), COMPONENT_TYPES, _COMPONENT_SYNONYMS, "component_type", row_no, problems)
        if ct:
            device["component_type"] = ct
        sf = _enum(cell("system_function"), SYSTEM_FUNCTIONS, {}, "system_function", row_no, problems)
        if sf:
            device["system_function"] = sf
        for field in ("is_ot", "public_facing", "is_critical_system"):
            if mapping.get(field) is not None:
                b = _bool(cell(field), field, row_no, problems)
                if b is not None:
                    device[field] = b
        for field in ("operating_system", "vendor", "model"):
            if cell(field):
                device[field] = cell(field)
        devices.append(device)
    return {"devices": devices, "problems": problems,
            "columns": {k: header[v] for k, v in mapping.items()}, "rows": len(devices)}


def apply_inventory(answers, devices, device_path="devices"):
    """Merge parsed devices into the answer set by nickname. Returns (answers, report).

    An existing device keeps every field the import does not mention; an
    imported field overwrites. New devices are appended. Nothing is deleted.
    """
    out = dict(answers)
    existing = [dict(d) for d in answers.get(device_path, [])]
    index = {d.get("nickname", "").lower(): d for d in existing}
    added, updated = [], []
    for dev in devices:
        key = dev["nickname"].lower()
        if key in index:
            index[key].update({k: v for k, v in dev.items() if k != "nickname"})
            updated.append(dev["nickname"])
        else:
            existing.append(dict(dev))
            index[key] = existing[-1]
            added.append(dev["nickname"])
    out[device_path] = existing
    return out, {"added": added, "updated": updated, "total": len(existing)}


# --------------------------------------------------------------------------
# scanner output
# --------------------------------------------------------------------------

def parse_nessus(text):
    """Hosts and their CVE ids from a Nessus v2 export."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise IngestError("not a Nessus XML export (%s)" % exc)
    if root.tag != "NessusClientData_v2":
        raise IngestError("not a Nessus v2 export; root element is %r" % root.tag)
    hosts = []
    for rh in root.iter("ReportHost"):
        tags = {t.get("name"): (t.text or "").strip() for t in rh.findall("./HostProperties/tag")}
        cves = set()
        items = rh.findall("./ReportItem")
        for item in items:
            for c in item.findall("cve"):
                cve = (c.text or "").strip().upper()
                if CVE_ID.match(cve):
                    cves.add(cve)
        names = [tags.get(k) for k in ("netbios-name", "hostname", "host-fqdn", "host-rdns") if tags.get(k)]
        hosts.append({
            "host": rh.get("name"),
            "ip": tags.get("host-ip") or rh.get("name"),
            "names": sorted(set(names)),
            "operating_system": tags.get("operating-system"),
            "scan_start": tags.get("HOST_START"),
            "items": len(items),
            "cves": sorted(cves),
        })
    if not hosts:
        raise IngestError("Nessus export carries no ReportHost")
    return {"format": "nessus", "hosts": hosts}


def parse_scanner_csv(text):
    """Hosts and CVE ids from a generic scanner CSV: one row per finding."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [_fold(h) for h in next(reader)]
    except StopIteration:
        raise IngestError("scanner CSV is empty")
    host_col = next((i for i, h in enumerate(header)
                     if h in ("host", "ip", "ip_address", "asset", "hostname", "dns_name", "netbios_name")), None)
    cve_col = next((i for i, h in enumerate(header) if h in ("cve", "cve_id", "cves", "cve_ids")), None)
    if host_col is None or cve_col is None:
        raise IngestError("scanner CSV needs a host column and a CVE column; saw %s" % header)
    by_host = {}
    for row in reader:
        if len(row) <= max(host_col, cve_col):
            continue
        host = row[host_col].strip()
        if not host:
            continue
        entry = by_host.setdefault(host, {"host": host, "ip": host, "names": [host],
                                          "operating_system": None, "scan_start": None,
                                          "items": 0, "cves": set()})
        entry["items"] += 1
        for cve in CVE_SPLIT.split(row[cve_col].strip().upper()):
            if CVE_ID.match(cve):
                entry["cves"].add(cve)
    hosts = []
    for h in by_host.values():
        h["cves"] = sorted(h["cves"])
        hosts.append(h)
    if not hosts:
        raise IngestError("scanner CSV carries no host rows")
    return {"format": "scanner_csv", "hosts": hosts}


def parse_scan(text, filename=""):
    """Dispatch on content, then on filename."""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return parse_nessus(text)
    if filename.lower().endswith(".nessus"):
        return parse_nessus(text)
    return parse_scanner_csv(text)


def match_hosts(hosts, devices):
    """Exact matching only: IP equals device ip, or a scanner name equals a nickname.

    Returns matched and unmatched lists. A host matching more than one device
    is reported as ambiguous and left unmatched.
    """
    by_ip, by_name = {}, {}
    for d in devices:
        if d.get("ip"):
            by_ip.setdefault(str(d["ip"]).strip(), []).append(d.get("nickname"))
        if d.get("nickname"):
            by_name.setdefault(str(d["nickname"]).strip().lower(), []).append(d.get("nickname"))
    matched, unmatched = [], []
    for h in hosts:
        candidates, how = [], None
        if h.get("ip") and h["ip"] in by_ip:
            candidates, how = list(by_ip[h["ip"]]), "ip"
        else:
            for n in list(h.get("names", [])) + [h.get("host")]:
                if n and n.lower() in by_name:
                    candidates, how = list(by_name[n.lower()]), "name"
                    break
        summary = {"host": h["host"], "ip": h.get("ip"), "names": h.get("names", []),
                   "operating_system": h.get("operating_system"), "items": h.get("items"),
                   "cves": h["cves"], "cve_count": len(h["cves"])}
        if len(candidates) == 1:
            matched.append(dict(summary, device=candidates[0], match_by=how))
        elif len(candidates) > 1:
            unmatched.append(dict(summary, reason="ambiguous: matches %s" % ", ".join(candidates)))
        else:
            unmatched.append(dict(summary, reason="no device with this IP or name"))
    return {"matched": matched, "unmatched": unmatched}


def apply_scan(answers, assignments, source, device_path="devices"):
    """Union confirmed CVE observations onto devices. Returns (answers, report).

    `assignments` is the human-confirmed list: [{device, cves, host}]. A CVE that
    does not match the identifier format is dropped and counted, never coerced.
    """
    out = dict(answers)
    devices = [dict(d) for d in answers.get(device_path, [])]
    index = {d.get("nickname", "").lower(): d for d in devices}
    applied, dropped, unknown = [], 0, []
    for a in assignments:
        dev = index.get(str(a.get("device", "")).lower())
        if dev is None:
            unknown.append(a.get("device"))
            continue
        seen = {c.upper() for c in dev.get("observed_cves", dev.get("open_kevs", [])) or []}
        before = len(seen)
        for cve in a.get("cves", []):
            cve = str(cve).strip().upper()
            if CVE_ID.match(cve):
                seen.add(cve)
            else:
                dropped += 1
        dev["observed_cves"] = sorted(seen)
        dev.setdefault("scan_sources", [])
        dev["scan_sources"] = sorted(set(dev["scan_sources"]) | {source})
        applied.append({"device": dev["nickname"], "host": a.get("host"),
                        "cves_before": before, "cves_after": len(seen)})
    out[device_path] = devices
    log = list(answers.get("scan_imports", []))
    log.append({"source": source, "devices": [a["device"] for a in applied], "dropped_ids": dropped})
    out["scan_imports"] = log
    return out, {"applied": applied, "dropped_ids": dropped, "unknown_devices": unknown}
