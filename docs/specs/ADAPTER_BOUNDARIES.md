# Adapter Boundaries

Release clarification, 2026-10-07: this restates the supplied kit's boundary rules
for the existing offline runtime; it adds no provider or domain semantics. Derived
specification provenance remains subject to the release license audit.

Core owns conversation, identity/customer/organization, routing, policy, handoff,
resolution and evaluation semantics. Adapters own vendor parsing, message sending,
provider request/response mapping and transport. Core must not import vendor code.
Application depends on provider-neutral ports; assembly explicitly injects adapters.

A Chatwoot webhook format change belongs in its adapter or the upstream project.
Refund approval requirements belong in OpenCS policy; routing criteria belong in
OpenCS routing; judging a handoff belongs in evaluation. Industry workflows belong
in examples and company-specific rules in configuration/extensions. Infrastructure
fixes should be contributed upstream without moving customer-service semantics
out of OpenCS.

Existing Chatwoot/Responses/HTTP contracts are included in this alpha. LiveKit
callbacks and transfer execution remain future adapter concerns; no Voice runtime
is shipped. New providers and production integrations are paused for this release.
Upstream contribution patches/checkouts remain in the original private workspace
and are excluded from the public manifest.
