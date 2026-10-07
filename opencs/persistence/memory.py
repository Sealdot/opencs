"""Thread-safe entity repository for offline tests, not durable persistence."""

from copy import deepcopy
from threading import RLock
from uuid import uuid4

from opencs.domain import Conversation, Customer, Identity, Interaction, Organization, Tenant
from opencs.domain.validation import nonempty


class MemoryRepository:
    """Tenant-scoped references, atomic external mapping, and defensive snapshots."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._tenants: dict[str, Tenant] = {}
        self._organizations: dict[tuple[str, str], Organization] = {}
        self._customers: dict[tuple[str, str], Customer] = {}
        self._identities: dict[tuple[str, str], Identity] = {}
        self._identity_values: dict[tuple[str, str, str, str], str] = {}
        self._conversations: dict[tuple[str, str], Conversation] = {}
        self._interactions: dict[tuple[str, str], Interaction] = {}
        self._external_conversations: dict[tuple[str, str, str], str] = {}

    def _tenant(self, tenant_id: str) -> None:
        if tenant_id not in self._tenants:
            raise ValueError("Unknown tenant")

    def add_tenant(self, tenant: Tenant) -> None:
        with self._lock:
            if tenant.id in self._tenants:
                raise ValueError("Tenant already exists")
            self._tenants[tenant.id] = deepcopy(tenant)

    def add_organization(self, organization: Organization) -> None:
        with self._lock:
            self._tenant(organization.tenant_id)
            key = (organization.tenant_id, organization.id)
            if key in self._organizations:
                raise ValueError("Organization already exists")
            self._organizations[key] = deepcopy(organization)

    def add_customer(self, customer: Customer) -> None:
        with self._lock:
            self._tenant(customer.tenant_id)
            key = (customer.tenant_id, customer.id)
            if key in self._customers:
                raise ValueError("Customer already exists")
            if (
                customer.organization_id is not None
                and (customer.tenant_id, customer.organization_id) not in self._organizations
            ):
                raise ValueError("Organization does not belong to this tenant")
            self._customers[key] = deepcopy(customer)

    def add_identity(self, identity: Identity) -> None:
        with self._lock:
            if (identity.tenant_id, identity.customer_id) not in self._customers:
                raise ValueError("Customer does not belong to this tenant")
            key = (identity.tenant_id, identity.id)
            value_key = (identity.tenant_id, identity.type, identity.provider, identity.value)
            if key in self._identities or value_key in self._identity_values:
                raise ValueError("Identity already exists within tenant")
            self._identities[key] = deepcopy(identity)
            self._identity_values[value_key] = identity.customer_id

    def add_conversation(self, conversation: Conversation) -> None:
        with self._lock:
            if (conversation.tenant_id, conversation.customer_id) not in self._customers:
                raise ValueError("Customer does not belong to this tenant")
            key = (conversation.tenant_id, conversation.id)
            if key in self._conversations:
                raise ValueError("Conversation already exists")
            self._conversations[key] = deepcopy(conversation)

    def get_customer(self, tenant: str, id: str) -> Customer:
        with self._lock:
            return deepcopy(self._customers[(tenant, id)])

    def get_conversation(self, tenant: str, id: str) -> Conversation:
        with self._lock:
            return deepcopy(self._conversations[(tenant, id)])

    def update_conversation(self, conversation: Conversation) -> None:
        with self._lock:
            key = (conversation.tenant_id, conversation.id)
            previous = self._conversations[key]
            if conversation.customer_id != previous.customer_id:
                raise ValueError("Conversation customer cannot change")
            self._conversations[key] = deepcopy(conversation)

    def resolve_external(
        self, tenant: str, provider: str, external_conversation_id: str, external_contact_id: str
    ) -> Conversation:
        for name, value in (
            ("tenant", tenant),
            ("provider", provider),
            ("external_conversation_id", external_conversation_id),
            ("external_contact_id", external_contact_id),
        ):
            nonempty(value, name)
        with self._lock:
            self._tenant(tenant)
            key = (tenant, provider, external_conversation_id)
            identity_key = (tenant, "external_id", provider, external_contact_id)
            customer_id = self._identity_values.get(identity_key)
            if key in self._external_conversations:
                conversation = self._conversations[(tenant, self._external_conversations[key])]
                if customer_id != conversation.customer_id:
                    raise ValueError("External conversation contact mapping conflicts")
                return deepcopy(conversation)
            if customer_id is None:
                customer_id = f"cus_{uuid4().hex}"
                self.add_customer(Customer(tenant, customer_id))
                self.add_identity(
                    Identity(
                        tenant,
                        f"ident_{uuid4().hex}",
                        customer_id,
                        "external_id",
                        external_contact_id,
                        provider,
                    )
                )
            conversation = Conversation(tenant, f"conv_{uuid4().hex}", customer_id)
            self.add_conversation(conversation)
            self._external_conversations[key] = conversation.id
            return deepcopy(conversation)

    def add_interaction(self, interaction: Interaction) -> None:
        with self._lock:
            conversation = self._conversations.get(
                (interaction.tenant_id, interaction.conversation_id)
            )
            if conversation is None or conversation.customer_id != interaction.customer_id:
                raise ValueError("Interaction subject does not match conversation")
            key = (interaction.tenant_id, interaction.id)
            if key in self._interactions:
                raise ValueError("Interaction already exists")
            self._interactions[key] = deepcopy(interaction)

    def list_interactions(self, tenant: str, conversation: str) -> list[Interaction]:
        with self._lock:
            if (tenant, conversation) not in self._conversations:
                raise KeyError((tenant, conversation))
            return deepcopy(
                [
                    entry
                    for entry in self._interactions.values()
                    if entry.tenant_id == tenant and entry.conversation_id == conversation
                ]
            )
