"""Landlord-network graph traversal over the Companies House API.

UK property landlords routinely hold stock through a fan of single-purpose
vehicles controlled by a small cluster of directors and corporate PSCs.
Reconstructing that cluster from Companies House is a repeated pattern —
this module encapsulates it behind a single entry point so actors, agent
tools, and notebooks all share one traversal:

    seed company
      ├── officers   (via /company/{n}/officers)
      │     └── appointments (via /officers/{id}/appointments) -> other cos
      └── PSCs       (via /company/{n}/persons-with-significant-control)
            └── corporate PSC's company  (when registration_number present)

Depth is interpreted as hop distance from the seed node:

* ``depth=1`` — seed company + its officers + its PSCs (no outward expansion).
* ``depth=2`` — also pull each officer's other appointments and each
  corporate PSC's own profile. This is the canonical "landlord portfolio"
  layer.
* ``depth>=3`` — recursively fetch officers/PSCs for every newly reached
  company. Fan-out can blow up, so the traversal respects
  ``max_companies`` and ``max_officers`` safety caps.

The result is a transport-agnostic :class:`LandlordGraph` built from
`LandlordGraphNode` + `LandlordGraphEdge` records that downstream code
can render as JSON, a networkx graph, or a UI.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uk_property_apis._core.exceptions import NotFoundError, UKPropertyAPIError

if TYPE_CHECKING:
    from uk_property_apis.companies_house.client import CompaniesHouseClient
    from uk_property_apis.companies_house.models import (
        PSC,
        Officer,
        OfficerAppointment,
    )

logger = logging.getLogger(__name__)


NodeKind = Literal["company", "officer", "psc"]
EdgeRelation = Literal["officer_of", "psc_of"]


class LandlordGraphNode(BaseModel):
    """One node in a landlord graph.

    ``identifier`` is the canonical join key for its kind:

    * ``company`` → Companies House company number.
    * ``officer`` → Companies House officer ID (from ``/officers/<id>``).
    * ``psc``     → Companies House PSC ID when available, else a
      deterministic ``"kind:hash"`` fallback.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NodeKind
    identifier: str = Field(..., description="Stable dedupe key within ``kind``.")
    label: str = Field(..., description="Human-readable display name.")
    depth: int = Field(..., ge=0, description="Hop distance from the seed node.")
    data: dict[str, Any] = Field(default_factory=dict)


