# Email Blaster — Claude session context

Running state of the project so any Claude session (this one or a future one)
can pick up where the last one left off. The remaining-work roadmap is in
[`docs/roadmap.md`](docs/roadmap.md); this file is the snapshot.

> **Convention:** update this file at the end of every meaningful task.
> Move finished work into "Last completed", pull the next item from the
> roadmap into "Next up", note anything surprising under "Conventions and
> gotchas", and refresh the test counts.

---

## Mission

AI-powered cold email + multi-channel outreach. Upload CSV → research each
lead → Claude composes a personalized email → multi-step sequence (email +
delays + later LinkedIn) → schedule + send → track replies → analyze.

Phase 1 (email-only) shipped. We're now in **Phase 1.5 — multi-step
sequences + LinkedIn outreach**, broken into M1–M5 in the roadmap.

## Where we are

- **Last completed:** **Beat-driven first-email pacer — killed the
  self-re-enqueue send storm.**  Diagnosed a campaign with ~1000 first
  emails "stuck": the broker held **10.6k `send.send_lead` tasks** all
  bouncing on the rate gate.  Root cause: the legacy first email is sent
  via `compose → send_lead.delay()` (immediate, unstaggered), and on a
  rate-limit defer the `send_lead` wrapper **self-re-enqueued with a
  far-future eta** (daily-cap defer retried at next local midnight,
  ~hours out).  The broker `visibility_timeout` is **300s**, so every
  long-eta task got **redelivered every 5 min**, multiplying ~1k pending
  leads into a 10k+ churning-task storm (≈1 send per 168 `rate_limited`
  bounces).  `_kick_off_full_campaign` (approve-all) and `resume_campaign`
  made it worse — they eta-staggered the whole composed batch
  (`i * min_delay`), also producing far-future-eta tasks.
  - **Fix — a paced dispatcher** (`send.pace_first_emails`, beat every
    60s, `acks_late=False`).  Per RUNNING campaign: skip if outside the
    send window or at the hourly/daily cap; with a min-delay, feed exactly
    ONE lead and only while the atomic `min_gate` is open (so near-zero
    wasted dispatches); with no min-delay, feed a small headroom-bounded
    batch.  Excludes empty-body (non-email-entry) + suppressed leads so a
    gate slot is never spent on a lead that can't send.  `send_lead` does
    the actual send + counter bump.
  - **`send_lead` wrapper no longer self-re-enqueues** (rate_limited /
    scheduled just return; the lead stays PENDING/SCHEDULED and the pacer
    re-feeds it).  **`_kick_off_full_campaign` + `resume_campaign` no
    longer eta-dispatch** — they transition to RUNNING and let the pacer
    drain.  This removes every far-future-eta source, so the redelivery
    storm can't form.
  - **Live remediation:** restarted worker + beat (cleared the stale
    `celerybeat-schedule` shelve so the new beat entry registered),
    purged the **10,547** unacked `send.send_lead` messages from the
    broker.  Verified: pacer feeds 1/window, `send_lead` returns clean
    `sent` (no `rate_limited` churn), `unacked_index` holds at 0.  Caps
    kept at 15/hr · 100/day (user's choice — safest deliverability;
    ~10 days to clear ~1000 leads).
  - **Gotcha worth remembering:** any `apply_async(eta=…)` more than
    `visibility_timeout` (300s) in the future gets **redelivered** by the
    Redis broker, spawning duplicates — never schedule far-future sends
    that way.  Pace with a beat dispatching IMMEDIATE tasks instead (the
    sequencer already does this for follow-up/LinkedIn steps).
  - Tests: 5 backend (`test_phase9_send_task.py`: pacer feeds-one /
    skips-on-gate / skips-at-cap / no-delay-batch-headroom /
    excludes-unsendable-and-non-running) + 3 updated (send wrapper no
    longer re-enqueues; approve-all + resume no longer self-dispatch).
    Tests: **backend 1084, frontend 376**.
- **Previously:** **Fixed: re-publishing a sequence restarted every
  lead's `days_since_entered_node` clock (follow-up replies never fired).**
  Diagnosed a live campaign whose `email_reply` node ("reply 2 days after
  the first email", edge `days_since_entered_node >= 2`) wasn't firing even
  for leads emailed days earlier.
  - **Root cause:** `days_since_entered_node` measures from
    `LeadSequenceState.entered_current_at` (when the lead entered the
    *node*), not from when the email was sent.  `reenroll_for_new_nodes`
    (run on every publish) reset `entered_current_at = now` for every
    re-queued lead — so each sequence edit/republish **restarted the
    2-day clock for all leads**, and they never crossed the gate.  The
    chain: publish changes the entry node id → leads on the now
    soft-deleted entry node get halted by the beat → reenroll re-queues
    them to the new entry node with `entered_current_at = now`.
  - **Fix (`sequence_service.reenroll_for_new_nodes`):** anchor
    `entered_current_at` to when the lead ACTUALLY reached the node — the
    node's real `LeadStepExecution.attempted_at` when it has one, else
    preserve the lead's existing entry timestamp, else `now`.  No longer
    blanket-resets to now, so time-based edge waits survive re-publishing.
  - **Live remediation (one-off):** backfilled `entered_current_at` for
    the affected campaign's leads from the actual send time embedded in
    `brevo_message_id` (`<YYYYMMDDHHMM.…@smtp-relay.mailin.fr>`):
    `UPDATE lead_sequence_states SET entered_current_at =
    to_timestamp(substring(brevo_message_id from '\d{12}'),
    'YYYYMMDDHH24MI') …`.  319 leads (sent ≥2 days prior) immediately
    became eligible and advanced to the reply node; the rest correctly
    waited out their 2-day mark.
  - **Gotcha worth remembering:** `days_since_entered_node` is
    node-entry-relative, NOT send-relative.  In a clean run they're within
    minutes (enrol → send), but any republish that re-enrols leads used to
    reset it.  The legacy first email writes NO `lead_step_executions`, so
    the only durable per-lead "first email sent at" signal is the
    timestamp embedded in `brevo_message_id`.
  - Tests: 1 backend (`test_phase16_sequencer.py`:
    `test_reenroll_preserves_entered_current_at_clock` — re-publish keeps a
    5-day-old entry clock instead of resetting to now; `_e()` helper gained
    an optional `condition` arg).  Tests: **backend 1079, frontend 376**.
- **Previously:** **Per-node "Sequence performance" report on the
  campaign Analytics tab.**  The campaign reporting only ever showed the
  FIRST email (the Email-pipeline counters + analytics rates key off
  `lead.send_status` / `email_events`, which the legacy compose→send path
  populates).  Follow-up emails, in-thread replies, waits, and every
  LinkedIn step were invisible.  New card surfaces a per-node funnel for
  the whole sequence.
  - **Backend** — `GET /campaigns/{id}/sequence/analytics` (already
    existed, drives the SequenceBuilder overlay) enriched: `NodeAnalytics`
    now carries `kind` / `title` / `is_entry`; `per_node` is returned in
    **funnel order** (entry node first, then BFS over live edges via
    `_funnel_order`, unreachable nodes trailing in creation order).
  - **Entry-email override (the subtle bit):** the legacy first email
    (compose→`send_lead`) NEVER writes `lead_step_executions` — only the
    sequencer does.  So an entry EMAIL node would show `sent=0` in a pure
    execution funnel even though every first email went out.  The endpoint
    folds the real first-email outcome (`lead.send_status` SENT/FAILED)
    into the entry email node's counts.  `EMAIL_REPLY` can't be an entry;
    a LinkedIn/wait entry IS sequencer-driven so its executions are already
    correct and left alone.  All other (secondary) nodes aggregate
    `lead_step_executions` as before.
  - **Frontend** (`Analytics.jsx` `SequencePerformance`, rendered in
    `AnalyticsContent` between the timeline and the reputation card):
    per-step table (kind badge + title, Sent / Skipped / Failed / Here
    now) + a sequence-status summary (active/completed/pending/halted).
    Only shown when the sequence has >1 live node (nothing secondary to
    report on a plain single-email campaign).  Reuses the existing
    `getSequenceAnalytics` API.
  - **Known gap (documented, not faked):** per-node OPEN/CLICK/REPLY
    rates aren't available — the Brevo events poller matches events only
    by `Lead.brevo_message_id` (the first email's id), so engagement on
    secondary emails isn't even recorded yet (would need event→step
    attribution via the per-step Brevo message_id on
    `lead_step_executions.external_id`).  The funnel reports
    sent/skipped/failed/here, which is accurate.
  - Tests: 2 backend (`test_phase20_analytics.py`: secondary node
    aggregates executions incl. kind/title; entry-email node uses
    send_status) + 2 frontend (`Analytics.test.jsx`: card renders
    secondary steps; hidden for single-node).  Tests: **backend 1078,
    frontend 376**.
- **Previously:** **"Stop research & compose" button greys out
  when the pipeline is idle.**  The red Stop button on the campaign
  Overview is now only red + clickable while research or compose is
  actually pending/running; once everything is composed (or terminally
  failed) it greys out (disabled, "nothing to stop" tooltip) — so the
  user can't fire a no-op stop on a finished campaign.
  - **New `PreviewProgress.pipeline_active`** (`schemas/preview.py`),
    computed in `GET /campaigns/{id}/preview/progress`
    (`routers/preview.py`): True when any lead has `research_status IN
    (pending, running)` OR (`research_status == done` AND
    `compose_status IN (pending, running)`).  The research-DONE guard on
    the compose half matters — a **research-FAILED** lead also sits at
    `compose_status=pending` but will never compose, so it's terminal,
    not active (otherwise the button would stay red forever on a
    partially-failed campaign).
  - **Frontend** (`CampaignDetail.jsx` `OverviewTab`):
    `pipelineActive = !!progress?.pipeline_active`; the button is
    `disabled` + slate-styled when false, red when true.  The
    progress query is already enabled for exactly the previewing/
    running/paused statuses the button shows on, so it's always
    fetched when the button is visible.
  - Tests: 2 backend (`test_phase8_preview.py`: inactive-when-all-
    composed incl. a research-failed lead, active-while-composing) + 1
    assertion on the existing counts test + 2 frontend
    (`CampaignDetail.test.jsx`: greyed+disabled when inactive,
    enabled while active) + 3 existing stop-button tests updated to
    await the enabled state.  Tests: **backend 1077, frontend 373**.
- **Previously:** **Preview the follow-up reply draft before it
  sends.**  The campaign Leads "View email" modal now has a *Preview
  follow-up reply* button that composes — **without sending** — the
  draft an `email_reply` node would deliver for that lead, so the user
  can vet follow-up copy the same way they vet the first email.
  - **Endpoint** `POST /campaigns/{cid}/leads/{lid}/reply-preview`
    (optional `?node_id=` to pick a specific reply step) in
    `routers/campaigns.py`.  Resolves the campaign's live
    (`deleted_at IS NULL`) `email_reply` nodes; composes via the EXACT
    send-time path — subject = `_reply_subject(composed_subject)`, body
    = manual `_substitute(body_template)` OR AI
    `generate_followup_reply(...)` +
    `apply_signature(resolve_campaign_signature)` (the two sequencer
    helpers + compose fn are lazy-imported inside the handler to dodge
    the circular import).  Returns `{node_id, title, ai_compose,
    ai_prompt, subject, body, regenerated_at_send, has_original_email,
    available_nodes}`.  404 when the sequence has no reply step /
    unknown lead / unknown node.  Nothing is sent or mutated.
  - **Honesty caveats:** manual templates preview byte-exact; AI
    replies are regenerated fresh at send time (`regenerated_at_send`),
    so the preview is *representative*.  `has_original_email=False`
    (first email not yet composed/sent) surfaces a "uses an empty
    original" note — the real reply threads onto the original once sent.
  - **Schemas** `ReplyPreviewResponse` / `ReplyPreviewNode` in
    `schemas/lead.py`.  **Frontend** (`CampaignDetail.jsx`
    `LeadEmailModal`): a "Follow-up reply" section with a
    Preview/Regenerate button, a subject+body card, the AI prompt, the
    regenerated-at-send + missing-original caveats, and a node-picker
    `<select>` when the sequence has >1 reply step.  `previewLeadReply`
    in `api/campaigns.js`.
  - Tests: 5 backend (`test_phase44_email_reply.py`: manual exact, AI
    delegates to `generate_followup_reply`, missing-original flag,
    404 no-reply-node, 404 wrong-lead) + 1 frontend
    (`CampaignDetail.test.jsx`).  Tests: **backend 1057, frontend 366**.
- **Previously:** **Contact enrichment for IRS BMF orgs + deferred-
  enrichment queue (gate the review queue).**  IRS BMF pulled many new
  501(c)(3)s but resolved no contact, so they all fell through to
  notification-only and cluttered the review queue.  Now contactless orgs
  NEVER become a ProspectSignal — they park in a deferred queue and a daily
  worker re-tries (new orgs stand up sites within months), promoting them
  once a contact resolves and optionally falling back to direct mail.
  - **`resolve_contact` rewritten → `ContactResult`** (status `resolved` |
    `no_domain` | `no_contact`).  Cheapest-first pipeline: (a) domain — the
    org's site else ONE capped Haiku web-search (`_discover_domain`,
    aggregator hosts rejected); (b) **website scrape** of homepage +
    contact/about/staff/team/leadership pages (`_scrape_contacts`, capped at
    `FUNDING_SCRAPE_MAX_PAGES=6`, on-domain emails only, one Haiku call to
    PAIR names/titles) — the primary free path; (c) Hunter **domain-search**
    fallback; (d) optional **ProPublica** officer-name → Hunter email-finder
    (established 990-filers only); (e) role-priority pick (ED → Development
    → Grants → any named → generic info@ LAST) verified deliverable.  Every
    step soft-fails.  `signal_enrichment` (the "Find contact" feature)
    adapted to the new ContactResult.
  - **`hunter.domain_search(domain)`** (Domain Search, permissive `[]`
    fallback); **`funding_sources/propublica.lookup_org(ein)`** (Nonprofit
    Explorer, best-effort).
  - **`FundingEnrichmentQueue`** (model + migration 0036; status enum
    pending|resolved|exhausted|mailed; UNIQUE `dedup_key` = the eventual
    signal key so promotion never double-emits; cached `website`, `payload`
    with mailing_address, `attempts`/`next_attempt_at` backoff).  BMF
    mailing address now carried on `DiscoveredOrg.mailing_address` +
    `detail` (powers direct mail).
  - **Gating** (`_stage_discovery_signal`): resolved → existing Lead +
    ProspectSignal + task + notification; else → upsert a pending queue row
    (no signal).  Re-discovery of a queued/known org doesn't re-resolve.
  - **Retry worker** `funding.retry_enrichment` (daily 06:00 UTC): re-runs
    `resolve_contact` on due pending rows (batch
    `FUNDING_ENRICHMENT_BATCH=50`); resolved → promote via the shared
    staging path (original dedup_key); else backoff
    (`FUNDING_ENRICHMENT_RETRY_DAYS=[7,30,60]`) until
    `FUNDING_ENRICHMENT_MAX_ATTEMPTS=3` → exhausted, or (when
    `FUNDING_DIRECT_MAIL_FALLBACK`) a campaign-less email-less Lead + a
    "Direct mail —" CRM task using the BMF address → mailed.  acks_late=False
    like the poll tasks.  Autonomy boundary intact (never enrolls a campaign).
  - Tests: 16 new (`test_phase45_funding_enrichment.py`: domain discovery +
    aggregator reject, scrape extract/cap/pair, domain_search no-key,
    resolve_contact statuses + role priority + undeliverable skip, retry
    promote/backoff/exhaust/direct-mail) + 6 updated in phase43 for the
    ContactResult shape + gating.  Migration up/down verified.
    Tests: **backend 1025**.
  - **Follow-up: per-run enrichment cap.**  A 30-day USAspending window
    returns ~2000 orgs; with per-org enrichment an uncapped poll would
    fire thousands of Haiku/Hunter calls + a notification per resolved org
    in one shot.  New `FUNDING_DISCOVERY_MAX_PER_RUN` (default 100):
    `_stage_all` stops after enriching that many NEW (non-deduped) orgs and
    logs how many were left.  Dedup means each daily run advances through
    the backlog, so the cap bounds cost without dropping anyone.  Result
    dict gains `capped`.  1 test.  Tests: **backend 1026**.
  - **Follow-up: cap lowered 100 → 25 (rate-limit safety).**  A live
    USAspending run at cap=100 (a) **drained the Anthropic credit balance**
    mid-run — every domain-discovery web_search 400'd with "credit balance
    is too low", so all 100 orgs got `no_domain` → queued (correct graceful
    degradation; nothing lost — the daily retry re-resolves once credits
    return), and (b) tripped Hunter's free-tier `429 Too Many Requests`.
    Each org costs ~1 Anthropic web-search + 1-3 Hunter calls, so 100/run is
    too aggressive for free tiers.  Dropped `FUNDING_DISCOVERY_MAX_PER_RUN`
    + `FUNDING_ENRICHMENT_BATCH` defaults to 25.  Resolution itself verified
    working (live re-test resolved `info@pedaids.org` for Elizabeth Glaser
    via website scrape).  Raise on paid Anthropic + Hunter tiers.
- **Previously:** **Fixed the discovery Stop button (redelivery storm
  + cooperative stop).**  User reported Stop didn't stop a USAspending run
  — it kept "pulling."  Root cause: `task_acks_late=True` +
  `broker_transport_options.visibility_timeout=300s`, but a funding poll
  (esp. the now-30-day USAspending window enriching ~100 orgs) runs longer
  than 5 min, so Redis REDELIVERS the unacked message → concurrent copies.
  Stop killed the visible copy while another was redelivered moments later.
  - **`acks_late=False` on both poll tasks** (`funding.poll_usaspending` /
    `funding.poll_irs_bmf`, overriding the global True) — the message is
    acked on receipt, so a long run is NEVER redelivered into duplicate
    copies.  A periodic poll lost on a worker crash just re-runs next tick.
  - **Cooperative stop flag** (`funding:stop:<source>` in Redis, 900s TTL):
    `stop_funding_run` now raises it FIRST (before revoke/purge), and
    `_stage_all` polls it before each org and aborts the batch promptly —
    so Stop works even if the SIGKILL races or the child is mid-`httpx`
    await.  Each poll `_clear_stop`s at start so a prior Stop can't wedge
    the next run.  `stop_funding_run` keeps the revoke+terminate+broker
    purge as the hard backstop; response gains `stop_flagged`.
  - Tests: 3 backend (`test_phase43_funding_discovery.py`: `_stage_all`
    aborts on flag, stop raises the flag, poll clears a stale flag).
    Tests: **backend 1009**.
- **Previously:** **Fixed USAspending discovery returning 0 signals
  (trailing window instead of an advancing cursor).**  Diagnosed a feed
  configured with `lookback_days=1` surfacing nothing.  Root cause: the
  poll advanced its cursor (`last_action_date → today`) every run, so
  after the first poll the query window was always `[last_run_day, today]`
  (~1 day) regardless of the configured lookback — and USAspending
  `action_date` data lags reporting by days-to-weeks, so back-dated awards
  that only just became visible were permanently skipped (the cursor had
  already moved past them).  Live-verified: a 1-day window returns 0; a
  30-day window returns 100+ nonprofit grants; the `nonprofit` filter is
  valid.
  - **Fix** (`workers/funding_signals.poll_usaspending`): always query a
    TRAILING window `since = today - lookback_days` (no since-cursor).
    Re-scanning the overlapping window each poll is free — the
    `dedup_key` guard (`grant_awarded:<award_id>` + the UNIQUE on
    `prospect_signals`) makes an already-seen award a no-op, so no
    duplicate signal/lead/Anthropic/Hunter work.  Cursor now stores
    `last_window_start` + `last_run_date` for display only.
  - **Default lookback 7 → 30** (`config.py` + compose) — 7 is too narrow
    for a source that lags; 30 covers typical reporting lag.  (IRS BMF is
    unaffected — it has its own ruling-month cursor + first-run guard.)
  - Tests: 2 backend (`test_phase43_funding_discovery.py`: trailing-window
    since/until + cursor shape; re-scan dedup makes overlap a no-op) —
    replaces the old advance-cursor test.  Tests: **backend 1006**.
- **Previously:** **"Reply to previous email" sequence node
  (AI-written or manual).**  A new builder node (`email_reply`, migration
  0035) that replies IN-THREAD to the lead's original campaign email
  instead of starting a new thread.  The body is either AI-written
  (guided by a generalized prompt the user types) or a manual template.
  - **Threading**: `brevo.send_email` gained `in_reply_to` → sets
    `In-Reply-To` + `References` headers (normalised to `<...>` via
    `_wrap_message_id`) so the recipient's client threads it as a reply.
    The node threads to `lead.brevo_message_id` (the original email's
    RFC Message-ID) with subject `Re: {lead.composed_subject}`
    (`_reply_subject`, idempotent — no `Re: Re:`).  Skips with "no
    previous email to reply to" when the original was never sent.
  - **AI vs manual**: node `config` = `{ai_compose, ai_prompt,
    body_template}`.  AI mode calls new
    `compose.generate_followup_reply(...)` (Sonnet) — reuses the lead's
    EXISTING `research_data`, so **no new research runs** — steered by
    `ai_prompt` (the generalized idea) + the original email for context,
    then applies the campaign's effective signature.  Manual mode
    substitutes `body_template`.
  - **Sequencer**: `_send_email_step_async` handles both EMAIL and
    EMAIL_REPLY (snapshots context, composes outside the session, sends
    with `in_reply_to`); the beat dispatch + `_has_downstream_email`
    treat the two kinds together.  `EMAIL_REPLY` added to
    `PUBLISHABLE_KINDS_M1`; `validate_graph` rejects it as an entry node
    and requires body-or-ai.
  - **Builder**: "Reply" palette item + node editor (AI toggle → prompt
    textarea, else reply-body textarea) + indigo node dot.
  - Tests: 12 backend (5 reply-worker in `test_phase16_sequencer.py`,
    3 threading in `test_phase9_brevo.py`, 4 publish-validation in new
    `test_phase44_email_reply.py`) + 1 frontend palette assertion.
    Tests: **backend 1005, frontend 350**.
