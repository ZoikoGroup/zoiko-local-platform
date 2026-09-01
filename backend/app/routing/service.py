"""Advanced IVR builder (Architecture doc's "Call Flow Designer": draft/live
versions, validation, publishing, rollback, schedules, failover, and audit —
Phase 3 scope). A flow is an ordered list of typed nodes, exactly like the
email spec's ROUTE domain describes them (Call Flow Published/Changed/
Rollback, Business Hours Changed, Failover Activated/Cleared, Route Has No
Reachable Destination). Deliberately no visual canvas.

The "queue" node type hands a caller off to the separate contact-center-lite
module (app.queues) for real FIFO hold + agent-pull distribution - this
module only knows a queue node has a queue_id and an overflow_node_id, never
anything Twilio-Queue-specific (that lives in app.queues.service and
media.voice, same provider-neutral split as every other node type).
"""

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import publish_call_flow_published, publish_call_flow_rolled_back
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.notifications.service import notify_call_flow_published, notify_call_flow_rolled_back
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.queues.models import CallQueue
from app.routing.models import CallFlow, CallFlowVersion, CallFlowVersionStatus

TERMINAL_NODE_TYPES = {"forward", "voicemail", "ai_receptionist", "hangup", "queue"}
ROUTING_NODE_TYPES = {"menu", "business_hours"}
ALL_NODE_TYPES = TERMINAL_NODE_TYPES | ROUTING_NODE_TYPES

# Defensive bound on business-hours-only chains during resolution - real
# flows never approach this; it only protects against a malformed cycle of
# business_hours nodes that all point only to each other (a case
# validate_flow's reachability check already keeps out of anything
# publishable, but draft flows are never validated, and evaluate_* always
# reads the *live*, published version, so this is belt-and-suspenders).
MAX_RESOLUTION_HOPS = 50


class CallFlowNotFoundError(Exception):
    pass


class CallFlowValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class NodeNotFoundError(Exception):
    pass


class NumberNotOwnedError(Exception):
    pass


def _is_within_business_hours(start: time, end: time, tz_name: str) -> bool:
    # Deliberately duplicated from media.service.is_within_business_hours
    # (same 5-line calculation) rather than imported, to avoid a
    # media<->routing import cycle - media will need to call INTO routing
    # once a phone number has a live call flow, and routing evaluating a
    # business_hours node has no other reason to depend on media at all.
    now_local = datetime.now(ZoneInfo(tz_name)).time()
    if start <= end:
        return start <= now_local <= end
    return now_local >= start or now_local <= end  # overnight range, e.g. 22:00-06:00


def account_queue_ids(db: Session, account_id: str) -> set[str]:
    return {row[0] for row in db.query(CallQueue.id).filter(CallQueue.account_id == account_id).all()}


def _get_flow(db: Session, account_id: str, call_flow_id: str) -> CallFlow:
    flow = (
        db.query(CallFlow)
        .filter(CallFlow.id == call_flow_id, CallFlow.account_id == account_id)
        .first()
    )
    if flow is None:
        raise CallFlowNotFoundError(call_flow_id)
    return flow


def _get_draft(db: Session, call_flow_id: str) -> CallFlowVersion:
    draft = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == call_flow_id, CallFlowVersion.status == CallFlowVersionStatus.DRAFT)
        .first()
    )
    if draft is None:
        # Every call flow is created with a draft and always has exactly one -
        # publish() immediately opens the next draft. Missing one is a bug,
        # not a user-facing state.
        raise CallFlowNotFoundError(f"no draft for call flow {call_flow_id}")
    return draft


def _lock_flow_versions(db: Session, call_flow_id: str) -> None:
    """Row-locks every CallFlowVersion for this call_flow_id, held until
    commit - same "Atomicity law" pattern as reserve_number's
    with_for_update() in numbering/numbers/service.py. CallFlowVersion's
    own docstring requires "exactly one PUBLISHED version per call_flow_id"
    to be enforced atomically; without this, two concurrent
    publish_flow/rollback_flow calls for the same flow can both read the
    same stale draft/live state and each create a colliding new version,
    silently losing one admin's edits. Must be called before _get_draft,
    the live-version lookup, and _next_version_number - every read that
    determines the next version number or flips PUBLISHED/ARCHIVED status
    has to happen after the lock is acquired, inside the same transaction."""
    db.query(CallFlowVersion).filter(CallFlowVersion.call_flow_id == call_flow_id).with_for_update().all()


