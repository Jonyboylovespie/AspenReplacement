"""Lossless, readable JSON compaction for AI records; no summarization or truncation."""
from collections import Counter
import json


FORMAT = "aspen-compact-v1"
ABSENT = {"$absent": True}
RESERVED = {"$ref", "$table", "$object", "$absent"}
FORMAT_GUIDE = """The Aspen JSON uses lossless aspen-compact-v1 encoding.
Read records as the root object. {"$ref":[B,R]} means the value at index R of
shared[B]. Indices are zero-based. If that block is a $table, reconstruct row R
using its columns and defaults; otherwise use array item R. Resolve nested refs.
References are
shared values, not record IDs: preserve enclosing lists' order and multiplicity.
Identical values in different courses can describe different assignments.
{"$table":{"columns":[...],
"defaults":{...},"rows":[...]}} means a list of objects: each row's positional
values correspond to columns; omitted trailing values inherit defaults, or
indicate an absent field if no default exists. {"$absent":true} indicates an
absent field, distinct from null, false, zero, or empty text. Defaults apply only
to omitted columns. {"$object":[[key,value],...]} escapes literal reserved keys.
All original period membership, values, descriptions, and event order are kept.
"""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def identity(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def compact_records(records):
    """Intern repeated values and use tables when smaller than ordinary JSON."""
    occurrences = Counter()
    walked = set()

    def candidate(value):
        # References cost tokens too: don't replace short labels with them.
        return isinstance(value, (dict, list, str)) and len(dumps(value)) >= 80

    def count(value):
        if candidate(value):
            key = identity(value)
            occurrences[key] += 1
            if key in walked:
                return
            walked.add(key)
        if isinstance(value, dict):
            for item in value.values():
                count(item)
        elif isinstance(value, list):
            for item in value:
                count(item)

    count(records)
    shared = []
    indices = {}

    def table(items):
        if len(items) < 2 or not all(isinstance(item, dict) and not RESERVED.intersection(item) for item in items):
            return items
        columns = sorted({key for item in items for key in item})
        defaults = {}
        for key in columns:
            values = [item[key] for item in items if key in item]
            frequencies = Counter(identity(value) for value in values)
            if frequencies:
                common, number = frequencies.most_common(1)[0]
                # Use common defaults, never equate absent fields with nulls.
                if number >= 2 and number > len(items) - len(values):
                    defaults[key] = json.loads(common)
        # Varying columns first lets rows omit common fields at the end.
        columns.sort(key=lambda key: (key in defaults, key))
        rows = []
        for item in items:
            row = [item.get(key, ABSENT) for key in columns]
            while row and identity(row[-1]) == identity(defaults.get(columns[len(row) - 1], ABSENT)):
                row.pop()
            rows.append(row)
        packed = {"$table": {"columns": columns, "defaults": defaults, "rows": rows}}
        return packed if len(dumps(packed)) < len(dumps(items)) else items

    def encode(value, defining=False):
        if not defining and candidate(value):
            key = identity(value)
            if occurrences[key] > 1:
                if key not in indices:
                    encoded = encode(value, defining=True)
                    indices[key] = len(shared)
                    shared.append(encoded)
                return {"$ref": indices[key]}
        if isinstance(value, dict):
            if RESERVED.intersection(value):
                return {"$object": [[key, encode(item)] for key, item in value.items()]}
            return {key: encode(item) for key, item in value.items()}
        if isinstance(value, list):
            return table([encode(item) for item in value])
        return value

    # Fixed ordering puts academic records before changing freshness metadata.
    ordered = {key: records[key] for key in ("gradePeriods", "attendance", "activityFeed") if key in records}
    ordered.update({key: value for key, value in records.items() if key not in ordered and key != "syncedAt"})
    if "syncedAt" in records:
        ordered["syncedAt"] = records["syncedAt"]
    root = encode(ordered)
    # Pool assignment/course objects in schema groups so their keys occur once.
    # Address blocks and rows directly so the model need not count across blocks.
    groups = {}
    for index, item in enumerate(shared):
        schema = tuple(sorted(item)) if isinstance(item, dict) and not RESERVED.intersection(item) else None
        groups.setdefault(schema, []).append((index, item))
    remapping = {old: [block, row] for block, group in enumerate(groups.values())
                 for row, (old, _) in enumerate(group)}

    def remap(value):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                return {"$ref": remapping[value["$ref"]]}
            return {key: remap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [remap(item) for item in value]
        return value

    blocks = [table([remap(item) for _, item in group]) for group in groups.values()]
    compact = {"format": FORMAT, "shared": blocks, "records": remap(root)}
    # Small or unusual datasets need not pay for an encoding they don't benefit from.
    return compact if len(dumps(compact)) + len(FORMAT_GUIDE) < len(dumps(ordered)) else ordered


def expand_records(payload):
    """Reconstruct the original academic context exactly (also useful for audits)."""
    if payload.get("format") != FORMAT:
        return payload
    def table_rows(table):
        result = []
        for row in table["rows"]:
            item = {}
            for index, key in enumerate(table["columns"]):
                field = row[index] if index < len(row) else table["defaults"].get(key, ABSENT)
                if field != ABSENT:
                    item[key] = field
            result.append(item)
        return result

    shared = [table_rows(block["$table"]) if isinstance(block, dict) and set(block) == {"$table"} else block
              for block in payload["shared"]]

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"$ref"}:
            block, row = value["$ref"]
            return decode(shared[block][row])
        if set(value) == {"$object"}:
            return {key: decode(item) for key, item in value["$object"]}
        if set(value) == {"$table"}:
            return [decode(item) for item in table_rows(value["$table"])]
        return {key: decode(item) for key, item in value.items()}

    return decode(payload["records"])
