"""The tools Muse (or any MCP agent holding a workspace API key) can call.

Every tool drives the SAME HTTP route the dashboard uses, in-process,
carrying the caller's key.  That's deliberate: ownership validation,
notifications, stage-change audit rows, tenant scoping and RLS all live
in those routes, and a second implementation would drift from the first.

WHAT IS ABSENT MATTERS AS MUCH AS WHAT IS HERE.  No tool launches or
approves a campaign, deletes anything, or touches the team, integrations
or API keys.  The ONE path that reaches a prospect is the research-a-lead
outreach flow, and it is human-gated server-side: research_prospect only
drafts; confirm_outreach pins recipient + sender + exact text and issues a
one-time code; send_outreach needs that code, user_approved=true and an
unchanged draft, and sends at most once.  ``resume_campaign`` only resumes
a campaign a person already launched.  A guardrail test pins this list,
so widening it is a decision, not an accident.
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
                **self.extra,
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


async def get_campaign_analytics(ctx: ToolContext, args: dict) -> Any:
    a = await ctx.call("GET", f"/campaigns/{args['campaign_id']}/analytics")
    out = _pick(a, "campaign_id", "overview", "rates", "sender_reputation_score",
                "reply_tracking_enabled", "click_tracking_enabled", "send_cohorts",
                "best_subject_lines", "research_quality_breakdown")
    if args.get("include_timeline"):
        out["timeline"] = a.get("timeline")
    return out


async def get_sequence_funnel(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("GET", f"/campaigns/{args['campaign_id']}/sequence/analytics")


async def get_campaign_deliverability(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("GET", f"/campaigns/{args['campaign_id']}/deliverability")


async def search_leads(ctx: ToolContext, args: dict) -> Any:
    email = (args.get("email") or "").strip().lower()
    page = await ctx.call("GET", "/leads", params={
        "search": email or args.get("query"), "campaign_id": args.get("campaign_id"),
        "owner": args.get("owner"), "page_size": min(int(args.get("limit") or 20), 50),
    })
    leads = [_lead_summary(i) for i in page["items"]]
    if email:
        # Exact address wins; the same person can be a lead in several
        # campaigns, so every exact match is returned.
        exact = [l for l in leads if (l.get("email") or "").lower() == email]
        return {"total": len(exact), "match": "exact", "leads": exact} if exact else {
            "total": page["total"], "match": "partial", "leads": leads,
        }
    return {"total": page["total"], "leads": leads}


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
        "search": args.get("query"), "stage": args.get("stage"), "owner": args.get("owner"),
        "open_only": "true" if args.get("open_only") else None, "page_size": 100,
    })
    return {"total": page["total"], "opportunities": [_opp_summary(o) for o in page["items"]]}


async def get_opportunity(ctx: ToolContext, args: dict) -> Any:
    oid = args["opportunity_id"]
    opp = await ctx.call("GET", f"/crm/opportunities/{oid}")
    acts = await ctx.call("GET", "/crm/activities", params={"opportunity_id": oid, "page_size": 30})
    return {**opp, "activities": [_task_summary(a) for a in acts.get("items", [])]}


async def list_lead_activities(ctx: ToolContext, args: dict) -> Any:
    page = await ctx.call("GET", "/crm/activities", params={
        "lead_id": args["lead_id"], "activity_type": args.get("activity_type"),
        "page_size": min(int(args.get("limit") or 50), 200),
    })
    return {"total": page["total"], "activities": [_task_summary(a) for a in page["items"]]}


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


async def ignore_lead(ctx: ToolContext, args: dict) -> Any:
    lead = await ctx.call("GET", f"/leads/{args['lead_id']}")
    if not lead.get("email"):
        raise ToolError("this lead has no email address to ignore")
    res = await ctx.call("POST", f"/leads/{args['lead_id']}/ignore")
    return {"email": lead["email"], **res}


# --- reports -----------------------------------------------------------------

MAX_REPORT_ROWS = 200  # rows handed to the agent (the query itself caps at 5000)
DATA_SOURCES = ["leads", "opportunities", "activities", "contacts", "accounts"]


async def _member_names(ctx: ToolContext) -> dict[str, str]:
    members = await ctx.call("GET", "/team/members")
    return {m["user_id"]: m["display_name"] for m in members}


async def _report_result(ctx: ToolContext, res: dict, limit: int) -> dict:
    """Trim rows for the agent and show owners as names, not ids."""
    rows = res.get("rows") or []
    owner_cols = [c["key"] for c in res.get("columns", []) if c.get("type") == "owner"]
    if owner_cols and rows:
        names = await _member_names(ctx)
        for row in rows:
            for key in owner_cols:
                if row.get(key):
                    row[key] = names.get(str(row[key]), "former member")
                elif key in row:
                    row[key] = "unassigned"
    return {
        "columns": res.get("columns"),
        "rows": rows[:limit],
        "row_count": res.get("row_count"),
        "rows_shown": min(len(rows), limit),
        "grouped": res.get("grouped"),
        "truncated": res.get("truncated") or len(rows) > limit,
    }


async def report_fields(ctx: ToolContext, args: dict) -> Any:
    meta = await ctx.call("GET", "/reports/metadata")
    wanted = args.get("data_source")
    objects = [o for o in meta["objects"] if not wanted or o["key"] == wanted]
    return {
        "objects": [
            {"key": o["key"], "label": o["label"], "default_columns": o["default_columns"],
             "fields": [{k: f[k] for k in ("key", "label", "type", "operators", "aggregates", "enum_values")
                         if f.get(k)} for f in o["fields"]]}
            for o in objects
        ],
        "relative_ranges": meta.get("relative_ranges"),
        "definition_format": {
            "columns": ["field keys (ungrouped reports)"],
            "filters": [{"field": "key", "op": "operator", "value": "…  ('me' works for owner)"}],
            "group_by": ["field keys"],
            "aggregates": [{"fn": "count|sum|avg|min|max", "field": "key (omit for count)"}],
            "sort": [{"field": "key or aggregate alias like amount_sum", "dir": "asc|desc"}],
            "limit": "max rows (≤5000)",
        },
    }


def _definition(args: dict) -> dict:
    return {k: args[k] for k in ("columns", "filters", "group_by", "aggregates", "sort", "limit") if k in args}


async def run_report(ctx: ToolContext, args: dict) -> Any:
    res = await ctx.call("POST", "/reports/run", body={
        "data_source": args["data_source"], "definition": _definition(args),
    })
    return await _report_result(ctx, res, min(int(args.get("limit") or 50), MAX_REPORT_ROWS))


async def list_saved_reports(ctx: ToolContext, args: dict) -> Any:
    rows = await ctx.call("GET", "/reports")
    return [_pick(r, "id", "name", "description", "data_source", "definition", "owner_id", "updated_at")
            for r in rows]


async def run_saved_report(ctx: ToolContext, args: dict) -> Any:
    res = await ctx.call("POST", f"/reports/{args['report_id']}/run")
    return await _report_result(ctx, res, min(int(args.get("limit") or 50), MAX_REPORT_ROWS))


async def save_report(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("POST", "/reports", body={
        "name": args["name"], "description": args.get("description"),
        "data_source": args["data_source"], "definition": _definition(args),
    })


async def update_report(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("name", "description") if k in args}
    definition = _definition(args)
    if definition:
        body["definition"] = definition
    if "data_source" in args:
        body["data_source"] = args["data_source"]
    if not body:
        raise ToolError("nothing to update")
    return await ctx.call("PATCH", f"/reports/{args['report_id']}", body=body)


async def crm_overview(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("GET", "/crm/reports/overview", params=_pick(args, "start", "end"))


async def deals_report(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("GET", "/crm/reports/deals", params=_pick(args, "outcome", "start", "end"))


async def activities_report(ctx: ToolContext, args: dict) -> Any:
    return await ctx.call("GET", "/crm/reports/activities", params=_pick(args, "start", "end", "activity_type"))


# --- research-a-lead outreach (human-approved send) ---------------------------

def _draft_view(d: dict) -> dict:
    keep = ("id", "status", "channel", "version", "linkedin_url", "profile", "research_highlights",
            "subject", "body", "char_count", "to_email", "to_name", "sender_email", "sender_name",
            "linkedin_account_id", "recipient_suggestions", "sender_options", "confirmation",
            "sent_at", "send_error", "crm_lead_id", "next_step")
    return {k: d.get(k) for k in keep if d.get(k) not in (None, [], {})}


async def research_prospect(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("linkedin_url", "goal", "channel", "tone", "research_mode",
                                 "char_limit", "sender_name") if k in args}
    return _draft_view(await ctx.call("POST", "/outreach-drafts", body=body))


async def redraft_outreach(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("feedback", "goal", "tone", "channel", "char_limit") if k in args}
    return _draft_view(await ctx.call("POST", f"/outreach-drafts/{args['draft_id']}/redraft", body=body))


async def edit_outreach_draft(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("subject", "body", "to_email", "to_name", "sender_email",
                                 "sender_name", "linkedin_account_id") if k in args}
    return _draft_view(await ctx.call("PATCH", f"/outreach-drafts/{args['draft_id']}", body=body))


async def confirm_outreach(ctx: ToolContext, args: dict) -> Any:
    body = {k: args[k] for k in ("to_email", "to_name", "sender_email", "linkedin_account_id") if k in args}
    return _draft_view(await ctx.call("POST", f"/outreach-drafts/{args['draft_id']}/confirm", body=body))


async def send_outreach(ctx: ToolContext, args: dict) -> Any:
    if args.get("user_approved") is not True:
        raise ToolError("not sent — ask the user to approve the confirmation summary first")
    return _draft_view(await ctx.call("POST", f"/outreach-drafts/{args['draft_id']}/send", body={
        "confirmation_code": args["confirmation_code"], "user_approved": True,
    }))


async def discard_outreach(ctx: ToolContext, args: dict) -> Any:
    return _draft_view(await ctx.call("POST", f"/outreach-drafts/{args['draft_id']}/discard"))


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
    Tool("get_campaign_analytics", "Campaign analytics",
         "A campaign's Analytics tab: sent/delivered/opened/clicked/replied/bounced counts and "
         "rates, sender reputation score, open rate by send week (`send_cohorts`: every email "
         "sent that week — first emails + follow-ups — and the share opened; `accumulating` "
         "means opens are still arriving), best subject lines and open rate by research "
         "quality. Set include_timeline for the daily opens/clicks/replies series (30 days).",
         _obj({"campaign_id": UUID_S, "include_timeline": {"type": "boolean"}}, ["campaign_id"]),
         get_campaign_analytics),
    Tool("get_sequence_funnel", "Sequence step funnel",
         "Per-step funnel for a campaign's sequence: for each step (email, follow-up, LinkedIn "
         "action, wait) how many sends were attempted / sent / skipped / failed and how many "
         "leads are on it now, plus active / completed / halted lead totals.",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), get_sequence_funnel),
    Tool("get_campaign_deliverability", "Campaign deliverability",
         "Recent bounce / spam / open rates, the sending domain's remaining hourly and daily "
         "send headroom, and whether the deliverability circuit breaker has paused the campaign.",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), get_campaign_deliverability),
    Tool("search_leads", "Search leads",
         "Search leads across all campaigns. Use `email` to look someone up by email address "
         "(exact matches first — the same person may be a lead in several campaigns), or "
         "`query` for name / company / partial email. Filter by campaign or owner.",
         _obj({"email": {"type": "string", "description": "An email address, e.g. jane@acme.com"},
               "query": {"type": "string", "description": "Name, company, or part of an email"},
               "campaign_id": UUID_S, "owner": OWNER_S,
               "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), search_leads),
    Tool("get_lead", "Lead details",
         "A lead's contact info, CRM status, research summary and recent email/LinkedIn history.",
         _obj({"lead_id": UUID_S}, ["lead_id"]), get_lead),
    Tool("list_replies", "Inbound replies",
         "Recent inbound email replies, newest first, with AI sentiment and any suggested draft.",
         _obj({"sentiment": {"type": "string", "enum": ["positive", "neutral", "negative",
                                                         "out_of_office", "unsubscribe"]},
               "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), list_replies),
    Tool("list_opportunities", "Search deals",
         "Opportunities in the pipeline. `query` searches deal name, company and contact email; "
         "filter by stage, owner, or open only.",
         _obj({"query": {"type": "string"}, "stage": {"type": "string", "enum": STAGES},
               "owner": OWNER_S,
               "open_only": {"type": "boolean"}}), list_opportunities),
    Tool("get_opportunity", "Deal details",
         "One opportunity plus its activity timeline and tasks.",
         _obj({"opportunity_id": UUID_S}, ["opportunity_id"]), get_opportunity),
    Tool("list_lead_activities", "Lead activity log",
         "Calls, meetings, notes, emails and tasks logged on a lead, newest first "
         "(get_lead has the lead's notes field and its email/LinkedIn sequence history).",
         _obj({"lead_id": UUID_S,
               "activity_type": {"type": "string", "enum": ["call", "email", "meeting", "note", "task"]},
               "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["lead_id"]),
         list_lead_activities),
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
    Tool("ignore_lead", "Add a lead to the ignore list",
         "Suppress this lead's email for the whole workspace and halt it in every campaign it's "
         "in, so no further outreach reaches them (same as Ignore in the dashboard). Use when "
         "someone asks not to be contacted. Undoing it is done in the dashboard.",
         _obj({"lead_id": UUID_S}, ["lead_id"]), ignore_lead, mutates=True),
    Tool("crm_overview", "CRM overview report",
         "Headline CRM numbers for a date range: pipeline value, win rate, deals opened/closed, "
         "lead funnel and activity volume (the Reports tab's overview).",
         _obj({"start": DATE_S, "end": DATE_S}), crm_overview),
    Tool("deals_report", "Deals report",
         "Won, lost, open or all deals in a date range, with totals.",
         _obj({"outcome": {"type": "string", "enum": ["won", "lost", "open", "all"]},
               "start": DATE_S, "end": DATE_S}), deals_report),
    Tool("activities_report", "Activities report",
         "Calls, emails, meetings, notes and tasks logged in a date range, with breakdowns.",
         _obj({"start": DATE_S, "end": DATE_S,
               "activity_type": {"type": "string", "enum": ["call", "email", "meeting", "note", "task"]}}),
         activities_report),
    Tool("report_fields", "Report builder fields",
         "What the report builder can query: data sources, their fields, allowed filter operators "
         "and aggregates, and the definition format. Call this before building a custom report.",
         _obj({"data_source": {"type": "string", "enum": DATA_SOURCES}}), report_fields),
    Tool("run_report", "Run a custom report",
         "Build and run a report without saving it. Ungrouped: pick `columns`. Summary: use "
         "`group_by` + `aggregates` (e.g. deals by stage with amount sum). Filters use the "
         "operators from report_fields; owner filters accept 'me'.",
         _obj({"data_source": {"type": "string", "enum": DATA_SOURCES},
               "columns": {"type": "array", "items": {"type": "string"}},
               "filters": {"type": "array", "items": {"type": "object"}},
               "group_by": {"type": "array", "items": {"type": "string"}},
               "aggregates": {"type": "array", "items": {"type": "object"}},
               "sort": {"type": "array", "items": {"type": "object"}},
               "limit": {"type": "integer", "minimum": 1, "maximum": 5000}}, ["data_source"]),
         run_report),
    Tool("list_saved_reports", "Saved reports",
         "Reports saved in the report builder, with their definitions.", _obj({}), list_saved_reports),
    Tool("run_saved_report", "Run a saved report", "Run one saved report and return its rows.",
         _obj({"report_id": UUID_S, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
              ["report_id"]), run_saved_report),
    Tool("save_report", "Save a report",
         "Save a report definition to the report builder (shows up in the Reports page).",
         _obj({"name": {"type": "string"}, "description": {"type": "string"},
               "data_source": {"type": "string", "enum": DATA_SOURCES},
               "columns": {"type": "array", "items": {"type": "string"}},
               "filters": {"type": "array", "items": {"type": "object"}},
               "group_by": {"type": "array", "items": {"type": "string"}},
               "aggregates": {"type": "array", "items": {"type": "object"}},
               "sort": {"type": "array", "items": {"type": "object"}},
               "limit": {"type": "integer", "minimum": 1, "maximum": 5000}},
              ["name", "data_source"]), save_report, mutates=True),
    Tool("update_report", "Update a saved report",
         "Rename a saved report or change its definition (replaces the whole definition when any "
         "definition field is given).",
         _obj({"report_id": UUID_S, "name": {"type": "string"}, "description": {"type": "string"},
               "data_source": {"type": "string", "enum": DATA_SOURCES},
               "columns": {"type": "array", "items": {"type": "string"}},
               "filters": {"type": "array", "items": {"type": "object"}},
               "group_by": {"type": "array", "items": {"type": "string"}},
               "aggregates": {"type": "array", "items": {"type": "object"}},
               "sort": {"type": "array", "items": {"type": "object"}},
               "limit": {"type": "integer", "minimum": 1, "maximum": 5000}}, ["report_id"]),
         update_report, mutates=True),
    Tool("research_prospect", "Research a lead and draft outreach",
         "Research someone from their LinkedIn profile URL and draft a personalised email, "
         "LinkedIn message (1st-degree connections) or LinkedIn connection request with a note "
         "(≤200 chars, for people you're not connected to). SENDS NOTHING. Returns the draft, "
         "suggested recipient email(s) and sender options. Next: show the user the draft plus "
         "recipient and sender, and ask them to approve, edit or redraft.",
         _obj({"linkedin_url": {"type": "string", "description": "https://www.linkedin.com/in/<slug>"},
               "goal": {"type": "string", "description": "What the outreach should achieve"},
               "channel": {"type": "string", "enum": ["email", "linkedin_dm", "linkedin_connect"]},
               "tone": {"type": "string"},
               "research_mode": {"type": "string", "enum": ["fast", "deep"]},
               "char_limit": {"type": "integer", "minimum": 50, "maximum": 5000},
               "sender_name": {"type": "string", "description": "Signs the message; defaults to you"}},
              ["linkedin_url", "goal"]),
         research_prospect, mutates=True),
    Tool("redraft_outreach", "Redraft outreach",
         "Rewrite a draft from the same research (no new research cost), e.g. with feedback like "
         "'shorter, mention their podcast', or switch channel. Voids any prior confirmation.",
         _obj({"draft_id": UUID_S, "feedback": {"type": "string"}, "goal": {"type": "string"},
               "tone": {"type": "string"},
               "channel": {"type": "string", "enum": ["email", "linkedin_dm", "linkedin_connect"]},
               "char_limit": {"type": "integer", "minimum": 50, "maximum": 5000}}, ["draft_id"]),
         redraft_outreach, mutates=True),
    Tool("edit_outreach_draft", "Edit outreach draft",
         "Apply the user's exact edits (subject, body, recipient, sender, LinkedIn account). "
         "Voids any prior confirmation.",
         _obj({"draft_id": UUID_S, "subject": {"type": "string"}, "body": {"type": "string"},
               "to_email": {"type": "string"}, "to_name": {"type": "string"},
               "sender_email": {"type": "string"}, "sender_name": {"type": "string"},
               "linkedin_account_id": UUID_S}, ["draft_id"]),
         edit_outreach_draft, mutates=True),
    Tool("confirm_outreach", "Confirm recipient and sender",
         "Lock in the recipient, sender and the exact message after the user has reviewed the "
         "draft. Validates the recipient (not on the ignore list) and sender, and returns a final "
         "summary plus a one-time confirmation_code. SENDS NOTHING. Show the summary and ask the "
         "user explicitly whether to send.",
         _obj({"draft_id": UUID_S, "to_email": {"type": "string"}, "to_name": {"type": "string"},
               "sender_email": {"type": "string"}, "linkedin_account_id": UUID_S}, ["draft_id"]),
         confirm_outreach, mutates=True),
    Tool("send_outreach", "Send approved outreach",
         "Send a confirmed draft — ONLY after the user has explicitly said to send it in response "
         "to the confirmation summary. Requires the confirmation_code from confirm_outreach and "
         "user_approved=true. Fails if the draft changed since it was confirmed. Sends once.",
         _obj({"draft_id": UUID_S, "confirmation_code": {"type": "string"},
               "user_approved": {"type": "boolean",
                                 "description": "true only if the user explicitly approved this send"}},
              ["draft_id", "confirmation_code", "user_approved"]),
         send_outreach, mutates=True,
         extra={"destructiveHint": True, "openWorldHint": True}),
    Tool("discard_outreach", "Discard outreach draft", "Throw a draft away without sending.",
         _obj({"draft_id": UUID_S}, ["draft_id"]), discard_outreach, mutates=True),
    Tool("pause_campaign", "Pause a campaign",
         "Stop a running campaign's sends immediately (resume later from here or the dashboard).",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), pause_campaign, mutates=True),
    Tool("resume_campaign", "Resume a campaign",
         "Resume a campaign that was already launched and is paused. Cannot launch a new campaign, "
         "and refuses campaigns the deliverability circuit breaker paused (those need a human).",
         _obj({"campaign_id": UUID_S}, ["campaign_id"]), resume_campaign, mutates=True),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
