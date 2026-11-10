#!/usr/bin/env python3
"""Structural checks and transparent rubric arithmetic; Python 3, stdlib only."""
import argparse
import json
import math
from pathlib import Path
import sys

WEIGHTS = {"R1": 20, "R2": 20, "R3": 15, "R4": 15, "R5": 10, "R6": 15, "R7": 5}
CRITICAL = {"R1", "R2", "R3", "R6"}


class DataError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DataError(message)


def obj(value, label):
    require(isinstance(value, dict), f"{label}: expected object")
    return value


def seq(value, label):
    require(isinstance(value, list), f"{label}: expected array")
    return value


def string(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}: nonempty string required")
    return value


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, f"{label}: integer >= {minimum} required")
    return value


def version(data):
    obj(data, "input")
    require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
            "schema_version must be 1")


def index_rows(data, key):
    rows = seq(data.get(key), key)
    indexed = {}
    for row in rows:
        obj(row, key)
        ident = string(row.get("id"), f"{key}.id")
        require(ident not in indexed, f"{key}: duplicate id {ident}")
        indexed[ident] = row
    return indexed


def ids(value, label, available):
    seq(value, label)
    require(all(isinstance(v, str) and v.strip() for v in value), f"{label}: string IDs required")
    require(len(value) == len(set(value)), f"{label}: duplicate link")
    unknown = set(value) - set(available)
    require(not unknown, f"{label}: unknown IDs {sorted(unknown)}")
    return value


def check_plan(data):
    version(data)
    require(type(data.get("reference_complete")) is bool, "reference_complete must be boolean")
    sources = index_rows(data, "sources")
    outcomes = index_rows(data, "outcomes")
    units = index_rows(data, "units")
    activities = index_rows(data, "activities")
    assessments = index_rows(data, "assessments")
    warnings = []

    def warn(code, ident, message):
        warnings.append({"code": code, "id": ident, "message": message})

    for sid, source in sources.items():
        string(source.get("title"), f"{sid}.title")
        string(source.get("locator"), f"{sid}.locator")
        require(isinstance(source.get("status"), str) and source["status"] in {"read_full", "read_partial", "indexed_only", "unavailable"},
                f"{sid}.status: unsupported source status")
    for oid, outcome in outcomes.items():
        string(outcome.get("text"), f"{oid}.text")
        require(type(outcome.get("required")) is bool, f"{oid}.required must be boolean")
        provenance = outcome.get("provenance")
        require(isinstance(provenance, str) and provenance in {"verified", "proposed", "unverified"}, f"{oid}.provenance invalid")
        sid = outcome.get("source_id")
        if sid is not None:
            require(isinstance(sid, str) and sid in sources, f"{oid}: unknown source_id")
        if provenance == "verified":
            require(sid in sources, f"{oid}: verified outcome needs a source")
            require(sources[sid]["status"] in {"read_full", "read_partial"},
                    f"{oid}: verified outcome needs a read source")
            string(outcome.get("locator"), f"{oid}.locator")
    if not data["reference_complete"]:
        warn("reference_incomplete", None, "لا يمكن إثبات اكتمال تغطية المنهاج من هذه البيانات.")

    for uid, unit in units.items():
        integer(unit.get("periods"), f"{uid}.periods")
        ids(unit.get("outcome_ids"), f"{uid}.outcome_ids", outcomes)
    for label, rows in [("activities", activities), ("assessments", assessments)]:
        for rid, row in rows.items():
            string(row.get("title"), f"{rid}.title")
            uid = row.get("unit_id")
            require(isinstance(uid, str) and uid in units, f"{rid}: unknown unit_id")
            linked = ids(row.get("outcome_ids"), f"{rid}.outcome_ids", outcomes)
            require(set(linked) <= set(units[uid]["outcome_ids"]),
                    f"{rid}: outcome link missing from parent unit")
            if not linked:
                warn("empty_links", rid, "لا توجد روابط نتاجات لهذا البند.")
            if label == "assessments":
                criterion = row.get("success_criteria")
                require(criterion is None or isinstance(criterion, str), f"{rid}.success_criteria invalid")
                if not criterion or not criterion.strip():
                    warn("missing_success_criteria", rid, "معيار النجاح غير موثق؛ يلزم حكم مهني.")

    time = obj(data.get("time"), "time")
    basis = time.get("basis")
    reserve = integer(time.get("reserve_periods", 0), "reserve_periods")
    losses = seq(time.get("losses", []), "losses")
    if basis == "gross":
        require("net_periods" not in time, "gross basis cannot include net_periods")
        gross = integer(time.get("weeks"), "weeks", 1) * integer(time.get("periods_per_week"), "periods_per_week", 1)
        lost = 0
        for loss in losses:
            obj(loss, "loss")
            string(loss.get("reason"), "loss.reason")
            string(loss.get("source"), "loss.source")
            lost += integer(loss.get("periods"), "loss.periods")
        require(lost <= gross, "losses exceed gross periods")
        net = gross - lost
    elif basis == "net":
        require(not losses, "net basis: losses already deducted; do not deduct twice")
        require("weeks" not in time and "periods_per_week" not in time,
                "net basis cannot include gross fields")
        gross = lost = None
        net = integer(time.get("net_periods"), "net_periods")
    else:
        raise DataError("time.basis must be gross or net")
    require(reserve <= net, "reserve exceeds net periods")
    planned = sum(u["periods"] for u in units.values())
    capacity = net - reserve
    if planned > capacity:
        warn("time_overload", None, f"تجاوز التوزيع المتاح بمقدار {planned - capacity} حصة.")

    matrix = []
    for oid, outcome in outcomes.items():
        row = {"outcome_id": oid, "required": outcome["required"],
               "provenance": outcome["provenance"],
               "unit_ids": [uid for uid, u in units.items() if oid in u["outcome_ids"]],
               "activity_ids": [aid for aid, a in activities.items() if oid in a["outcome_ids"]],
               "assessment_ids": [aid for aid, a in assessments.items() if oid in a["outcome_ids"]]}
        matrix.append(row)
        if outcome["required"]:
            for key, code in [("unit_ids", "no_unit"), ("activity_ids", "no_activity"),
                              ("assessment_ids", "no_assessment")]:
                if not row[key]:
                    warn(code, oid, "رابط بنيوي مفقود لنتاج مطلوب؛ افحص الأصل قبل الحسم.")
    required = [r for r in matrix if r["required"]]
    structural_count = sum(bool(r["unit_ids"] and r["activity_ids"] and r["assessment_ids"]) for r in required)
    return {"schema_version": 1, "kind": "structural_check",
            "notice": "فحص حسابي وبنيوي فقط؛ لا يثبت المطابقة الرسمية أو الجودة الدلالية.",
            "reference_complete_asserted": data["reference_complete"],
            "time": {"basis": basis, "gross_periods": gross, "lost_periods": lost,
                     "net_periods": net, "reserve_periods": reserve, "capacity_periods": capacity,
                     "planned_periods": planned, "remaining_periods": capacity - planned,
                     "overload_periods": max(0, planned - capacity)},
            "links": {"required_outcomes": len(required), "fully_linked_required": structural_count,
                      "structural_link_percent": round(100 * structural_count / len(required), 1) if required else None},
            "matrix": matrix, "warnings": warnings}