class LandlordGraphEdge(BaseModel):
    """One edge in a landlord graph.

    Edges are always directed from the *person / controller* to the company
    being governed (``officer_of`` / ``psc_of``), regardless of which side
    was discovered first during traversal.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(
        ..., description="``{kind}:{identifier}`` of the controlling node."
    )
    target_id: str = Field(
        ..., description="``{kind}:{identifier}`` of the controlled company."
    )
    relation: EdgeRelation
    role: str | None = Field(default=None, description="officer_role when known.")
    active: bool | None = Field(
        default=None,
        description="False when the officer has resigned / the PSC has ceased.",
    )
    since: str | None = Field(default=None, description="Appointed / notified date.")
    until: str | None = Field(default=None, description="Resigned / ceased date.")


class LandlordGraph(BaseModel):
    """Materialised landlord-network graph for a seed company."""

    model_config = ConfigDict(extra="forbid")

    seed_company_number: str
    depth: int = Field(..., ge=1)
    nodes: list[LandlordGraphNode] = Field(default_factory=list)
    edges: list[LandlordGraphEdge] = Field(default_factory=list)
    truncated: bool = Field(
        default=False,
        description=(
            "True when traversal stopped early due to ``max_companies`` / "
            "``max_officers`` caps rather than natural completion."
        ),
    )

    def companies(self) -> list[LandlordGraphNode]:
        return [n for n in self.nodes if n.kind == "company"]

    def officers(self) -> list[LandlordGraphNode]:
        return [n for n in self.nodes if n.kind == "officer"]

    def pscs(self) -> list[LandlordGraphNode]:
        return [n for n in self.nodes if n.kind == "psc"]


def _node_key(kind: NodeKind, identifier: str) -> str:
    return f"{kind}:{identifier}"


def _officer_node(officer: Officer, *, officer_id: str, depth: int) -> LandlordGraphNode:
    data: dict[str, Any] = {
        "nationality": officer.nationality,
        "occupation": officer.occupation,
    }
    return LandlordGraphNode(
        kind="officer",
        identifier=officer_id,
        label=officer.name or officer_id,
        depth=depth,
        data={k: v for k, v in data.items() if v is not None},
    )


def _psc_node(
    psc: PSC, *, identifier: str, depth: int
) -> LandlordGraphNode:
    data: dict[str, Any] = {
        "kind": psc.kind,
        "nationality": psc.nationality,
        "country_of_residence": psc.country_of_residence,
        "natures_of_control": psc.natures_of_control,
        "is_corporate": psc.is_corporate,
    }
    return LandlordGraphNode(
        kind="psc",
        identifier=identifier,
        label=psc.name or psc.kind or identifier,
        depth=depth,
        data={k: v for k, v in data.items() if v is not None},
    )


def _company_node(
    *, number: str, name: str | None, depth: int, extras: dict[str, Any] | None = None
) -> LandlordGraphNode:
    data: dict[str, Any] = {}
    if extras:
        data.update({k: v for k, v in extras.items() if v is not None})
    return LandlordGraphNode(
        kind="company",
        identifier=number,
        label=name or number,
        depth=depth,
        data=data,
    )


def _officer_edge(
    *,
    officer_identifier: str,
    company_number: str,
    officer: Officer | OfficerAppointment,
) -> LandlordGraphEdge:
    active = True
    resigned = getattr(officer, "resigned_on", None)
    if resigned:
        active = False
    return LandlordGraphEdge(
        source_id=_node_key("officer", officer_identifier),
        target_id=_node_key("company", company_number),
        relation="officer_of",
        role=getattr(officer, "officer_role", None),
        active=active,
        since=getattr(officer, "appointed_on", None),
        until=resigned,
    )


def _psc_edge(
    *, psc: PSC, psc_identifier: str, company_number: str
) -> LandlordGraphEdge:
    return LandlordGraphEdge(
        source_id=_node_key("psc", psc_identifier),
        target_id=_node_key("company", company_number),
        relation="psc_of",
        role=None,
        active=psc.ceased_on is None,
        since=psc.notified_on,
        until=psc.ceased_on,
    )


def _derive_psc_identifier(psc: PSC) -> str:
    """Pick a stable dedupe key for a PSC.

    Preference order:

    1. ``psc_id`` extracted from ``links.self`` (the canonical Companies
       House identifier within one company);
    2. a corporate registration number when the PSC is another company;
    3. a deterministic hash of ``kind + name`` so individual PSCs that
       lack links still dedupe within the graph.
    """

    canonical = psc.psc_id
    if canonical:
        return canonical
    corporate = psc.corporate_company_number
    if corporate:
        return f"company:{corporate}"
    fingerprint = f"{(psc.kind or '').lower()}|{(psc.name or '').strip().lower()}"
    return f"fp:{fingerprint}"


@dataclass
class _Builder:
    """Mutable working set for one traversal.

    Kept out of :class:`LandlordGraph` because callers shouldn't mutate the
    published graph, and keeping index maps internal lets us change them
    later without breaking the public surface.
    """

    depth: int
    max_companies: int
    max_officers: int
    expand_corporate_pscs: bool
    nodes: dict[str, LandlordGraphNode]
    edges: list[LandlordGraphEdge]
    edge_keys: set[tuple[str, str, EdgeRelation]]
    companies_visited: set[str]
    officers_visited: set[str]
    pending_companies: list[tuple[str, int]]
    pending_officers: list[tuple[str, int]]
    truncated: bool

    @classmethod
    def new(
        cls,
        *,
        depth: int,
        max_companies: int,
        max_officers: int,
        expand_corporate_pscs: bool,
    ) -> _Builder:
        return cls(
            depth=depth,
            max_companies=max_companies,
            max_officers=max_officers,
            expand_corporate_pscs=expand_corporate_pscs,
            nodes={},
            edges=[],
            edge_keys=set(),
            companies_visited=set(),
            officers_visited=set(),
            pending_companies=[],
            pending_officers=[],
            truncated=False,
        )

    def _count(self, kind: NodeKind) -> int:
        return sum(1 for n in self.nodes.values() if n.kind == kind)

    def add_node(self, node: LandlordGraphNode) -> str | None:
        """Insert ``node`` respecting per-kind caps.

        Returns the canonical ``{kind}:{id}`` key when the node is present
        in the graph after the call, or ``None`` when the per-kind cap
        forced us to reject it (in which case ``truncated=True`` is set).
        Callers should skip edges / expansion for rejected nodes.
        """

        key = _node_key(node.kind, node.identifier)
        existing = self.nodes.get(key)
        if existing is not None:
            if node.depth < existing.depth:
                self.nodes[key] = node
            return key
        if node.kind == "company" and self._count("company") >= self.max_companies:
            self.truncated = True
            return None
        if node.kind == "officer" and self._count("officer") >= self.max_officers:
            self.truncated = True
            return None
        self.nodes[key] = node
        return key

    def add_edge(self, edge: LandlordGraphEdge) -> None:
        signature = (edge.source_id, edge.target_id, edge.relation)
        if signature in self.edge_keys:
            return
        if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
            return
        self.edge_keys.add(signature)
        self.edges.append(edge)

    def enqueue_company(self, number: str, next_depth: int) -> None:
        if next_depth > self.depth:
            return
        if number in self.companies_visited:
            return
        self.companies_visited.add(number)
        self.pending_companies.append((number, next_depth))

    def enqueue_officer(self, officer_id: str, next_depth: int) -> None:
        if next_depth > self.depth:
            return
        if officer_id in self.officers_visited:
            return
        self.officers_visited.add(officer_id)
        self.pending_officers.append((officer_id, next_depth))


async def _expand_company(
    client: CompaniesHouseClient,
    *,
    number: str,
    depth: int,
    builder: _Builder,
) -> None:
    """Fetch officers + PSCs for ``number`` and wire them into the builder."""

    officers_task = client.get_officers(number, items_per_page=100)
    psc_task = client.get_psc(number, items_per_page=100)
    officers_res, psc_res = await asyncio.gather(
        officers_task, psc_task, return_exceptions=True
    )

    if isinstance(officers_res, Exception):
        logger.warning("officers fetch failed for %s: %s", number, officers_res)
    else:
        for officer in officers_res.items:
            officer_id = officer.officer_id
            if not officer_id:
                continue
            added = builder.add_node(
                _officer_node(officer, officer_id=officer_id, depth=depth)
            )
            if added is None:
                continue
            builder.add_edge(
                _officer_edge(
                    officer_identifier=officer_id,
                    company_number=number,
                    officer=officer,
                )
            )
            builder.enqueue_officer(officer_id, depth + 1)

    if isinstance(psc_res, Exception):
        logger.warning("PSC fetch failed for %s: %s", number, psc_res)
    else:
        for psc in psc_res.items:
            identifier = _derive_psc_identifier(psc)
            psc_added = builder.add_node(
                _psc_node(psc, identifier=identifier, depth=depth)
            )
            if psc_added is None:
                continue
            builder.add_edge(
                _psc_edge(
                    psc=psc, psc_identifier=identifier, company_number=number
                )
            )
            if builder.expand_corporate_pscs and psc.is_corporate:
                corp_no = psc.corporate_company_number
                if not corp_no:
                    continue
                company_added = builder.add_node(
                    _company_node(
                        number=corp_no,
                        name=psc.name,
                        depth=depth,
                        extras={"via": "corporate_psc"},
                    )
                )
                if company_added is None:
                    continue
                builder.enqueue_company(corp_no, depth)


async def _expand_officer(
    client: CompaniesHouseClient,
    *,
    officer_id: str,
    depth: int,
    builder: _Builder,
) -> None:
    """Fetch appointments for an officer and fan out to new companies."""

    try:
        response = await client.get_officer_appointments(officer_id, items_per_page=100)
    except NotFoundError:
        logger.info("officer %s has no appointments endpoint", officer_id)
        return
    except UKPropertyAPIError as exc:
        logger.warning("appointments fetch failed for %s: %s", officer_id, exc)
        return

    officer_label = response.name
    if officer_label:
        existing_key = _node_key("officer", officer_id)
        existing = builder.nodes.get(existing_key)
        if existing is not None and not existing.label:
            builder.nodes[existing_key] = existing.model_copy(
                update={"label": officer_label}
            )

    for appointment in response.items:
        company_number = appointment.company_number
        if not company_number:
            continue
        name: str | None = None
        status: str | None = None
        appointed_to = appointment.appointed_to
        if hasattr(appointed_to, "company_name"):
            name = appointed_to.company_name  # type: ignore[union-attr]
            status = appointed_to.company_status  # type: ignore[union-attr]
        elif isinstance(appointed_to, dict):
            raw = appointed_to.get("company_name")
            name = raw if isinstance(raw, str) else None
            raw_status = appointed_to.get("company_status")
            status = raw_status if isinstance(raw_status, str) else None
        added = builder.add_node(
            _company_node(
                number=company_number,
                name=name,
                depth=depth,
                extras={"status": status} if status else None,
            )
        )
        if added is None:
            continue
        builder.add_edge(
            _officer_edge(
                officer_identifier=officer_id,
                company_number=company_number,
                officer=appointment,
            )
        )
        builder.enqueue_company(company_number, depth + 1)


async def build_landlord_graph(
    client: CompaniesHouseClient,
    seed_company_number: str,
    *,
    depth: int = 2,
    max_companies: int = 50,
    max_officers: int = 200,
    expand_corporate_pscs: bool = True,
) -> LandlordGraph:
    """Build the landlord-network graph rooted at ``seed_company_number``.

    Parameters
    ----------
    client:
        An authenticated :class:`CompaniesHouseClient`. The caller owns its
        lifetime; ``build_landlord_graph`` does not enter or exit its async
        context.
    seed_company_number:
        Starting company number (e.g. SPV or property-holding entity).
    depth:
        Hop distance from the seed (``depth=1`` returns just its officers
        and PSCs; ``depth=2`` also fetches each officer's other
        appointments and each corporate PSC's profile).
    max_companies, max_officers:
        Safety caps. When either is hit the returned graph has
        ``truncated=True`` and callers can decide whether to re-run with
        larger budgets.
    expand_corporate_pscs:
        When True (default), follow a corporate PSC's registration number
        through to its own officer/PSC fan-out. Set False for plain
        "officers only" portfolios.

    Returns
    -------
    LandlordGraph
        De-duplicated nodes + directed edges describing the immediate
        control network around the seed.
    """

    if depth < 1:
        raise ValueError("depth must be >= 1")
    seed = seed_company_number.strip()
    if not seed:
        raise ValueError("seed_company_number must be non-empty")

    builder = _Builder.new(
        depth=depth,
        max_companies=max_companies,
        max_officers=max_officers,
        expand_corporate_pscs=expand_corporate_pscs,
    )

    try:
        seed_profile = await client.get_company(seed)
    except NotFoundError:
        builder.add_node(_company_node(number=seed, name=None, depth=0))
    else:
        builder.add_node(
            _company_node(
                number=seed_profile.company_number,
                name=seed_profile.company_name,
                depth=0,
                extras={
                    "status": seed_profile.company_status,
                    "type": seed_profile.type,
                    "date_of_creation": seed_profile.date_of_creation,
                    "sic_codes": seed_profile.sic_codes,
                },
            )
        )
        seed = seed_profile.company_number

    builder.companies_visited.add(seed)
    builder.pending_companies.append((seed, 0))

    while builder.pending_companies or builder.pending_officers:
        if builder.pending_companies:
            number, cur_depth = builder.pending_companies.pop(0)
            if cur_depth >= builder.depth:
                continue
            await _expand_company(
                client, number=number, depth=cur_depth + 1, builder=builder
            )
            continue
        officer_id, cur_depth = builder.pending_officers.pop(0)
        if cur_depth > builder.depth:
            continue
        await _expand_officer(
            client, officer_id=officer_id, depth=cur_depth, builder=builder
        )

    return LandlordGraph(
        seed_company_number=seed,
        depth=depth,
        nodes=sorted(builder.nodes.values(), key=lambda n: (n.depth, n.kind, n.identifier)),
        edges=builder.edges,
        truncated=builder.truncated,
    )


__all__ = [
    "EdgeRelation",
    "LandlordGraph",
    "LandlordGraphEdge",
    "LandlordGraphNode",
    "NodeKind",
    "build_landlord_graph",
]
