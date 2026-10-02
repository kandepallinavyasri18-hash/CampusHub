"""Contribution ledger and derived leadership statistics for CampusHub."""
from collections import defaultdict
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

POINTS = {
    "question_asked": 1,
    "answer_submitted": 5,
    "answer_accepted": 10,
    "resource_uploaded": 10,
    "positive_resource_rating": 2,
    "helpful_response": 5,
}

BADGE_RULES = (
    ("contributor", "Contributor", lambda stats: stats["points"] >= 50),
    ("active-contributor", "Active Contributor", lambda stats: stats["points"] >= 100),
    ("knowledge-contributor", "Knowledge Contributor", lambda stats: stats["points"] >= 200),
    ("top-helper", "Top Helper", lambda stats: stats["helpfulAnswers"] >= 5),
    ("resource-champion", "Resource Champion", lambda stats: stats["positiveResources"] >= 5),
    ("problem-solver", "Problem Solver", lambda stats: stats["acceptedAnswers"] >= 3),
    ("campus-leader", "Campus Leader", lambda stats: stats["points"] >= 200 and stats["activeMonths"] >= 3),
)


def ensure_indexes(store):
    store.contributions.create_index("actionKey", unique=True)
    store.contributions.create_index([("userId", 1), ("createdAt", -1)])
    store.resource_ratings.create_index([("resourceId", 1), ("userId", 1)], unique=True)
    store.badge_achievements.create_index([("userId", 1), ("badgeId", 1)], unique=True)


def backfill_existing_activity(store):
    for resource in store.resources.find({"isActive": {"$ne": False}}):
        uploader = resource.get("uploadedBy")
        if uploader:
            record_contribution(
                store, uploader, "resource_uploaded", "resource", resource["_id"]
            )
    for question in store.questions.find({"isActive": {"$ne": False}}):
        asked_by = question.get("askedBy")
        if asked_by:
            record_contribution(
                store, asked_by, "question_asked", "question", question["_id"]
            )
        answered_by = question.get("answeredBy")
        if answered_by and question.get("answer"):
            record_contribution(
                store,
                answered_by,
                "answer_submitted",
                "question",
                question["_id"],
                action_key=f"answer_submitted:question:{question['_id']}:{answered_by}",
            )
        if answered_by and question.get("acceptedByAsker"):
            record_contribution(
                store,
                answered_by,
                "answer_accepted",
                "question",
                question["_id"],
                action_key=f"answer_accepted:question:{question['_id']}",
            )