- **Previously:** **Editable campaign goal — rewrites unsent emails
  (no new research).**  The Overview tab now has a Goal editor.  Saving a
  new goal on a non-complete campaign re-composes every already-composed,
  not-yet-sent email so the new goal takes effect immediately; sent
  emails are left alone.  **No research re-runs** — re-compose reuses each
  lead's existing `research_data` (research is a separate task).
  - **`update_campaign`**: `goal` moved into `_status_exempt_fields` so
    it's editable on running/paused/approved (the other content fields —
    tone, sender_*, research_mode, templates — stay draft/previewing
    gated); explicit 409 on COMPLETE.  When `goal` actually changes (value
    differs) on a non-DRAFT campaign, snapshots every
    `compose_status=DONE` lead with `send_status != SENT` and dispatches
    `compose_lead.delay(...)` for each after commit.  Unchanged-goal saves
    and DRAFT saves dispatch nothing.
  - **Frontend** `GoalEditor` card (Overview, above Campaign config; Goal
    removed from the read-only config list): read-only value + Edit →
    textarea + Save/Cancel, with a hint that saving rewrites unsent emails
    using existing research (no new research).  Hidden Edit on COMPLETE.
    Toast: "Goal saved — unsent emails are being rewritten" (or just
    "Goal saved" on draft).
  - Tests: 4 backend (`test_phase4_campaigns.py`: running recomposes only
    unsent, unchanged-goal no-op, draft no recompose, COMPLETE 409; +2
    pre-existing guard tests repointed from `goal` to `tone`) + 2 frontend
    (`CampaignDetail.test.jsx`: save via updateCampaign, read-only on
    complete).  Tests: **backend 993, frontend 350**.
- **Previously:** **Campaign signature inherits from Settings, with
  a per-campaign override.**  The campaign Leads-tab signature editor
  used to be a blank box even though the user had already set a signature
  on their connected account in Settings.  Now a campaign with no
  signature of its own INHERITS the bound connected account's signature;
  the editor shows that inherited signature (read-only preview) with a
  "Customize for this campaign" button to override, and a "Use Settings
  signature" button to revert the override.
  - **Shared resolver** `signature.resolve_campaign_signature(session,
    campaign)` — returns `campaign.signature` if set (the per-campaign
    OVERRIDE), else the bound `ConnectedAccount.signature` (Settings),
    else None.  Lazy-imports `ConnectedAccount` so the string-utility
    module stays DB-free at import.
  - **Compose** (`workers/compose.py`) now resolves the effective
    signature via that helper instead of reading `campaign.signature`
    directly, so AI emails get the Settings signature when the campaign
    has no override.  **`POST /campaigns/{id}/apply-signature`** applies
    the effective signature too (400 only when NEITHER campaign nor
    account has one); message updated.
  - **`CampaignResponse.account_signature`** (new field) carries the
    bound account's signature so the UI can show what's inherited;
    `campaign.signature` stays the override (None = inherit).
  - **Frontend** `SignatureEditor` rewritten: inherited-preview mode
    (emerald "Using your Settings signature" pill + HTML preview, or an
    amber "no signature set" note) vs override mode (textarea + Save +
    "Use Settings signature" revert).  "Apply to all emails" enables on
    the effective signature (override or inherited).
  - Tests: 4 backend (`test_phase4_campaigns.py`: response exposes
    account_signature, apply uses account when no override, override
    wins, resolver fallback chain) + 2 frontend (inherited preview shown
    / no textarea until customize; revert clears override) + 1 updated
    (save now clicks Customize first).  Tests: **backend 989, frontend
    348**.
- **Previously:** **"Find contact" — on-demand light contact
  enrichment for notification-only signals.**  Discovery signals whose
  org had no resolvable contact at discovery time land in the queue
  notification-only (no `lead_id`).  A per-signal "Find contact" button
  retries the SAME low-cost resolver on demand and, on a hit, stages +
  links a campaign-less Lead so the existing Draft / Send /
  Add-to-campaign flows light up.
  - **LinkedIn search included** — one capped Haiku web-search
    (`_find_linkedin_decision_maker`, reusing `signal_detection._web_lookup`)
    finds the org's top decision-maker + their public `/in/` LinkedIn
    profile + the org domain.  That name drives Hunter's **Email Finder**
    (name+domain, more accurate than a blind domain search) with a
    `verify_email_hunter` deliverability gate; the role-priority domain
    search is the fallback.  The returned domain seeds `org.website` so
    the fallback doesn't spend a second web-search — net cost is ONE
    Haiku web-search per enrich (only when no contact exists yet).  The
    found `linkedin_url` is stored on the staged lead, and is returned
    even when no email resolves (so there's still a usable contact path;
    non-`/in/` URLs like company pages are dropped).
  - **Service** `services/signal_enrichment.enrich_signal_contact(db,
    signal)` — rebuilds a `DiscoveredOrg` from the signal (org name from
    `detail.org_name`, else parsed from the IRS / USAspending summary;
    state/ein/ntee/website from `detail`), runs the LinkedIn→Hunter→role
    resolution, then find-or-creates a campaign-less Lead by canonical
    email (backfilling `linkedin_url`) and sets `signal.lead_id`.
    Idempotent: a signal that already has a contactable lead returns that
    contact with `already_had_contact=True` and spends nothing (neither
    the LinkedIn lookup nor the resolver runs).  Stays inside the
    autonomy boundary — never sets `campaign_id` / enrolls a sequence.
  - **Worker change** — `_stage_discovery_signal` now persists
    `org_name` / `website` / `state` / `ein` / `ntee_code` into the
    signal `detail` so later enrichment rebuilds the org without
    re-parsing the summary (existing rows fall back to summary parsing).
  - **Endpoint** `POST /signals/{id}/enrich` → `SignalEnrichResponse`
    (found, email, names, title, generic, lead_id, lead_created,
    already_had_contact); 404 unknown.
  - **A lead is ALWAYS staged when any contact is found — including
    LinkedIn-only** (no email).  `leads.email` is now NULLABLE
    (migration 0034) so an email-less LinkedIn lead can exist; the
    enrichment dedups by email when present, else by `linkedin_url`.
    The campaign-bound send path is unaffected (a null-email lead is
    campaign-less and never composes/sends).  `LeadSummary.email` →
    `str | None`.  `enrich_signal_contact` returns `has_email`; the
    signals list serializer exposes `lead_has_email` (one batched query)
    so the UI shows "Draft & send" only for emailable leads and "View
    lead" otherwise.
  - **Frontend** (`pages/Signals.jsx`): contactless `new` signal cards
    show a "Find contact" button; an email-less linked lead shows "View
    lead" (not "Draft & send").  The detail modal's no-contact panel
    gains a "🔎 Find contact (web + LinkedIn)" button that, on a hit,
    flips to the draft flow for an emailable contact, or to a
    "Lead created — LinkedIn only" panel (with a clickable profile link)
    when no email resolved.  Toast reports lead created/updated + the
    email or "from LinkedIn profile (no email found)".
  - Tests: 11 backend (`test_phase43_funding_discovery.py`: finds +
    links, summary-parse org name, no-contact, already-has-contact skips
    lookup, reuses existing lead by email, LinkedIn name drives Hunter,
    LinkedIn-only creates an email-less lead, non-profile URL dropped,
    `lead_has_email` in the feed, 404) + 4 frontend (`Signals.test.jsx`).
    Tests: **backend 985, frontend 346**.
