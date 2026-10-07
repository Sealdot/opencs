"""Customer-service use cases over explicit ports; no vendor SDK or experiment imports."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from opencs.domain import Event, Interaction, transition_handoff

from .decisions import validate_decision
from .policy import ToolPolicy
from .ports import AgentPort, ChannelPort, EntityRepository, RuntimeStore, ToolPort


class Runtime:
    def __init__(
        self,
        *,
        repository: EntityRepository,
        store: RuntimeStore,
        agent: AgentPort,
        channel: ChannelPort,
        tools: ToolPort,
        policy: ToolPolicy,
        handoff_target: dict[str, str],
        handoff_reason: str,
        pending_resolution: str,
        max_steps: int = 8,
    ) -> None:
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be between 1 and 100")
        if set(handoff_target) != {"type", "id"} or not all(handoff_target.values()):
            raise ValueError("A configured handoff target is required")
        if not handoff_reason or not pending_resolution:
            raise ValueError("Example handoff reason and pending resolution must be configured")
        self.repository, self.store = repository, store
        self.agent, self.channel, self.tools, self.policy = agent, channel, tools, policy
        self.handoff_target = deepcopy(handoff_target)
        self.handoff_reason, self.pending_resolution = handoff_reason, pending_resolution
        self.max_steps = max_steps

    def _audit(self, event: Event, kind: str, **data: Any) -> str:
        return self.store.append_audit(
            event.tenant_id, event.conversation_id, kind, {"event_id": event.event_id, **data}
        )

    def _interaction(
        self, event: Event, identifier: str, kind: str, content: Any, *, source: str = "action"
    ) -> None:
        self.repository.add_interaction(
            Interaction(
                tenant_id=event.tenant_id,
                id="interaction:"
                + str(
                    uuid5(
                        NAMESPACE_URL,
                        json.dumps(
                            [event.tenant_id, event.conversation_id, source, identifier, kind]
                        ),
                    )
                ),
                conversation_id=event.conversation_id,
                customer_id=event.customer_id,
                kind=kind,
                content=content,
            )
        )

    def ingest(
        self, event: Event, *, trusted_tenant: str, can_transition: bool = False
    ) -> dict[str, Any]:
        if trusted_tenant != event.tenant_id:
            raise PermissionError("Event tenant differs from authenticated tenant")
        conversation = self.repository.get_conversation(trusted_tenant, event.conversation_id)
        if conversation.customer_id != event.customer_id:
            raise ValueError("Conversation/customer reference mismatch")
        if event.event_type.startswith("handoff_"):
            if not can_transition:
                raise PermissionError("Handoff callbacks require a trusted responsibility grant")
            return self._receive_handoff(event)
        if (
            event.event_type != "customer_message"
            or event.content_type != "text"
            or not isinstance(event.content, str)
        ):
            raise ValueError(
                "This application supports customer text and authorized handoff events"
            )
        created, prior = self.store.claim(event.to_dict())
        if not created:
            return prior
        with self.store.processing(trusted_tenant, event.conversation_id):
            try:
                self._audit(event, "event_received")
                self._interaction(
                    event, event.event_id, "customer_message", event.content, source="input"
                )
                result = self._serve(event)
            except Exception as exc:
                result = self._failed(event, exc)
            self.store.finish(trusted_tenant, event.event_id, result)
            return deepcopy(result)

    def _serve(self, event: Event) -> dict[str, Any]:
        tool_results: list[dict[str, Any]] = []
        for step in range(self.max_steps):
            with self.store.locked():
                conversation = self.repository.get_conversation(
                    event.tenant_id, event.conversation_id
                )
                if conversation.ai_paused:
                    self._audit(event, "agent_suppressed")
                    return self._result(event, "paused")
                history = self.repository.list_interactions(event.tenant_id, event.conversation_id)
            context = {
                "tenant_id": event.tenant_id,
                "customer_id": event.customer_id,
                "conversation_id": event.conversation_id,
                "step": step,
                "interactions": [
                    {"id": x.id, "kind": x.kind, "content": x.content} for x in history
                ],
                "tool_results": deepcopy(tool_results),
                "policy": {
                    "allowed_tools": sorted(self.policy.allowed_tools),
                    "forbidden_tools": sorted(self.policy.forbidden_tools),
                    "handoff_reason": self.handoff_reason,
                },
            }
            action_id = str(
                uuid5(NAMESPACE_URL, json.dumps([event.tenant_id, event.event_id, step]))
            )
            self._audit(event, "agent_requested", step=step, action_id=action_id)
            decision = validate_decision(self.agent.decide(deepcopy(context)))
            self._audit(event, "agent_decision", decision=decision)
            action = decision["action"]
            if action["type"] == "done":
                return self._result(event, "completed")
            if action["type"] == "tool":
                name, arguments = action["name"], action["arguments"]
                self._audit(
                    event, "tool_attempt", name=name, arguments=arguments, action_id=action_id
                )
                if not self.policy.permits(event.tenant_id, name):
                    self._audit(event, "tool_denied", name=name, action_id=action_id)
                    tool_results.append({"name": name, "status": "denied"})
                    continue
                # Do not initiate further actions on a stale AI plan after an external handoff.
                with self.store.locked():
                    if self.repository.get_conversation(
                        event.tenant_id, event.conversation_id
                    ).ai_paused:
                        self._audit(event, "tool_suppressed", name=name, action_id=action_id)
                        return self._result(event, "paused")
                    self._interaction(
                        event,
                        action_id + ":call",
                        "tool_call",
                        {"name": name, "arguments": arguments},
                    )
                    self._audit(
                        event, "tool_prepared", name=name, action_id=action_id, authorized=True
                    )
                    try:
                        value = self.tools.execute(
                            name,
                            deepcopy(arguments),
                            tenant_id=event.tenant_id,
                            action_id=action_id,
                        )
                        if not isinstance(value, dict):
                            raise ValueError("Tool must return a JSON object")
                        json.dumps(value, allow_nan=False)
                    except TimeoutError:
                        self._audit(event, "tool_unknown", name=name, action_id=action_id)
                        return self._result(event, "unknown")
                    except Exception as exc:
                        self._audit(
                            event,
                            "tool_failed",
                            name=name,
                            action_id=action_id,
                            error_type=type(exc).__name__,
                        )
                        tool_results.append({"name": name, "status": "failed"})
                    else:
                        ref = self._audit(
                            event, "tool_succeeded", name=name, value=value, action_id=action_id
                        )
                        tool_results.append(
                            {
                                "name": name,
                                "status": "succeeded",
                                "value": deepcopy(value),
                                "evidence_ref": ref,
                            }
                        )
                        self._interaction(
                            event,
                            action_id + ":result",
                            "tool_result",
                            {"name": name, "value": value},
                        )
                continue
            if action["type"] == "handoff":
                if action["reason"] != self.handoff_reason:
                    raise ValueError("Unconfigured handoff reason")
                with self.store.locked():
                    if self.repository.get_conversation(
                        event.tenant_id, event.conversation_id
                    ).ai_paused:
                        return self._result(event, "paused")
                    snapshot = self._snapshot(event, decision["intent"], action_id, tool_results)
                    self._request_handoff(event, snapshot)
                    self._audit(
                        event,
                        "handoff_delivery_prepared",
                        action_id=action_id,
                        target=self.handoff_target,
                    )
                    try:
                        delivery = self.channel.handoff(
                            tenant_id=event.tenant_id,
                            conversation_id=event.conversation_id,
                            snapshot=deepcopy(snapshot),
                            action_id=action_id,
                        )
                    except TimeoutError:
                        delivery = {"delivery": "unknown"}
                    except Exception as exc:
                        delivery = {"delivery": "failed", "error_type": type(exc).__name__}
                    delivery = self._delivery(delivery)
                    self._audit(
                        event, "handoff_delivery_result", action_id=action_id, result=delivery
                    )
                    # Delivery acknowledgement never means human acceptance.
                    # Do not automatically roll back responsibility state.
                    return self._result(event, "pending_human", delivery=delivery["delivery"])
            with self.store.locked():
                # Critical final guard after the potentially slow/reentrant agent call.
                if self.repository.get_conversation(
                    event.tenant_id, event.conversation_id
                ).ai_paused:
                    self._audit(event, "reply_suppressed", action_id=action_id)
                    return self._result(event, "paused")
                self._audit(
                    event,
                    "output_prepared",
                    action_id=action_id,
                    target={"tenant_id": event.tenant_id, "conversation_id": event.conversation_id},
                )
                try:
                    delivery = self.channel.send(
                        tenant_id=event.tenant_id,
                        conversation_id=event.conversation_id,
                        text=action["text"],
                        action_id=action_id,
                    )
                except TimeoutError:
                    delivery = {"delivery": "unknown"}
                except Exception as exc:
                    delivery = {"delivery": "failed", "error_type": type(exc).__name__}
                delivery = self._delivery(delivery)
                self._audit(event, "output_result", action_id=action_id, result=delivery)
                if delivery["delivery"] != "sent":
                    return self._result(event, delivery["delivery"])
                self._interaction(event, action_id + ":reply", "agent_message", action["text"])
        self._audit(event, "step_limit_reached", max_steps=self.max_steps)
        return self._result(event, "step_limit")

    @staticmethod
    def _delivery(value: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("delivery"), str)
            or value["delivery"] not in {"sent", "failed", "unknown"}
        ):
            return {"delivery": "unknown", "error": "invalid_channel_response"}
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            return {"delivery": "unknown", "error": "invalid_channel_response"}
        return deepcopy(value)

    def _result(self, event: Event, status: str, **extra: Any) -> dict[str, Any]:
        conversation = self.repository.get_conversation(event.tenant_id, event.conversation_id)
        return {
            "status": status,
            "event_id": event.event_id,
            "conversation_id": event.conversation_id,
            "resolution": conversation.resolution,
            "handoff": self.store.get_handoff(event.tenant_id, event.conversation_id),
            **extra,
        }

    def _snapshot(
        self, event: Event, intent: str, action_id: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        customer = self.repository.get_customer(event.tenant_id, event.customer_id)
        successes = [x for x in results if x["status"] == "succeeded"]
        return {
            "id": "ho_" + action_id,
            "tenant_id": event.tenant_id,
            "conversation_id": event.conversation_id,
            "state": "requested",
            "attempt": 1,
            "version": 1,
            "from": {"type": "ai_agent", "id": "support"},
            "to": deepcopy(self.handoff_target),
            "reason": {
                "code": self.handoff_reason,
                "description": "Configured human review is required",
            },
            "customer": {"id": customer.id, "organization_id": customer.organization_id},
            "intent": {"primary": intent, "confidence": None},
            "priority": {"level": "normal", "reason": "no_priority_override"},
            "summary": "Customer request: " + str(event.content),
            "facts": [
                json.dumps({"tool": x["name"], "result": x["value"]}, sort_keys=True)
                for x in successes
            ],
            "actions_completed": [x["name"] for x in successes],
            "actions_pending": [self.handoff_reason],
            "recommended_action": {"tool": "human_review", "parameters": {}},
            "evidence_refs": [x["evidence_ref"] for x in successes],
        }

    def _request_handoff(self, event: Event, snapshot: dict[str, Any]) -> None:
        if self.store.get_handoff(event.tenant_id, event.conversation_id) is not None:
            raise ValueError("Existing handoff requires an explicit transition")
        transition_handoff(None, snapshot, trigger="request", expected_version=0)
        conversation = self.repository.get_conversation(event.tenant_id, event.conversation_id)
        updated = replace(conversation, ai_paused=True, resolution=self.pending_resolution)
        self._interaction(event, snapshot["id"] + ":1", "handoff_requested", snapshot)
        self.store.save_handoff(snapshot)
        self.repository.update_conversation(updated)
        self._audit(event, "handoff_requested", snapshot=snapshot)
        self._audit(event, "resolution_recorded", resolution=self.pending_resolution)

    def _failed(self, event: Event, exc: Exception) -> dict[str, Any]:
        # Audit failure itself must not strand a claimed event in pending.
        recorded = True
        try:
            self._audit(event, "processing_failed", error_type=type(exc).__name__)
        except Exception:
            recorded = False
        return {
            "status": "failed",
            "event_id": event.event_id,
            "error": type(exc).__name__,
            "failure_audit_recorded": recorded,
        }

    def _receive_handoff(self, event: Event) -> dict[str, Any]:
        with self.store.locked():
            created, prior = self.store.claim(event.to_dict())
            if not created:
                return prior
            try:
                self._audit(event, "event_received")
                result = self._apply_handoff(event)
            except Exception as exc:
                result = self._failed(event, exc)
            self.store.finish(event.tenant_id, event.event_id, result)
            return result

    def _apply_handoff(self, event: Event) -> dict[str, Any]:
        previous = self.store.get_handoff(event.tenant_id, event.conversation_id)
        snapshot = event.content
        # Validate the whole transition before any responsibility-state mutation.
        try:
            if not isinstance(snapshot, dict):
                raise ValueError("Handoff snapshot must be an object")
            if previous is not None:
                for key in previous.keys() - {"state", "version", "attempt", "evidence_refs"}:
                    if snapshot[key] != previous[key]:
                        raise ValueError("Callback cannot rewrite handoff context")
            transition_handoff(
                previous,
                snapshot,
                trigger=event.metadata.get("trigger", ""),
                expected_version=event.metadata.get("expected_version", -1),
            )
            if previous is None:
                customer = self.repository.get_customer(event.tenant_id, event.customer_id)
                if snapshot["customer"]["organization_id"] != customer.organization_id:
                    raise ValueError("Handoff organization differs from trusted customer")
                if (
                    snapshot["to"] != self.handoff_target
                    or snapshot["reason"]["code"] != self.handoff_reason
                ):
                    raise ValueError("Unconfigured handoff target or reason")
        except (ValueError, KeyError, TypeError):
            self._audit(event, "handoff_rejected")
            return {"status": "rejected", "event_id": event.event_id}
        if previous is None:
            self._request_handoff(event, snapshot)
        else:
            self._interaction(event, event.event_id, event.event_type, snapshot, source="input")
            self.store.save_handoff(snapshot)
            conversation = self.repository.get_conversation(event.tenant_id, event.conversation_id)
            self.repository.update_conversation(replace(conversation, ai_paused=True))
            self._audit(event, event.event_type, snapshot=snapshot)
        return self._result(event, "handoff_updated")