def _next_version_number(db: Session, call_flow_id: str) -> int:
    latest = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == call_flow_id)
        .order_by(CallFlowVersion.version.desc())
        .first()
    )
    return (latest.version if latest else 0) + 1


def create_flow(db: Session, account_id: str, name: str, actor_id: str) -> CallFlow:
    flow = CallFlow(account_id=account_id, name=name, created_by_user_id=actor_id)
    db.add(flow)
    db.flush()
    draft = CallFlowVersion(
        call_flow_id=flow.id,
        version=1,
        status=CallFlowVersionStatus.DRAFT,
        entry_node_id="",
        nodes=[],
        created_by_user_id=actor_id,
    )
    db.add(draft)
    db.commit()
    _invalidate_flows_cache(account_id)
    log_event(db, actor_id=actor_id, action="call_flow.created", target_type="call_flow", target_id=flow.id,
               metadata={"name": name})
    return flow


def _flows_cache_key(account_id: str) -> str:
    return f"call_flows:list:{account_id}"


# Real N+1 query cost, unlike a plain single-table list: list_flows runs two
# extra queries PER flow (live version, assigned numbers), so this is a
# genuine perf win, not just the usual "avoid one requery" case. Invalidated
# at every write site that changes a field this response actually reflects -
# create_flow (id/name/created_at), publish_flow/rollback_flow (live_version),
# assign_to_number (assigned_numbers). save_draft is deliberately excluded -
# draft node content never appears in this list's output.
_FLOWS_CACHE_TTL_SECONDS = 30


def _invalidate_flows_cache(account_id: str) -> None:
    cache_delete(_flows_cache_key(account_id))


def list_flows(db: Session, account_id: str) -> list[dict]:
    cache_key = _flows_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    flows = db.query(CallFlow).filter(CallFlow.account_id == account_id).order_by(CallFlow.created_at.desc()).all()
    results = []
    for flow in flows:
        live = (
            db.query(CallFlowVersion)
            .filter(CallFlowVersion.call_flow_id == flow.id, CallFlowVersion.status == CallFlowVersionStatus.PUBLISHED)
            .first()
        )
        assigned = db.query(PhoneNumber.e164).filter(PhoneNumber.call_flow_id == flow.id).all()
        results.append(
            {
                "id": flow.id,
                "name": flow.name,
                "created_at": flow.created_at.isoformat() if flow.created_at else None,
                "has_draft": True,
                "live_version": live.version if live else None,
                "assigned_numbers": [row[0] for row in assigned],
            }
        )
    cache_set(cache_key, results, ttl_seconds=_FLOWS_CACHE_TTL_SECONDS)
    return results


def get_flow_detail(db: Session, account_id: str, call_flow_id: str) -> dict:
    flow = _get_flow(db, account_id, call_flow_id)
    versions = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == flow.id)
        .order_by(CallFlowVersion.version.desc())
        .all()
    )
    draft = next((v for v in versions if v.status == CallFlowVersionStatus.DRAFT), None)
    live = next((v for v in versions if v.status == CallFlowVersionStatus.PUBLISHED), None)
    return {
        "id": flow.id,
        "account_id": flow.account_id,
        "name": flow.name,
        "created_at": flow.created_at,
        "draft": draft,
        "live": live,
        "version_history": versions,
    }


def save_draft(
    db: Session, account_id: str, call_flow_id: str, entry_node_id: str, nodes: list[dict],
    actor_id: str | None = None,
) -> CallFlowVersion:
    flow = _get_flow(db, account_id, call_flow_id)
    draft = _get_draft(db, flow.id)
    draft.entry_node_id = entry_node_id
    draft.nodes = nodes
    db.commit()
    # Real gap fix - every sibling write in this file (create_flow/
    # publish_flow/rollback_flow/assign_to_number) logs via log_event;
    # editing a draft's node graph is exactly as state-changing as those
    # and previously left zero audit trail. `actor_id` is optional and
    # keyword-only-in-practice here since routing/routes.py's existing
    # /draft route doesn't pass current_user.id through yet (out of scope
    # for this fix) - falls back to account_id as the actor, same
    # "no specific user in the loop" convention numbering.numbers.service's
    # reserve_number already uses (actor_id=account_id) rather than
    # skipping the audit entry entirely.
    log_event(
        db, actor_id=actor_id or account_id, action="call_flow.draft_saved", target_type="call_flow",
        target_id=flow.id, metadata={"version": draft.version},
    )
    return draft


