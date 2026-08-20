#!/usr/bin/env python3
import argparse
import json
import sys

SCHEMA = "router-step-facts-v1"

def evaluate(facts):
    errors = []
    warnings = []

    if facts.get("schema") != SCHEMA:
        errors.append("schema must be %s" % SCHEMA)

    a = facts.get("assessment", {})
    if not isinstance(a, dict):
        errors.append("assessment must be object")
        a = {}

    success = a.get("success_checks", {})
    absence = a.get("expected_absence", {})
    blockers = a.get("blockers", [])
    raw_warnings = a.get("warnings", [])

    if not isinstance(success, dict):
        errors.append("success_checks must be object")
        success = {}
    if not isinstance(absence, dict):
        errors.append("expected_absence must be object")
        absence = {}
    if blockers is None:
        blockers = []
    if not isinstance(blockers, list):
        blockers = [str(blockers)]
    if raw_warnings is None:
        raw_warnings = []
    if not isinstance(raw_warnings, list):
        raw_warnings = [str(raw_warnings)]

    for k, v in success.items():
        if not isinstance(v, bool):
            errors.append("success_checks.%s must be boolean" % k)
    for k, v in absence.items():
        if not isinstance(v, bool):
            errors.append("expected_absence.%s must be boolean" % k)

    blockers = [str(x) for x in blockers if str(x).strip()]
    warnings = [str(x) for x in raw_warnings if str(x).strip()]

    success_ok = bool(success) and all(v is True for v in success.values())
    expected_absence_ok = all(v is False for v in absence.values())
    blockers_ok = len(blockers) == 0
    structure_ok = len(errors) == 0
    all_ok = structure_ok and success_ok and expected_absence_ok and blockers_ok

    return {
        "schema": SCHEMA,
        "step": facts.get("step", ""),
        "structure_ok": structure_ok,
        "success_ok": success_ok,
        "expected_absence_ok": expected_absence_ok,
        "blockers_ok": blockers_ok,
        "all_ok": all_ok,
        "decision": "PASS" if all_ok else "STOP",
        "errors": errors,
        "warnings": warnings,
        "blockers": blockers,
        "false_success_checks": sorted([k for k, v in success.items() if v is False]),
        "true_expected_absence": sorted([k for k, v in absence.items() if v is True]),
        "success_checks_count": len(success),
        "expected_absence_count": len(absence),
    }

def render(r):
    lines = [
        "=== ROUTER STEP FACTS CONTRACT VALIDATION ===",
        "step=%s" % r.get("step", ""),
        "decision=%s" % r.get("decision", ""),
        "all_ok=%s" % r.get("all_ok"),
        "structure_ok=%s" % r.get("structure_ok"),
        "success_ok=%s" % r.get("success_ok"),
        "expected_absence_ok=%s" % r.get("expected_absence_ok"),
        "blockers_ok=%s" % r.get("blockers_ok"),
        "success_checks_count=%s" % r.get("success_checks_count"),
        "expected_absence_count=%s" % r.get("expected_absence_count"),
        "",
    ]
    for title in ["errors", "blockers", "false_success_checks", "true_expected_absence", "warnings"]:
        vals = r.get(title) or []
        if vals:
            lines.append(title + ":")
            for v in vals:
                lines.append("  - " + str(v))
            lines.append("")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("facts_json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        with open(args.facts_json, "r", encoding="utf-8", errors="replace") as f:
            facts = json.load(f)
        result = evaluate(facts)
    except Exception as e:
        result = {
            "step": "",
            "all_ok": False,
            "decision": "STOP",
            "errors": [type(e).__name__ + ": " + str(e)],
            "warnings": [],
            "blockers": [],
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(result))

    return 0 if result.get("all_ok") else 2

if __name__ == "__main__":
    sys.exit(main())
