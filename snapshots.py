"""Lossless snapshot storage with shared course and assignment records."""
import json

FORMAT = "betteraspen-snapshot-v1"


def pack_snapshot(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("gradePeriods"), dict):
        return snapshot
    pools = {name: [] for name in ("courses", "assignments", "terms", "summaries")}
    indices = {name: {} for name in pools}
    objects = {}

    def intern(name, value):
        key = name, id(value)
        if name != "courses" and key in objects:
            return objects[key]
        identity = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if identity not in indices[name]:
            indices[name][identity] = len(pools[name])
            pools[name].append(value)
        index = indices[name][identity]
        if name != "courses":
            objects[key] = index
        return index

    def courses(items):
        result = []
        for course in items:
            if not isinstance(course, dict) or {"assignmentsRef", "termsRef", "summaryRef"}.intersection(course):
                raise ValueError("Invalid course record")
            record = {key: value for key, value in course.items()
                      if key not in {"assignments", "terms", "averageSummary"}}
            if "assignments" in course:
                record["assignmentsRef"] = [intern("assignments", item) for item in course["assignments"]]
            if "terms" in course:
                record["termsRef"] = intern("terms", course["terms"])
            if "averageSummary" in course:
                record["summaryRef"] = intern("summaries", course["averageSummary"])
            result.append(intern("courses", record))
        return result

    try:
        packed = {**snapshot, "snapshotFormat": FORMAT, "recordPool": pools,
                  "gradePeriods": {key: {**period, "classes": courses(period["classes"])}
                                   for key, period in snapshot["gradePeriods"].items()}}
        if "classes" in snapshot:
            packed["classes"] = courses(snapshot["classes"])
        return packed
    except (KeyError, TypeError, ValueError):
        # Older or incomplete caches remain readable during upgrades.
        return snapshot


def expand_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("snapshotFormat") != FORMAT:
        return snapshot
    pools = snapshot["recordPool"]
    courses = []
    for record in pools["courses"]:
        course = {key: value for key, value in record.items()
                  if key not in {"assignmentsRef", "termsRef", "summaryRef"}}
        if "assignmentsRef" in record:
            course["assignments"] = [pools["assignments"][index] for index in record["assignmentsRef"]]
        if "termsRef" in record:
            course["terms"] = pools["terms"][record["termsRef"]]
        if "summaryRef" in record:
            course["averageSummary"] = pools["summaries"][record["summaryRef"]]
        courses.append(course)
    result = {key: value for key, value in snapshot.items() if key not in {"snapshotFormat", "recordPool"}}
    result["gradePeriods"] = {key: {**period, "classes": [courses[index] for index in period["classes"]]}
                              for key, period in snapshot["gradePeriods"].items()}
    if "classes" in snapshot:
        result["classes"] = [courses[index] for index in snapshot["classes"]]
    return result