def record_contribution(store, user_id, kind, reference_type, reference_id, action_key=None, points=None):
    action_key = action_key or f"{kind}:{reference_type}:{reference_id}:{user_id}"
    now = datetime.now(timezone.utc)
    try:
        store.contributions.update_one(
            {"actionKey": action_key},
            {"$setOnInsert": {
                "actionKey": action_key,
                "userId": user_id,
                "type": kind,
                "referenceType": reference_type,
                "referenceId": reference_id,
                "points": POINTS[kind] if points is None else points,
                "active": True,
                "createdAt": now,
                "updatedAt": now,
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        if not store.contributions.find_one({"actionKey": action_key}):
            raise


def set_contribution_active(store, action_key, active, reason=None):
    changes = {"active": active, "updatedAt": datetime.now(timezone.utc)}
    if reason:
        changes["moderationReason"] = reason
    store.contributions.update_one({"actionKey": action_key}, {"$set": changes})


def deactivate_reference(store, reference_type, reference_id, reason=None):
    changes = {"active": False, "updatedAt": datetime.now(timezone.utc)}
    if reason:
        changes["moderationReason"] = reason
    store.contributions.update_many(
        {"referenceType": reference_type, "referenceId": reference_id, "active": True},
        {"$set": changes},
    )


def user_statistics(store, user):
    user_id = user["_id"]
    events = list(store.contributions.find({"userId": user_id, "active": True}).sort("createdAt", -1))
    events = [
        event for event in events
        if not (
            event.get("referenceType") == "resource"
            and not store.resources.find_one({"_id": event.get("referenceId"), "isActive": True})
        ) and not (
            event.get("referenceType") == "question"
            and not store.questions.find_one({"_id": event.get("referenceId")})
        )
    ]

    by_type = defaultdict(int)
    months = set()
    monthly_points = defaultdict(int)
    for event in events:
        by_type[event.get("type", "")] += 1
        created = event.get("createdAt")
        if isinstance(created, datetime):
            months.add(created.strftime("%Y-%m"))
            monthly_points[created.strftime("%Y-%m")] += int(event.get("points", 0))

    resources = list(store.resources.find({"uploadedBy": user_id, "isActive": True}))
    resource_ids = [item["_id"] for item in resources]
    ratings = list(store.resource_ratings.find({
        "resourceId": {"$in": resource_ids}, "active": {"$ne": False},
    })) if resource_ids else []
    accepted = by_type["answer_accepted"]
    helpful_response_count = by_type["helpful_response"]
    helpful_questions = {
        str(event.get("referenceId"))
        for event in events
        if event.get("type") in ("answer_accepted", "helpful_response")
    }
    rating_values = [float(item["rating"]) for item in ratings]
    quality_values = rating_values + [5.0] * (accepted + helpful_response_count)
    rating = round(sum(quality_values) / len(quality_values), 1) if quality_values else 0.0

    stats = {
        "points": sum(int(event.get("points", 0)) for event in events),
        "resourcesUploaded": by_type["resource_uploaded"],
        "questionsAsked": by_type["question_asked"],
        "questionsAnswered": by_type["answer_submitted"],
        "helpfulAnswers": len(helpful_questions),
        "acceptedAnswers": accepted,
        "positiveResources": sum(1 for value in rating_values if value >= 4),
        "rating": rating,
        "ratingCount": len(quality_values),
        "activeMonths": len(months),
        "monthlyPoints": [
            {"month": month, "points": monthly_points[month]}
            for month in sorted(monthly_points)
        ],
        "breakdown": {
            "resources": by_type["resource_uploaded"],
            "questionsAnswered": by_type["answer_submitted"],
            "helpfulAnswers": len(helpful_questions),
            "acceptedAnswers": accepted,
        },
        "history": [
            {
                "id": str(event["_id"]),
                "type": event.get("type"),
                "points": event.get("points", 0),
                "referenceType": event.get("referenceType"),
                "createdAt": event.get("createdAt").isoformat() if isinstance(event.get("createdAt"), datetime) else None,
            }
            for event in events[:50]
        ],
    }

    badges = []
    for badge_id, label, qualifies in BADGE_RULES:
        if not qualifies(stats):
            continue
        key = {"userId": user_id, "badgeId": badge_id}
        try:
            store.badge_achievements.update_one(
                key,
                {"$setOnInsert": {"label": label, "earnedAt": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except DuplicateKeyError:
            if not store.badge_achievements.find_one(key):
                raise
    for achievement in store.badge_achievements.find({"userId": user_id}).sort("earnedAt", 1):
        earned_at = achievement.get("earnedAt")
        badges.append({
            "id": achievement["badgeId"],
            "name": achievement.get("label", achievement["badgeId"]),
            "earnedAt": earned_at.isoformat() if isinstance(earned_at, datetime) else None,
        })
    stats["badges"] = badges
    return stats


def public_leader(user, stats, include_branch=False):
    result = {
        "id": str(user["_id"]),
        "name": user.get("fullName", "CampusHub member"),
        "role": user.get("role"),
        "rating": stats["rating"],
        "points": stats["points"],
        "resourcesUploaded": stats["resourcesUploaded"],
        "questionsAnswered": stats["questionsAnswered"],
        "helpfulAnswers": stats["helpfulAnswers"],
        "acceptedAnswers": stats["acceptedAnswers"],
        "badges": stats["badges"],
    }
    if include_branch:
        result["branch"] = user.get("branch", "")
    return result


def leaderboard(store, role):
    leaders = []
    for user in store.users.find({"role": role}):
        leaders.append(public_leader(user, user_statistics(store, user)))
    return sorted(
        leaders,
        key=lambda item: (
            -item["points"],
            -item["rating"],
            -item["helpfulAnswers"],
            -item["resourcesUploaded"],
            -item["questionsAnswered"],
            item["name"].casefold(),
        ),
    )


def student_branch_rankings(store, selected_branch=None):
    branches = sorted({
        user.get("branch", "").strip()
        for user in store.users.find({"role": "student"})
        if user.get("branch", "").strip()
    }, key=str.casefold)
    if selected_branch:
        branches = [branch for branch in branches if branch.casefold() == selected_branch.casefold()]

    result = []
    for branch in branches:
        rows = [
            public_leader(user, user_statistics(store, user), include_branch=True)
            for user in store.users.find({"role": "student", "branch": branch})
        ]
        rows.sort(key=lambda item: (
            -item["rating"],
            -item["points"],
            -item["helpfulAnswers"],
            -item["acceptedAnswers"],
            -item["resourcesUploaded"],
            -item["questionsAnswered"],
            item["name"].casefold(),
        ))
        last_key = None
        rank = 0
        for position, row in enumerate(rows, 1):
            key = (
                row["rating"], row["points"], row["helpfulAnswers"],
                row["acceptedAnswers"], row["resourcesUploaded"], row["questionsAnswered"],
            )
            if key != last_key:
                rank = position
                last_key = key
            row["rank"] = rank
        result.append({"branch": branch, "students": rows})
    return branches, result
