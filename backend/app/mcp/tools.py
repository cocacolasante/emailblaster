"""The tools Muse (or any MCP agent holding a workspace API key) can call.

Every tool drives the SAME HTTP route the dashboard uses, in-process,
carrying the caller's key.  That's deliberate: ownership validation,
notifications, stage-change audit rows, tenant scoping and RLS all live
in those routes, and a second implementation would drift from the first.

WHAT IS ABSENT MATTERS AS MUCH AS WHAT IS HERE.  No tool sends an email
or a LinkedIn message, launches or approves a campaign, deletes anything,
or touches the team, integrations or API keys.  Anything that reaches a
prospect stays a deliberate act in the dashboard.  ``resume_campaign``
only resumes a campaign a person already launched.  A guardrail test pins
this list, so widening it is a decision, not an accident.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx


class ToolError(Exception):
    """Refusal/validation message surfaced to the agent as tool output."""


@dataclass
class ToolContext:
    authorization: str       # the caller's "Bearer eb_…" header, re-presented in-process
    user_id: uuid.UUID       # the key's member (resolves owner "me")
    client: httpx.AsyncClient

    async def call(self, method: str, path: str, *, params: dict | None = None,
                   body: Any = None) -> Any:
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                path = f"{path}?{urlencode(clean, doseq=True)}"
        resp = await self.client.request(
            method, path, json=body, headers={"authorization": self.authorization},
        )
        data: Any = resp.json() if resp.content else None
        if resp.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):  # pydantic validation errors
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail
                )
            raise ToolError(str(detail or f"HTTP {resp.status_code}"))
        return data


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    run: Callable[[ToolContext, dict], Awaitable[Any]]
    mutates: bool = False
    extra: dict = field(default_factory=dict)

    def listing(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": not self.mutates,
                "destructiveHint": False,
                "idempotentHint": not self.mutates,
                "openWorldHint": False,
            },
        }


# --- schema helpers ----------------------------------------------------------

def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


UUID_S = {"type": "string", "format": "uuid"}
OWNER_S = {"type": "string", "description": "'me', 'unassigned', or a member's user id "
                                             "(see list_team_members)."}
OWNER_ID_S = {"type": ["string", "null"],
              "description": "A member's user id, 'me', or null to leave unassigned."}
STAGES = ["prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost"]
DATE_S = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
DATETIME_S = {"type": "string", "format": "date-time", "description": "ISO-8601, e.g. 2026-10-01T15:00:00Z"}


def _owner(ctx: ToolContext, value: Any) -> Any:
    return str(ctx.user_id) if value == "me" else value


def _pick(d: dict, *keys: str) -> dict:
    return {k: d.get(k) for k in keys if k in d}


def _campaign_summary(c: dict) -> dict:
    stats = c.get("stats") or {}
    return {
        **_pick(c, "id", "name", "status", "owner_id", "auto_pause_reason", "auto_paused_until"),
        "leads": c.get("lead_counts"),
        "sent": stats.get("sent_count"),
        "open_rate": stats.get("open_rate"),
        "reply_rate": stats.get("reply_rate"),
        "bounce_rate": stats.get("bounce_rate"),
        "replied": stats.get("replied"),
    }


def _lead_summary(lead: dict) -> dict:
    return _pick(lead, "id", "email", "first_name", "last_name", "company", "job_title",
                 "campaign_id", "campaign_name", "crm_status", "send_status", "sequence_stage",
                 "owner_id", "converted_opportunity_id", "notes")


def _opp_summary(o: dict) -> dict:
    return _pick(o, "id", "name", "stage", "amount", "close_date", "probability", "company",
                 "email", "owner_id", "open_task_count", "updated_at")


def _task_summary(a: dict) -> dict:
    return _pick(a, "id", "subject", "body", "due_at", "completed_at", "owner_id",
                 "lead_id", "opportunity_id", "activity_type", "occurred_at", "sentiment")


# --- read tools --------------------------------------------------------------

async def daily_brief(ctx: ToolContext, args: dict) -> Any:
    campaigns = await ctx.call("GET", "/campaigns/")
    live = [c for c in campaigns if c["status"] in ("running", "paused", "previewing")]
    replies = await ctx.call("GET", "/agent/replies", params={"page_size": 10})
    tasks = await ctx.call("GET", "/crm/activities",
                           params={"open_tasks": "true", "owner": "me", "page_size": 20})
    pipeline = await ctx.call("GET", "/crm/opportunities/pipeline")
    notes = await ctx.call("GET", "/agent/notifications", params={"unread": "true", "page_size": 5})
    return {
        "campaigns": [_campaign_summary(c) for c in live],
        "recent_replies": [
            _pick(r, "lead_id", "lead_name", "lead_company", "sentiment", "subject",
                  "body_preview", "occurred_at", "convert_eligible")
            for r in replies.get("items", [])
        ],
        "my_open_tasks": [_task_summary(t) for t in tasks.get("items", [])],
        "pipeline": [s for s in pipeline if s.get("count")],
        "unread_notifications": notes.get("unread"),
        "latest_notifications": [_pick(n, "title", "kind", "created_at") for n in notes.get("items", [])],
    }


async def list_campaigns(ctx: ToolContext, args: dict) -> Any:
    rows = await ctx.call("GET", "/campaigns/", params={"owner": args.get("owner")})
    status = args.get("status")
    return [_campaign_summary(c) for c in rows if not status or c["status"] == status]


async def get_campaign(ctx: ToolContext, args: dict) -> Any:
    c = await ctx.call("GET", f"/campaigns/{args['campaign_id']}")
    return {
        **_campaign_summary(c),
        **_pick(c, "goal", "tone", "sender_name", "sender_email", "schedule_days",
                "schedule_time_start", "schedule_time_end", "schedule_timezone",
                "max_per_day", "created_at"),
        "stats": c.get("stats"),
    }


async def search_leads(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/leads", params={
        "search": args.get("query"), "campaign_id": args.get("campaign_id"),
        "owner": args.get("owner"), "page_size": min(int(args.get("limit") or 20), 50),
    })
    return {"total": page["total"], "leads": [_lead_summary(i) for i in page["items"]]}


async def get_lead(ctx: ToolContext, args: dict) -> Any:
    d = await ctx.call("GET", f"/leads/{args['lead_id']}")
    out = _lead_summary(d)
    out.update(_pick(d, "phone", "linkedin_url", "company_website", "history_counts",
                     "research_summary", "is_suppressed", "linkedin_connection_status"))
    out["recent_history"] = (d.get("history") or [])[:20]
    return out


async def list_replies(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/agent/replies", params={
        "sentiment": args.get("sentiment"), "page_size": min(int(args.get("limit") or 20), 50),
    })
    return {"total": page["total"], "replies": page["items"]}


async def list_opportunities(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/crm/opportunities", params={
        "stage": args.get("stage"), "owner": args.get("owner"),
        "open_only": "true" if args.get("open_only") else None, "page_size": 100,
    })
    return {"total": page["total"], "opportunities": [_opp_summary(o) for o in page["items"]]}


async def get_opportunity(ctx: ToolContext, args: dict) -> Any:
    oid = args["opportunity_id"]
    opp = await ctx.call("GET", f"/crm/opportunities/{oid}")
    acts = await ctx.call("GET", "/crm/activities", params={"opportunity_id": oid, "page_size": 30})
    return {**opp, "activities": [_task_summary(a) for a in acts.get("items", [])]}


async def list_tasks(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/crm/activities", params={
        "open_tasks": "true", "owner": args.get("owner", "me"), "page_size": 100,
    })
    return {"total": page["total"], "tasks": [_task_summary(t) for t in page["items"]]}


async def list_team_members(ctx: ToolContext, args: dict) -> Any:
    members = await ctx.call("GET", "/team/members")
    return [
        {**_pick(m, "user_id", "display_name", "email", "role"), "is_me": m["user_id"] == str(ctx.user_id)}
        for m in members
    ]


async def list_notifications(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/agent/notifications", params={
        "unread": "true" if args.get("unread_only", True) else None, "page_size": 25,
    })
    return {
        "unread": page.get("unread"),
        "notifications": [
            _pick(n, "id", "kind", "title", "body", "lead_id", "opportunity_id", "created_at", "read_at")
            for n in page.get("items", [])
        ],
    }


# --- CRM write tools ---------------------------------------------------------

_LEAD_FIELDS = ("email", "first_name", "last_name", "company", "job_title", "phone",
                "linkedin_url", "company_website", "notes")


async def create_lead(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in _LEAD_FIELDS if k in args}
    if "owner_id" in args:
        body["owner_id"] = _owner(ctx, args["owner_id"])
    return await ctx.call("POST", "/crm/leads", body=body)


async def update_lead(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in (*_LEAD_FIELDS, "crm_status") if k in args}
    if "owner_id" in args:
        body["owner_id"] = _owner(ctx, args["owner_id"])
    if not body:
        raise ToolError("nothing to update")
    return await ctx.call("PATCH", f"/crm/leads/{args['lead_id']}", body=body)


def _parent(args: dict) -> dict:
    parent = {k: args[k] for k in ("lead_id", "opportunity_id") if args.get(k)}
    if not parent:
        raise ToolError("give a lead_id and/or an opportunity_id")
    return parent


async def log_activity(ctx: ToolContext, args: dict) -> Any:
    body = {**_parent(args), "activity_type": args["activity_type"], "subject": args["subject"]}
    body.update({k: args[k] for k in ("body", "occurred_at", "direction") if args.get(k)})
    return _task_summary(await ctx.call("POST", "/crm/activities", body=body))


async def create_task(ctx: ToolContext, args: dict) -> Any:
    body = {**_parent(args), "activity_type": "task", "subject": args["subject"]}
    body.update({k: args[k] for k in ("body", "due_at") if args.get(k)})
    if "owner_id" in args:
        body["owner_id"] = _owner(ctx, args["owner_id"])
    return _task_summary(await ctx.call("POST", "/crm/activities", body=body))


async def complete_task(ctx: ToolContext, args: dict) -> Any:
    return _task_summary(
        await ctx.call("PATCH", f"/crm/activities/{args['task_id']}", body={"completed": True})
    )


async def convert_lead(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("name", "amount", "close_date", "stage") if k in args}
    res = await ctx.call("POST", f"/crm/leads/{args['lead_id']}/convert", body=body)
    return {"opportunity": _opp_summary(res["opportunity"]), "lead_crm_status": res.get("lead_crm_status")}


_OPP_FIELDS = ("name", "stage", "amount", "close_date", "probability", "description",
               "company", "email", "first_name", "last_name", "phone", "job_title")


async def create_opportunity(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in _OPP_FIELDS if k in args}
    if "owner_id" in args:
        body["owner_id"] = _owner(ctx, args["owner_id"])
    return _opp_summary(await ctx.call("POST", "/crm/opportunities", body=body))


async def update_opportunity(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in (*_OPP_FIELDS, "loss_reason") if k in args}
    if "owner_id" in args:
        body["owner_id"] = _owner(ctx, args["owner_id"])
    if not body:
        raise ToolError("nothing to update")
    return _opp_summary(await ctx.call("PATCH", f"/crm/opportunities/{args['opportunity_id']}", body=body))


async def assign_owner(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("POST", "/owners/assign", body={
        "record_type": args["record_type"], "ids": args["ids"],
        "owner_id": _owner(ctx, args.get("owner_id")),
    })


# --- campaign control --------------------------------------------------------

async def pause_campaign(ctx: ToolContext, args: dict) -> Any:
    return _campaign_summary(await ctx.call("POST", f"/campaigns/{args['campaign_id']}/pause"))


async def resume_campaign(ctx: ToolContext, args: dict) -> Any:
    current = await ctx.call("GET", f"/campaigns/{args['campaign_id']}")
    if current.get("auto_pause_reason") or current.get("auto_paused_at"):
        # The deliverability circuit breaker paused it (bounce/spam spike).
        # Resuming overrides that breaker, which is deliberately a human call.
        raise ToolError(
            "This campaign was auto-paused by the deliverability circuit breaker "
            f"({current.get('auto_pause_reason') or 'bounce/spam spike'}). Review it and resume "
            "from the Email Blaster dashboard."
        )
    return _campaign_summary(await ctx.call("POST", f"/campaigns/{args['campaign_id']}/resume"))


# --- registry ----------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool("daily_brief", "Daily brief",
         "Start here. Live campaigns with send/open/reply stats, the latest inbound replies "
         "(with sentiment), my open tasks, the deal pipeline and unread notifications.",
         _obj({}), daily_brief),
    Tool("list_campaigns", "List campaigns",
         "Campaigns with status, lead counts and headline stats.",
         _obj({"status": {"type": "string", "enum": ["draft", "previewing", "approved", "running",
                                                      "paused", "complete"]},
               "owner": OWNER_S}), list_campaigns),
    Tool("get_campaign", "Campaign details",
         "One campaign's goal, schedule and full stats.",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), get_campaign),
    Tool("search_leads", "Search leads",
         "Search leads across all campaigns by name, email or company; filter by campaign or owner.",
         _obj({"query": {"type": "string"}, "campaign_id": UUID_S, "owner": OWNER_S,
               "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), search_leads),
    Tool("get_lead", "Lead details",
         "A lead's contact info, CRM status, research summary and recent email/LinkedIn history.",
         _obj({"lead_id": UUID_S}, ["lead_id"]), get_lead),
    Tool("list_replies", "Inbound replies",
         "Recent inbound email replies, newest first, with AI sentiment and any suggested draft.",
         _obj({"sentiment": {"type": "string", "enum": ["positive", "neutral", "negative",
                                                         "out_of_office", "unsubscribe"]},
               "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), list_replies),
    Tool("list_opportunities", "List deals",
         "Opportunities in the pipeline; filter by stage, owner, or open only.",
         _obj({"stage": {"type": "string", "enum": STAGES}, "owner": OWNER_S,
               "open_only": {"type": "boolean"}}), list_opportunities),
    Tool("get_opportunity", "Deal details",
         "One opportunity plus its activity timeline and tasks.",
         _obj({"opportunity_id": UUID_S}, ["opportunity_id"]), get_opportunity),
    Tool("list_tasks", "Open tasks",
         "Incomplete tasks ordered by due date (defaults to mine).",
         _obj({"owner": OWNER_S}), list_tasks),
    Tool("list_team_members", "Team members",
         "Members of this workspace — use their user_id to assign owners.",
         _obj({}), list_team_members),
    Tool("list_notifications", "Notifications",
         "Alerts addressed to me or the whole workspace (assignments, positive replies, due tasks, "
         "auto-paused campaigns).",
         _obj({"unread_only": {"type": "boolean"}}), list_notifications),

    Tool("create_lead", "Create a CRM lead",
         "Add a lead to the CRM (not to a campaign — nothing is sent).",
         _obj({"email": {"type": "string"}, "first_name": {"type": "string"},
               "last_name": {"type": "string"}, "company": {"type": "string"},
               "job_title": {"type": "string"}, "phone": {"type": "string"},
               "linkedin_url": {"type": "string"}, "company_website": {"type": "string"},
               "notes": {"type": "string"}, "owner_id": OWNER_ID_S}, ["email"]),
         create_lead, mutates=True),
    Tool("update_lead", "Update a lead",
         "Change a lead's CRM status, notes, contact details or owner.",
         _obj({"lead_id": UUID_S,
               "crm_status": {"type": "string", "enum": ["new", "working", "qualified", "unqualified"]},
               "notes": {"type": "string"}, "email": {"type": "string"},
               "first_name": {"type": "string"}, "last_name": {"type": "string"},
               "company": {"type": "string"}, "job_title": {"type": "string"},
               "phone": {"type": "string"}, "linkedin_url": {"type": "string"},
               "owner_id": OWNER_ID_S}, ["lead_id"]),
         update_lead, mutates=True),
    Tool("log_activity", "Log an activity",
         "Record a call, meeting, note or an email that happened outside the app on a lead and/or "
         "deal. This only LOGS it — nothing is sent.",
         _obj({"lead_id": UUID_S, "opportunity_id": UUID_S,
               "activity_type": {"type": "string", "enum": ["call", "meeting", "note", "email"]},
               "subject": {"type": "string"}, "body": {"type": "string"},
               "direction": {"type": "string", "enum": ["inbound", "outbound"]},
               "occurred_at": DATETIME_S}, ["activity_type", "subject"]),
         log_activity, mutates=True),
    Tool("create_task", "Create a task",
         "Create a follow-up task on a lead and/or deal (defaults to the deal/lead owner).",
         _obj({"lead_id": UUID_S, "opportunity_id": UUID_S, "subject": {"type": "string"},
               "body": {"type": "string"}, "due_at": DATETIME_S, "owner_id": OWNER_ID_S},
              ["subject"]),
         create_task, mutates=True),
    Tool("complete_task", "Complete a task", "Mark a task done.",
         _obj({"task_id": UUID_S}, ["task_id"]), complete_task, mutates=True),
    Tool("convert_lead", "Convert a lead to a deal",
         "Turn a lead into an opportunity (keeps the lead's owner).",
         _obj({"lead_id": UUID_S, "name": {"type": "string"},
               "stage": {"type": "string", "enum": STAGES}, "amount": {"type": "number", "minimum": 0},
               "close_date": DATE_S}, ["lead_id"]),
         convert_lead, mutates=True),
    Tool("create_opportunity", "Create a deal", "Add an opportunity to the pipeline.",
         _obj({"name": {"type": "string"}, "stage": {"type": "string", "enum": STAGES},
               "amount": {"type": "number", "minimum": 0}, "close_date": DATE_S,
               "probability": {"type": "integer", "minimum": 0, "maximum": 100},
               "description": {"type": "string"}, "company": {"type": "string"},
               "email": {"type": "string"}, "first_name": {"type": "string"},
               "last_name": {"type": "string"}, "owner_id": OWNER_ID_S}, ["name"]),
         create_opportunity, mutates=True),
    Tool("update_opportunity", "Update a deal",
         "Move a deal's stage or change its amount, close date, probability, notes or owner.",
         _obj({"opportunity_id": UUID_S, "stage": {"type": "string", "enum": STAGES},
               "amount": {"type": "number", "minimum": 0}, "close_date": DATE_S,
               "probability": {"type": "integer", "minimum": 0, "maximum": 100},
               "description": {"type": "string"}, "loss_reason": {"type": "string"},
               "owner_id": OWNER_ID_S}, ["opportunity_id"]),
         update_opportunity, mutates=True),
    Tool("assign_owner", "Assign owner",
         "Assign (or unassign with null) the owner of leads, deals, tasks or campaigns in bulk.",
         _obj({"record_type": {"type": "string", "enum": ["lead", "opportunity", "activity", "campaign"]},
               "ids": {"type": "array", "items": UUID_S, "minItems": 1, "maxItems": 500},
               "owner_id": OWNER_ID_S}, ["record_type", "ids", "owner_id"]),
         assign_owner, mutates=True),
    Tool("pause_campaign", "Pause a campaign",
         "Stop a running campaign's sends immediately (resume later from here or the dashboard).",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), pause_campaign, mutates=True),
    Tool("resume_campaign", "Resume a campaign",
         "Resume a campaign that was already launched and is paused. Cannot launch a new campaign, "
         "and refuses campaigns the deliverability circuit breaker paused (those need a human).",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), resume_campaign, mutates=True),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