def score_rubric(data):
    version(data)
    rows = index_rows(data, "ratings")
    require(set(rows) == set(WEIGHTS), "ratings must contain exactly R1..R7 once each")
    failures = seq(data.get("critical_failures", []), "critical_failures")
    require(all(isinstance(f, str) and f.strip() for f in failures), "critical_failures must be nonempty strings")
    applicable = evidence_weight = numerator = 0
    unresolved = []
    normalized = []
    for rid, default_weight in WEIGHTS.items():
        row = rows[rid]
        weight = row.get("weight", default_weight)
        require(type(weight) in {int, float} and math.isfinite(weight) and 0 < weight <= 100,
                f"{rid}.weight must be finite and in (0,100]")
        state = row.get("status")
        require(isinstance(state, str) and state in {"rated", "insufficient", "not_applicable"}, f"{rid}.status invalid")
        string(row.get("reason"), f"{rid}.reason")
        score = row.get("score")
        if state == "rated":
            integer(score, f"{rid}.score")
            require(score <= 3, f"{rid}.score must be 0..3")
            evidence = seq(row.get("evidence"), f"{rid}.evidence")
            require(bool(evidence) and all(isinstance(e, str) and e.strip() for e in evidence),
                    f"{rid}: rated needs evidence locations")
            evidence_weight += weight
            numerator += weight * score / 3
        else:
            require(score is None, f"{rid}: unscored state requires null score")
        if state != "not_applicable":
            applicable += weight
        if rid in CRITICAL and state != "rated":
            unresolved.append(rid)
        normalized.append({"id": rid, "status": state, "score": score, "weight": weight})
    coverage = 100 * evidence_weight / applicable if applicable else None
    blockers = list(failures)
    if unresolved:
        blockers.append("محاور حاسمة غير مقدرة: " + ", ".join(unresolved))
    if coverage is None or coverage < 80:
        blockers.append("تغطية التقييم دون العتبة التشغيلية المقترحة 80% أو غير قابلة للحساب.")
    return {"schema_version": 1, "kind": "rubric_arithmetic", "rubric_version": "1.0",
            "score_on_available_evidence": round(100 * numerator / evidence_weight, 1) if evidence_weight else None,
            "evidence_coverage_percent": round(coverage, 1) if coverage is not None else None,
            "applicable_weight": applicable, "rated_weight": evidence_weight,
            "ratings": normalized, "unresolved_critical_axes": unresolved,
            "global_judgment_blockers": blockers,
            "eligible_for_global_professional_judgment": not blockers,
            "notice": "الحساب لا يمنح جاهزية أو اعتماداً؛ يلزم تفسير الأدلة والحكم المهني."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "score"])
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = check_plan(data) if args.mode == "check" else score_rubric(data)
    except (OSError, UnicodeError, json.JSONDecodeError, DataError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        try:
            require(args.output.resolve() != args.input.resolve(), "output cannot overwrite input")
            args.output.write_text(text, encoding="utf-8")
        except (OSError, DataError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
