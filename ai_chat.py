"""Server-only AI transport and a credential-free academic context."""
import requests

from compact_records import FORMAT, FORMAT_GUIDE, compact_records, dumps
from aspen import numeric_grade, numeric_summary

MODEL = "gpt-6.1-sol"
INSTRUCTIONS = """You are BetterAspen's school assistant. Help this student understand
their grades, assignments, category weights, and absences using only the supplied
Aspen records. Be very concise with your responses. Be friendly, and use plain text with short paragraphs or
simple lists. You have every saved grade period, assignment, attendance record,
and activity entry supplied for this account. Use all relevant school years and quarters
unless the student's question asks for a specific period. Label years and terms
clearly when comparing records, and do not double-count assignments that appear
in both all-quarter and individual-quarter views.
Do not claim access to other students or to live Aspen. Identify sample data, stale data, unavailable features, partial attendance, and period
scope when relevant. Daily absences and class absences are distinct; do not add
them together or infer absence from missing records. Never invent grades, school
policies, attendance codes, or missing records. If records
cannot answer a question, say what is missing. Do not claim to change any records.
Minimum percentages, checked from highest to lowest (inclusive):
A: 92.5; A−: 89.5; B+: 86.5; B: 82.5; B−: 79.5; C+: 76.5; C: 72.5;
C−: 69.5; D+: 66.5; D: 62.5; D−: 59.5; below 59.5: F. There is no fallback A+.
Use the unrounded percentage for cutoffs (92.49 is A−, even if displayed as 92.5%).
Assignment percentages are earned points / possible points * 100, only with numeric scores and positive possible
points; extra credit can exceed 100%. Use these cutoffs for hypothetical target
letters. Only use reported categories when they effect grade calculations, if they don't, don't mention them at all.
Stay focused on school grades, assignments, and attendance.
"""


class ChatError(Exception):
    pass


def pick(data, keys):
    return {key: data[key] for key in keys.split() if key in data}


def academic_period(period):
    result = pick(period, "gradeFilters")
    result["classes"] = []
    for course in period.get("classes", []):
        item = pick(course, "courseName gradeSource")
        item["displayGrade"] = numeric_grade(course.get("displayGrade")) or numeric_grade(course.get("percentageValue")) or ""
        item["averageSummary"] = numeric_summary(course.get("averageSummary", []))
        item["terms"] = [pick(term, "gradeTermId") for term in course.get("terms", [])]
        item["assignments"] = []
        for assignment in course.get("assignments", []):
            entry = pick(assignment, "name categoryName termName assignedDate dueDate totalPoints description")
            entry["scores"] = []
            for score in assignment.get("scoreLightModels", []):
                values = pick(score, "specialCode behavior dropped exempt missing late incomplete comment")
                if "score" in score:
                    values["score"] = numeric_grade(score["score"])
                entry["scores"].append(values)
            item["assignments"].append(entry)
        result["classes"].append(item)
    return result


def academic_context(snapshot, stale):
    periods = {key: academic_period(value)
               for key, value in snapshot.get("gradePeriods", {}).items()}
    # Include legacy snapshots too, or a snapshot period absent from the cache.
    filters = snapshot.get("gradeFilters", {})
    key = f"{filters.get('year', 'current')}:{filters.get('quarter', 'current')}"
    periods.setdefault(key, academic_period(snapshot))
    result = {**pick(snapshot, "mode syncedAt warnings"), "stale": stale,
              "gradePeriods": periods}
    attendance = snapshot.get("attendance", {})
    result["attendance"] = {**pick(attendance, "available scope partial stale fetchedAt error summary"),
                            "records": [pick(row, "date code reason") for row in attendance.get("records", [])]}
    activity = snapshot.get("activityFeed", {})
    events = []
    for row in activity.get("events", []):
        event = pick(row, "type date dateMeaning className assignmentName code period absent tardy dismissed excused")
        if "grade" in row:
            event["grade"] = numeric_grade(row["grade"], points=True)
        events.append(event)
    result["activityFeed"] = {**pick(activity, "available scope partial stale fetchedAt error datePrecision attendanceEnabled gradesEnabled"),
                              "events": events}
    return result


def validate_messages(body):
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not 1 <= len(messages) <= 21 or len(messages) % 2 != 1:
        raise ChatError("Send a question with at most 10 previous replies.")
    clean = []
    for index, message in enumerate(messages):
        role = "user" if index % 2 == 0 else "assistant"
        if (not isinstance(message, dict) or message.get("role") != role
                or not isinstance(message.get("content"), str)
                or not message["content"].strip() or len(message["content"]) > (8000 if role == "user" else 20000)):
            raise ChatError("Questions and replies must be plain text, up to 8,000 characters each.")
        clean.append({"role": role, "content": message["content"].strip()})
    if sum(len(message["content"]) for message in clean) > 40000:
        raise ChatError("This conversation is too long. Start a new chat.")
    return clean


def ask(endpoint, key, messages, context):
    compact = compact_records(context)
    records = dumps(compact)
    if len(records) > 300000:
        raise ChatError("Your saved records are too large for chat right now.")
    try:
        response = requests.post(endpoint, headers={"Authorization": f"Bearer {key}"},
                                 json={"model": MODEL, "reasoning": {"effort": "low"},
                                       "store": False, "max_output_tokens": 4000,
                                       "instructions": INSTRUCTIONS + (FORMAT_GUIDE if compact.get("format") == FORMAT else ""),
                                       "input": [{"role": "developer", "content": "Aspen records (JSON):\n" + records}, *messages]},
                                 timeout=(5, 60), allow_redirects=False)
        # Never expose upstream error bodies: these may contain credentials or records.
        if response.status_code != 200:
            raise ChatError("The AI service is unavailable. Try again shortly.")
        data = response.json()
        if data.get("status") not in (None, "completed"):
            raise ChatError("The AI could not finish its reply. Try a shorter question.")
        reply = "\n".join(part["text"] for item in data.get("output", []) if item.get("type") == "message"
                          for part in item.get("content", []) if part.get("type") == "output_text")
        if not reply.strip():
            raise ChatError("The AI returned an empty reply. Try again.")
        return reply
    except requests.Timeout:
        raise ChatError("The AI took too long to respond. Try again.") from None
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        raise ChatError("The AI service could not be reached. Try again shortly.") from None