def validate_flow(nodes: list[dict], entry_node_id: str, valid_queue_ids: set[str] | None = None) -> list[str]:
    """Pure validation (mostly) - reused by publish() and exposed as its
    own thing so the frontend can call it (via a dedicated endpoint) before
    the user tries to publish, matching the Call Flow Designer's
    "validation" step being distinct from "publishing" itself.
    `valid_queue_ids` is the one non-pure input: the account's real
    app.queues.models.CallQueue ids, so a queue node can't reference a
    queue that doesn't exist (or belongs to another account). Callers
    without DB access (e.g. the pure-logic checks used during development)
    can omit it to skip that one check.
    """
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            errors.append("Every node needs a non-empty id.")
            continue
        if node_id in by_id:
            errors.append(f"Duplicate node id '{node_id}'.")
            continue
        by_id[node_id] = node

    if not entry_node_id:
        errors.append("An entry node is required.")
    elif entry_node_id not in by_id:
        errors.append(f"Entry node '{entry_node_id}' does not exist.")

    def check_ref(from_id: str, ref: str | None, field: str):
        if ref is not None and ref not in by_id:
            errors.append(f"Node '{from_id}': {field} references unknown node '{ref}'.")

    for node_id, node in by_id.items():
        node_type = node.get("type")
        if node_type not in ALL_NODE_TYPES:
            errors.append(f"Node '{node_id}' has unknown type '{node_type}'.")
            continue

        if node_type == "menu":
            if not node.get("prompt"):
                errors.append(f"Menu node '{node_id}' needs a prompt.")
            options = node.get("options") or {}
            if not options:
                errors.append(f"Menu node '{node_id}' needs at least one option.")
            for digit, target in options.items():
                check_ref(node_id, target, f"option '{digit}'")
            check_ref(node_id, node.get("invalid_node_id"), "invalid_node_id")
            check_ref(node_id, node.get("timeout_node_id"), "timeout_node_id")

        elif node_type == "business_hours":
            if node.get("start") is None or node.get("end") is None or not node.get("timezone"):
                errors.append(f"Business-hours node '{node_id}' needs start, end, and timezone.")
            if not node.get("within_node_id") or not node.get("outside_node_id"):
                errors.append(f"Business-hours node '{node_id}' needs both within_node_id and outside_node_id.")
            check_ref(node_id, node.get("within_node_id"), "within_node_id")
            check_ref(node_id, node.get("outside_node_id"), "outside_node_id")

        elif node_type == "forward":
            destinations = node.get("destinations") or []
            if not destinations or not all(destinations):
                errors.append(f"Forward node '{node_id}' needs at least one destination number.")
            check_ref(node_id, node.get("on_no_answer_node_id"), "on_no_answer_node_id")

        elif node_type == "queue":
            if not node.get("queue_id"):
                errors.append(f"Queue node '{node_id}' needs a queue_id.")
            elif valid_queue_ids is not None and node["queue_id"] not in valid_queue_ids:
                errors.append(f"Queue node '{node_id}' references a queue that doesn't exist on this account.")
            check_ref(node_id, node.get("overflow_node_id"), "overflow_node_id")

    if errors:
        return errors

    # Reachability: is there at least one node reachable from the entry
    # point that actually terminates a call (forward/queue/voicemail/AI
    # receptionist/hangup)? A flow that's all menus/business-hours checks
    # looping among themselves has "no reachable destination" - the exact
    # failure mode the email spec's ROUTE-009 template exists for.
    reachable: set[str] = set()
    to_visit = [entry_node_id]
    hops = 0
    while to_visit and hops < MAX_RESOLUTION_HOPS * 4:
        hops += 1
        current = to_visit.pop()
        if current in reachable or current not in by_id:
            continue
        reachable.add(current)
        node = by_id[current]
        node_type = node.get("type")
        if node_type == "menu":
            to_visit.extend((node.get("options") or {}).values())
            if node.get("invalid_node_id"):
                to_visit.append(node["invalid_node_id"])
            if node.get("timeout_node_id"):
                to_visit.append(node["timeout_node_id"])
        elif node_type == "business_hours":
            to_visit.append(node["within_node_id"])
            to_visit.append(node["outside_node_id"])
        elif node_type == "forward" and node.get("on_no_answer_node_id"):
            to_visit.append(node["on_no_answer_node_id"])
        elif node_type == "queue" and node.get("overflow_node_id"):
            to_visit.append(node["overflow_node_id"])

    if not any(by_id[node_id]["type"] in TERMINAL_NODE_TYPES for node_id in reachable):
        errors.append("This flow has no reachable destination - every path must eventually forward, "
                       "reach a queue, go to voicemail, reach the AI receptionist, or hang up.")

    return errors


