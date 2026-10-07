"""Offline domain contract tests: isolation, mappings and lifecycle boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from opencs.domain import (
    Conversation,
    Customer,
    Event,
    Identity,
    Interaction,
    Organization,
    Tenant,
    transition_handoff,
    validate_handoff,
)
from opencs.domain.events import EVENT_TYPES
from opencs.domain.handoff import TRANSITIONS
from opencs.persistence.memory import MemoryRepository

ROOT = Path(__file__).resolve().parents[1]


def fixture(name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((ROOT / "tests" / "fixtures" / name).read_text())
    return result


def repository() -> MemoryRepository:
    repo = MemoryRepository()
    repo.add_tenant(Tenant("t1"))
    repo.add_tenant(Tenant("t2"))
    return repo


def test_explicit_mapping_is_atomic_and_tenant_scoped() -> None:
    repo = repository()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(lambda _: repo.resolve_external("t1", "im", "a:1", "a:9"), range(64))
        )
    assert len({result.id for result in results}) == 1
    assert len({result.customer_id for result in results}) == 1
    cross_tenant = repo.resolve_external("t2", "im", "a:1", "a:9")
    cross_provider = repo.resolve_external("t1", "voice", "a:1", "a:9")
    second_conversation = repo.resolve_external("t1", "im", "a:2", "a:9")
    assert cross_tenant.customer_id != results[0].customer_id
    assert cross_provider.customer_id != results[0].customer_id
    assert second_conversation.customer_id == results[0].customer_id
    assert second_conversation.id != results[0].id
    with pytest.raises(ValueError, match="mapping conflicts"):
        repo.resolve_external("t1", "im", "a:1", "a:other")
    assert repo.resolve_external("t1", "im", "a:1", "a:9") == results[0]


def test_explicit_identity_is_reused_without_name_inference() -> None:
    repo = repository()
    repo.add_customer(Customer("t1", "manual", name="Same Name"))
    repo.add_customer(Customer("t1", "separate", name="Same Name"))
    repo.add_identity(Identity("t1", "ext", "manual", "external_id", "account:9", "im"))
    conv = repo.resolve_external("t1", "im", "account:1", "account:9")
    assert conv.customer_id == "manual"
    other = repo.resolve_external("t1", "im", "account:2", "account:10")
    assert other.customer_id not in {"manual", "separate"}


def test_reopen_preserves_mapping_history_and_ai_pause() -> None:
    repo = repository()
    conv = repo.resolve_external("t1", "im", "a:1", "a:9")
    repo.add_interaction(
        Interaction("t1", "i1", conv.id, conv.customer_id, "customer_message", "help")
    )
    repo.update_conversation(replace(conv, resolution="pending_human", ai_paused=True))
    reopened = repo.resolve_external("t1", "im", "a:1", "a:9")
    assert reopened.id == conv.id
    assert reopened.resolution == "pending_human"
    assert reopened.ai_paused is True
    assert len(repo.list_interactions("t1", conv.id)) == 1


def test_repository_rejects_cross_tenant_references_and_identity_duplicates() -> None:
    repo = repository()
    repo.add_organization(Organization("t1", "org"))
    repo.add_customer(Customer("t1", "customer", "org"))
    repo.add_customer(Customer("t1", "other"))
    repo.add_customer(Customer("t2", "customer"))
    with pytest.raises(ValueError, match="Organization"):
        repo.add_customer(Customer("t2", "cross", "org"))
    with pytest.raises(ValueError, match="Customer"):
        repo.add_conversation(Conversation("t2", "cross", "other"))
    with pytest.raises(ValueError, match="Customer"):
        repo.add_identity(Identity("t2", "cross", "other", "email", "a@example.test"))
    first = Identity("t1", "email", "customer", "email", "a@example.test")
    repo.add_identity(first)
    with pytest.raises(ValueError, match="Identity"):
        repo.add_identity(replace(first, id="new", customer_id="other"))
    repo.add_identity(replace(first, tenant_id="t2"))
    repo.add_conversation(Conversation("t1", "conv", "customer"))
    with pytest.raises(ValueError, match="cannot change"):
        repo.update_conversation(Conversation("t1", "conv", "other"))
    with pytest.raises(KeyError):
        repo.get_conversation("t2", "conv")
    with pytest.raises(KeyError):
        repo.list_interactions("t2", "conv")
    with pytest.raises(ValueError, match="subject"):
        repo.add_interaction(Interaction("t2", "i", "conv", "customer", "system", None))


def test_repository_snapshots_do_not_expose_internal_mutable_content() -> None:
    repo = repository()
    conv = repo.resolve_external("t1", "im", "a:1", "a:9")
    content: dict[str, Any] = {"nested": ["original"]}
    interaction = Interaction("t1", "i", conv.id, conv.customer_id, "tool_result", content)
    repo.add_interaction(interaction)
    content["nested"].append("caller mutation")
    result = repo.list_interactions("t1", conv.id)[0]
    assert isinstance(result.content, dict)
    result.content["nested"].append("reader mutation")
    assert repo.list_interactions("t1", conv.id)[0].content == {"nested": ["original"]}
    with pytest.raises(ValueError, match="already exists"):
        repo.add_interaction(interaction)
    with pytest.raises(FrozenInstanceError):
        conv.id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_all_seventeen_event_types_match_the_envelope_contract(event_type: str) -> None:
    payload = fixture("event.json")
    payload["event"]["type"] = event_type
    if event_type.startswith("handoff_"):
        handoff = fixture("handoff.json")
        handoff["state"] = event_type.removeprefix("handoff_")
        handoff["tenant_id"] = payload["tenant_id"]
        handoff["conversation_id"] = payload["conversation_id"]
        handoff["customer"]["id"] = payload["customer_id"]
        payload["event"].update(content_type="application/json", content=handoff)
    event = Event.from_dict(payload)
    assert event.event_type == event_type
    assert event.to_dict() == payload
    assert event.event_id == payload["event_id"]
    assert event.tenant_id == payload["tenant_id"]
    assert event.conversation_id == payload["conversation_id"]
    assert event.customer_id == payload["customer_id"]
    assert event.channel_type == payload["channel"]["type"]
    assert event.provider == payload["channel"]["provider"]
    assert event.occurred_at == payload["occurred_at"]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-05",
        "2026-09-05T01:00:00",
        "2026-09-05 01:00:00Z",
        "2026-02-30T01:00:00Z",
        "2026-09-05T01:00:00+25:00",
        "2026-09-05T01:00:00+00:60",
        "",
        None,
    ],
)
def test_event_requires_valid_timezone_aware_datetime(timestamp: Any) -> None:
    payload = fixture("event.json")
    payload["occurred_at"] = timestamp
    with pytest.raises(ValueError):
        Event.from_dict(payload)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), {1: "bad"}, ("tuple",), object()])
def test_event_rejects_non_json_metadata(invalid: Any) -> None:
    payload = fixture("event.json")
    payload["metadata"] = {"invalid": invalid}
    with pytest.raises(ValueError):
        Event.from_dict(payload)


def test_event_strict_fields_subjects_and_snapshot_ownership() -> None:
    payload = fixture("handoff-event.json")
    event = Event.from_dict(payload)
    original = deepcopy(payload)
    payload["event"]["content"]["facts"].append("changed")
    content = event.content
    assert isinstance(content, dict)
    content["facts"].clear()
    event.metadata["untrusted"] = True
    assert event.to_dict() == original
    for path, value in [
        (("extra",), 1),
        (("event", "extra"), 1),
        (("channel", "extra"), 1),
        (("tenant_id",), "different"),
        (("customer_id",), "different"),
        (("event", "type"), "handoff_completed"),
        (("event", "content_type"), "text"),
    ]:
        invalid = deepcopy(original)
        target = invalid
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = value
        with pytest.raises(ValueError):
            Event.from_dict(invalid)


def test_runtime_handoff_edges_match_approved_machine_file() -> None:
    lifecycle = json.loads((ROOT / "docs/specs/handoff-lifecycle.json").read_text())
    assert TRANSITIONS == {
        (edge["from"], edge["to"], edge["trigger"]) for edge in lifecycle["transitions"]
    }


@pytest.mark.parametrize("previous_state,current_state,trigger", sorted(TRANSITIONS))
def test_all_approved_handoff_edges(previous_state: str, current_state: str, trigger: str) -> None:
    previous = fixture("handoff.json")
    previous.update(state=previous_state, version=4)
    current = deepcopy(previous)
    current.update(state=current_state, version=5, attempt=2 if trigger == "retry" else 1)
    transition_handoff(previous, current, trigger=trigger, expected_version=4)


def test_handoff_initial_retry_conflicts_and_evidence() -> None:
    initial = fixture("handoff.json")
    transition_handoff(None, initial, trigger="request", expected_version=0)
    for active_id, expected in [("other", 0), (None, 1), (None, True)]:
        with pytest.raises(ValueError):
            transition_handoff(
                None,
                initial,
                trigger="request",
                expected_version=expected,
                active_handoff_id=active_id,
            )
    accepted = deepcopy(initial)
    accepted.update(state="accepted", version=2)
    with pytest.raises(ValueError, match="evidence trigger"):
        transition_handoff(initial, accepted, trigger="assignment", expected_version=1)
    for field, value in [("attempt", 2), ("version", 1), ("tenant_id", "cross")]:
        invalid = deepcopy(accepted)
        invalid[field] = value
        with pytest.raises(ValueError):
            transition_handoff(initial, invalid, trigger="human_acceptance", expected_version=1)
    accepted["evidence_refs"] = []
    with pytest.raises(ValueError, match="evidence"):
        validate_handoff(accepted)


@pytest.mark.parametrize(
    "old", ["requested", "accepted", "in_progress", "completed", "cancelled", "failed"]
)
@pytest.mark.parametrize(
    "new", ["requested", "accepted", "in_progress", "completed", "cancelled", "failed"]
)
def test_illegal_handoff_state_edges(old: str, new: str) -> None:
    if any(start == old and finish == new for start, finish, _ in TRANSITIONS):
        return
    previous = fixture("handoff.json")
    previous["state"] = old
    current = deepcopy(previous)
    current.update(state=new, version=2)
    with pytest.raises(ValueError):
        transition_handoff(previous, current, trigger="request", expected_version=1)
