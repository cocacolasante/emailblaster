from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Campaign,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    Sequence,
    SequenceEdge,
    SequenceNode,
)
from app.schemas.sequence import (
    NodeAnalytics,
    PublishResponse,
    SequenceAnalyticsResponse,
    SequenceResponse,
    SequenceUpdate,
    ValidateResponse,
)
from app.services.sequence_service import (
    ensure_default_sequence,
    replace_graph,
    validate_graph,
)

router = APIRouter(tags=["sequences"])


async def _get_campaign_or_404(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    c = await db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


async def _load_response(db: AsyncSession, sequence: Sequence) -> SequenceResponse:
    nodes = (await db.execute(
        select(SequenceNode)
        .where(
            SequenceNode.sequence_id == sequence.id,
            SequenceNode.deleted_at.is_(None),
        )
        .order_by(SequenceNode.created_at.asc())
    )).scalars().all()
    edges = (await db.execute(
        select(SequenceEdge)
        .where(SequenceEdge.sequence_id == sequence.id)
        .order_by(SequenceEdge.priority.asc(), SequenceEdge.created_at.asc())
    )).scalars().all()
    return SequenceResponse(
        id=sequence.id,
        campaign_id=sequence.campaign_id,
        is_published=sequence.is_published,
        nodes=[
            {
                "id": n.id,
                "kind": n.kind,
                "config": n.config or {},
                "position_x": n.position_x,
                "position_y": n.position_y,
                "is_entry": n.is_entry,
            }
            for n in nodes
        ],
        edges=[
            {
                "id": e.id,
                "from_node_id": e.from_node_id,
                "to_node_id": e.to_node_id,
                "condition": e.condition or {"op": "always"},
                "priority": e.priority,
            }
            for e in edges
        ],
        created_at=sequence.created_at,
        updated_at=sequence.updated_at,
    )


@router.get("/campaigns/{campaign_id}/sequence", response_model=SequenceResponse)
async def get_sequence(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SequenceResponse:
    campaign = await _get_campaign_or_404(db, campaign_id)
    seq = await ensure_default_sequence(db, campaign)
    await db.commit()
    await db.refresh(seq)
    return await _load_response(db, seq)


@router.put("/campaigns/{campaign_id}/sequence", response_model=SequenceResponse)
async def update_sequence(
    campaign_id: uuid.UUID,
    payload: SequenceUpdate,
    db: AsyncSession = Depends(get_db),
) -> SequenceResponse:
    campaign = await _get_campaign_or_404(db, campaign_id)
    seq = await ensure_default_sequence(db, campaign)

    nodes_dicts = [n.model_dump() for n in payload.nodes]
    edges_dicts = [e.model_dump() for e in payload.edges]

    # Structural validation always runs (catches bad client_id refs, bad
    # condition shapes). Publish-only validation (M1 kind restriction,
    # reachability) runs in /publish so the user can save drafts.
    await replace_graph(db, seq, nodes_dicts, edges_dicts)
    await db.commit()
    await db.refresh(seq)
    return await _load_response(db, seq)


@router.post("/campaigns/{campaign_id}/sequence/validate", response_model=ValidateResponse)
async def validate_sequence(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ValidateResponse:
    """Run the publish-time checks against the current saved graph. The UI
    calls this to surface errors before the user hits Publish.
    """
    campaign = await _get_campaign_or_404(db, campaign_id)
    seq = await ensure_default_sequence(db, campaign)
    await db.commit()

    nodes = (await db.execute(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id,
            SequenceNode.deleted_at.is_(None),
        )
    )).scalars().all()
    edges = (await db.execute(
        select(SequenceEdge).where(SequenceEdge.sequence_id == seq.id)
    )).scalars().all()

    # Re-shape into the dict form validate_graph expects (client_id == str(id)).
    nodes_dicts = [
        {
            "client_id": str(n.id),
            "kind": n.kind.value if hasattr(n.kind, "value") else n.kind,
            "is_entry": n.is_entry,
            "config": n.config or {},
        }
        for n in nodes
    ]
    edges_dicts = [
        {
            "from_client_id": str(e.from_node_id),
            "to_client_id": str(e.to_node_id) if e.to_node_id else None,
            "condition": e.condition or {"op": "always"},
        }
        for e in edges
    ]
    errors = validate_graph(nodes_dicts, edges_dicts)
    return ValidateResponse(ok=len(errors) == 0, errors=errors)


@router.post("/campaigns/{campaign_id}/sequence/publish", response_model=PublishResponse)
async def publish_sequence(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PublishResponse:
    """Validate + flip is_published=True. Already-active lead_sequence_state
    rows keep using whichever node graph they were on at the moment they
    advanced; the new published graph applies from the next state transition.
    """
    campaign = await _get_campaign_or_404(db, campaign_id)
    seq = await ensure_default_sequence(db, campaign)

    nodes = (await db.execute(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id,
            SequenceNode.deleted_at.is_(None),
        )
    )).scalars().all()
    edges = (await db.execute(
        select(SequenceEdge).where(SequenceEdge.sequence_id == seq.id)
    )).scalars().all()

    nodes_dicts = [
        {
            "client_id": str(n.id),
            "kind": n.kind.value if hasattr(n.kind, "value") else n.kind,
            "is_entry": n.is_entry,
            "config": n.config or {},
        }
        for n in nodes
    ]
    edges_dicts = [
        {
            "from_client_id": str(e.from_node_id),
            "to_client_id": str(e.to_node_id) if e.to_node_id else None,
            "condition": e.condition or {"op": "always"},
        }
        for e in edges
    ]
    errors = validate_graph(nodes_dicts, edges_dicts)
    if errors:
        return PublishResponse(ok=False, is_published=seq.is_published, errors=errors)

    seq.is_published = True
    await db.commit()
    return PublishResponse(ok=True, is_published=True, errors=[])


# --------------------------------------------------------------------------
# Analytics (M5)
# --------------------------------------------------------------------------


@router.get(
    "/campaigns/{campaign_id}/sequence/analytics",
    response_model=SequenceAnalyticsResponse,
)
async def get_sequence_analytics(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SequenceAnalyticsResponse:
    """Per-node funnel counts derived from lead_step_executions +
    lead_sequence_states. Soft-deleted nodes are excluded — analytics for
    retired topology aren't currently exposed (we'd need a "show history"
    toggle, deferred).
    """
    campaign = await _get_campaign_or_404(db, campaign_id)
    seq = await ensure_default_sequence(db, campaign)
    await db.commit()

    # Live nodes (the current topology).
    nodes = (await db.execute(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id,
            SequenceNode.deleted_at.is_(None),
        )
    )).scalars().all()
    node_ids = [n.id for n in nodes]

    # Aggregate step executions by (node, result).
    exec_rows = (await db.execute(
        select(
            LeadStepExecution.node_id,
            LeadStepExecution.result,
            func.count().label("c"),
        )
        .where(LeadStepExecution.node_id.in_(node_ids))
        .group_by(LeadStepExecution.node_id, LeadStepExecution.result)
    )).all() if node_ids else []
    by_node: dict[uuid.UUID, dict[str, int]] = {nid: {"attempted": 0, "sent": 0, "skipped": 0, "failed": 0} for nid in node_ids}
    for nid, result, c in exec_rows:
        bucket = by_node.setdefault(nid, {"attempted": 0, "sent": 0, "skipped": 0, "failed": 0})
        bucket["attempted"] += c
        if result == LeadStepResult.SENT:
            bucket["sent"] += c
        elif result == LeadStepResult.SKIPPED:
            bucket["skipped"] += c
        elif result == LeadStepResult.FAILED:
            bucket["failed"] += c

    # Active leads currently sitting on each node.
    currently_rows = (await db.execute(
        select(
            LeadSequenceState.current_node_id,
            func.count().label("c"),
        )
        .where(
            LeadSequenceState.sequence_id == seq.id,
            LeadSequenceState.status == LeadSequenceStatus.ACTIVE,
            LeadSequenceState.current_node_id.is_not(None),
        )
        .group_by(LeadSequenceState.current_node_id)
    )).all()
    currently: dict[uuid.UUID, int] = {nid: c for nid, c in currently_rows}

    per_node = [
        NodeAnalytics(
            node_id=nid,
            attempted=by_node[nid]["attempted"],
            sent=by_node[nid]["sent"],
            skipped=by_node[nid]["skipped"],
            failed=by_node[nid]["failed"],
            currently_here=currently.get(nid, 0),
        )
        for nid in node_ids
    ]

    # Overall status breakdown.
    status_rows = (await db.execute(
        select(LeadSequenceState.status, func.count().label("c"))
        .where(LeadSequenceState.sequence_id == seq.id)
        .group_by(LeadSequenceState.status)
    )).all()
    by_status: dict[LeadSequenceStatus, int] = {s: c for s, c in status_rows}

    return SequenceAnalyticsResponse(
        campaign_id=campaign.id,
        sequence_id=seq.id,
        total_leads=sum(by_status.values()),
        halted=by_status.get(LeadSequenceStatus.HALTED, 0),
        completed=by_status.get(LeadSequenceStatus.COMPLETED, 0),
        active=by_status.get(LeadSequenceStatus.ACTIVE, 0),
        pending=by_status.get(LeadSequenceStatus.PENDING, 0),
        per_node=per_node,
    )