def publish_flow(db: Session, account_id: str, call_flow_id: str, actor_id: str) -> tuple[bool, list[str], CallFlowVersion | None]:
    flow = _get_flow(db, account_id, call_flow_id)
    _lock_flow_versions(db, flow.id)
    draft = _get_draft(db, flow.id)

    errors = validate_flow(draft.nodes, draft.entry_node_id, account_queue_ids(db, account_id))
    if errors:
        return False, errors, None

    live = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == flow.id, CallFlowVersion.status == CallFlowVersionStatus.PUBLISHED)
        .first()
    )
    if live is not None:
        live.status = CallFlowVersionStatus.ARCHIVED

    draft.status = CallFlowVersionStatus.PUBLISHED
    draft.published_at = datetime.utcnow()
    draft.published_by_user_id = actor_id
    published_version = draft

    new_draft = CallFlowVersion(
        call_flow_id=flow.id,
        version=_next_version_number(db, flow.id),
        status=CallFlowVersionStatus.DRAFT,
        entry_node_id=published_version.entry_node_id,
        nodes=published_version.nodes,
        created_by_user_id=actor_id,
    )
    db.add(new_draft)
    db.commit()

    _invalidate_flows_cache(account_id)
    log_event(db, actor_id=actor_id, action="call_flow.published", target_type="call_flow", target_id=flow.id,
               metadata={"version": published_version.version})
    publish_call_flow_published(account_id, call_flow_id=flow.id, version=published_version.version)

    numbers = db.query(PhoneNumber).filter(PhoneNumber.call_flow_id == flow.id).all()
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    actor = db.query(User).filter(User.id == actor_id).first()
    if owner is not None:
        notify_call_flow_published(
            db, account_id=account_id, account_email=owner.email, flow_name=flow.name,
            number_summary=", ".join(n.e164 for n in numbers) if numbers else "no assigned numbers yet",
            actor_display_name=actor.email if actor is not None else "an account admin",
        )

    return True, [], published_version


def rollback_flow(db: Session, account_id: str, call_flow_id: str, target_version: int, actor_id: str) -> CallFlowVersion:
    flow = _get_flow(db, account_id, call_flow_id)
    _lock_flow_versions(db, flow.id)
    target = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == flow.id, CallFlowVersion.version == target_version)
        .first()
    )
    if target is None:
        raise CallFlowNotFoundError(f"version {target_version} does not exist for call flow {call_flow_id}")

    live = (
        db.query(CallFlowVersion)
        .filter(CallFlowVersion.call_flow_id == flow.id, CallFlowVersion.status == CallFlowVersionStatus.PUBLISHED)
        .first()
    )
    if live is not None:
        live.status = CallFlowVersionStatus.ARCHIVED

    rolled_back = CallFlowVersion(
        call_flow_id=flow.id,
        version=_next_version_number(db, flow.id),
        status=CallFlowVersionStatus.PUBLISHED,
        entry_node_id=target.entry_node_id,
        nodes=target.nodes,
        created_by_user_id=actor_id,
        published_at=datetime.utcnow(),
        published_by_user_id=actor_id,
        rolled_back_from_version=target_version,
    )
    db.add(rolled_back)
    db.commit()

    _invalidate_flows_cache(account_id)
    log_event(db, actor_id=actor_id, action="call_flow.rolled_back", target_type="call_flow", target_id=flow.id,
               metadata={"restored_version": target_version, "new_version": rolled_back.version})
    publish_call_flow_rolled_back(
        account_id, call_flow_id=flow.id, restored_version=target_version, new_version=rolled_back.version,
    )

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_call_flow_rolled_back(
            db, account_id=account_id, account_email=owner.email, flow_name=flow.name, restored_version=target_version,
        )

    return rolled_back


def assign_to_number(
    db: Session, account_id: str, call_flow_id: str | None, phone_number_id: str, actor_id: str,
) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id, PhoneNumber.account_id == account_id).first()
    if number is None:
        raise NumberNotOwnedError(phone_number_id)
    if call_flow_id is not None:
        _get_flow(db, account_id, call_flow_id)  # raises CallFlowNotFoundError if not this account's
    number.call_flow_id = call_flow_id
    db.commit()
    _invalidate_flows_cache(account_id)
    log_event(db, actor_id=actor_id, action="call_flow.assigned", target_type="phone_number", target_id=number.id,
               metadata={"call_flow_id": call_flow_id})
    return number


# --- Call execution (read by app.media.voice at inbound-call time) ---