- **Previously:** **Bulk add signals to a campaign — emails run
  automatically.**  The Signals feed now lets the user select any
  number of `new` signals (per-card checkbox), pick a target campaign,
  and copy their staged leads into it; the campaign's normal
  research→compose→send pipeline takes over from there.  Stays inside
  the autonomy boundary — discovery never auto-enrolls; this is the
  explicit human "one click" action.
  - **Shared core** `services/campaign_membership.add_leads_to_campaign`
    — extracted from the Leads-page bulk-add so both entry points share
    one copy+enroll+research-kick path.  COPY, not move
    (`leads.campaign_id` cascades on campaign delete, so reassigning a
    CRM/signal lead would let routine deletion destroy its history): a
    fresh `Lead` row enters the pipeline, the source row is untouched.
    De-dupes ids, skips suppressed / already-in-campaign / missing /
    in-batch dups (all counted, none error), `ensure_default_sequence`
    + `enroll_leads`, then for non-DRAFT campaigns kicks
    `run_campaign_research.delay`.  Returns `AddLeadsResult`.  The
    Leads router endpoint now delegates to it (behavior-identical).
  - **Endpoint** `POST /signals/add-to-campaign` ({signal_ids,
    campaign_id}) — 404 unknown campaign, 409 COMPLETE; loads the
    signals, keeps only those with a `lead_id` (`skipped_no_contact`
    counts the rest), copies via the shared core, then bulk-flips the
    contactable signals to ACTIONED in one `update(...)`.  Response
    carries the add counts + `research_started` + `signals_actioned`.
  - **Frontend** (`pages/Signals.jsx`): per-card checkbox on `new`
    signals (`stopPropagation` so it doesn't open the detail modal); a
    bulk bar appears once anything is selected with a campaign picker
    (complete campaigns filtered out) + Add button; success toast
    summarises added/skipped and notes "emails will run automatically",
    clears the selection, invalidates the feed.
  - Tests: 4 backend (`test_phase43_funding_discovery.py`: copies +
    actions, draft-campaign defers research, COMPLETE 409, unknown 404)
    + 2 frontend (`Signals.test.jsx`: select reveals bar / excludes
    complete / Add calls API; checkbox doesn't open modal).  Tests:
    **backend 975, frontend 342**.
- **Previously:** **Manual Stop button for nonprofit discovery
  feeds.**  Diagnosed a runaway IRS BMF run that was burning Anthropic
  tokens: the broker `visibility_timeout` is **300s** (`celery_app.py:49`)
  but an IRS run (per-state CSV download + Haiku enrichment per org)
  runs far longer, so Redis kept restoring the message and stacking
  **concurrent copies** of the same task — each enriching orgs in
  parallel.  Killed it manually (revoke+terminate + purge the funding
  message from Redis `unacked`/`unacked_index` + worker restart), then
  shipped a UI Stop button so it's a one-click recovery:
  - `funding_signals.stop_funding_run(source)` — revokes + SIGKILLs
    every active/reserved Celery task whose name is `funding.poll_<source>`
    (across all workers, via `control.inspect`), then
    `_purge_broker_messages` removes matching messages from the ready
    `celery` list AND the `unacked` hash + `unacked_index` zset so a
    long run can't be redelivered past the visibility timeout.  Safe
    no-op (zero counts) when nothing is running.  Per-source: stopping
    one feed never touches the other or the `send.send_lead` backlog.
  - `POST /signals/funding/sources/{source}/stop` runs it in a
    threadpool (Celery control + sync redis are blocking), sets
    `last_run_status="stopped"` only when it actually killed/purged
    something, returns the `stopped` counts.
  - Worker now stamps `last_run_status="running"` at run start (before
    the slow I/O commit) so the UI can show in-flight state; the
    Discovery tab polls every 5s while any feed is `running`.
  - Frontend: red **Stop** button on each Discovery feed card (solid
    red while running, outline otherwise); toast reports tasks
    terminated or "No <feed> run was in progress".
  - Tests: 6 backend (`test_phase43_funding_discovery.py`: revoke
    matching/terminate, idle no-op, purge-only-matching with a fake
    redis, endpoint marks-stopped / noop-keeps-status / unknown-404)
    + 2 frontend (`Settings.test.jsx`).  Tests: **backend 966,
    frontend 337**.  **Follow-up worth doing:** raise
    `visibility_timeout` well above the worst-case IRS run time so the
    redelivery storm can't happen in the first place.
  - Previously: **CRM & inbox AI agent** (2026-06-12 snapshot at
  the bottom of this file has full detail).  Previously: **Social Listening Radar — intent-feed for
  LinkedIn posts.**  New sidebar item with two tabs (Feed + Searches).
  The user types a plain-English topic ("frustrated with our IT
  provider"), Claude (Haiku) fans it out to 15-30 LinkedIn search
  phrases, Claude (Sonnet+web_search) finds matching public posts,
  Claude (Haiku) scores each post 1-10 for buying intent + drafts a
  suggested comment / connection request / follow-up DM.  **All
  LinkedIn writes stay manual** — the feature never auto-posts; the
  Copy buttons drop suggested copy onto the clipboard.
  - **3 new tables** (migration 0014): `social_listening_searches`,
    `social_listening_posts` (unique on `(provider, post_url)`),
    `social_listening_opportunities` (1:1 with post via UNIQUE
    `post_id`).  7 new enums for source / frequency / status /
    provider / category / action / opp-status.
  - **Discovery is Anthropic web_search**, NOT Unipile.  Unipile's
    raw passthrough is on a narrow allowlist and `/posts/search`
    isn't on it.  We use the same `web_search_20250305` tool already
    powering `web_research.py` + `research_client.py`.  URL filter
    drops hallucinated links (must contain `linkedin.com/posts/` or
    `/feed/update/`).
  - **Per-search frequency** via the sequencer pattern: a new beat
    task `social_listening.scheduled_runner` runs every 60s, selects
    `status=active AND frequency!=manual AND next_run_at<=now AND
    last_run_status!=running`, dispatches `run_social_search.delay(...)`
    for each.  `last_run_status != "running"` is NULL-safe via
    `or_(is_(None), != "running")` (vanilla SQL `NULL != 'x'` would
    silently exclude fresh rows).
  - **Soft cost caps per search** (`max_queries_per_run` default 20,
    `max_posts_per_query` 30, `max_qualified_per_run` 100) keep a
    runaway topic from burning ~$50 on Anthropic.  Default config
    runs ~$0.50-$1 per full search.
  - **Re-qualification overwrites AI fields but preserves user-set
    `status` and `notes`** via `on_conflict_do_update(set_={...AI
    fields only})`.  So a user marking an opportunity "Saved" or
    adding CRM notes won't have those wiped on re-run.
  - **Suggested copy length-capped** at 500 chars (comment), 280
    chars (connect note), 600 chars (follow-up) via
    `compose_client._truncate_at_sentence` — same helper the
    research-a-client tool already uses.
  - **Shared `_anthropic.py` helpers** — when the third copy of
    `get_client()` / `extract_text()` / `parse_json_*()` was about
    to land, factored those into `app/services/_anthropic.py`.  The
    existing `web_research.py` + `research_client.py` keep their own
    inline copies (no scope creep into refactoring them).
  - Frontend: 9 new tests + new `SocialRadar.jsx` page with Feed +
    Searches tabs, opportunity cards with copy buttons + score badge
    + status pill + per-row status dropdown, and an editor modal with
    a "Preview queries" button that runs the AI expansion
    synchronously so you can see what Claude generated before saving.
  - Tests: 47 new backend + 9 new frontend.  Tests: **backend 610,
    frontend 190**.
  - **Follow-up: stricter qualifier rubric + re-qualify-all endpoint.**
    After Reddit-RSS started returning hundreds of real posts, the
    qualifier was scoring most of them generously — a score-9 "buying
    signal" was *"What obligations do I have to an ex employer?"*
    (employment-law question), a score-8 was vendor self-promotion
    ("we built X"), and a score-6 was unrelated humanitarian aid.
    Two changes:
    1. **Shared ``_RUBRIC`` constant** consumed by BOTH the single-post
       and batch prompt templates so they apply identical strictness:
       - Required checklist: must hit ≥2 of (specific vendor named,
         buying event mentioned, decision-maker title, time-bound
         urgency) to score ≥ 5.
       - Explicit named anti-patterns ALWAYS score 1: career/job posts,
         vendor self-promotion, blog/listicle/article content, for-sale
         listings, news/commentary, recruiting posts, off-topic personal,
         generic rants with no vendor.
       - Score 10 reserved for decision-maker + specific vendor +
         buying event + urgency; score 7-9 walks down from there.
       - ``buying_signal=true`` ONLY when score ≥ 5.
       - Suggested-copy fields are empty strings when action=ignore
         (no more wasted output tokens on rejected posts).
    2. **``POST /social-radar/searches/{id}/requalify-all``** endpoint
       enqueues batch-qualify for every post in the search.  Lets the
       user rescore the whole backlog after a rubric tighten without
       running discovery again.  Preserves user-set ``status`` /
       ``notes`` (already the case in ``_upsert_opportunity``).
       Frontend: "Re-score all" button on the detail header next to
       Run now / Edit / Clean up.  Cost: ~$0.10 per 100 posts (Haiku-
       batched at 10/call).
    Live verification on csuite (290 posts): old rubric scored
    66 posts as 1, 18 as 2, 7 marked buying-signal at 5+.  New rubric:
    269 of 290 correctly at score 1 (93%), only 6 real buying signals
    surface — and the top ones are actual buying-intent posts like
    "Aircall vs Nextiva" and "How do you prevent cloud vendor lock-in
    when planning an ERP".  4 new tests (prompt-contains-anti-patterns,
    batch-shares-rubric, requalify enqueue, requalify empty no-op).
    Tests: **backend 656**.

  - **Follow-up: direct Reddit RSS replaces Anthropic for Reddit
    discovery.**  After all the prompt tuning, runs were STILL
    returning ``total_raw=0`` across every pair.  Direct probe of
    Anthropic confirmed:
    > *"the site-specific search for Reddit did not return usable
    > results"*
    Brave (the engine behind Anthropic's ``web_search_20250305``)
    deprioritizes Reddit pages, so even perfect queries like
    ``"ringcentral alternatives"`` returned nothing.  This was a
    structural limit of the discovery channel, not the prompts.
    Replaced with direct hits against Reddit's free RSS endpoint
    ``reddit.com/search.rss?q=<query>&sort=new&type=link``:
    - New ``app/services/social_listening_reddit_api.py``: parses
      the Atom feed via stdlib ``xml.etree.ElementTree``, extracts
      title + body + author + subreddit + post_date from each
      ``<entry>``, returns the same ``DiscoveryResult`` shape so the
      worker doesn't care which backend surfaced the posts.
    - ``discover_posts`` is now a front-door dispatcher: ``source=
      reddit`` routes to the new RSS service; ``linkedin`` /
      ``twitter`` still go through Anthropic.  Old Anthropic-path
      function renamed to ``_discover_via_anthropic``.
    - **Twitter dropped from default sources.**  Anthropic
      web_search can't reach it (same Brave issue) and the X API v2
      is paid-only ($100/mo).  ``SocialListeningSearchCreate.sources``
      now defaults to ``[linkedin, reddit]``.
    - **Router gate added**: ``run-now`` 409s on paused/archived
      searches (previously the worker would dispatch + bail, wasting
      a task slot).
    - **NOT** adding ``t=month`` to the Reddit RSS params: it filters
      Reddit-side and very frequently returns an empty feed.
      ``sort=new`` + our own ``max_post_age_days`` filter does the
      job better.
    Live verification on csuite search: 0 raw → **475 raw, 303 kept,
    289 new posts at $0.00 discovery cost** in a single run.  Tests:
    5 new for the Reddit service + 1 router gate + 5 existing-fixture
    updates (sources/dispatch changes).  Tests: **backend 652**.
  - **Follow-up: web_search recall bundle — shorter queries, multi-
    variation prompt, lenient date gate for Reddit/Twitter.**  After
    the cost-cut work, a clean run still returned `total_raw=0` across
    40 (source, query) pairs.  Root cause: topic expansion was
    generating verbose 10+ word sentences ("anyone else fed up with
    their msp not returning calls").  Web search engines treat 10+
    word strings as near-exact-match, so they returned zero hits.
    Three coordinated fixes:
    1. **Expansion produces SHORT keyword queries.**  Prompt rewritten
       with keyword-style examples ("msp slow response time",
       "ringcentral alternatives small business").  Server-side
       enforces `3 ≤ word_count ≤ 8`.  ``include_keywords`` also pass
       through the same min-words filter now (users were typing
       single-word junk like "hacked", "phished" that surfaced SEO
       listicles).
    2. **Discovery prompts handle long queries gracefully.**  Reddit +
       Twitter prompts explicitly say "if the query is long, break it
       into 2-3 short keyword variations and search each one."  Bumped
       `SOCIAL_DISCOVERY_WEB_SEARCH_MAX_USES` default 2 → 3 so Claude
       has the budget to try those variations.  Small cost bump,
       large recall lift.
    3. **Date gate lenient for Reddit/Twitter (still strict on
       LinkedIn).**  Reddit `/comments/<id>/` URLs and Twitter
       `/status/<id>` URLs are intrinsically time-ordered; web-search
       ranking surfaces fresh first.  Undated posts from those
       sources now pass through and land in a new ``kept_undated``
       counter (separate from ``dropped_undated``).  LinkedIn keeps
       the strict gate (undated LinkedIn results are usually stale
       SEO articles).  Bonus: a Twitter snowflake decoder
       (`_twitter_id_to_date`) extracts the real post timestamp from
       any `/status/<id>` URL when Anthropic didn't.
    Tests: 5 new (drops too-long, drops short include_keywords, Reddit
    keeps undated, LinkedIn still drops undated, Twitter snowflake
    decoder).  Tests: **backend 646, frontend 208**.
  - **Follow-up: cost-cut bundle — LinkedIn web-search off by default,
    pre-run estimate, batched qualification, mid-run cost cap.**  After
    racking up ~$20 in testing the user asked for cost reductions.
    Three coordinated changes:
    1. **`linkedin_web_search_enabled` (BOOL, default FALSE)** on
       `social_listening_searches` (migration 0019).  LinkedIn web
       search runs on Sonnet with max_uses=5 — most expensive part
       of a run, and it rarely produces results because LinkedIn
       blocks indexing.  Worker now skips the (linkedin, query)
       pairs entirely when this is False; the watchlist remains the
       reliable LinkedIn channel.  ~75% per-run cost cut for the
       typical case.
    2. **`GET /social-radar/searches/{id}/estimate`** returns
       `{discovery_pairs, discovery_cost_usd, qualify_cost_usd,
       total_cost_usd, max_run_cost_usd, notes}` using a per-source
       price table in new `app/services/_anthropic_cost.py`.  Detail
       page "Run now" button now reads `Run now (~$0.32)` so the user
       sees spend BEFORE clicking.
    3. **`qualify_social_posts_batch`** task batches 10 posts per
       Anthropic call (was 1 per call).  New
       `qualify_posts(list_of_inputs)` in the qualifier service uses
       a structured JSON-array I/O.  Worker dispatches the batch
       task instead of per-post.  Cuts qualify cost ~70% (from
       ~$0.005/post to ~$0.0012/post on Haiku).  The single-post
       `qualify_social_post` task is kept around for backward compat
       with anything already in the queue.
    4. **`max_run_cost_usd` (NUMERIC, default $1.00)** per-search cap.
       Worker tracks actual Anthropic spend via `message.usage`
       tokens × price table, fans out discovery in chunks of 5, and
       aborts BEFORE the next chunk if running cost crosses the cap.
       Status flips to `cost_capped` (distinct from `done`) and
       `last_run_error` reads "Cost cap reached: spent $X of $Y after
       N of M calls — raise the cap or narrow the search."
    Frontend: cost-controls section in editor modal (toggle + cap input),
    Run-now button shows estimated cost.  6 new backend tests + the
    20 existing-fixture flag updates.  Tests: **backend 642, frontend
    208**.  Typical per-run cost dropped from ~$3-4 to ~$0.30-0.50.
  - **Follow-up: LinkedIn boost + Unipile watchlist.**  Two changes
    to make LinkedIn actually productive:
    1. **Per-source web_search budget + model.**  Discovery used to
       use the same Haiku + max_uses=2 for every source.  LinkedIn
       gets so little back from web_search (the platform blocks
       indexing) that those defaults found ~0 posts.  New settings
       `LINKEDIN_DISCOVERY_MODEL=claude-sonnet-4-6` and
       `LINKEDIN_DISCOVERY_WEB_SEARCH_MAX_USES=5` mean LinkedIn
       queries run on Sonnet with a 2.5x bigger search budget.
       Reddit + Twitter stay on the cheap default (Haiku, 2 uses)
       since they're well-indexed.  `_source_model()` /
       `_source_max_uses()` in the discovery service do the dispatch.
       Also: `linkedin.com/pulse/` URLs now count as valid LinkedIn
       results (Pulse is the indexed long-form path; carries real
       buying-intent).  LinkedIn prompt rewritten to instruct
       multiple search strategies (`site:linkedin.com/posts`,
       `site:linkedin.com/pulse`, broader queries, archive.org).
    2. **`linkedin_profile_watchlist` JSONB column** (migration
       0018) — per-search list of LinkedIn profile URLs to monitor
       directly via Unipile, bypassing indexing entirely.  Worker
       picks the first OK LinkedIn account in the workspace, calls
       new `UnipileLinkedInProvider.recent_posts(account, profile,
       limit=10)` per profile, upserts the returned posts with
       `provider=linkedin` and `discovered_via=watchlist:<url>`.
       Per-profile stats land under `last_run_stats.watchlist`.
       Soft-fails (every profile gets `no_account=True`) when no
       LinkedIn account is connected.  Frontend: textarea in
       editor modal, count in detail header.  4 new backend tests
       (2 per-source dispatch + 2 watchlist) + 2 frontend.  Tests:
       **backend 636, frontend 207**.
  - **Follow-up: multi-source discovery (LinkedIn + Reddit + Twitter/X).**
    After the expansion + diagnostics work, user's runs were still
    returning 0 posts.  Root cause: **Anthropic web_search has poor
    recall on LinkedIn** — LinkedIn aggressively blocks indexing so
    even well-formed queries surface SEO listicles, not personal
    posts.  Reddit (r/sysadmin, r/msp, r/networking, r/nonprofit,
    r/ITManagers, r/k12sysadmin, etc.) and public X/Twitter ARE
    heavily indexed and constantly have buying-intent signal.
    Migration 0017 adds:
    1. `reddit` and `twitter` values to the `social_post_provider`
       enum (PG `ALTER TYPE ADD VALUE` inside Alembic txn, safe on
       PG 12+ since we don't USE them in the same txn).
    2. `sources` JSONB array on `social_listening_searches`,
       default `["linkedin"]` for existing rows (backfilled from the
       legacy single `source` column), default
       `["linkedin", "reddit", "twitter"]` for new searches via the
       Create schema.  Old single-`source` column is kept as a
       deprecated shim.
    Discovery service split into per-source prompts + URL validators:
    - LinkedIn: `linkedin.com/posts/` + `linkedin.com/feed/update/`
    - Reddit: `reddit.com/r/<sub>/comments/<id>/` + `redd.it/<id>`,
      Reddit-specific prompt naming popular IT subreddits.
    - Twitter/X: `twitter.com|x.com/<user>/status/<id>` (bare profile
      URLs dropped — must include `/status/`).
    Worker now fans out across every (source, query) pair concurrently
    (semaphore=4) and tags each `social_listening_posts` row with the
    correct provider.  Per-query stats now key on `(source, query)`
    too — the detail Activity stats table gained a Source column.
    Frontend: 3-button source picker in the editor modal (default all
    3 on); detail header shows the configured sources list.  6 new
    backend tests + 2 frontend.  Tests: **backend 632, frontend 205**.
  - **Follow-up: better expansion + editable queries + per-query
    diagnostics.**  User's first runs returned ~0 posts because topic
    expansion was generating single-word junk like `"internet"`,
    `"phone"`, `"voip"`, `"crash"` — Anthropic web_search returns SEO
    listicles for those, not LinkedIn posts.  Three changes:
    1. **Stronger expansion prompt** in
       `services/social_listening_topic_expander.py`: explicit GOOD
       vs BAD examples, 4-word minimum, "phrases a human would TYPE
       in a LinkedIn post" framing.  Server-side `_MIN_WORDS_AI_GENERATED
       = 4` drops short AI output (user-supplied `include_keywords`
       bypass — explicit override).
    2. **Editable expanded queries** via PATCH.  `expanded_queries:
       list[str] | None` on the Update schema.  Frontend modal gains a
       multi-line textarea (edit-mode only) so the user can hand-tune
       the list.  Editing does NOT trigger re-expansion — only a
       topic change does.
    3. **Per-query diagnostics.**  `discover_posts` now returns
       `DiscoveryResult(posts, raw, dropped_invalid_url,
       dropped_excluded, dropped_duplicate, dropped_undated,
       dropped_stale, kept)`.  Worker aggregates per-query +
       `summary` and persists on `social_listening_searches.last_run_stats`
       (JSONB, migration 0016).  Detail Activity card now has a
       collapsible per-query stats table showing
       Phrase / Raw / Kept / New / Stale / Undated / Seen / Dup / Bad URL.
       Lets the user see which phrases produced posts and which were
       duds so they can iterate the list.
    Tests: 2 expander + 1 worker (stats persistence) + 1 router
    (editable queries) + 2 frontend (stats panel + editable textarea)
    + 5 existing fixture updates (short-phrase fixtures became invalid).
    Tests: **backend 626, frontend 203**.
  - **Follow-up: strict freshness filter + cleanup endpoint.**  The
    first cut of the lookback window kept posts with no parsable
    `post_date` ("kept undated").  In practice Anthropic web_search
    returned a lot of undated SEO/listicle results that turned out to
    be ancient — the user's csuite search had 133 posts where 101 had
    dates (all old, oldest 2018) and 32 were undated.  Two fixes:
    1. **Strict filter** in `discover_posts`: when `max_post_age_days`
       is set, drop posts with no `post_date` AND posts whose date is
       past the cutoff.  Prompt was strengthened — Anthropic is now
       told to OMIT THE WHOLE POST if it can't determine a date with
       confidence, and to return `{posts: []}` rather than padding
       with stale matches.
    2. **Cleanup endpoint** `POST /social-radar/searches/{id}/cleanup-stale`
       deletes posts (and cascading opportunities) where
       `post_date IS NULL OR post_date < now - max_post_age_days`.
       Returns `{deleted: N}`.  Surfaced as a "Clean up stale"
       button in the detail view header (amber styling — destructive
       but not as destructive as Delete).  Confirm dialog warns the
       user that saved/commented opportunities on stale posts will
       be lost.
    Tests: 1 strict-filter (replaces the old "undated passes through"
    test, plus 4 fixture updates) + 3 cleanup endpoint + 1 frontend.
    Tests: **backend 622, frontend 201**.
  - **Follow-up: per-search lookback window (default 30 days).**  New
    `social_listening_searches.max_post_age_days` column (migration
    0015, NOT NULL DEFAULT 30, range 1-3650).  Editable on create AND
    PATCH.  Enforced two ways in `discover_posts`:
    1. The prompt embeds the cutoff date — "only consider posts on or
       after YYYY-MM-DD (within the last N days). Today's date is …"
    2. Defensive post-filter: if a returned post's parsed `post_date`
       is older than the cutoff, drop it.  Posts with no parsable
       date pass through (the LLM occasionally omits the date field;
       we'd rather keep a borderline match than drop a real signal).
    Frontend: new "Look back (days)" number input in the editor
    modal (default 30) AND a "lookback: N days" line in the detail
    view header.  Use cases: trend research → widen to 90; real-time
    intent → tighten to 7.  5 new backend tests + 2 frontend tests.
    Tests: **backend 619, frontend 200**.
  - **Follow-up: discovery cost reduction (Sonnet → Haiku).**  A
    single full run was costing $5+ because `social_listening_discovery`
    used `settings.ANTHROPIC_MODEL` (Sonnet) and each of the
    `max_queries_per_run` calls ingested ~90K tokens of web-search-
    result pages on Sonnet input pricing.  Same lesson the bulk
    research pipeline learned in 2026-05-19.  Added two new settings:
    - `ANTHROPIC_SOCIAL_DISCOVERY_MODEL` (default
      `claude-haiku-4-5-20251001`) — discovery is extraction-from-
      search-results, exact same task profile as `research_person_web`.
    - `SOCIAL_DISCOVERY_WEB_SEARCH_MAX_USES` (default `2`, down from
      the shared `RESEARCH_WEB_SEARCH_MAX_USES=3`) — each web search
      ingests pages as input tokens AND has a per-search tool fee, so
      this is a direct cost lever.  2 is enough to find recent posts;
      3 rarely surfaced anything the second pass didn't.
    Expected per-run cost: ~$5+ → ~$1.50 (≈4x cheaper).  Both vars are
    wired in the backend + worker compose env blocks (the gotcha:
    `.env` alone isn't enough; the compose `environment:` allowlist
    has to mention them or pydantic-settings falls back to the code
    default).  1 new test asserting model = Haiku and max_uses = 2.
    Tests: **backend 614**.
  - **Follow-up: skip already-pulled posts on rerun.**  Each call to
    `discover_posts` now accepts `exclude_urls: list[str] | None`.
    `_run_social_search_async` snapshots the search's existing
    `post_url`s (200 most-recent, ordered by `discovered_at desc`) and
    passes them through.  Inside the discovery service, exclude URLs
    are (a) baked into the prompt as a "DO NOT include these — we
    already have them" list capped at 200 entries (keeps the prompt
    sane on long-running searches), and (b) post-filtered defensively
    in case the model returns one anyway.  Effect: a rerun on a
    saturated search may still spend the discovery call, but the LLM
    is nudged toward new posts AND any stale ones it returns are
    silently dropped — zero qualify calls fire for already-scored
    posts, which is where the real Anthropic spend was.  3 new tests
    (2 discovery, 1 worker).  Tests: **backend 613**.
  - **Follow-up: per-search detail view.**  Clicking a search row's
    name now opens a dedicated detail page (`SearchDetailView`) instead
    of the edit modal.  The detail shows an Activity card (last_run_at,
    last_run_status, last_run_error, post / opp counts, expanded-
    queries chips, next_run_at if scheduled), the Run now / Edit /
    Delete actions, and the same `OpportunityCard` feed filtered to
    just this search (with a per-detail status filter).  The detail
    auto-refreshes the search + opps every 5s while `last_run_status
    == 'running'` so the user watches results land in real time.  Per-
    row table actions are now: name → View, plus separate Edit / Run
    now / Delete buttons.  8 new frontend tests.  Tests: **frontend
    198**.

- **Previously:** **Schedule & throughput editable on a running /
  paused campaign.**  `update_campaign` previously rejected any edit
  outside DRAFT/PREVIEWING with 409.  Added `_SCHEDULE_FIELDS`
  (`schedule_days`, `schedule_time_start`, `schedule_time_end`,
  `schedule_timezone`) and `_THROUGHPUT_FIELDS` (`min_delay_seconds`,
  `max_per_hour`, `max_per_day`) to `_status_exempt_fields` so they're
  edit-on-any-status.  Content fields (goal, tone, sender_*,
  research_mode, templates, ...) stay 409-gated to draft/previewing
  since changing them mid-flight would split voice between sent and
  unsent batches.
  - **Schedule changes re-enqueue waiting leads.**  When any of the 4
    schedule fields changes on a RUNNING / PAUSED campaign,
    `update_campaign` snapshots every `compose_status=DONE` lead with
    `send_status IN (PENDING, SCHEDULED)` and stagger-dispatches
    `send_lead.apply_async(eta=base + i*min_delay)` — same shape as
    `resume_campaign` and `_kick_off_full_campaign`.  Required because
    nothing reads `lead.scheduled_send_at`; without this, widening the
    window wouldn't unstick orphans parked at the OLD eta.  Throughput-
    only edits don't re-enqueue (new `min_delay` takes effect on the
    next gate claim naturally).
  - **Frontend `ScheduleEditor`** card on the Overview tab right
    column (above Campaign config).  Read-only summary by default;
    "Edit" toggles a form with day chips, start/end time pickers, a
    curated tz dropdown (with a fallback `<option>` for whatever the
    campaign currently has), and the 3 throughput knobs.  Validates
    start<end inline; save flashes a toast that reads "Schedule saved
    — waiting leads re-queued under the new window" when schedule
    fields changed, else "Pacing updated".
  - Tests: 4 new in `test_phase4_campaigns.py` (schedule edit allowed
    on running/paused, schedule change re-enqueues staggered,
    throughput-only does NOT re-enqueue, content fields still 409) +
    3 new in `CampaignDetail.test.jsx` (renders summary, saves new
    window, blocks save on bad time ordering).  Tests: **backend 563,
    frontend 181**.

- **Previously:** **Pause = hard stop; resume re-enqueues.**  Two
  changes that together make the pause / resume cycle behave correctly
  for the legacy `send_lead` pipeline:
  - **Pause is a hard stop.**  `workers/send.py:370` previously
    re-enqueued itself via `apply_async(countdown=300)` whenever
    `send_lead_async` returned `{status: paused}` — meaning every
    paused-campaign queued task respawned every 5 min, holding tens of
    thousands of zombie tasks for as long as the campaign stayed
    paused.  Now the task acks-and-drops on paused; the lead's
    `send_status` is left alone (the gate doesn't mutate it on the
    paused branch), and resume re-enqueues.
  - **Resume re-enqueues PENDING + SCHEDULED.**  `routers/campaigns.py`
    `resume_campaign` now snapshots every `compose_status=DONE` lead
    where `send_status IN (PENDING, SCHEDULED)` and stagger-dispatches
    `send_lead.apply_async(eta=base + i*min_delay)` — same pattern as
    `_kick_off_full_campaign`.  Required because nothing reads
    `lead.scheduled_send_at` (write-only DB stamp); without this,
    SCHEDULED orphans sit forever.  Sequencer-driven follow-up /
    LinkedIn steps don't need parallel treatment — the beat already
    filters `Campaign.status == RUNNING`, so paused campaigns aren't
    visited.
  - **Bug story.**  Diagnosed when the user reported the csuite tech
    advisor campaign stuck.  Found: (a) the `worker` container had died
    17h prior — only `beat` was up, queue piled to 7,200+ tasks with
    no consumer; (b) 148 csuite leads sat in `SCHEDULED` orphan state;
    (c) once the worker came back, the 594 PENDING leads of the paused
    `grantmind email` campaign churned thousands of self-respawned
    paused-retries every 5 min.  Fix lands all three: worker restart
    + resume re-enqueue + paused hard-stop.
  - Tests: 2 new in `test_phase4_campaigns.py` (resume re-enqueues
    PENDING+SCHEDULED staggered; clean no-op when nothing pending),
    1 updated in `test_phase9_send_task.py` (paused does NOT
    re-enqueue).  Tests: **backend 559, frontend 178**.
  - Remediation script `backend/scripts/reenqueue_stuck_scheduled.py
    <campaign-id>` re-dispatches existing orphans — used to recover
    the 148 csuite leads.

- **Previously:** **Lite-CRM Leads tab + 90-day research
  cache + per-lead notes.**  Three threads:
  - **Research cache (cuts repeated API spend).**  New `research_cache`
    table — primary key is the lowercased+stripped lead email; columns
    `research_data` (JSONB) + `refreshed_at` (timestamptz).  New
    `app/services/research_cache.py`: `lookup(session, email)` returns
    the JSONB blob iff `now - refreshed_at <=
    RESEARCH_CACHE_TTL_DAYS` (new setting, default 90); `upsert(...)`
    uses `pg_insert(...).on_conflict_do_update(...)` to refresh stale
    rows in place.  Wired into `research.research_lead_async`: before
    any Apollo / Hunter / web fan-out, hit the cache; on hit, stamp
    `research_data["from_cache"] = True`, write the lead's
    `research_data` to the cached blob, mark DONE, enqueue compose —
    zero external calls.  On miss the existing fan-out runs and the
    resulting blob is upserted before the compose enqueue.  NONE /
    TEMPLATE modes short-circuit before the cache is consulted (no
    point caching a `{skipped: True}` blob).  Migration 0013 adds the
    table.
  - **Per-lead notes (lite CRM).**  New nullable `leads.notes` TEXT
    column (same migration 0013).  `PATCH /campaigns/{cid}/leads/{lid}`
    renamed from `update_campaign_lead_email` → `update_campaign_lead`
    and now accepts `{composed_subject?, composed_body?, notes?}`;
    composed-content edits still 409 when `send_status == SENT`, but
    `notes` is always editable.  `LeadSummary` exposes `notes`,
    `has_notes`, and `campaign_name`; `list_campaign_leads` trims notes
    preview to 280 chars.
  - **Global Leads page.**  New top-level nav item between Campaigns
    and Research-a-client.  New `GET /leads` (paginated; filters:
    `campaign_id`, `send_status`, `search` over email/name/company,
    `has_notes`).  Frontend `pages/Leads.jsx` shows a table (Name,
    Email, Company, Campaign, Send pill, Notes preview); row click
    opens a CRM modal with the composed email preview + a notes
    textarea + Save (calls the renamed `updateLeadEmail({notes})`).
    Filters: campaign dropdown, search input, has-notes toggle.
  - Tests: 5 research-cache unit + 2 research-worker (cache hit skips
    APIs / miss upserts) + 2 router (notes editable when SENT, global
    list pagination/filter/campaign_name) + 5 frontend.
    Tests: **backend 557, frontend 178**.

- **Previously:** **Edit composed emails + bulk signature.**  Two
  related editing features:
  - **Reusable campaign signature.**  New nullable `campaigns.signature`
    column (migration 0012) — the sender's contact / website / calendar
    block.  New pure service `app/services/signature.py`
    `apply_signature(body, signature)`: finds the AI's sign-off (a
    closing-phrase line like ``Best,`` near the end, `_CLOSINGS` set,
    within the last 6 lines) and replaces from there to the end with the
    signature; falls back to appending when no sign-off is found;
    idempotent (a body already ending with the signature is returned
    unchanged, so bulk-apply / re-compose are safe to repeat).
    `compose_lead_async` applies it to every AI-composed body (template
    mode is left verbatim).  `POST /campaigns/{id}/apply-signature`
    bulk-applies to all composed, not-yet-sent leads (returns
    `{updated}`); sent emails are skipped.
  - **Per-email edit.**  `PATCH /campaigns/{id}/leads/{lid}` edits
    `composed_subject`/`composed_body` for ANY lead (not just preview
    samples); 409 if already sent.  Frontend: the `LeadEmailModal`
    ("View email") gained an Edit mode (subject + body, save via
    `updateLeadEmail`), and the Leads tab has a `SignatureEditor`
    (textarea + Save + "Apply to all emails").
  - `signature` on Campaign Create/Update/Response + `campaign_to_dict`
    (+ `_blank_to_none`).  **`update_campaign`'s draft/previewing-only
    content guard exempts `signature`** (alongside the account-id
    fields) so it's editable on paused/running campaigns — it only
    affects emails on Apply / future composes.  Tests: 7
    signature-service unit + 1 compose (sign-off swap) + 4+3 endpoint
    (edit, edit-blocked-when-sent, bulk-apply, no-signature-400,
    signature-editable-in-any-status×3) + 3 frontend.  Tests:
    **backend 548, frontend 173**.

- **Previously:** **Email sends staggered by the campaign's
  min_delay (no more bulk-of-N).**  Emails were going out in bursts of
  ~8 (Celery prefork concurrency) because the rate gate was
  read-then-act: `check_rate_limits` GET `last_sent`, and the bump
  (`increment_rate_counters`) ran AFTER the Brevo send — so N concurrent
  send tasks all read the stale timestamp, all passed `min_delay`, and
  all sent.  Same TOCTOU race the LinkedIn limiter had.  Two changes:
  - **Atomic min-delay gate.**  `check_rate_limits` now claims the
    window with `SET rate:{cid}:min_gate <now> NX EX min_delay` — the
    single serialization point; only one task per `min_delay_seconds`
    claims it, the rest defer with `retry_in = TTL` and reschedule.
    `SET NX EX` is atomic on real Redis AND fakeredis (no Lua, so the
    existing fakeredis-based send tests keep working).  Hard caps
    (hour/day) are read-checked first but aren't raced since the gate
    serializes.  `increment_rate_counters` now only bumps hour/day
    (the `last_sent` key is gone — the gate replaces it).
  - **Staggered approve-all dispatch.**  `_kick_off_full_campaign`
    schedules sends `min_delay` apart via `apply_async(eta=...)` instead
    of firing the whole composed batch with `.delay()` at once, so the
    common in-window case spaces cleanly without relying on gate
    bounces.  The atomic gate is the backstop for residual collisions
    (compose-trickle, beat follow-ups, out-of-window re-bunching).
  - Tests: atomic-gate (first claims, second bounces), approve-all eta
    spacing, updated min_delay + increment tests.  Tests: **backend
    531, frontend 170**.
  - **Follow-up: email daily cap resets on the calendar day too.**
    `increment_rate_counters` now expires the `rate:{cid}:day` counter
    at the next local midnight (campaign tz) via
    `_seconds_until_local_midnight` (pytz, mirrors the LinkedIn
    `_seconds_until_midnight`), instead of a rolling 86400 — matching
    the `daily_cap` retry_at.  Hourly counter stays a rolling 60-min
    window.  2 tests (helper + day-counter TTL).  Tests: **backend
    533, frontend 170**.

- **Previously:** **AI cost optimization (research ~$40→~$12 per
  300 leads).**  "Fast" research was making TWO Sonnet+web-search calls
  per lead — `web_research.research_person_web` AND
  `site_scraper.scrape_company_site`, each `max_uses=5` (up to 10 web
  searches/lead) — and the ingested search-result pages on Sonnet were
  the dominant spend.  Three levers:
  - **Merged the two research calls into ONE.**  `research_person_web`
    now returns person + company fields (person_news, company_news,
    company_description, recent_updates, industry, size_hint) in a
    single call.  **Deleted `site_scraper.py`** (+ its test); the worker
    fan-out dropped from `[web, site, hunter, (apollo)]` to `[web,
    hunter, (apollo)]`.  `_assess_quality` is now `(web_data,
    apollo_data)` (2-arg).
  - **Research runs on Haiku, compose stays on Sonnet.**  New
    `ANTHROPIC_RESEARCH_MODEL` (default `claude-haiku-4-5-20251001`)
    used by `research_person_web`; `ANTHROPIC_MODEL` (Sonnet) still
    composes the emails (quality).  Research is extraction, so Haiku is
    ~3.75x cheaper on the dominant ingested-token cost.  (The one-off
    `research_client` tool keeps Sonnet — it's interactive, one call per
    request.)
  - **`max_uses` 5→3** via new `RESEARCH_WEB_SEARCH_MAX_USES`.
  - All three settings wired into docker-compose for backend+worker (and
    `ANTHROPIC_MODEL`, which wasn't configurable before).  Tests updated
    (merged single-call shape, 2-arg `_assess_quality`, model+max_uses
    assertions); `test_phase6_site_scraper.py` deleted.  Tests:
    **backend 529, frontend 170**.

- **Previously:** **Paused campaigns freeze; LinkedIn cap
  auto-pauses (and auto-resumes) the campaign.**  Two related scheduler
  changes:
  - **Paused = frozen in place.**  The beat (`_advance_sequences_async`)
    now JOINs `sequences`/`campaigns` and only selects leads whose
    `Campaign.status == RUNNING` (`.with_for_update(..., of=
    LeadSequenceState)` keeps the row lock scoped to the state rows).
    A paused campaign's leads are skipped entirely — current node +
    `next_run_at` untouched — so the beat no longer churns paused
    campaigns or burns the per-account stagger slots that running
    campaigns sharing the LinkedIn account need.  (Previewing/draft/
    complete are also not driven by the sequencer, which is correct.)
  - **Auto-pause at the LinkedIn cap + auto-resume.**  New nullable
    `campaigns.auto_paused_until` column (migration 0011).  When a
    LinkedIn step defers with a cap reason (`daily_cap` / `connect_cap`
    / `dm_cap`) AND no email node is reachable downstream of the capped
    node (`_has_downstream_email` BFS over live edges), the campaign is
    set PAUSED with `auto_paused_until = now + retry_in` (the cap-reset
    time).  The beat resumes it (status→RUNNING, marker→NULL) once that
    time passes — done in the same tick's leading `UPDATE` before the
    main SELECT.  Gated on `node.kind in LI_KINDS` because `daily_cap`
    is also a Brevo (email) gate reason — only the *LinkedIn* cap
    pauses.  Manual pause/resume endpoints clear `auto_paused_until`
    (so a manual pause stays paused; only cap-pauses auto-resume).
    `CampaignResponse` exposes `auto_paused_until`; `CampaignDetail`
    shows an "Auto-paused (LinkedIn daily cap) — resumes <time>" note.
  - Tests: paused-freeze, cap-auto-pause (no email downstream),
    no-pause (email downstream), auto-resume vs manual/future, + 2
    frontend.  Tests: **backend 524, frontend 170**.
  - **Follow-up: daily cap resets on the calendar day, not a rolling
    24h window.**  The cap counters used `EXPIRE 86400 NX`, anchoring
    the reset to the day's first action (so a "new day" didn't lift the
    cap until ~24h after you first started).  Now `_li_rate_acquire`
    takes `daily_ttl_seconds` = `_seconds_until_midnight(campaign.
    schedule_timezone)`, so the daily + per-kind counters expire at
    local midnight in the campaign's tz.  `auto_paused_until` (from the
    cap `retry_in`) therefore lands at midnight + a 60s
    `_CAP_RESUME_BUFFER_SECONDS` cushion so the beat resumes AFTER the
    counter clears (no resume-then-recap flap).  3 tests
    (seconds-to-midnight align + bad-tz fallback + calendar-day TTL
    stamped).  Tests: **backend 527, frontend 170**.

- **Previously:** **Staggered LinkedIn dispatch (one lead at a
  time, not bulk).**  The sequencer beat (`_advance_sequences_async`)
  used to dispatch every due LinkedIn step in a tick at once (jittered
  2-8s), leaving the worker's rate limiter to bounce the losers to
  5-min retries — a messy burst.  Now it releases **at most one
  LinkedIn step per account per `LINKEDIN_STAGGER_SECONDS`** (new
  config, default 120s; 0 disables).  New atomic Redis Lua
  `_LI_STAGGER_LUA` + `_reserve_li_stagger_slot(account_key, now)`:
  returns 0 when the account's slot is open now (and claims it,
  recording the dispatch instant), else the epoch timestamp when the
  next slot opens.  In the `LI_KINDS` dispatch block, a lead whose
  account slot isn't open is parked (`next_run_at = slot`,
  `counts["staggered_linkedin"]++`) WITHOUT dispatching; the open-slot
  lead dispatches as before (`next_run_at = now+10min`).  Keyed per
  LinkedIn account (`_li_stagger_key`, memoised per tick) since rate
  limits are per-account — different accounts run in parallel; leads
  with no bound account fall back to the campaign id.  `advance_sequences`
  now resets `_LI_REDIS_CLIENT=None` at task entry (same fresh-loop
  pattern as `send_linkedin_step`) since the beat now uses Redis.
  **`view_profile` is exempt from staggering** (it's a low-risk read
  that doesn't count as a throttled action — same set that's already
  exempt from the daily cap, renamed `_DAILY_CAP_EXEMPT_KINDS →
  _UNCOUNTED_ACTION_KINDS` and now consulted by both the cap and the
  stagger block); views still respect the per-action min-delay
  anti-burst floor.  Effect: a batch of N due write/notify leads peels
  off one per interval per account, while profile views fire promptly;
  the per-action min-delay + daily caps remain as the worker-side
  backstop.  3 new tests in `test_phase17_linkedin_sequencer.py`
  (staggered: 1 dispatched + 2 parked; disabled: all 3 dispatched;
  view_profile exempt: all 3 dispatched despite a stagger interval).
  Tests: **backend 518 passing**.
  - **Follow-up: distinct stagger slots across ticks.**  The first cut
    parked every waiting lead on the same "next slot" timestamp, which
    made the schedule/UI look like a simultaneous batch (the beat runs
    every 60s but the interval is 120s, so non-dispatch ticks re-piled
    leads onto one timestamp).  Reworked `_LI_STAGGER_LUA` to a TWO-key
    design (`:last` dispatch marker + `:hw` high-water) so each parked
    lead gets a DISTINCT slot one interval apart, persisting across
    ticks, while re-queued leads still fire on time (no push-back).
    2 more tests (distinct-spread within a tick; cross-tick distinct +
    no-push-back).  Tests: **backend 520 passing**.

- **Previously:** **Any node can be the sequence entry/start node.**
  Lifted the M1 "entry node must be an email node" rule
  (`sequence_service.validate_graph` no longer rejects non-email
  entries).  A sequence can now start with a LinkedIn connect, wait,
  etc.  Because the legacy `compose -> send_lead` pipeline sends the
  first email **unconditionally** (it's not sequence-aware), this
  required gating it on the entry kind:
  - New helpers in `sequence_service.py`: `get_live_entry_node`,
    `is_legacy_first_email_node` (True for an email entry node; also
    True when no entry resolves — the backward-compatible default so
    sequence-less test campaigns still compose), and
    `campaign_sends_legacy_first_email`.
  - `replace_graph` now stamps `use_campaign_compose=True` onto any
    email node promoted to entry, so the sequencer's entry-email
    branch keys off a reliable flag.
  - `compose.compose_lead_async`: when the entry node isn't a legacy
    compose email, skip the Anthropic call AND the send enqueue, mark
    compose DONE with empty body, return `skipped_non_email_entry`.
    Research still runs (for AI LinkedIn-DM personalization).
  - `send.send_lead_async`: defensive guard — never send an empty
    `composed_body` even if a stray dispatch reaches it.
  - `leads.confirm_upload`: when the entry isn't an email, launch
    straight into RUNNING (no sample-email preview to show) and return
    `auto_launched=True` on `ConfirmUploadResponse`; email-first
    campaigns still go to PREVIEWING.  The sequencer drives the first
    action.
  - Frontend: `CampaignCreate` jumps to the campaign detail page
    (skips the step-4 preview) when `auto_launched` is true; the
    `SequenceBuilder` node editor shows a "this is the start node, no
    first email is sent" hint on a non-email entry.  The builder
    already allowed "Set as entry" on any node — the blocker was
    purely server-side validation.
  - Tests: non-email entry publishes; email-entry gets
    use_campaign_compose stamped; compose skips for non-email entry;
    confirm_upload auto-launches; frontend skips preview + navigates
    on auto-launch.  Tests: **backend 515 passing, frontend 168
    passing**.

- **Previously:** **Fully-templated campaign mode (no AI).**  Added
  a fourth `ResearchMode.TEMPLATE = "template"` plus two nullable
  campaign columns `template_subject` / `template_body` (migration
  `0010_campaign_templates.py` — `ALTER TYPE ADD VALUE 'template'` +
  the two `add_column`s).  In this mode the pipeline makes **zero**
  external API calls: the research worker short-circuits (same branch
  as NONE — `mode in (NONE, TEMPLATE)`), and `compose_lead_async`
  renders the template per-lead instead of calling Anthropic.  New
  pure-function service `app/services/template_render.py`:
  `build_merge_context(lead)` flattens standard fields + raw CSV
  columns into one dict (populated standard field wins; raw columns
  fill empty/unmapped ones); `render_template(text, ctx)` substitutes
  `{{field}}` / `{{field|default}}` placeholders (case-insensitive,
  inline pipe-default when empty, collapses the double-spaces an empty
  placeholder leaves, preserves newlines).  No em-dash sanitiser on
  this path — the copy is the user's own verbatim text.  Send path is
  unchanged (it just reads `composed_subject`/`composed_body`), so
  preview/sample rendering + approve-all all work as-is.  Schema:
  `template_subject`/`template_body` on Create/Update/Response +
  `campaign_to_dict`; a `_blank_to_none` validator nulls all-whitespace
  fields so an empty textarea doesn't trip template mode, and a
  model-validator requires `template_body` when mode is TEMPLATE.
  Frontend: fourth "Template (no AI, you write it)" pill in
  `CampaignCreate.jsx` reveals subject + body textareas with a
  merge-field hint; payload only carries the copy in template mode;
  client-side guard blocks an empty body.  The Step-4 progress screen
  is now mode-aware (it fetches the campaign via `getCampaign`): fast/
  deep → "Researching and composing emails…  X of Y composed · Z
  researched"; none → "Composing emails…  X of Y composed"; template →
  "Rendering your templated emails…  X of Y rendered" (no research
  count).  The step-indicator chip label changed from "Research" to
  the mode-agnostic "Prepare".  Tests: 11 renderer unit tests
  (`test_phase32_template_render.py`) + 1 compose test (renders
  without calling Anthropic) + 1 research test (TEMPLATE skips all
  research) + 4 frontend tests (incl. a template-wording Step-4 test).
  Tests: **backend 511 passing, frontend 167 passing**.

- **Previously:** **"No research" campaign mode.**  Added a third
  `ResearchMode.NONE = "none"` alongside FAST / DEEP.  Campaigns set to
  this mode make **zero** external research calls (no Apollo / Hunter /
  web): `research_lead_async` short-circuits when
  `mode == ResearchMode.NONE`, writing `{"quality": "low", "skipped":
  True}` and marking research DONE, then still enqueues `compose_lead`.
  Compose runs as normal but falls into its existing generic
  name+company-only prompt (`quality == "low"` branch), so the only API
  spend is one Anthropic call per lead.  This is the "AI compose, no
  research" path — the model still writes each email (no static
  template / merge fields).  Migration `0009_research_mode_none.py`
  does `ALTER TYPE research_mode ADD VALUE IF NOT EXISTS 'none'`
  (PG 12+ allows this inside Alembic's txn since the value isn't used
  in the same migration; downgrade is a documented no-op — Postgres
  can't drop enum values).  Frontend: third "None (no research, AI
  writes from name + company)" pill in the `CampaignCreate.jsx`
  research-mode selector + an inline hint when selected.  1 new backend
  test (`test_phase6_research_task.py` — asserts every provider mock is
  un-called yet compose is enqueued) + 1 new frontend test
  (`CampaignCreate.test.jsx`).  Tests: **backend 498 passing, frontend
  163 passing**.

- **Previously:** **Lifetime per-node idempotency across every
  action kind.**  New `_already_executed_ever(session, lead_id,
  node_id)` helper in `app/workers/sequencer.py` queries
  `lead_step_executions` for any SENT row matching the pair — no time
  bound, no kind filter.  Wired in at the top of both
  `_send_email_step_async` and `_send_linkedin_step_async` right after
  the stale-dispatch guard.  Effect: once any node has produced a SENT
  execution row for a lead, a subsequent dispatch (re-enrollment,
  manual cursor reset, loop-back sequence, duplicate Celery delivery
  past the visibility-timeout guard) skip-and-advances with
  `error="action/email already sent for this node — skipping duplicate"`.
  Applies uniformly to view_profile, follow_profile, react_post,
  connect, DM, page-invite, InMail, comment_post, AND follow-up
  email.  The status-based connect-skip (INVITED/CONNECTED) and
  DM-defer-when-INVITED guards are now belt-and-suspenders behind
  this — the lifetime check catches the duplicate-dispatch case while
  the status-based ones catch the "lead's state changed externally"
  case.  Multi-touch ("view profile 3x over 10 days") must be modelled
  as separate nodes; loop-back to the same node will now skip.
  2 new backend tests in `test_phase18_linkedin_write.py` (LinkedIn
  view + email follow-up).  Updated
  `test_deferred_cap_skip_does_not_burn_retry_budget` in phase17 to
  use TWO leads (the same-lead-twice scenario it tested is no longer
  possible in production).  Tests: **backend 497 passing**.

- **Previously:** **Smart connect/DM idempotency in the
  sequencer.**  Two new short-circuits in
  `_send_linkedin_step_async`:
  - `LINKEDIN_CONNECT` step now skips-and-advances when the lead is
    already INVITED or CONNECTED.  Avoids duplicate invites (which
    Unipile 4xxs and which look automated) and CONNECTED-targets that
    don't need an invite at all.  No rate-slot burn, no API call.
  - `LINKEDIN_DM` step changed behavior for INVITED leads: previously
    skip-and-advance (which silently dropped the DM every time);
    now `deferred` with `reason="waiting_for_connection"`, parks the
    lead on the DM node, re-checks every `EDGE_WAIT_RETRY_MINUTES` (30
    min) until the Unipile webhook flips the lead to CONNECTED.
    Bounded by `MAX_EDGE_WAIT_DAYS` (14); past that → skip-and-advance.
    UNKNOWN / DECLINED → skip (no signal coming, deferring forever
    would silently halt).  The existing edge-parking pattern (`{op:
    linkedin_connection, value: connected}` on the outgoing edge)
    still works; this fix covers the case where the user wired
    `{op: always}` between connect and DM.
  - 4 new backend tests in `test_phase18_linkedin_write.py`
    (connect-skips-when-INVITED, connect-skips-when-CONNECTED,
    DM-defers-when-INVITED, DM-gives-up-after-14d).  Fixed
    `test_connect_cap_blocks_after_subcap_hit` to use two distinct
    leads since the first one now becomes INVITED after the first
    connect.  Tests: **backend 494 passing**.

- **Previously:** **Relaxed the freshness gate on IDENTITY
  fields in 'Research a client'.**  Yesterday's freshness constraint
  was applied to both news AND identity ("verified within the
  freshness window"; "leave job_title/company blank rather than
  guess").  Side effect: typical sales prospects without recent press
  coverage came back with every identity field blank → compose path
  fell into the `quality=low` branch → user reported "no research."
  Reproduced live: Nadella (high-profile) returned `rich` research
  with 4 dated person-news items; throwaway profile with no public
  footprint returned every field blank.  Fix in
  `app/services/research_client.py`: split the prompt into IDENTITY
  fields (no freshness gate — LinkedIn profile is source of truth for
  current role) and PERSONALIZATION signals (kept the strict 6-month
  freshness + parenthetical-date rule).  The compose stage still
  refuses to reference a prior role since it only sees the verified-
  current role.  Regression test in `test_phase31_research_client.py`
  now asserts both sections appear in the prompt and that the
  too-strict phrases ("verified within the freshness window",
  "fresh-enough source") never come back.  Tests: **backend 490 passing**.

- **Previously:** **Per-lead delete on the campaign Leads tab.**
  New `DELETE /campaigns/{cid}/leads/{lid}` (returns 204) — every
  child table (`lead_sequence_states`, `lead_step_executions`,
  `email_events`) already had `ondelete=CASCADE` on its `lead_id`
  FK, so one row delete cleans up the lead plus all history in a
  single transaction.  Any Celery task already in flight for the
  deleted lead (compose / send / send_linkedin_step) short-circuits
  via the existing `lead is None → {"status": "not_found"}` guards.
  Frontend: red "Delete" link per row in `components/LeadTable.jsx`,
  opens a confirmation modal that spells out the cascade ("halts
  every future step in the sequence (DM, follow-up emails, profile
  views).  Already-sent emails, opens, clicks, and reply events for
  this lead are also wiped").  Invalidates both `campaign-leads` and
  `campaign` query keys on success so the progress widget reflects
  the new total.  4 new backend tests + 4 new frontend tests.
  Tests: **backend 490 passing, frontend 162 passing**.

- **Previously:** **Stopped the IMAP reply poller from
  auto-marking replies as read in the user's mailbox.**  Symptom: every
  20 minutes the beat task ran `STORE +FLAGS \Seen` on every reply it
  fetched (as the dedup story), so real replies hit the user's Gmail
  inbox already-read and easy to miss.  Fix in three pieces:
  - `app/services/imap_client.py`: replaced `fetch_unseen_messages` →
    `fetch_recent_messages`.  Now selects INBOX with `readonly=True`,
    searches `SINCE <date>` (no UNSEEN), fetches via
    `BODY.PEEK[HEADER]` (RFC 3501 §6.4.5 — the side-effect-free
    variant; plain `BODY[]` sets `\Seen`).  Removed the
    `STORE +FLAGS \Seen` call entirely.  Extracts the `Message-ID`
    header into the new `FetchedMessage["message_id"]` field and
    short-circuits per-message when the caller passes a
    `processed_message_ids` set containing the ID.
  - `app/models/connected_account.py` + migration `0008`: new
    `processed_imap_message_ids` JSONB column (default `'[]'::jsonb`).
    Worker trims to last 500 entries after each poll so the column
    doesn't grow unbounded.
  - `app/workers/reply_poller.py`: threads the set through to the IMAP
    fetcher, records every fetched Message-ID (matched OR unmatched)
    so unmatched-but-recent messages don't get re-parsed every cycle.
    Uses a 1-hour `SINCE_GRACE` to absorb clock skew between Brevo
    delivery and the next poll.
  - New `app/services/imap_client.unmark_seen_uids` + script at
    `backend/scripts/unmark_seen_replies.py` for one-off remediation
    of historical replies the old poller wrongly marked as read.  Run
    once against my mailbox: flipped 1 reply back to unread.
  - 8 new backend tests in `test_phase10_imap_polling.py` /
    `test_phase10_reply_poller.py` (readonly assertion + PEEK fetch
    assertion + SINCE-not-UNSEEN search + processed-ID short-circuit +
    cap-trim + unmark_seen contract).  Tests: **backend 487 passing**.

- **Previously:** **Shipped 'Research a client' — one-off
  outreach generator off a LinkedIn URL.**  New nav item / route at
  `/research-client`.  Backend: `POST /research-client` takes
  `{linkedin_url, goal, tone, sender_name, research_mode,
  output_kind, char_limit}`.  Two depth modes mirror the campaign
  `ResearchMode`: `fast` (~10s, `max_uses=3` web_search) and `deep`
  (~30-45s, `max_uses=8` + a more thorough prompt asking for richer
  signals).  One Anthropic call with the `web_search_20250305` tool
  does identity extraction + personalization research in a single
  shot (avoiding the bulk pipeline's CSV-input assumptions); a
  second Anthropic call composes the message with an embedded
  `char_limit` constraint.  Output_kind toggles between `email`
  (subject + body) and `linkedin_dm` (body only).
  - **Anthropic-only, no Unipile.**  Deliberately skips the
    `view_profile` call we use in the bulk pipeline: a one-off
    research tool shouldn't notify the prospect via LinkedIn's "who
    viewed your profile" feed every time the user runs it.  Name
    fallback uses URL-slug parsing (strips trailing dedup hex tokens
    like `jane-doe-a5898840a` → "Jane Doe") for when web search
    can't confidently identify the person.
  - **Char-limit enforcement is belt-and-suspenders:** prompt asks
    for "at most N characters", plus `_truncate_at_sentence` in
    `compose_client.py` hard-caps the body at the limit preferring a
    sentence boundary (≥60% of the budget) before falling back to a
    word boundary.
  - **Sync HTTP**, not Celery — the user is waiting in the browser
    with a live elapsed-time counter.  Frontend axios timeout is 60s.
  - Files: `app/services/research_client.py`,
    `app/services/compose_client.py`, `app/schemas/research_client.py`,
    `app/routers/research_client.py`, `pages/ResearchClient.jsx`,
    `api/researchClient.js`.  14 new backend tests in
    `test_phase31_research_client.py` (parser + truncator +
    prompt-builder smoke + end-to-end with Anthropic mocked); 7 new
    frontend tests in `ResearchClient.test.jsx`.  Tests: **backend
    477 passing, frontend 157 passing**.

- **Previously:** **Fixed silent webhook drop of `new_relation` events + reconciled 5 historically-stuck leads.**
  Symptom: 51 leads parked on `linkedin_connect` indefinitely, no DMs
  ever firing.  Root cause was two bugs in `app/routers/webhooks.py`:
  (1) `_find_lead_for_event` only inspected nested
  `sender`/`from`/`attendee` blocks, missing the top-level
  `user_public_identifier` / `user_provider_id` / `user_profile_url`
  fields Unipile sends on `new_relation`; (2) `_handle_message_received`
  and `_handle_invitation_accepted` called `.get()` on
  `payload["data"]`/`payload["message"]` unconditionally, which raised
  `AttributeError` when Unipile sent a string there.  Both were dedup'd
  by the `webhook_events` table so the failures looked like quiet
  no-ops.  Fix: new `_extract_unipile_account_id()` helper with
  `isinstance(x, dict)` guards; rewrote `_find_lead_for_event` to scan
  top-level + nested for 8 slug/URL/provider-id keys, normalise to bare
  slug, match against each lead's `linkedin_url` slug.  5 new
  regression tests in `test_phase30_unipile_webhook_matching.py`
  covering both bugs + the no-match-leaves-lead-alone contract.
  Historical reconciliation script
  `backend/scripts/reconcile_linkedin_connections.py` pulls
  `GET /api/v1/users/relations` (paginated, ~1885 connections returned
  per page through `cursor`) and flips matching INVITED/UNKNOWN leads
  to CONNECTED — single bulk call, no ghost-view notifications.  Run
  flipped 5 leads; sequencer advanced all 5 from `linkedin_connect`
  to `linkedin_dm` on the next 60s tick.  Tests: **backend 463 passed**.

- **Previously:** **Validated every Unipile endpoint live + fixed 7 endpoint-shape bugs.**
  Ran the full action suite against a real Unipile account
  (`Anthony Colasante`, Sales Nav premium, unipile_id
  `eU5rAYqAQo-6yDRdjgNAiw`) targeting the throwaway profile
  `ant-cola-a5898840a` + the public Bill Gates profile.  Working live:
  `test_connection`, `view_profile`, `latest_post_urn`,
  `inbox_recent_events`, `follow_profile`, `send_connect_request`,
  `react_to_post`, `comment_on_post`.  `send_dm` correctly returns
  `no_connection_with_recipient` against a non-1st-degree target.
  - **Bug fixes in `unipile_impl.py`:** (1) `inbox_recent_events` was
    sending `isoformat()` with microseconds + `+00:00`; Unipile requires
    strict `YYYY-MM-DDTHH:MM:SS.sssZ`. (2) New `_resolve_provider_id`
    helper — most endpoints (`/users/invite`, `/users/{id}/posts`,
    `/chats`) reject LinkedIn public slugs with
    `errors/invalid_recipient`; they need the canonical `ACoAAA...`
    member token via `GET /users/{slug}` first. The resolver caches
    the result on the ProfileRef. (3) `follow_profile` rewritten to
    use Unipile's raw Voyager passthrough at `POST /api/v1/linkedin`
    with a `followingStates` patch — Unipile doesn't package follow.
    (4) `react_to_post` moved from `POST /posts/{urn}/reactions`
    (404) to `POST /api/v1/posts/reaction` body-based with
    `{account_id, post_id, type}`. (5) `comment_on_post` needed
    `account_id` in the body, not the query. (6) `send_inmail`
    rewritten to use form-encoded body with bracket-notation
    `linkedin[api]=sales_navigator` + `linkedin[inmail]=true`; live
    test returns Unipile 403 `errors/resource_access_restricted`
    (Sales Nav API access not enabled on the Unipile workspace, not a
    code bug — endpoint shape is correct). (7) `_raise_for_special_codes`
    tightened: was grepping error bodies for "restricted" and
    false-positively raising `AccountRestricted` on
    `errors/resource_access_restricted` (a Unipile plan error). Now
    matches only specific LinkedIn-account-state codes.
  - **`_request` extended** to support form-encoded bodies via `data=`
    (pre-encoded as bytes through `content=` so httpx ships an
    AsyncByteStream).
  - **`invite_to_page` is blocked on Unipile's passthrough whitelist.**
    HAR-captured the exact LinkedIn Voyager request that powers their
    admin-UI "Invite connections to follow" action:
    `POST /voyager/api/voyagerRelationshipsDashInvitations?inviter=(organizationUrn:urn:li:fsd_company:<PAGE_ID>)`
    with `x-restli-method: batch_create` + body
    `{"elements":[{"inviteeMember":"urn:li:fsd_profile:<MEMBER>","genericInvitationType":"ORGANIZATION"}]}`.
    Tried routing via Unipile's raw passthrough at `POST /api/v1/linkedin`
    in every documented shape (inlined query string, separate
    `query_params` field per Unipile's "Raw Data" docs, `encoding=true/false`,
    `headers` dict, with/without `x-restli-method`, URL-encoded vs raw
    URN colons) — every variant returns Unipile's
    `errors/malformed_request` from their forwarder, BEFORE the request
    reaches LinkedIn (a plain GET to `identity/profiles/me` via the
    passthrough fails the same way).  `follow_profile` works through the
    same passthrough only because `feed/dash/followingStates` happens to
    be on Unipile's allowlist.  The impl now returns a clean
    `unipile_passthrough_blocked` ActionResult and
    `LINKEDIN_INVITE_TO_PAGE` is gated out of `PUBLISHABLE_KINDS_M1`.
    Next step: open a Unipile support ticket asking them to allowlist
    `voyagerRelationshipsDashInvitations`, then restore the impl + gate
    from git history (it lives in commits prior to the gating revert).
  - Added a `_resolve_provider_id` unit test, a `resource_access_restricted`
    error-mapping regression test, a form-encoded InMail body test, and
    rewrote `test_publish_rejects_non_numeric_page_id` →
    `test_publish_rejects_linkedin_invite_to_page` to assert the new gate.
  - Ad-hoc smoke scripts at `backend/scripts/test_unipile_endpoints.py`
    and `backend/scripts/test_unipile_post_actions.py` — handy for
    re-validating against the live API.
  Tests: **backend 405 passed**, **frontend 150 passed**.

- **Previously:** **Stripped the dead Playwright / DIY code paths.**
  Unipile is the only LinkedIn provider now.  Deleted
  `services/linkedin/playwright_impl.py`, `hybrid_impl.py`,
  `linkedin_api_impl.py`; deleted `tests/test_phase21_voyager_error_parser.py`
  and `tests/test_phase22_human_dwell.py`; dropped `_AccountLock` /
  `AccountLockBusy` from `base.py` + the corresponding sequencer except
  branch; deleted `LINKEDIN_PROVIDER`, `LINKEDIN_PROXY_URL`, and
  `LINKEDIN_PROFILES_DIR` from `app/config.py`; dropped `playwright`,
  `playwright-stealth`, and `linkedin-api` from `requirements.txt`;
  removed the Chromium install + runtime libs from `backend/Dockerfile`
  (saves ~170MB image bloat); deleted the legacy
  `POST /linkedin-accounts/` create endpoint + the password/li_at
  PATCH fields; collapsed `ConnectLinkedInModal` to Unipile-only (no
  more "Local (legacy)" toggle); updated the `encryption.decrypt`
  allowlist in `test_phase15_hardening.py` to `{imap_client.py,
  encryption.py}` (the DIY consumer is gone).  The
  `password_encrypted` / `session_cookies_encrypted` / `proxy_url`
  columns on `LinkedInAccount` are kept nullable for legacy rows but
  nothing in the code touches them.
  Tests: **backend 402 passed**, **frontend 150 passed**.

- **Previously:** **Unipile hosted-API integration.**  Switched the
  default provider to Unipile (real desktop Chrome on residential IPs),
  added `UnipileLinkedInProvider`, hosted-auth flow endpoints
  (`POST /linkedin-accounts/connect-via-unipile`, `/sync-unipile`,
  `/discoverable`, `/import-from-unipile`), webhook handler at
  `POST /webhooks/unipile` with static-custom-header auth, frontend
  modal redesign, and 33 new provider tests
  (`test_phase23_unipile_provider.py`).  See the **Unipile setup
  runbook** below for fresh-dev-box wiring.

- **In flight:** nothing.
- **Next up:** open.  Suggested directions:
  - **Open a Unipile support ticket** for two passthrough/permission
    issues at once: (a) allowlist `voyagerRelationshipsDashInvitations`
    on `/api/v1/linkedin` so `invite_to_page` can fire; (b) enable Sales
    Navigator API access on the workspace so `send_inmail` (POST
    `/api/v1/chats` with `linkedin[api]=sales_navigator`) stops returning
    `errors/resource_access_restricted`.  Both impls are HAR / docs-
    verified — neither is a code bug.
  - **Unlock InMail in Unipile.** Live test returned 403
    `errors/resource_access_restricted` — the impl is correct but the
    Unipile workspace doesn't have Sales Nav API access enabled.
    Either upgrade the Unipile plan or contact their support.
  - **Webhook idempotency.** Unipile's "at-least-once" delivery means
    we may double-process retries.  Add a `webhook_events` table keyed
    on event_id when this becomes a real issue.
  - **Drop legacy DB columns.**  `password_encrypted` /
    `session_cookies_encrypted` / `proxy_url` on `linkedin_accounts`
    are dead.  Schedule a migration to drop them once the legacy DIY
    rows are gone from production.
  - **Cross-cutting cleanup** from `docs/roadmap.md#cross-cutting-tasks`
    (sequence templates, multi-tenant readiness, named node_modules
    volume).
  - **Phase 2** — whatever you have in mind next.

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · async SQLAlchemy 2 · Celery 5 |
| DB / cache | PostgreSQL 15 · Redis 7 |
| AI compose | Anthropic Claude Sonnet 4.6 (`ANTHROPIC_MODEL`) |
| AI research | Anthropic Claude Haiku 4.5 (`ANTHROPIC_RESEARCH_MODEL`) — cheaper; extraction only |
| Email | Brevo transactional API + webhooks |
| Reply tracking | IMAP poller (Celery beat, every 20 min) |
| Frontend | React 18 · Vite 6 · TanStack Query · `@xyflow/react` |
| Infra | docker-compose: postgres, redis, backend, worker, beat, frontend |

## Architecture cheat sheet

- **Routers** in `backend/app/routers/`: `campaigns`, `leads`, `preview`,
  `analytics`, `settings`, `webhooks`, `connected_accounts`,
  **`sequences`** (M1), **`linkedin_accounts`** (M2).
- **Workers** in `backend/app/workers/`:
  - Legacy email pipeline: `ingest → research → compose → send`.
  - **M1 additions:** `sequencer.advance_sequences` (beat, every 60s) +
    `sequencer.send_email_step` (channel handler for follow-up emails).
  - **M2 additions:** `sequencer.send_linkedin_step` (LinkedIn action
    handler — view/follow/react) + `linkedin_poller.poll_all` (beat,
    every 5 min) for inbound events (DM replies, connection accepts).
  - **M3 additions:** same `send_linkedin_step` now also handles
    `linkedin_connect`, `linkedin_dm`, `linkedin_invite_to_page`. Has
    per-kind subcaps + 1st-degree check for DM + page invites.
  - **M4 additions:** `linkedin_inmail` (requires Premium — returns
    `skipped` with `premium_required` meta when credits run out) and
    `linkedin_comment_post`. InMail counts against the DM cap.
  - `reply_poller` runs separately (IMAP email replies, every 20 min).
- **Sequence engine:**
  - `app/services/sequence_conditions.py` — JSON expression language
    (`always` / `not` / `and` / `or` / `replied` / `opened` / `clicked` /
    `bounced` / `linkedin_connection` / `days_since_entered_node`) +
    evaluator + DAG cycle detector.
  - `app/services/sequence_service.py` — `ensure_default_sequence`,
    `enroll_leads`, `validate_graph`, `replace_graph`.
    `PUBLISHABLE_KINDS_M1` whitelist gates which kinds can be published
    (M2 added `linkedin_view_profile`, `linkedin_follow_profile`,
    `linkedin_react_post`; write actions still rejected).
  - `app/workers/sequencer.py` — beat task that walks per-lead state
    cursors; dispatches email + LinkedIn channel handlers.
  - **5 sequence tables:** `sequences`, `sequence_nodes`,
    `sequence_edges`, `lead_sequence_states`, `lead_step_executions`.
- **LinkedIn engine:**
  - `app/services/linkedin/base.py` — `LinkedInProvider` ABC +
    `ProfileRef`, `ActionResult`, `InboundEvent`, `ChallengeRequired`,
    `AccountRestricted` types.
  - `app/services/linkedin/unipile_impl.py` — the only concrete impl.
    Async httpx wrapper around Unipile's REST API; maps Unipile error
    envelopes onto our domain types.  Reads `UNIPILE_DSN` +
    `UNIPILE_API_KEY` from settings.
  - `LinkedInAccount` table: rows are created either via the hosted-
    auth flow (`POST /linkedin-accounts/connect-via-unipile`) or by
    binding a pre-existing Unipile account (`POST /linkedin-accounts/
    import-from-unipile`).  Status flips to `challenged` when Unipile
    surfaces a checkpoint — user completes it in Unipile's hosted
    browser, then we `/resolve-challenge` to clear the local flag.
  - Per-account rate limits via Redis: `LINKEDIN_DAILY_ACTION_CAP`
    (default 20) + `LINKEDIN_MIN_ACTION_DELAY_SECONDS` (default 30).
    Unipile humanises on its side; these are our burst-control floor.
  - **Per-kind subcaps:** `LINKEDIN_DAILY_CONNECT_CAP` (20),
    `LINKEDIN_DAILY_DM_CAP` (30), `LINKEDIN_MONTHLY_PAGE_INVITE_CAP`
    (250 per page). Redis keys: `li-rate:{aid}:day:connect`,
    `li-rate:{aid}:day:dm`, `li-rate:page:{page_id}:month`. Page invite
    cap is keyed by page_id, NOT account, since LinkedIn enforces per
    company page.
- **Default sequences are auto-created** on campaign creation, and every
  enrolled lead gets a `lead_sequence_state` row. Old data was backfilled
  in `alembic/versions/0002_sequences.py`.
- **The legacy `compose → send_lead` path is the source of truth for the
  FIRST email — but ONLY when the entry node is an email node.** The new
  sequencer skips email entry nodes whose `lead.send_status` is already
  SENT and advances to the next node.  Since the legacy pipeline isn't
  sequence-aware, it's gated on the entry kind via
  `sequence_service.campaign_sends_legacy_first_email`: a non-email start
  node (LinkedIn / wait / ...) makes `compose` skip the AI call + send
  (`skipped_non_email_entry`) and `confirm_upload` auto-launch into
  RUNNING (no email preview).  Research still runs.  `is_legacy_first_email_node(None)`
  returns True (backward-compatible default) so a campaign with no live
  entry node still composes.  Don't remove the legacy path without a
  migration plan.

## Conventions and gotchas

- **Connected accounts:** `email_address` is for display; `username` is
  the IMAP login. Gmail aliases share their parent mailbox — set
  `email_address` to the alias and `username` to the primary mailbox.
  The connect modal has an inline hint about this
  ([`ConnectInboxModal.jsx`](frontend/src/components/ConnectInboxModal.jsx)).
- **Encryption invariant:** only `app/services/imap_client.py` may call
  `encryption.decrypt(` (allowlist is `{imap_client.py, encryption.py}`
  — the DIY LinkedIn consumer was stripped along with Playwright).  A
  test in `test_phase15_hardening.py` greps the codebase to enforce.
- **Docker network quirk:** if Docker Desktop restarts while containers
  are up, postgres can become detached from `emailblaster_default`
  (you'll see `socket.gaierror: Name or service not known` in backend
  logs). Fix without a restart:
  ```bash
  docker network connect --alias postgres emailblaster_default emailblaster-postgres-1
  ```
- **CORS-on-error:** 500 responses go through Starlette's
  `ServerErrorMiddleware` which sits OUTSIDE `CORSMiddleware`. The
  `unhandled_exception_handler` in `app/main.py` manually attaches CORS
  headers so the browser shows the 500 instead of masking it as CORS.
- **Migrations:** run `docker compose exec backend alembic upgrade head`
  after pulling. Migrations use `postgresql.ENUM(..., create_type=False)`
  for column references; pre-create the enum types up front (see
  `0002_sequences.py` for the pattern).
- **LinkedIn enum kinds are already in the DB.** `publish()` rejects
  sequences containing any LinkedIn kind in M1
  (`sequence_service.PUBLISHABLE_KINDS_M1`). M2 starts removing that
  restriction.
- **Test DB truncate list** in `tests/conftest.py` must include every
  table. Update it when you create new ones.
- **Frontend node_modules quirk:** the running `frontend` container has
  an anonymous `/app/node_modules` volume. After `npm install` on the
  host, the running container won't see new packages until
  `docker compose exec frontend npm install` also runs. Fresh
  `docker compose run --rm` containers create a NEW empty anonymous
  volume — rebuild the image with `docker compose build frontend` if
  you need that path to work too.
- **Sequence builder handles:** custom xyflow nodes need explicit
  `<Handle>` components to be connectable. `SequenceBuilder.NodeCard`
  has source on the right + target on the left; entry nodes hide
  their target. Edges use `MarkerType.ArrowClosed` for direction.
- **LinkedIn rate-limit singleton + pytest:** `sequencer._LI_REDIS_CLIENT`
  is a module-level cache. pytest-asyncio gives each test a fresh event
  loop; tests that exercise `_li_redis()` must monkeypatch the global
  back to None at the start. `test_phase18_linkedin_write.py` uses an
  `autouse` fixture that does this for every test in the file — copy
  that pattern in any new file exercising LinkedIn rate limits.
- **`encryption.decrypt` allowlist:** the hardening grep test in
  `test_phase15_hardening.py` lists `imap_client.py` + `encryption.py`.
  Adding a new credential consumer means updating this list AND keeping
  plaintext within the new module's local scope (delete before return).
- **InMail premium-required path:** when Unipile reports the account
  lacks InMail credits, `send_inmail` returns
  `ok=False, premium_required=True` rather than raising.  The sequencer
  maps that to a `skipped` execution row with a clear error
  ("InMail unavailable — account needs Premium / Sales Nav credits")
  so the campaign keeps moving rather than retrying forever.
- **Soft-deleted sequence nodes** (M5): when the user re-edits a
  graph, the OLD nodes get `deleted_at` stamped, not deleted. This
  preserves historical `lead_step_executions` for analytics. The
  trade-offs:
  - Live queries (router, scheduler, analytics) must filter
    `deleted_at IS NULL`. A partial index
    `ix_sequence_nodes_live` is in place for that filter.
  - Leads whose `current_node_id` points at a soft-deleted node get
    auto-halted by the scheduler with a clear reason.
  - The analytics endpoint currently shows ONLY live nodes; retired
    nodes' counts aren't surfaced. If you want lifetime totals, add a
    `?include_deleted=true` flag later.
- **Rebuild ALL THREE Python services when you change `requirements.txt`.**
  `backend`, `worker`, and `beat` all build from the same Dockerfile but
  docker-compose tags them as separate images. `docker compose build
  backend` does NOT rebuild `worker` / `beat`. Symptom: backend boots
  fine but campaigns get stuck because tasks never run; `docker compose
  logs worker` shows `ModuleNotFoundError`. Always run
  `docker compose build backend worker beat` after editing requirements.
- **`docker compose restart` does NOT re-read `.env`.** Env vars get
  substituted into containers at *create* time, not on restart. After
  editing `.env`, a plain restart leaves the old values in the running
  containers. Symptom: a config-only change (new Brevo key, new
  Anthropic key, etc.) appears in `.env` but the app still hits the
  old credential and 401s. Fix: recreate the affected containers —
  ```bash
  docker compose up -d --force-recreate backend worker beat
  ```
  Verify by comparing host `.env` against `docker compose exec backend
  printenv VAR_NAME` after the recreate.
- **A new setting in `.env` alone does NOT reach the containers.**
  `docker-compose.yml` passes env via an explicit per-service
  `environment:` allowlist (`VAR: ${VAR:-default}`), NOT `env_file`,
  so only enumerated vars are injected.  The app's pydantic `Settings`
  then falls back to its *code default* for anything missing — which
  silently masks the problem when the `.env` value happens to equal the
  default.  Symptom: you set `FOO=...` in `.env`, recreate, but
  `docker compose exec worker printenv FOO` is empty and changes to it
  never take effect.  Fix: add the var to the `environment:` block of
  **all three Python services** (`backend`, `worker`, `beat`) as
  `FOO: ${FOO:-<default>}`, then `docker compose up -d --force-recreate
  backend worker beat`.  (This is how `LINKEDIN_STAGGER_SECONDS` was
  wired — it read 120 from the default until added to the compose
  blocks.)  `docker compose exec worker printenv FOO` returning the
  value is the real confirmation; `settings.FOO` matching can be a
  false positive when it equals the default.
- **Pre-M1 leads have no `lead_sequence_state` row.** Campaigns +
  leads that existed before the M1 migration's backfill ran are fine
  going forward, but if your dev DB had leads inserted via the legacy
  CSV flow that doesn't enroll, the analytics endpoint will show
  `total_leads=0` for them. The fix is a one-off backfill or just
  re-create the campaign.
- **Don't cache aioredis at module scope in worker code.** Celery
  prefork tasks each call `asyncio.run(...)` which builds a fresh
  event loop; a cached `aioredis.Redis` carries connection-pool state
  bound to whichever loop first created it and fails with "Event loop
  is closed" on the second task.  `sequencer._li_redis()` still caches
  (legacy) — fine for tests that monkeypatch `_LI_REDIS_CLIENT=None`,
  but rewrite if it ever causes issues in production.
- **Activity tab dedupes consecutive same-(lead, node) rows.**  The
  `/campaigns/{id}/activity` endpoint pulls 100 raw rows from
  `lead_step_executions`, clusters them by `(lead_id, node_id)`
  preserving desc-by-time order, then returns up to 30 clusters with
  an `attempt_count` field.  The UI shows a small ``×N`` badge next to
  the step name and a tooltip with the earliest + latest attempt time.
  Spec / wire format in `SequenceStepEvent` schema; tests in
  `test_phase29_activity_clustering.py`.
- **LinkedIn step has a stale-dispatch guard.**  After a worker restart
  (or a manual ``next_run_at`` nudge while a Celery task was in
  ``unacked``), the same ``send_linkedin_step`` task could re-deliver
  with its original ``node_id`` even though the lead's cursor had
  advanced.  Without a guard, this fired Unipile a second time (real
  duplicate ghost-view / connect / DM).  Fix: ``_send_linkedin_step_async``
  checks ``state.current_node_id == node_id`` at the top; mismatch →
  returns ``{"status": "stale_dispatch"}``, no API call, no rate-slot
  burn.  ``_record_execution_and_advance`` recognises that status and
  writes no execution row + leaves the cursor untouched.
- **LinkedIn steps honour the campaign schedule window + paused state.**
  `_send_linkedin_step_async` checks `campaign.status == PAUSED` and
  `compute_next_send_window(campaign)` up front; outside-window /
  paused → returns `{"status": "deferred", "reason": "scheduled"|"paused",
  "retry_at": ...}` BEFORE `_li_rate_acquire` runs, so a deferred lead
  doesn't burn a LinkedIn rate slot.  `view_profile` respects the
  window too — even though it's exempt from the daily-action cap, an
  account that only operates in business hours should "look human" to
  LinkedIn's behavioural scorer.  Defers to the next window-open ETA
  the same way the email step does.
- **Follow-up email step honours send gates.**  `_send_email_step_async`
  reuses `send.check_send_gates` so suppression / paused / schedule /
  Brevo rate-limit checks behave identically to the legacy first-email
  path.  A tripped gate returns `{"status": "deferred", "reason": "...",
  "retry_at" | "retry_in": ...}`; `_record_execution_and_advance` parks
  the lead on the current node and reschedules to the exact retry time
  the gate returned WITHOUT consuming `MAX_TRANSIENT_RETRIES`.  A
  campaign paused for a day no longer silently advances past every
  follow-up.  Brevo rate counters bump on the follow-up step too so
  steps are metered together with the first email.
- **`send_lead_async` holds a row-level lock through the Brevo POST.**
  `select(...).with_for_update()` on the lead row keeps a concurrent
  invocation (rate-limit retry colliding with a beat-dispatched task)
  blocked on the lock until the commit; the second one then reads
  `SendStatus.SENT` and short-circuits.  Defends against the duplicate-
  send race the audit flagged.
- **Pause is a HARD STOP.**  When `send_lead_async` returns `{status:
  paused}`, the Celery wrapper does NOT self-re-enqueue (`workers/send.
  py:370` — was `apply_async(countdown=300)`, now an early `return`).
  Previously every paused-campaign queued task was respawning every 5
  min, holding tens of thousands of zombie tasks for as long as the
  campaign stayed paused.  Now the task is acked-and-gone; the lead's
  `send_status` is left at whatever the gate left it (PENDING for
  legacy first-email, SCHEDULED if a prior pass deferred it on the
  window), and `resume_campaign` re-enqueues every composed
  PENDING+SCHEDULED lead via staggered `send_lead.apply_async(eta=...)`
  — same pattern as `_kick_off_full_campaign`.  **Resume IS the
  trigger; pause is the off switch.**  Required because nothing reads
  `lead.scheduled_send_at` (it's a write-only DB stamp); without the
  resume re-enqueue, SCHEDULED leads are orphans forever.  Sequencer-
  driven follow-up / LinkedIn steps don't need this same treatment —
  the beat already filters `Campaign.status == RUNNING`, so paused
  campaigns aren't visited.  Remediation for pre-fix orphans:
  `backend/scripts/reenqueue_stuck_scheduled.py <campaign-id>`.
- **The email min-delay gate is an atomic CLAIM, not a read.**
  `check_rate_limits` does `SET rate:{cid}:min_gate <now> NX EX
  min_delay` — whoever sets it first owns that `min_delay` window; the
  rest get the key's TTL as `retry_in`.  This is what stops Celery
  prefork (N tasks at once) from all passing a read-only check and
  bulk-sending.  Consequences: (1) it has a SIDE EFFECT inside a
  "check" function, so it's the LAST gate (after suppression/paused/
  window) — only claimed when everything else passed; (2) a send that
  then fails at Brevo still "wastes" that window (acceptable over-
  spacing, never under); (3) `increment_rate_counters` only bumps
  hour/day now — the old `rate:{cid}:last_sent` key is gone.
  `SET NX EX` is atomic on fakeredis too, so no Lua / real-Redis test
  swap was needed (unlike the LinkedIn limiter).  approve-all also
  pre-spaces dispatch by `min_delay` (`apply_async eta`) so the gate
  rarely has to bounce in the common in-window case.  The daily cap
  counter (`rate:{cid}:day`) expires at the next local midnight in the
  campaign tz (`_seconds_until_local_midnight`), so "daily" is a
  calendar day — same as LinkedIn; the hourly counter stays rolling 60m.
- **Unsubscribe link is HMAC-signed + POST-confirm.**  GET renders a
  confirm page (no side effect), POST applies the suppression.  Token is
  `hmac_sha256(SECRET_KEY, lead.id.bytes)[:32]` so email-scanner GET
  prefetchers (Outlook/Defender) can't auto-unsubscribe leads, and
  knowing one lead's URL doesn't let an attacker forge another's.
  `make_unsubscribe_url(lead_id)` is the helper for templates that
  embed the link.
- **Unipile webhook is now idempotent.**  ``webhook_events(provider,
  event_id)`` table (migration 0007) records every incoming Unipile
  event id and acts as the dedup guard.  The route inserts the row in
  its own transaction up front; UniqueViolation → ``{"ok": True,
  "duplicate": True}`` with no handler dispatch.  When Unipile sends a
  payload with no id we fall back to a SHA-256 of the raw body so
  byte-identical retries still dedup.  Trade-off: a handler crash
  after the dedup commit drops Unipile's retry — at-most-once on our
  side — which is intentional and replaces the previous at-least-once
  with double-apply.
- **Stuck RUNNING leads get swept back to PENDING.**  ``lead_sweeper``
  beat task every 5 min flips ``compose_status`` / ``research_status``
  RUNNING rows whose ``updated_at`` is older than
  ``STALE_AFTER_MINUTES`` (15) back to PENDING and re-enqueues the
  matching Celery worker.  Defends against the "worker crashed
  between RUNNING-commit and final-commit" pattern that previously
  left rows invisible to ``/retry-failed`` (which only sees FAILED).
- **LinkedIn rate-limit acquire is atomic.**  ``_li_rate_acquire``
  runs a Lua script in Redis that does min-delay + daily-cap + per-kind
  subcap + per-page-monthly check + counter bump as ONE operation.
  Two concurrent ticks can no longer both pass under a cap of one.
  Trade-off: a failed action still counts the slot (no refund) — a
  cleaner over-count than the previous race that double-fired.
- **The sequencer beat only drives RUNNING campaigns.**
  `_advance_sequences_async` filters `Campaign.status == RUNNING`, so a
  paused campaign freezes in place (leads keep their node + next_run_at,
  no dispatch, no stagger-slot consumption).  The per-step handlers
  still re-check paused as belt-and-suspenders.  Two consequences:
  (1) a campaign paused mid-flight resumes exactly where it left off;
  (2) the beat auto-resumes campaigns that were cap-auto-paused — it
  runs an `UPDATE campaigns SET status=RUNNING WHERE status=PAUSED AND
  auto_paused_until <= now` at the top of each tick (same transaction
  as the SELECT, so resumed leads run immediately).  Manual pauses have
  `auto_paused_until IS NULL` and are never auto-resumed.
- **LinkedIn cap auto-pauses a campaign (`auto_paused_until`).**  When a
  LinkedIn step hits `daily_cap`/`connect_cap`/`dm_cap` and no email
  node is reachable downstream (`_has_downstream_email`), the campaign
  is paused with `auto_paused_until` = cap-reset time and auto-resumes
  then.  Gated on `node.kind in LI_KINDS` — `daily_cap` is ALSO the
  Brevo email-gate reason, so without that gate an email rate-limit
  would wrongly pause the campaign.  Email-bearing sequences keep
  running at the cap (email isn't LinkedIn-capped).
- **Transient LinkedIn failures park-and-retry; only permanent ones
  skip the lead.**  A failed action's `ActionResult.meta` now carries
  `http_status` (the Unipile HTTP status; network failures use the
  synthetic `0`).  `_send_linkedin_step_async` classifies via
  `_is_transient_failure`: 5xx / 0 (network) / 429 / 3xx (stray
  redirect) → status `transient_error` (in `TRANSIENT_SKIP_STATUSES`,
  so it parks on the node and retries every `TRANSIENT_RETRY_MINUTES`
  up to `MAX_TRANSIENT_RETRIES`, then advances).  Specific 4xx client
  errors (invalid recipient, profile locked, already-invited, ...) and
  unknown/absent status stay `failed` → advance the cursor (permanent
  skip), as before.  `transient_error` maps to a SKIPPED execution row
  so it counts against the per-visit retry budget like the other
  transient statuses.
- **The Unipile httpx client follows redirects (`follow_redirects=True`).**
  A LinkedIn public slug with accented characters (e.g.
  `rahnà-wakę-...`, `josé-...`) makes Unipile **301-redirect**
  `GET /api/v1/users/{slug}` to a Unicode-normalized (NFD +
  percent-encoded) URL.  httpx does NOT follow redirects by default, so
  without this the client returned the 301's HTML "Redirecting" page,
  which surfaced as `{"status": "failed", "error": "<!DOCTYPE html>…"}`
  — and since a failed LinkedIn step *advances the cursor*, the lead
  got skipped past the connect without ever sending it.  If you ever
  rebuild the client kwargs, keep `follow_redirects=True`.  (One-off
  remediation when this bit us: find `lead_step_executions` with
  `result='failed'` and `error ILIKE '%DOCTYPE%'` on connect nodes,
  then reset those leads' `current_node_id` back to the live connect
  node.)
- **The LinkedIn daily cap resets on the calendar day (campaign tz),
  not a rolling 24h.**  `_li_rate_acquire(daily_ttl_seconds=...)` is
  passed `_seconds_until_midnight(campaign.schedule_timezone)`, so the
  `li-rate:{aid}:day[:connect|:dm]` counters expire at local midnight
  (set NX on the day's first action).  A "new day" lifts the cap.  The
  per-account counter's TTL is set by whichever campaign acts first
  that day (its tz wins) — fine for the common single-tz case.  When
  changing this, note the auto-pause `retry_in` (and thus
  `auto_paused_until`) rides on this TTL, so the campaign auto-resumes
  at the next local midnight + a 60s buffer.
- **LinkedIn dispatch is staggered, not bulk — with DISTINCT slots.**
  The sequencer beat releases at most one LinkedIn step per account per
  `LINKEDIN_STAGGER_SECONDS` (default 120; 0 disables).  Due leads
  beyond the open slot are PARKED (`next_run_at` set to a future slot)
  without dispatching.  Slot reservation is an atomic Redis Lua
  (`_LI_STAGGER_LUA` via `_reserve_li_stagger_slot`) using TWO keys per
  account: `li-stagger:{key}:last` (last *dispatch* time — a parked
  lead becomes dispatch-eligible when `now >= last + interval`, so it
  fires when its slot arrives with NO push-back) and
  `li-stagger:{key}:hw` (high-water of slots handed out — new parks go
  to `hw + interval`, giving every waiting lead a DISTINCT timestamp).
  The two-key design matters because the beat runs (60s) more often
  than the interval (120s): a naive single "next slot = last + interval"
  piled every non-dispatch tick's leads onto the same timestamp
  (looked like a simultaneous batch in the UI even though dispatch was
  correctly one-per-interval).  The worker rate limiter
  (`_li_rate_acquire`) is now just a backstop, rarely hit.  Because the
  beat uses Redis, `advance_sequences` resets `_LI_REDIS_CLIENT=None`
  at task entry (fresh client per `asyncio.run` loop) — same caveat as
  the worker tasks.  Staggering is per-account so distinct accounts run
  in parallel; only LinkedIn kinds (`LI_KINDS`) are staggered, email
  dispatch is unaffected.  `view_profile` (and anything in
  `_UNCOUNTED_ACTION_KINDS`) is exempt from BOTH the daily cap and the
  staggering — low-risk reads fire promptly — but the per-action
  min-delay still applies to them as a burst guard.  Note: leads
  newly advanced into a node via `_advance_cursor` get `next_run_at =
  now`, so a batch arriving in one tick momentarily shares a timestamp
  until the next tick re-spreads them — a transient, not a pile.
- **Brevo events come from polling, not a webhook.**  The inbound
  `/webhooks/brevo` route was removed; events are pulled from
  `GET /v3/smtp/statistics/events` by `brevo_events_poller.poll` every
  `BREVO_EVENTS_POLL_INTERVAL_MINUTES` (default 10).  Trade-off: up to
  10 min lag from event-at-Brevo to event-row-in-DB.  Reasoning:
  Brevo's outbound event webhook is gated behind paid plans on some
  tiers + needs a public tunnel; polling is free with the regular API
  key and works behind the localhost-only port binding.  Shared
  ``app.services.brevo_events.process_event`` does the lead lookup +
  suppression-table write + event-row insert; the poller is the only
  caller now.  Watermark is in Redis at `brevo:events:last_polled_at`
  with a 24h lookback floor and 5-min overlap between polls.  Per-event
  dedup is built into ``process_event`` so re-fetching the same day
  window (day-granularity API) doesn't double-insert.
- **All four ports bound to `127.0.0.1` only.**  `docker-compose.yml`
  uses `"127.0.0.1:8000:8000"` etc. so the unauthenticated API can't
  be reached from LAN.  Tunnel (ngrok/cloudflared) still works because
  it forwards via the host loopback.
- **Sequencer parks on async-event edges.** When an action step succeeds
  but no outgoing edge condition currently matches, the sequencer
  parks the lead on the current node instead of halting — as long as
  at least one unmatched edge uses a "deferrable" op (one of
  `linkedin_connection`, `replied`, `opened`, `clicked`, `bounced`,
  `days_since_entered_node`).  Re-evaluation runs every
  `EDGE_WAIT_RETRY_MINUTES` (30) up to `MAX_EDGE_WAIT_DAYS` (14), then
  halts with a clear reason.  This is how `linkedin_connect → DM
  (when linkedin_connection=connected)` waits for the prospect to
  accept the invite before firing the DM.  Re-dispatch is suppressed
  while parked via `_already_executed_this_visit` (checks for a SENT
  `lead_step_executions` row since `entered_current_at`).  Negations
  (`not replied`) and compound (`and`/`or`) conditions do NOT trigger
  parking — they halt immediately, preserving the "skip on reply" pattern.
- **Sequencer transient retries.** A `skipped` step normally advances
  the cursor immediately, but skips with `status` in
  `TRANSIENT_SKIP_STATUSES = {"challenged", "restricted", "rate_limited"}`
  keep the lead pinned on the current node and push `next_run_at` out
  by `TRANSIENT_RETRY_MINUTES` (5). After `MAX_TRANSIENT_RETRIES` (10)
  consecutive transient skips on the same visit, the cursor advances
  like a normal skip so a permanently-broken account doesn't hold the
  lead forever. The retry budget resets when the lead re-enters the
  node (via `entered_current_at`).

## Run / develop

```bash
docker compose up -d                               # bring stack up
docker compose exec backend alembic upgrade head   # apply migrations
docker compose run --rm backend pytest             # backend tests (402)
docker compose exec frontend npm test              # frontend tests (150)
docker compose logs -f backend                     # tail logs
```

App: <http://localhost:5173>  ·  API: <http://localhost:8000>  ·  Docs:
<http://localhost:8000/docs>

## Open questions

- **Per-edge funnel percentages.** Analytics currently shows
  per-node counts only. Per-edge "% of leads who took this branch"
  would need a transition-history log (we don't write one today). Add
  an `edge_id` column to `lead_step_executions` or a new table when
  this becomes useful.
- **Per-step email metrics.** Aggregate email open/reply rates per
  node would need `step_execution_id` on `email_events` so we can
  attribute opens to the specific step that sent them. Punt until
  someone actually asks.

## Recent decisions worth remembering

- **Build the framework, ship email-only first.** User chose this in the
  M1 kickoff. The default 1-node sequence + auto-skip of the entry email
  node means the legacy pipeline and the new sequencer coexist without
  conflict.
- **Rich if/then-tree branching** — user picked the most expressive
  option. Implemented as a JSON expression language with `and/or/not` +
  leaf ops. Visual builder deferred to M4 (a JSON textarea ships in M1).
- **LinkedIn path: Unipile** (final decision, supersedes the earlier DIY
  bet).  Tried DIY Playwright with persistent profiles, Redis locks,
  stealth patches, and human-dwell — every fresh Chromium launch still
  tripped LinkedIn's bot scorer because the headless+datacenter
  fingerprint was unsalvageable.  Unipile runs real desktop Chrome on
  residential IPs and has been steady; the entire DIY codebase was
  stripped in 2026-05-14.

---

_Last updated: 2026-06-15 (latest) — Contact enrichment + deferred queue
for funding discovery.  `resolve_contact` rewritten into a cheapest-first
`ContactResult` pipeline (domain discovery → website scrape → Hunter
domain-search → ProPublica → role-priority verified pick).  Contactless orgs
no longer flood the review queue: they park in `FundingEnrichmentQueue`
(migration 0036) and a daily `funding.retry_enrichment` worker promotes them
once a contact resolves, exhausting (or direct-mailing) after 3 tries.  IRS
BMF mailing address now carried for the direct-mail fallback.  16 new + 6
updated backend tests.  Tests: **backend 1025**.

_Previously: 2026-06-15 — Fixed the discovery Stop button.  A
funding poll runs longer than the broker visibility_timeout (300s), so with
`acks_late=True` Redis redelivered it → concurrent copies that kept pulling
(Stop killed one, another was just redelivered).  Fix: `acks_late=False` on
both poll tasks (no redelivery) + a cooperative `funding:stop:<source>` flag
that `_stage_all` checks before each org so Stop aborts mid-batch.  3 backend
tests.  Tests: **backend 1009**.

_Previously: 2026-06-15 — Fixed USAspending discovery 0-signals
bug.  The poll advanced a since-cursor to `today` each run, so the window
was always ~1 day regardless of lookback — and federal action_date data
lags, so back-dated awards were permanently skipped.  Now always queries a
trailing `lookback_days` window (dedup makes overlap free); default lookback
7 → 30.  2 backend tests.  Tests: **backend 1006**.

_Previously: 2026-06-15 — "Reply to previous email" sequence
node.  A new `email_reply` builder node (migration 0035) replies in-thread
to the lead's original campaign email (In-Reply-To/References via
`brevo.send_email(in_reply_to=...)`, subject `Re: …`) instead of a new
thread.  Body is AI-written (`compose.generate_followup_reply`, steered by
a user prompt, reusing existing research — NO new research) or a manual
template.  `validate_graph` rejects it as entry + requires body-or-ai;
builder gains a "Reply" node with an AI-toggle/prompt editor.  12 new
backend + 1 frontend test.  Tests: **backend 1005, frontend 350**.

_Previously: 2026-06-15 — Editable campaign goal that rewrites
unsent emails.  The Overview Goal editor lets you change the goal on a
running/paused campaign; saving re-composes every composed, not-yet-sent
email (reusing existing research — NO new research) so the new goal takes
effect, leaving sent emails alone.  `update_campaign` exempts `goal` from
the content guard (409 on complete) and dispatches `compose_lead` for
unsent leads on a real change.  4 new backend + 2 new frontend tests.
Tests: **backend 993, frontend 350**.

_Previously: 2026-06-15 — Campaign signature inherits from
Settings.  A campaign with no signature of its own now inherits the
bound connected account's signature (set in Settings); the Leads-tab
editor shows the inherited signature with a "Customize for this campaign"
override + a "Use Settings signature" revert.  Shared
`signature.resolve_campaign_signature` (override → account → None) drives
compose + apply-signature; `CampaignResponse.account_signature` exposes
the inherited value.  4 new backend + 2 new frontend tests.
Tests: **backend 989, frontend 348**.

_Previously: 2026-06-15 — "Find contact" light enrichment
(now searches LinkedIn).  A per-signal button enriches notification-only
signals: one capped Haiku web-search finds the org's decision-maker +
public LinkedIn profile + domain, that name drives Hunter's Email Finder
(verified), with the role-priority domain search as fallback.  On a hit
it ALWAYS stages + links a campaign-less Lead (storing `linkedin_url`) —
including a LinkedIn-only lead with no email (`leads.email` now NULLABLE,
migration 0034) — so the Draft / Send / Add-to-campaign flows light up
for emailable contacts and "View lead" / a profile link for
LinkedIn-only ones.  `POST /signals/{id}/enrich` + `services/signal_enrichment`;
the signals feed exposes `lead_has_email`; the funding worker persists
org_name/website/state/ein/ntee into signal `detail`.  11 new backend +
4 new frontend tests.  Tests: **backend 985, frontend 346**.

_Previously: 2026-06-15 — Bulk add signals to a campaign.
The Signals feed lets the user select `new` signals, pick a campaign,
and copy their staged leads in via shared
`services/campaign_membership.add_leads_to_campaign` (COPY not move;
de-dupe + skip suppressed/existing/missing; enroll + kick research for
non-draft campaigns).  `POST /signals/add-to-campaign` then flips the
contactable signals to ACTIONED.  Frontend: per-card checkbox + bulk
bar with campaign picker.  4 new backend + 2 new frontend tests.
Tests: **backend 975, frontend 342**.

_Previously: 2026-06-15 — Signal outreach: draft → pick
sender → send → logged (no migration).  Click a signal to open a detail
modal and send outreach right from the queue.
- **Shared send core** ``services/outreach.py`` — extracted the
  Research-a-client send+CRM-track logic into ``send_and_track`` (sender
  resolution: explicit > workspace default sender > Brevo sender; applies
  that account's signature via ``render_email_with_signature``; Brevo
  send; best-effort CRM find-or-create lead + outbound EMAIL activity +
  opportunity attach).  Raises ``OutreachSendError`` (router → 502).
  Takes an optional pre-resolved ``lead`` so signal sends log against the
  STAGED lead instead of find-or-create.  ``research_client/send`` now
  delegates to it (behavior preserved; its CRM-failure test repointed to
  patch ``app.services.outreach.CrmActivity``).
- **Signal composer** ``services/signal_outreach.compose_signal_email``
  — one Sonnet call (``ANTHROPIC_MODEL``) with a signal-tailored prompt
  that opens on the specific trigger (the grant / the new 501(c)(3) /
  the change); strips em dashes + truncates to char_limit; no signature
  (the send path appends it).
- **Endpoints** (``routers/signals.py``): ``POST /signals/{id}/draft``
  (compose from the signal + its linked lead; 409 when the signal has no
  contactable lead) and ``POST /signals/{id}/send`` (send via
  ``outreach.send_and_track`` against the staged lead, then flip the
  signal to ACTIONED; 502 on Brevo failure leaves it NEW).
- **Frontend** (``pages/Signals.jsx``): cards are now clickable → a
  ``SignalDetailModal`` showing the signal detail + a Draft button →
  editable subject/body + a send-from picker (connected accounts,
  workspace default) + signature preview → Send → success + CRM note;
  cards with a lead also get a "Draft & send" button.  Contact-less
  signals show a notification-only note.  The Actioned/Dismiss buttons
  ``stopPropagation`` so they don't open the modal.
- Tests: 5 new backend + 3 new frontend (+ 1 research_client patch
  repoint).  Tests: **backend 965, frontend 338**.

_Previously: 2026-06-15 — Settings → Discovery panel
(migration 0033).  Moved the nonprofit-feed toggles/config off
env-only into the DB so they're editable in-app.
- **Migration 0033** adds ``funding_source_state.enabled`` (bool, NULL
  = unseeded) + ``config`` (JSONB: ``{lookback_days}`` for usaspending,
  ``{ruling_lookback_months, states}`` for irs_bmf).  The env vars
  (``USASPENDING_ENABLED`` etc.) are now the FIRST-RUN SEED: the worker
  + API ``_get_or_create_state`` / ``_seed_funding_state`` populate
  these columns from env when NULL, then the DB row is authoritative
  (mirrors the AgentSettings env-seeds-DB pattern).  The poll tasks now
  gate on ``state.enabled`` and read lookback/states from
  ``state.config``, NOT directly from ``settings`` — but existing tests
  still pass because each test patches ``settings.*`` before the row is
  created fresh in its truncated DB, so the seed picks up the patched
  value.
- **API** (in ``routers/signals.py``): ``GET /signals/funding/sources``
  (both feeds: enabled, config, last_run, cursor, per-source
  signal_count, + top-level ``hunter_configured``), ``PATCH
  /signals/funding/sources/{source}`` (enabled + lookback/states/ruling;
  states normalised upper+dedupe), ``POST .../run-now`` (enqueues the
  poll task; 409 if disabled, or IRS with no states).
- **Frontend** Settings gains a **Discovery** tab: a card per feed with
  an enable toggle, config inputs (USAspending lookback days; IRS states
  + ruling-lookback months), last-run status, signals-surfaced count, a
  Save and a Run-now button, plus a Hunter-not-configured warning
  (discovered orgs are notification-only without a key).  Settings tests
  now wrap in ``ToastProvider`` (the Discovery tab uses ``useToast``).
- Tests: 7 new backend + 4 new frontend.  Tests: **backend 960,
  frontend 335**.

_Previously: 2026-06-15 — Nonprofit funding discovery (migration
0032).  A new DISCOVERY PATH (not a subsystem) feeding the existing
``prospect_signals`` review queue from two free external feeds — the
sibling relationship social_listening has with signals.
- **Migration 0032:** ``prospect_signals.watch_id`` → NULLABLE
  (discovery signals have no watch); ``prospect_signals.source`` TEXT
  indexed ('usaspending' | 'irs_bmf' | NULL=watch).  New
  ``funding_source_state`` (source PK, cursor JSONB, last_run_at/status)
  — the per-source diff baseline, like ``SignalWatch.last_seen``.
- **Feeds** (``app/services/funding_sources/``):
  ``usaspending.fetch_recent_awards`` POSTs the free no-auth
  ``/api/v2/search/spending_by_award/`` (award_type_codes 02-05,
  ``recipient_type_names=['nonprofit']``, action_date window,
  paginate on ``page_metadata.hasNext``); dedup_key
  ``grant_awarded:<award_id>``.  ``irs_bmf.fetch_new_501c3`` downloads
  per-state EO BMF CSVs (``irs.gov/pub/irs-soi/eo_<st>.csv``), keeps
  ``SUBSECTION==3 AND RULING(YYYYMM)>=since_ruling``; dedup_key
  ``new_501c3:<ein>``.  Both never raise (feed outage → log + partial).
  ``enrichment.resolve_contact`` finds a domain (org site else one
  capped Haiku ``_web_lookup``) then a decision-maker email via Hunter
  (role priority ED → Dev Director → Grants Manager), verified;
  generic ``info@`` flagged low-priority.
- **Hunter:** added ``find_email_hunter(domain, full_name=, role=)``
  (Email Finder when a name is known, else Domain Search ranked toward
  the role).  No key → None (feature opt-in; orgs become
  notification-only).
- **Worker** (``app/workers/funding_signals.py``):
  ``_stage_discovery_signal`` enforces the autonomy boundary — with a
  deliverable email it stages a **campaign-less** Lead
  (campaign_id=None, research_data={ein,ntee,source,...}) + a
  "Reach out" CRM TASK (reminder_sent_at=now) + ProspectSignal
  (watch_id=NULL, source set) + ONE owner notification; no email →
  signal + notification only.  NEVER sets campaign_id / enrolls a
  sequence.  Per-org commit so one bad org can't roll back the batch;
  dedup guard makes re-polls idempotent.  ``funding.poll_usaspending``
  (daily 04:00) and ``funding.poll_irs_bmf`` (monthly, 15th 05:00,
  after the 2nd-Tuesday refresh) advance the cursor; IRS first-run
  guard bounds since_ruling to the lookback floor so it never blasts
  the whole historical file.  Both no-op when their ``*_ENABLED`` flag
  is false (default).
- **Config:** ``USASPENDING_ENABLED`` (False), ``USASPENDING_LOOKBACK_DAYS``
  (7), ``IRS_BMF_ENABLED`` (False), ``IRS_BMF_STATES`` (``list[str]``,
  empty=skip — ``Annotated[..., NoDecode]`` + a before-validator so it
  accepts both ``PA,NJ`` and ``["PA","NJ"]``), ``IRS_BMF_RULING_LOOKBACK_MONTHS``
  (2).  Reuses HUNTER_API_KEY + ANTHROPIC_AGENT_MODEL.  Wired into the
  backend+worker compose blocks.
- **API/UI:** ``GET /signals`` serializer now returns ``source`` + a
  ``?source=usaspending|irs_bmf|watch`` filter (still NO inner join on
  signal_watches, so watch_id-NULL discovery signals appear in the
  queue).  Signals page gains a source badge (USASpending / IRS BMF /
  Watch) + a source filter.
- Tests: 14 backend (``test_phase43_funding_discovery.py``) + 3
  frontend.  Tests: **backend 953, frontend 331**.

_Previously: 2026-06-14 — CRM Reporting tab.  New read-only
reporting layer (no migration) mounted under ``/crm/reports`` +
a Reports nav page.
- **``GET /crm/reports/overview?start=&end=``** — date-scoped
  dashboard: KPIs (won/lost count+value, win rate, avg deal size,
  avg sales cycle days, open pipeline value + probability-weighted
  value, deals created, new leads, conversions, activities), won/lost
  monthly trend (continuous ``YYYY-MM`` buckets so charts have no
  gaps), current pipeline-by-stage snapshot (weighted by explicit
  probability else ``STAGE_DEFAULT_PROBABILITY``), forecast by
  ``close_date`` month (``"unscheduled"`` bucket last), loss-reason
  breakdown, activity breakdown (by type/direction/agent-vs-human),
  and a conversion funnel (leads → opps → won with rates).  Default
  window is last 90 days.  **Date semantics:** won/lost scoped by
  ``closed_at``; pipeline+forecast are a live snapshot (NOT
  date-scoped — pipeline is "where things stand now"); leads/
  conversions/activities scoped by their own timestamps.
- **``GET /crm/reports/deals?outcome=won|lost|open|all``** + **``GET
  /crm/reports/activities``** — detail rows backing the UI tables +
  client-side CSV export (capped at 2000 rows, ``truncated`` flag).
  won/lost ordered most-recently-closed; open/all are a snapshot
  ignoring the date window.
- All aggregation is in ``routers/reports.py`` (closed + open deal
  rows pulled and rolled up in Python — closed sets are small;
  activity/lead counts via SQL group-by/COUNT).  ``schemas/reports.py``
  holds the response models.  No new tables → nothing added to the
  conftest truncate list.
- Frontend ``pages/Reports.jsx``: date-range presets (30d/90d/12mo/
  YTD/all-time + custom start/end), KPI card grid, Recharts won-vs-lost
  bar chart, pipeline/forecast/loss-reason/activity/funnel cards, and
  Deal-detail (won/lost/open tabs) + Activity-detail tables each with
  an Export-CSV button (Blob download, no server round-trip).  Reuses
  ``STAGES``/``fmtAmount`` from Opportunities.jsx.  Nav entry between
  Opportunities and Replies.
- Tests: 10 backend (``test_phase42_crm_reports.py``) + 6 frontend
  (``Reports.test.jsx``, recharts stubbed, ``URL.createObjectURL``
  stubbed for the CSV-export assertion).  Tests: **backend 940,
  frontend 328**._

_Previously: Signals section build-out.  The
page shipped functional but unexplained; this pass makes it
self-documenting and list-friendly:
- **Per-type required fields enforced at creation** (422 with pointed
  messages instead of silently-useless checks forever): job_change →
  email (Apollo people/match keys on email; also 422 when tracking an
  email-less opportunity), funding/hiring → company, custom → at least
  one of email/company.  Lead/opp-linked watches inherit fields from
  the record.
- **Duplicate-watch 409** — one ACTIVE watch per (type, target), keyed
  on lead_id / opportunity_id / lower(email) / lower(company).
- **``POST /signals/watches/bulk``** — one watch per pasted company
  (funding/hiring only; job-change is per-person by email).  Blank /
  repeated lines dropped; companies already watched for that type are
  skipped, not errors.  Returns {created, skipped_duplicate, watch_ids}.
- **UI:** collapsible "How signals work" explainer (what a watch is,
  what each type needs, what happens on detection, the
  baseline-then-diff model); type picker buttons with per-type
  descriptions + dynamic required fields; "Multiple companies"
  textarea mode on funding/hiring; watch rows now show the target,
  CRM-link badge, and the last_seen baseline (title / stage / role
  count).  Leads modal gains "⚡ Track signals" (creates a custom
  watch for the lead; 409 reads as already-tracking).
5 new backend + 4 new/updated frontend tests.
Tests: **backend 928, frontend 322**._

_Previously: Add-to-campaign from the Leads page.
``POST /campaigns/{id}/leads/add`` bulk-adds existing leads (CRM/
manual, lookalike-accepted, signal-staged, or another campaign's) by
COPYING them — copy, not move, because ``leads.campaign_id`` cascades
on campaign delete, so assigning a CRM lead would let routine campaign
deletion destroy its CRM history.  The new row enters the target
campaign's pipeline fresh: sequence-enrolled via the same
``ensure_default_sequence``/``enroll_leads`` path as CSV upload, and
``run_campaign_research`` kicks immediately for non-draft campaigns
(research → compose → send follows the existing status gates:
PREVIEWING waits for approve-all, PAUSED waits for resume).  Draft
campaigns hold the rows until launch — ``confirm-upload`` researches
every pending lead, added ones included.  Skips are counted, never
errors: suppressed emails, emails already in the target, in-batch
duplicates, unknown ids.  COMPLETE campaigns 409.  Frontend: checkbox
column + select-all on the Leads table (checkbox clicks don't open the
row modal), a bulk bar with a campaign picker (complete campaigns
excluded) and a copy-semantics hint; success toast reports
added/skipped counts.  6 new backend + 3 new frontend tests.
Tests: **backend 923, frontend 319**._

_Previously: Prospecting & outreach upgrades:
four features, migrations 0028-0031, each committed separately.

- **B. Deliverability guard (0028).**  (1) Per-SENDING-DOMAIN Redis
  caps (``rate:domain:{domain}:hour|day``, ``DOMAIN_MAX_PER_HOUR/DAY``)
  shared across campaigns; domain resolves from the connected account's
  address (fallback sender_email) once in ``check_send_gates`` and
  rides the OK dict into ``increment_rate_counters`` (legacy +
  sequencer paths threaded).  (2) Send-time optimization (per-campaign
  toggle, ``Lead.timezone`` new column): ``compute_optimal_send_eta``
  defers to the recipient's optimal local hour — lead's own open/click
  hours → campaign aggregate → 9-11am Tue-Thu default — clamped inside
  the campaign window; nothing qualifying within a week → send normally.
  (3) Bounce/spam circuit breaker: ``services/deliverability.py`` trips
  at ≥5% hard-bounce or ≥0.1% spam over 24h with ≥20 outcomes; pauses
  with ``auto_paused_at`` + ``auto_pause_reason`` + a
  ``campaign_auto_paused`` notification; NEVER auto-resumes (manual
  Resume clears it; distinct from the LinkedIn ``auto_paused_until``).
  Hooks: inline in ``brevo_events.process_event`` + 15-min beat sweep.
  UI: Deliverability card + red breaker banner + STO checkbox in
  Schedule & pacing.
- **A. Reply-driven copy loop (0029).**  ``ReplyOutcome`` snapshots the
  SENT copy + verdict per classified reply (written from
  ``process_inbound_reply``; campaign-less leads skipped).
  ``services/copy_insights.py``: ``winning_examples`` (SQL only),
  ``refresh_angle_summary`` (one Haiku call, cached on
  ``campaign_copy_insights``, re-runs only after ≥3 new outcomes, LLM
  failure serves the stale cache), ``build_winning_block`` (cache-read
  only).  Compose prompts inject the block AFTER StyleCorrection with
  explicit user-voice-wins framing; absent data → prompts unchanged.
  Hourly ``copy_insights.refresh_all`` beat.  "What's working" panel on
  campaign Overview + ``GET /campaigns/{id}/copy-insights``.
- **C. Intent/trigger prospecting (0030).**  ``signal_watches``
  (job_change/funding/hiring/custom on a lead/opp/cold target;
  frequency reuses ``social_search_frequency``; ``last_seen`` JSONB is
  the diff baseline — first sighting seeds silently) +
  ``prospect_signals`` (UNIQUE dedup_key).  Detection: Apollo title /
  funding-stage diffs + capped Haiku web-search (hiring + funding
  fallback); LLM only normalises, never decides.  Worker
  (``signals.scheduled_runner`` 60s + ``run_watch``): tracked record →
  "Reach out" CRM task + notification; cold target WITH email →
  campaign-less CRM lead; never auto-added to a campaign.  /signals
  router + Signals page (Feed/Watches) + nav.
- **D. ICP lookalike expansion (0031).**  ``icp_profiles`` (auto
  profile from closed_won; ``insufficient_data`` below 3 wins) +
  ``lookalike_candidates`` (fit_score/reason, UNIQUE dedup_key,
  ``created_lead_id``).  Builder: Haiku summarises won deals into
  criteria JSON.  Discovery: NEW ``apollo.search_people`` /
  ``search_organizations`` helpers (search may need a paid Apollo
  tier — 403/empty falls back to a capped Haiku web search); scoring
  is RULE-BASED (no LLM judge); dedup forever vs leads/opps/candidates
  by domain.  Daily beats (refresh 02:00, discover 03:00 UTC).  /icp
  router + Lookalikes page (ICP card + ranked accept/reject table);
  accept → exactly one campaign-less lead (409 re-accept).
- **Gotcha fixed in passing:** the operator's real
  ``OWNER_NOTIFY_EMAIL`` in ``.env`` reached the TEST container and
  notification tests attempted real Brevo sends — ``conftest.py`` now
  force-blanks it unconditionally.  Digest "due today" became a rolling
  24h horizon (was end-of-calendar-day; flaky after 21:00 UTC).
- Tests: 49 new backend + 13 new frontend across the four features._

_Previously: CRM & inbox AI agent (6 phases,
migration 0027).  The agent automates CRM hygiene under a hard
autonomy boundary: it MAY log activities, create reminder TASKS,
email the OWNER, and flag stale deals; it may NEVER convert a lead,
message a prospect, change a deal stage, or delete anything — those
stay human actions it only prompts via reminders/notifications.

- **Data (0027):** ``agent_settings`` (singleton id=1, runtime
  toggles + min_confidence_to_act + quiet hours, bootstrapped lazily
  by ``agent_core.get_agent_settings``; env ``AGENT_ENABLED`` is the
  hard kill-switch above it), ``notifications`` (UI bell feed; UNIQUE
  ``dedup_key`` is the idempotency anchor; FKs SET NULL), and
  ``agent_actions`` (append-only audit incl. skips/failures with
  model + cost_usd).  ``crm_activities`` gained ``reminder_sent_at``
  (one reminder per task ever), ``sentiment``, ``is_agent_generated``.
- **Reply pipeline:** ``imap_client.FetchedMessage`` now carries
  ``body_text`` (text/plain preferred, tag-stripped HTML fallback,
  4000-char cap, fetched via a second ``BODY.PEEK[]`` — still no
  ``\Seen`` mutation; the PEEK contract test asserts EVERY fetch) +
  ``received_at``.  ``reply_sentiment.classify_reply`` (Haiku,
  ``ANTHROPIC_AGENT_MODEL``) returns strict-JSON sentiment/intent/
  confidence; OOO can never be positive; parse/API failure → neutral
  fallback with ``parse_failed=True``.  ``agent_core.
  process_inbound_reply`` logs the inbound activity (mirrored onto
  the deal when converted), creates an idempotent convert reminder on
  a confident positive (open-task check per lead, due next business
  day), a follow-up task on the deal when already converted, and
  notifications deduped on message id.  Wired into ``reply_poller``
  after the REPLIED event — only for newly-processed messages, so a
  re-poll never re-classifies; agent failure is caught + audited and
  never blocks reply recording.
- **Sweeps (beat):** ``agent_sweeper.sweep_reminders`` (every
  ``AGENT_REMINDER_SWEEP_INTERVAL_MINUTES``) pings due-soon/overdue
  open tasks once each; ``agent_sweeper.sweep_stale_opps`` (hourly) →
  ``agent_core.flag_stale_opportunities`` nudges open deals idle ≥
  ``AGENT_STALE_OPP_DAYS`` with no open task (nudge task pre-stamps
  ``reminder_sent_at`` so the reminder sweep doesn't double-ping;
  notification deduped per-deal-per-ISO-week); ``digest.send_daily``
  (crontab ``AGENT_DIGEST_HOUR_UTC``) sends one summary email a day
  (overdue/due-today/replies-by-sentiment/pipeline movement/unsent-
  alert count) — deliberately BYPASSES quiet hours since it's the
  sweep-up channel for alerts quiet hours deferred; idempotent via
  ``digest:<date>``.
- **Notifications:** ``services/notifications.py`` —
  ``create_notification`` (dedup-key guard), ``send_notification_email``
  (Brevo → ``OWNER_NOTIFY_EMAIL``, never raises, warns once when
  unset), ``notify()`` composite with quiet-hours email deferral (row
  persists, ``emailed_at`` NULL).
- **Drafts (opt-in, OFF by default):** ``reply_drafter.draft_reply``
  (Sonnet, ``ANTHROPIC_AGENT_DRAFT_MODEL``) writes a suggested reply
  to the ``draft_reply`` audit row (``detail.draft_body``) + the
  notification body.  NEVER sent.  Skipped for OOO/unsubscribe/
  not_interested intents.
- **API:** new ``/agent`` router — GET/PATCH ``/agent/settings``
  (quiet hours must be set as a pair; ``clear_quiet_hours`` resets),
  ``/agent/notifications`` (+ ``/read``, ``/read-all``),
  ``/agent/actions``, ``/agent/replies`` (triage feed with
  ``convert_eligible``; convert reuses the existing
  ``POST /crm/leads/{id}/convert``).
- **Frontend:** new ``pages/Replies.jsx`` (sentiment badges, one-click
  Convert, View-draft panel), ``components/NotificationBell.jsx``
  (fixed top-right, unread badge, 60s poll), Settings → Agent tab
  (toggles, confidence slider, quiet hours, owner-email status +
  kill-switch banners), AI tag + sentiment chip on agent-generated
  rows in ``ActivityLog``, Replies nav entry.
- **Config:** ``OWNER_NOTIFY_EMAIL`` (empty = in-app only, no
  emails), ``OWNER_NOTIFY_NAME``, ``AGENT_ENABLED``,
  ``ANTHROPIC_AGENT_MODEL`` (Haiku), ``ANTHROPIC_AGENT_DRAFT_MODEL``
  (Sonnet), sweep interval / digest hour / stale days / due-soon
  window — all wired into the compose ``environment:`` blocks of all
  three Python services + ``.env.example``.
- Tests: 61 new backend (models, sentiment, pipeline, sweeper, quiet
  hours, stale nudges, digest, router, drafter) + 13 new frontend._

_Previously: Research-a-client sends auto-track in the
CRM.  ``POST /research-client/send`` now finds-or-creates a CRM lead
after the Brevo send succeeds: lookup is case-insensitive on
``lower(Lead.email)`` (most-recently-updated row wins when the email
spans campaigns); a miss creates a campaign-less CRM lead with
first/last split from ``to_name``.  An outbound ``CrmActivity``
(type=email, subject capped 500, body truncated at 1000) is logged
against the lead AND any opportunity carrying the same email — so the
deal timeline captures one-off touches too.  The whole block is
best-effort: any CRM failure logs + rolls back and the send still
returns 200 (the email already left via Brevo); the response carries
``crm_lead_id`` / ``crm_lead_created`` / ``crm_activity_logged`` so
the UI can be honest about what happened.  Frontend: the sent-
confirmation row shows "Added to CRM as a new lead + email logged."
vs "Email logged on their existing CRM record." (testid
``crm-tracking-note``); nothing renders when tracking failed.  4 new
backend tests (creates lead + activity, reuses existing lead
case-insensitively with no duplicate, attaches to matching
opportunity, CRM explosion still returns 200) + 3 new frontend._

_Previously: Opportunity record pages (Salesforce
lite).  Each opportunity now has its own page at ``/opportunities/:id``
(Kanban cards navigate there; the old detail modal is gone).  Migration
0026 adds:
- ``crm_documents`` — file attachments per deal (proposals/contracts/
  quotes).  Bytes in Postgres BYTEA (single-operator-sized; no object
  store), 10MB cap enforced at the route (413 with a shared-drive hint),
  empty files 422.  Listing skips the BYTEA column; download sets
  Content-Disposition attachment.
- ``crm_opportunity_products`` — products-of-interest line items
  (free-text name + qty x unit price; line_total derived;
  products_total roll-up returned next to the manual deal amount with
  a "consider syncing" hint when they diverge).
The page: header (contact snapshot, amount, probability, closes date,
won/lost banner), stage stepper (closed_won asks confirm; closed_lost
opens an inline REQUIRED loss-reason form before the stage flips —
Salesforce-style), editable details card (amount/close date/probability
override/description, blur-to-save), products card, documents card
(upload/download/delete), the full ActivityLog (tasks/notes/calls/
emails/meetings), created/updated meta + source-lead link + delete
(un-converts the source lead).  8 new backend tests + 9 new frontend
(OpportunityDetail.test.jsx); Opportunities tests updated from modal
to navigation semantics._

_Previously: CRM sprint: Salesforce-style leads /
opportunities / activities.  Migration 0025:
- ``leads.campaign_id`` NULLABLE — manual CRM leads exist outside any
  campaign (they never enter compose/send; the global list outer-joins
  Campaign).  ``leads.crm_status`` (new/working/qualified/converted/
  unqualified) + ``leads.converted_opportunity_id``.
- ``crm_opportunities`` — stage pipeline (prospecting → qualification →
  proposal → negotiation → closed_won/closed_lost), amount, close date,
  probability (auto-follows stage default unless explicitly set),
  contact snapshot copied at conversion, source-lead link (SET NULL).
- ``crm_activities`` — manual touches (call/email/meeting/note/task)
  attachable to a lead AND/OR an opportunity (DB CHECK requires ≥1
  parent).  Tasks carry due_at/completed_at + a partial index for the
  open-tasks view; calls/emails carry direction.
New ``/crm`` router: manual lead create (canonicalises email), crm_status
PATCH (``converted`` reserved for the convert endpoint), lead→opportunity
conversion (snapshot copy + conversion logged as a note activity spanning
both parents + 409 on double-convert), opportunities CRUD (stage
transitions stamp/clear closed_at + loss_reason), pipeline summary
roll-up, activities CRUD with open_tasks=true ordered by due date.
Deleting an opportunity un-converts its source lead (back to qualified).
Lead-detail timeline now merges THREE sources: step executions + email
events + CRM activities (kind="crm", counts keyed crm_<type>).
Frontend: + New lead modal on Leads; CRM status select + Convert button
+ shared ActivityLog component in the lead modal; new Opportunities nav
page with Kanban board (per-stage roll-ups, closed columns toggleable),
stage-stepper detail modal, amount/close-date inline edit, loss reason
on closed_lost, activity log per deal.  21 new backend tests
(test_phase35_crm.py) + 16 new frontend; 1 pre-existing test updated
(lead-requires-campaign became lead-campaign-optional)._

_Previously: Watchlist scalability bundle.
Three coordinated changes to take the LinkedIn profile watchlist from
a comfortable ~50 profiles to a comfortable ~500:

1. **Parallel fan-out** in ``_run_watchlist`` (``asyncio.gather`` +
   ``Semaphore(10)``).  Unipile's read surface is generous (it doesn't
   go through the LinkedIn write-rate-limiter), so 10 in-flight fetches
   is comfortable.  200 profiles: ~13min → ~1-2min.
2. **Persistent slug→URN cache** (``linkedin_profile_cache`` table,
   migration 0021).  Unipile's ``GET /users/{slug}`` is the only way to
   map a public slug to the canonical ``ACoAA...`` URN, but the mapping
   is permanent — once resolved, never changes.  The worker now reads
   the cache before constructing ``ProfileRef``; on a hit it pre-sets
   ``profile_ref.urn`` so ``recent_posts``'s internal
   ``_resolve_provider_id`` short-circuits without the network call.
   On a miss, Unipile stamps ``profile_ref.urn`` during ``recent_posts``
   (existing behaviour) and the worker mirrors that into the cache for
   next time.  Cuts per-profile cost ~50% on every subsequent run.
3. **Bulk-paste-friendly watchlist editor**.  New
   ``parseWatchlistEntries`` / ``classifyWatchlistEntries`` helpers in
   ``SocialRadar.jsx`` split on any whitespace OR comma (so a pasted
   CSV column / spreadsheet cell / chat dump all work), then
   classify + dedupe by slug.  A live "X valid LinkedIn profiles, Y
   invalid" pill renders below the textarea.  Save sends ONLY the
   deduped valid list.

Tests: 4 new backend (cache hit pre-fills URN, cache miss persists,
20-profile parallel fan-out, no-account soft-fail still produces one
row per profile) + 9 new frontend (8 validator unit + 2 render tests
for the live count pill and dedup on save).  Live: csuite still
runs (688 backend / 219 frontend).

Tradeoffs documented: the watchlist concurrency knob is a fixed
constant (``_WATCHLIST_CONCURRENCY = 10``).  Unipile workspace plans
may have global concurrency limits — if a future plan tightens those,
this becomes a config setting.  The provider-id cache is permanent
(no TTL): we trust Unipile's guarantee that slug→URN mappings are
stable.  If a slug ever IS recycled to a different member, the
``recent_posts`` call would surface posts from the wrong account —
treat that as a manual cache-bust operation if it ever happens
(``DELETE FROM linkedin_profile_cache WHERE slug = '...'``)._

_Previously: LinkedIn buyer-intent at minimal cost:
Reddit → LinkedIn cross-link extraction + tighter LinkedIn web_search.
New pure-fn service `social_listening_linkedin_crosslink.py` mines every
Reddit post body the worker discovers for `linkedin.com/posts/...` and
`linkedin.com/feed/update/...` URLs; each unique URL becomes a
`provider=linkedin` opportunity row with a SYNTHESISED qualification
(score=5, action=research_further, empty suggested copy) — **zero
Anthropic cost**.  We can't fetch the LinkedIn post body (Unipile only
exposes `recent_posts(slug)`, ≤25 deep), so the user opens the URL
directly to assess; scoring the surrounding Reddit excerpt would
mis-score the wrong document.  New `linkedin_crosslink_enabled` boolean
on `social_listening_searches` (migration 0020, default `true`).  The
worker's `_run_social_search_async` inserts a new phase between
discovery upsert and watchlist that reuses the existing `_upsert_post`
(URL UNIQUE constraint dedupes — same LinkedIn URL across two Reddit
threads = one row) + `_upsert_opportunity` and records
`crosslink_stats = {reddit_posts_scanned, linkedin_urls_found,
linkedin_posts_new, linkedin_posts_existing}` on `last_run_stats`.
Crosslink posts are NOT enqueued to the qualify-batch (they already
have an opportunity).  Separately, the LinkedIn web_search path was
tightened: `_source_max_uses` now hard-caps LinkedIn at 1
(`min(1, settings.LINKEDIN_DISCOVERY_WEB_SEARCH_MAX_USES)`) regardless
of the config value, and the LinkedIn prompt was rewritten from a
5-strategy fan-out to ONE focused
`site:linkedin.com/pulse OR site:linkedin.com/posts "{query}"` call —
cuts the toggle-on cost from ~$1.50/run to ~$0.30 worst case.  New
frontend checkbox in the search editor's Advanced section ("Pull
LinkedIn URLs found in Reddit discussions — free, no extra cost").
Live smoke on csuite (231 Reddit posts scanned, 0 LinkedIn URLs found —
honest result; the regex + worker plumbing is verified by unit tests):
$0 marginal Anthropic spend.  Migration 0020 + 28 new backend tests +
2 new frontend tests._

_Backend tests: **1084 passing**.  Frontend tests: **376 passing**._

> **🚀 Starting on a fresh dev box?** Jump to
> [Unipile setup runbook](#unipile-setup-runbook-any-computer-local-dev)
> below — it walks through every step (ngrok / cloudflared tunnel,
> Unipile webhook config, .env wiring, force-recreate) needed to get
> the stack talking to Unipile on a new machine.

## Unipile integration

LinkedIn actions are powered by Unipile (real desktop Chrome on
residential IPs).  Key surface:

1. **`UnipileLinkedInProvider`** in `services/linkedin/unipile_impl.py`
   — the only concrete impl behind the `LinkedInProvider` ABC.  Async
   httpx wrapper around Unipile's REST API.  Maps Unipile error
   envelopes (status/code/message) onto our `ChallengeRequired` /
   `AccountRestricted` / `UnipileError` domain types.

2. **Router endpoints** in `routers/linkedin_accounts.py`:
   - `POST /connect-via-unipile` — creates a placeholder row, gets a
     hosted-login URL from Unipile, returns it for the frontend to
     open.  The local UUID rides along as Unipile's `name` field so
     webhook events can correlate back to the row.
   - `POST /{id}/sync-unipile` — polling fallback when the webhook
     hasn't reached us yet.
   - `GET /discoverable` + `POST /import-from-unipile` — bind a
     LinkedIn account that was connected via Unipile's dashboard
     (instead of our hosted-auth flow) to a fresh local row.
   - `DELETE /{id}` — also calls Unipile's `delete_account` so we
     don't leak a session on their side.

3. **Webhook handler** `POST /webhooks/unipile` in `routers/webhooks.py`.
   Unipile doesn't HMAC-sign bodies — auth is a static custom header.
   Handler reads `request.headers[settings.UNIPILE_WEBHOOK_AUTH_HEADER]`
   (default `X-Unipile-Auth`) and constant-time-compares against
   `settings.UNIPILE_WEBHOOK_SECRET`.  Routes events: `account.connected`,
   `account.disconnected`, `account.checkpoint`, `message.received`,
   `invitation.accepted`.  Event-name casing normalised.

4. **`ConnectLinkedInModal`** frontend has two surfaces in create mode:
   - "Connect via Unipile" button → calls `/connect-via-unipile` →
     opens hosted URL in new tab → polls `/sync-unipile` every 3s
     until status flips to OK (10-min timeout).
   - Collapsible "Already connected in Unipile?" panel → lists
     `/discoverable` rows → per-row Import button binds the
     unipile account to a fresh local row.

## Quick-refresh: ngrok + Unipile webhooks

For day-to-day dev (new ngrok URL on every restart) there's a one-shot
script that does the whole dance:

```bash
python3 scripts/dev_tunnel.py
```

It detects (or starts) ngrok pointing at `localhost:8000`, deletes
every Unipile webhook on the workspace, recreates the three canonical
ones (`messaging`, `account_status`, `users`) pointing at the live
tunnel, patches `.env` (`WEBHOOK_BASE_URL`, optionally
`UNIPILE_WEBHOOK_SECRET` with `--rotate-secret`), and only
force-recreates `backend`/`worker`/`beat` when `.env` actually
changed (idempotent — safe to re-run).  Pure stdlib, no pip install.
Useful flags: `--dry-run`, `--rotate-secret`, `--no-recreate`,
`--port N`.

Use the full runbook below only when wiring a fresh dev box from
scratch (Unipile account creation, ngrok install, etc.).

## Unipile setup runbook (any computer, local dev)

> **Multi-machine context:** this whole runbook is the one source of
> truth for getting Unipile wired up on a fresh dev box.  Follow it top
> to bottom on any new machine.  All steps are Windows / PowerShell;
> macOS / Linux equivalents are obvious (`brew install`, etc.).

### 1. Sign up + grab credentials (once per Unipile account)

1. Sign up at <https://www.unipile.com>.
2. Dashboard top bar shows your **DSN** like `api12.unipile.com:13443` —
   copy it.  Each tenant gets a different number (`api3.`, `api12.`, etc.).
3. Sidebar → **Access Tokens** → **Generate** → copy the token.  Unipile
   only shows it once; lose it and you generate a new one.

### 2. Public HTTPS tunnel (every dev box)

Unipile's servers can't reach `localhost`, so we need a tunnel.  Two
options:

**ngrok (recommended — has a request inspector at <http://127.0.0.1:4040>):**

```powershell
winget install Ngrok.Ngrok
# Sign up at ngrok.com, copy the authtoken from
# https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken <YOUR_TOKEN>
ngrok http 8000     # leave running in its own terminal
```

The output line `Forwarding https://<random>.ngrok-free.app -> http://localhost:8000`
is your public URL.

**Cloudflared (no signup):**

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```

Prints a `https://<random>.trycloudflare.com` URL.  Trade-off: no
request inspector.

⚠ **Free ngrok / cloudflared subdomains change on every restart.**  Keep
the tunnel terminal running; if it dies, update all three webhook URLs
in Unipile + `WEBHOOK_BASE_URL` in `.env` + force-recreate containers.

### 3. Create the three Unipile webhooks

Unipile splits webhooks by data source, so you need **three** webhooks
that all POST to the same `/webhooks/unipile` endpoint.  Our handler
dispatches on event-name internally, so it doesn't care which webhook
delivered the event.

**Unipile doesn't HMAC-sign request bodies.** Their auth model is a
**static custom header** — you specify a header key + value when
creating the webhook, and Unipile echoes that exact header (same value)
on every delivery. Our handler reads
`settings.UNIPILE_WEBHOOK_AUTH_HEADER` (default `X-Unipile-Auth`),
constant-time-compares the value against `settings.UNIPILE_WEBHOOK_SECRET`,
and 401s on mismatch. See `_verify_unipile_auth()` in `routers/webhooks.py`
and the Unipile docs "Authentication" section on the Webhooks page.

The dashboard UI for adding custom headers is inconsistent across
Unipile flavours, so the most reliable path is **creating webhooks via
the Unipile API**:

```bash
DSN=$(grep '^UNIPILE_DSN=' .env | cut -d'=' -f2-)
KEY=$(grep '^UNIPILE_API_KEY=' .env | cut -d'=' -f2-)
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
TUNNEL=https://<your-ngrok-or-cloudflared-host>

for SRC in messaging account_status users; do
  curl -s -X POST "https://$DSN/api/v1/webhooks" \
    -H "X-API-KEY: $KEY" -H "content-type: application/json" \
    -d "{
      \"name\": \"emailblaster - $SRC\",
      \"request_url\": \"$TUNNEL/webhooks/unipile\",
      \"source\": \"$SRC\",
      \"headers\": [
        {\"key\": \"Content-Type\", \"value\": \"application/json\"},
        {\"key\": \"X-Unipile-Auth\", \"value\": \"$SECRET\"}
      ]
    }"
done

# Save the secret to paste into .env next:
echo "$SECRET"
```

**Verified working `source` values (2026-05-14):**

| `source` | Covers |
|---|---|
| `messaging` | new chat messages (DM replies) |
| `account_status` | Unipile account state changes (`account.connected`, `account.checkpoint`, `account.disconnected`) |
| `users` | new relations (`invitation.accepted`, new connections) |

We don't use Unipile's `mailing` / `mail_tracking` / `calendar` sources.

To list / delete webhooks: `GET` / `DELETE` `https://$DSN/api/v1/webhooks[/<id>]`
with the `X-API-KEY` header.

### 4. Wire `.env`

Open `.env` (project root) and set these four:

```env
UNIPILE_DSN=api12.unipile.com:13443      # your DSN from step 1
UNIPILE_API_KEY=<access token from step 1>
UNIPILE_WEBHOOK_SECRET=<the static header-value secret from step 3>
UNIPILE_WEBHOOK_AUTH_HEADER=X-Unipile-Auth   # optional, this IS the default
WEBHOOK_BASE_URL=https://<tunnel-host-no-trailing-slash>
```

`WEBHOOK_BASE_URL` must point to the SAME tunnel host you gave Unipile
in step 3.  It's used by `POST /linkedin-accounts/connect-via-unipile`
to build the `notify_url` Unipile attaches to the hosted-auth flow.

### 5. Force-recreate so `.env` actually takes effect

`docker compose restart` does NOT re-read `.env` (already a gotcha in
the Conventions section).  Use:

```powershell
docker compose up -d --force-recreate backend worker beat
```

Verify it took:

```powershell
docker compose exec backend printenv UNIPILE_DSN UNIPILE_API_KEY UNIPILE_WEBHOOK_SECRET WEBHOOK_BASE_URL
```

All four should print non-empty values.

### 6. Sanity-check the tunnel is reachable

```powershell
# Without the auth header → 401 (proves auth-check fires AND routing works)
curl.exe -X POST "https://<tunnel>/webhooks/unipile" -H "Content-Type: application/json" -d "{}"
# Expected: 401 {"detail":"invalid auth header"}

# With the right header → 400 invalid JSON (auth passed, just no real event in body)
curl.exe -X POST "https://<tunnel>/webhooks/unipile" `
  -H "Content-Type: application/json" `
  -H "X-Unipile-Auth: <THE_SECRET>" `
  -d "{}"
# Expected: 400 {"detail":"invalid JSON: ..."} or similar — the point is we got PAST auth
```

Both responses confirm routing through the tunnel works.  Then hit the
**Send test event** button on each Unipile webhook and watch:

```powershell
docker compose logs -f backend | findstr /I unipile
```

You should see three `Unipile webhook event='...'` lines, each returning
200.

### 7. End-to-end smoke (link a real LinkedIn account)

1. Open <http://localhost:5173/settings>.
2. **LinkedIn Accounts → Connect new**.  Toggle stays on **Hosted
   (Unipile)** by default.  Enter a label, click **Connect via Unipile**.
3. New tab opens at Unipile's hosted login.  Complete the LinkedIn
   login there.
4. Modal in our app should auto-close as soon as Unipile fires
   `account.connected` → our handler stamps `unipile_account_id` + flips
   status to OK.  (Polling fallback at 3s intervals catches webhook
   delivery delays.)
5. Tail logs to confirm:
   ```powershell
   docker compose logs -f backend | findstr /I unipile
   ```

### 8. Run a campaign step

Re-enroll the lead from the Activity tab and watch:

```powershell
docker compose logs -f worker | findstr /I "send_linkedin_step Unipile"
```

A `view_profile` should complete in 1–3 seconds (single HTTPS call to
Unipile) vs. the old 9-second-then-challenge pattern.

### Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| `401 invalid auth header` in backend logs after Unipile send-test | `UNIPILE_WEBHOOK_SECRET` in `.env` doesn't match the `X-Unipile-Auth` header value in Unipile's webhook config | Re-copy the secret into `.env`, force-recreate, OR re-create the webhook via the API with the matching value |
| Unipile dashboard shows delivery `timeout` | Tunnel died, or `WEBHOOK_BASE_URL` host doesn't match Unipile's webhook URL host | Restart tunnel, update all three webhook URLs in Unipile + `WEBHOOK_BASE_URL`, force-recreate |
| `account.connected` fires but row never flips OK | Unipile sent the event with an empty `name` | Check the delivery payload in Unipile's dashboard for `name == <our local UUID>`; if blank, ensure the `connect-via-unipile` endpoint successfully called `create_hosted_auth_link` with `name=str(acc.id)` |
| `UnipileError: UNIPILE_DSN not set` | `.env` not loaded into the container | Did you force-recreate?  `docker compose restart` won't do it |