@dataclass
class ResolvedAction:
    kind: str  # "menu" | "forward" | "queue" | "voicemail" | "ai_receptionist" | "hangup"
    node_id: str | None = None
    prompt: str | None = None
    destinations: list[str] | None = None
    on_no_answer_node_id: str | None = None
    queue_id: str | None = None
    overflow_node_id: str | None = None
    message: str | None = None


def get_version_by_id(db: Session, version_id: str) -> CallFlowVersion | None:
    return db.query(CallFlowVersion).filter(CallFlowVersion.id == version_id).first()


def get_live_version(db: Session, phone_number: PhoneNumber) -> CallFlowVersion | None:
    if phone_number.call_flow_id is None:
        return None
    return (
        db.query(CallFlowVersion)
        .filter(
            CallFlowVersion.call_flow_id == phone_number.call_flow_id,
            CallFlowVersion.status == CallFlowVersionStatus.PUBLISHED,
        )
        .first()
    )


def _resolve(version: CallFlowVersion, node_id: str) -> ResolvedAction:
    by_id = {node["id"]: node for node in version.nodes}
    current = node_id
    for _ in range(MAX_RESOLUTION_HOPS):
        node = by_id.get(current)
        if node is None:
            raise NodeNotFoundError(current)
        node_type = node["type"]
        if node_type == "business_hours":
            # start/end round-trip through the JSON column as ISO strings
            # (see routing/routes.py save_draft's model_dump(mode="json"))
            # but may also arrive as native `time` objects when nodes are
            # constructed directly in Python (e.g. tests) - accept both.
            start = node["start"] if isinstance(node["start"], time) else time.fromisoformat(node["start"])
            end = node["end"] if isinstance(node["end"], time) else time.fromisoformat(node["end"])
            within = _is_within_business_hours(start, end, node["timezone"])
            current = node["within_node_id"] if within else node["outside_node_id"]
            continue
        if node_type == "menu":
            return ResolvedAction(kind="menu", node_id=current, prompt=node["prompt"])
        if node_type == "queue":
            return ResolvedAction(
                kind="queue",
                node_id=current,
                queue_id=node["queue_id"],
                overflow_node_id=node.get("overflow_node_id"),
            )
        if node_type == "forward":
            return ResolvedAction(
                kind="forward",
                node_id=current,
                destinations=node["destinations"],
                on_no_answer_node_id=node.get("on_no_answer_node_id"),
            )
        if node_type == "voicemail":
            return ResolvedAction(kind="voicemail")
        if node_type == "ai_receptionist":
            return ResolvedAction(kind="ai_receptionist")
        if node_type == "hangup":
            return ResolvedAction(kind="hangup", message=node.get("message"))
        raise NodeNotFoundError(f"unhandled node type '{node_type}' on published version {version.id}")
    # Only reachable if a published version somehow contains a pure
    # business_hours cycle - validate_flow's reachability check keeps this
    # out of anything that gets published, so this is a last-resort fallback
    # rather than a real call path.
    return ResolvedAction(kind="voicemail")


def resolve_entry(version: CallFlowVersion) -> ResolvedAction:
    return _resolve(version, version.entry_node_id)


def resolve_menu_input(version: CallFlowVersion, menu_node_id: str, digit: str | None) -> ResolvedAction:
    by_id = {node["id"]: node for node in version.nodes}
    node = by_id.get(menu_node_id)
    if node is None or node["type"] != "menu":
        raise NodeNotFoundError(menu_node_id)
    options = node.get("options") or {}
    if digit is None:
        next_id = node.get("timeout_node_id") or menu_node_id
    elif digit in options:
        next_id = options[digit]
    else:
        next_id = node.get("invalid_node_id") or menu_node_id
    return _resolve(version, next_id)


def resolve_forward_failover(version: CallFlowVersion, forward_node_id: str) -> ResolvedAction:
    by_id = {node["id"]: node for node in version.nodes}
    node = by_id.get(forward_node_id)
    if node is None or node["type"] != "forward":
        raise NodeNotFoundError(forward_node_id)
    fallback_id = node.get("on_no_answer_node_id")
    if not fallback_id:
        return ResolvedAction(kind="voicemail")
    return _resolve(version, fallback_id)


def resolve_specific_node(version: CallFlowVersion, node_id: str) -> ResolvedAction:
    """Public entry point for resolving an arbitrary node by id - used by
    media.voice when building a queue node's overflow TwiML inline (the
    overflow only needs resolving once, at enqueue time, not through a
    caller-input round trip like resolve_menu_input/resolve_forward_failover)."""
    return _resolve(version, node_id)
