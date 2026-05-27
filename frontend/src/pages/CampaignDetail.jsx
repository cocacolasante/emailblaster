import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  approveAll as approveAllCampaign,
  getCampaign,
  getCampaignActivity,
  getLeadDetail,
  getPreviewProgress,
  pauseCampaign,
  reEnrollHalted,
  resumeCampaign,
  updateCampaign,
} from '../api/campaigns.js';
import { listLinkedInAccounts } from '../api/linkedinAccounts.js';
import LeadTable from '../components/LeadTable.jsx';
import LeadUpload from '../components/LeadUpload.jsx';
import { useToast } from '../components/Toast.jsx';
import { AnalyticsContent } from './Analytics.jsx';

const STATUS_CLASSES = {
  draft:      'bg-slate-100 text-slate-600',
  previewing: 'bg-yellow-100 text-yellow-700',
  approved:   'bg-blue-100 text-blue-700',
  running:    'bg-emerald-100 text-emerald-700',
  paused:     'bg-amber-100 text-amber-700',
  complete:   'bg-indigo-100 text-indigo-700',
  failed:     'bg-red-100 text-red-600',
};

function StatusBadge({ status }) {
  const cls = STATUS_CLASSES[status] || STATUS_CLASSES.draft;
  return (
    <span
      data-testid="status-badge"
      data-status={status}
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium capitalize ${cls}`}
    >
      {status}
    </span>
  );
}

function pct(v) {
  if (v == null) return '--';
  return `${(v * 100).toFixed(1)}%`;
}

function ProgressBar({ value, max, color = 'bg-blue-500' }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-600 w-16 text-right tabular-nums">{value} / {max}</span>
    </div>
  );
}

function PipelineCard({ progress, status, onLaunch, launchLoading }) {
  if (!progress) return null;
  const { total_leads: total, researched, composed, sent, failed } = progress;
  const samplesReady = composed > 0;
  const isPreviewing = status === 'previewing';

  return (
    <div className={`bg-white rounded-xl border shadow-sm p-6 ${isPreviewing ? 'border-amber-300' : 'border-slate-200'}`}>
      <div className="flex items-start justify-between gap-3 mb-4">
        <h2 className="text-base font-semibold text-slate-900">Pipeline progress</h2>
        {isPreviewing && samplesReady && (
          <button
            type="button"
            onClick={onLaunch}
            disabled={launchLoading}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold rounded-lg transition-colors disabled:opacity-50 shrink-0"
          >
            {launchLoading ? 'Launching…' : 'Review & Launch →'}
          </button>
        )}
        {isPreviewing && !samplesReady && (
          <span className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5 font-medium shrink-0">
            Preparing samples…
          </span>
        )}
      </div>

      <div className="space-y-3">
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>Researched</span>
          </div>
          <ProgressBar value={researched} max={total} color="bg-sky-500" />
        </div>
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>Composed</span>
          </div>
          <ProgressBar value={composed} max={total} color="bg-blue-500" />
        </div>
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>Sent</span>
          </div>
          <ProgressBar value={sent} max={total} color="bg-emerald-500" />
        </div>
        {failed > 0 && (
          <div className="mt-2 text-xs text-red-600 font-medium">{failed} lead{failed !== 1 ? 's' : ''} failed — check the Analytics tab for details.</div>
        )}
      </div>

      {isPreviewing && samplesReady && (
        <p className="mt-3 text-xs text-slate-500">
          Sample emails are ready. Click "Review & Launch" to see them and start the campaign.
        </p>
      )}
    </div>
  );
}

function LinkedInAccountCard({ campaign, linkedinAccounts, onSave, saving }) {
  const current = linkedinAccounts.find((a) => a.id === campaign.linkedin_account_id);
  const [selected, setSelected] = useState(campaign.linkedin_account_id || '');

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      <h2 className="text-base font-semibold text-slate-900 mb-3">LinkedIn account</h2>
      {linkedinAccounts.length === 0 ? (
        <p className="text-sm text-slate-500">
          No LinkedIn accounts connected.{' '}
          <a href="/settings" className="text-blue-600 hover:underline">Add one in Settings.</a>
        </p>
      ) : (
        <div className="space-y-3">
          {current && selected === campaign.linkedin_account_id && (
            <p className="text-sm text-slate-700">
              <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium mr-2 ${current.status === 'ok' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                {current.status}
              </span>
              <strong>{current.label}</strong>{' '}
              <span className="text-slate-500">({current.linkedin_email})</span>
            </p>
          )}
          <div className="flex gap-2 items-center">
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="">— none —</option>
              {linkedinAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} ({a.linkedin_email}) — {a.status}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => onSave(selected || null)}
              disabled={saving || selected === (campaign.linkedin_account_id || '')}
              className="px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function OverviewTab({ campaign, progress, onPauseToggle, pauseLoading, onLaunch, launchLoading, linkedinAccounts, onSaveLinkedIn, savingLinkedIn }) {
  const total = campaign.lead_counts?.total ?? 0;
  const sent = campaign.lead_counts?.sent ?? 0;
  const sendProgress = total > 0 ? Math.min(100, (sent / total) * 100) : 0;

  const showPipeline = ['previewing', 'running', 'paused'].includes(campaign.status);

  return (
    <div data-testid="overview-tab" className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Left column */}
      <div className="space-y-4">
        {showPipeline && (
          <PipelineCard
            progress={progress}
            status={campaign.status}
            onLaunch={onLaunch}
            launchLoading={launchLoading}
          />
        )}

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Status</h2>
          <div className="flex items-center gap-3 mb-4">
            <StatusBadge status={campaign.status} />
            {campaign.status === 'running' && (
              <button
                type="button"
                onClick={onPauseToggle}
                disabled={pauseLoading}
                className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors disabled:opacity-50"
                data-testid="pause-resume-button"
              >
                Pause
              </button>
            )}
            {campaign.status === 'paused' && (
              <button
                type="button"
                onClick={onPauseToggle}
                disabled={pauseLoading}
                className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors disabled:opacity-50"
                data-testid="pause-resume-button"
              >
                Resume
              </button>
            )}
            {campaign.status === 'paused' && campaign.auto_paused_until && (
              <span data-testid="auto-paused-note" className="text-xs text-amber-700">
                Auto-paused (LinkedIn daily cap) — resumes {fmtDatetime(campaign.auto_paused_until)}
              </span>
            )}
          </div>
          <div className="text-sm text-slate-500 mb-2">{sent} of {total} sent</div>
          <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
            <div className="h-full bg-blue-600 rounded-full transition-all" style={{ width: `${sendProgress}%` }} />
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Quick stats</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Open rate</div>
              <div className="text-2xl font-bold text-slate-900">{pct(campaign.stats?.open_rate)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Click rate</div>
              <div className="text-2xl font-bold text-slate-900">{pct(campaign.stats?.click_rate)}</div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Reply rate</div>
              <div className="text-2xl font-bold text-slate-900" data-testid="reply-rate">
                {campaign.connected_account_configured ? pct(campaign.stats?.reply_rate) : '--'}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wide mb-1">Bounce rate</div>
              <div className="text-2xl font-bold text-slate-900">{pct(campaign.stats?.bounce_rate)}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Right column */}
      <div className="space-y-4">
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Reply tracking</h2>
          {campaign.connected_account_configured && campaign.connected_account ? (
            <p data-testid="reply-tracking-info" className="m-0 text-sm text-slate-700">
              <strong className="font-medium">{campaign.connected_account.label}</strong>{' '}
              <span className="text-slate-500">({campaign.connected_account.email_address})</span>
            </p>
          ) : (
            <p data-testid="reply-tracking-info" className="m-0 text-sm text-slate-500">
              Reply tracking not configured for this campaign.
            </p>
          )}
        </div>

        <LinkedInAccountCard
          campaign={campaign}
          linkedinAccounts={linkedinAccounts}
          onSave={onSaveLinkedIn}
          saving={savingLinkedIn}
        />

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Campaign config</h2>
          <dl className="space-y-2">
            {[
              { label: 'Goal', value: campaign.goal },
              { label: 'Tone', value: campaign.tone },
              { label: 'Sender', value: `${campaign.sender_name} <${campaign.sender_email}>` },
              { label: 'Research mode', value: campaign.research_mode },
              { label: 'Sample count', value: campaign.sample_count },
              {
                label: 'Schedule',
                value: `${campaign.schedule_time_start?.slice(0, 5)} – ${campaign.schedule_time_end?.slice(0, 5)} (${campaign.schedule_timezone})`,
              },
            ].map(({ label, value }) => (
              <div key={label} className="flex gap-4">
                <dt className="text-sm text-slate-500 w-32 flex-shrink-0">{label}</dt>
                <dd className="text-sm text-slate-900 font-medium m-0">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  );
}


const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function fmtRelative(isoStr) {
  if (!isoStr) return '—';
  const diff = Date.now() - new Date(isoStr).getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function fmtDatetime(isoStr) {
  if (!isoStr) return '—';
  return new Date(isoStr).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

const PILL = {
  pending:   'bg-slate-100 text-slate-500',
  running:   'bg-blue-100 text-blue-700',
  done:      'bg-emerald-100 text-emerald-700',
  sent:      'bg-emerald-100 text-emerald-700',
  failed:    'bg-red-100 text-red-600',
  skipped:   'bg-slate-100 text-slate-500',
  scheduled: 'bg-amber-100 text-amber-700',
};

function MiniPill({ value }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${PILL[value] || PILL.pending}`}>
      {value}
    </span>
  );
}

const NODE_KIND_LABELS = {
  email: 'Email',
  wait: 'Wait',
  linkedin_view_profile: 'View profile',
  linkedin_follow_profile: 'Follow',
  linkedin_react_post: 'React to post',
  linkedin_comment_post: 'Comment on post',
  linkedin_connect: 'Connect',
  linkedin_dm: 'DM',
  linkedin_inmail: 'InMail',
  linkedin_invite_to_page: 'Page invite',
};

const NODE_KIND_COLORS = {
  email: 'bg-blue-100 text-blue-700',
  wait: 'bg-slate-100 text-slate-500',
  linkedin_view_profile: 'bg-sky-100 text-sky-700',
  linkedin_follow_profile: 'bg-sky-100 text-sky-700',
  linkedin_react_post: 'bg-sky-100 text-sky-700',
  linkedin_comment_post: 'bg-indigo-100 text-indigo-700',
  linkedin_connect: 'bg-indigo-100 text-indigo-700',
  linkedin_dm: 'bg-purple-100 text-purple-700',
  linkedin_inmail: 'bg-purple-100 text-purple-700',
  linkedin_invite_to_page: 'bg-indigo-100 text-indigo-700',
};

function NodeKindBadge({ kind }) {
  const label = NODE_KIND_LABELS[kind] ?? kind;
  const cls = NODE_KIND_COLORS[kind] ?? 'bg-slate-100 text-slate-600';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

function LeadName({ ev }) {
  const name = (ev.first_name || ev.last_name)
    ? `${ev.first_name || ''} ${ev.last_name || ''}`.trim()
    : ev.email;
  return (
    <div>
      <div className="font-medium text-slate-800 text-xs">{name}</div>
      {(ev.first_name || ev.last_name) && (
        <div className="text-[11px] text-slate-400">{ev.email}</div>
      )}
      {ev.company && <div className="text-[11px] text-slate-400">{ev.company}</div>}
    </div>
  );
}

function ActivityTab({ campaignId, campaign }) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const { data: activity, isLoading } = useQuery({
    queryKey: ['campaign-activity', campaignId],
    queryFn: () => getCampaignActivity(campaignId),
    refetchInterval: ['running', 'previewing'].includes(campaign?.status) ? 6000 : 15000,
  });

  const reEnrollMutation = useMutation({
    mutationFn: () => reEnrollHalted(campaignId),
    onSuccess: (data) => {
      toast.success(`Re-enrolled ${data.re_enrolled} lead${data.re_enrolled !== 1 ? 's' : ''}`);
      queryClient.invalidateQueries({ queryKey: ['campaign-activity', campaignId] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Re-enroll failed'),
  });

  const days = campaign?.schedule_days?.length
    ? campaign.schedule_days.map((d) => DAY_NAMES[d]).join(', ')
    : 'every day';
  const windowStart = campaign?.schedule_time_start?.slice(0, 5) ?? '—';
  const windowEnd = campaign?.schedule_time_end?.slice(0, 5) ?? '—';
  const tz = campaign?.schedule_timezone ?? 'UTC';
  const minDelay = campaign?.min_delay_seconds ?? 60;
  const rateLabel = minDelay > 0
    ? `~${Math.round(3600 / minDelay)} sends/hour`
    : 'no delay between sends';

  const inWindow = !activity?.next_window_at;
  const hasSequenceActivity = activity && (
    activity.sequence_active + activity.sequence_halted +
    activity.sequence_completed + activity.sequence_pending > 0
  );
  const hasSteps = (activity?.recent_sequence_steps?.length ?? 0) > 0;
  const hasHalted = (activity?.halted_leads?.length ?? 0) > 0;
  const hasUpcoming = (activity?.upcoming_steps?.length ?? 0) > 0;

  return (
    <div className="space-y-4">
      {/* Schedule + window status */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-3">Send schedule</h3>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500">Days</dt>
              <dd className="font-medium text-slate-800">{days}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Hours</dt>
              <dd className="font-medium text-slate-800">{windowStart} – {windowEnd} {tz}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Rate</dt>
              <dd className="font-medium text-slate-800">{rateLabel}</dd>
            </div>
            {campaign?.max_per_hour && (
              <div className="flex justify-between">
                <dt className="text-slate-500">Hour cap</dt>
                <dd className="font-medium text-slate-800">{campaign.max_per_hour}/hr</dd>
              </div>
            )}
            {campaign?.max_per_day && (
              <div className="flex justify-between">
                <dt className="text-slate-500">Day cap</dt>
                <dd className="font-medium text-slate-800">{campaign.max_per_day}/day</dd>
              </div>
            )}
          </dl>
        </div>

        <div className={`bg-white rounded-xl border shadow-sm p-5 ${inWindow ? 'border-emerald-300' : 'border-amber-300'}`}>
          <h3 className="text-sm font-semibold text-slate-900 mb-3">Window status</h3>
          {isLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2.5 h-2.5 rounded-full ${inWindow ? 'bg-emerald-500' : 'bg-amber-400'}`} />
                <span className="text-sm font-semibold text-slate-800">
                  {inWindow ? 'Inside send window — sends are going out' : 'Outside send window'}
                </span>
              </div>
              {!inWindow && activity?.next_window_at && (
                <p className="text-sm text-slate-600">
                  Next window opens: <strong>{fmtDatetime(activity.next_window_at)}</strong>
                </p>
              )}
              {activity?.estimated_minutes_remaining != null && (
                <p className="text-sm text-slate-600">
                  Estimated completion:{' '}
                  <strong>
                    {activity.estimated_minutes_remaining < 60
                      ? `~${activity.estimated_minutes_remaining} min`
                      : `~${Math.ceil(activity.estimated_minutes_remaining / 60)} hr`}
                  </strong>
                  {' '}({activity.pending_send + activity.scheduled_send} left in queue)
                </p>
              )}
              {activity?.pending_send === 0 && activity?.scheduled_send === 0 && activity?.sent > 0 && (
                <p className="text-sm text-emerald-700 font-medium">All leads sent ✓</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Email pipeline queue counters */}
      {activity && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-3">Email pipeline</h3>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
            {[
              { label: 'Researching', value: activity.researching, color: 'text-blue-700' },
              { label: 'Composing', value: activity.composing, color: 'text-blue-700' },
              { label: 'Pending send', value: activity.pending_send, color: 'text-amber-700' },
              { label: 'Scheduled', value: activity.scheduled_send, color: 'text-amber-600' },
              { label: 'Sent', value: activity.sent, color: 'text-emerald-700' },
              { label: 'Failed', value: activity.failed, color: 'text-red-600' },
            ].map(({ label, value, color }) => (
              <div key={label} className="text-center">
                <div className={`text-2xl font-bold ${color}`}>{value}</div>
                <div className="text-xs text-slate-500 mt-0.5">{label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sequence state counters */}
      {hasSequenceActivity && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-3">Sequence state</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Active', value: activity.sequence_active, color: 'text-emerald-700' },
              { label: 'Pending', value: activity.sequence_pending, color: 'text-amber-600' },
              { label: 'Completed', value: activity.sequence_completed, color: 'text-blue-700' },
              { label: 'Halted', value: activity.sequence_halted, color: 'text-red-600' },
            ].map(({ label, value, color }) => (
              <div key={label} className="text-center">
                <div className={`text-2xl font-bold ${color}`}>{value}</div>
                <div className="text-xs text-slate-500 mt-0.5">{label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Halted leads */}
      {hasHalted && (
        <div className="bg-white rounded-xl border border-red-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-red-100 bg-red-50 flex justify-between items-center">
            <div>
              <h3 className="text-sm font-semibold text-red-800">Halted leads</h3>
              <p className="text-xs text-red-600 mt-0.5">
                These leads stopped because the sequence was rebuilt after they enrolled.
              </p>
            </div>
            <button
              type="button"
              onClick={() => reEnrollMutation.mutate()}
              disabled={reEnrollMutation.isPending}
              className="shrink-0 px-3 py-1.5 text-xs font-semibold bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors disabled:opacity-50"
            >
              {reEnrollMutation.isPending ? 'Re-enrolling…' : 'Re-enroll all'}
            </button>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50">
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Lead</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">At node</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Reason</th>
              </tr>
            </thead>
            <tbody>
              {activity.halted_leads.map((s) => (
                <tr key={s.lead_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2.5"><LeadName ev={s} /></td>
                  <td className="px-4 py-2.5">
                    {s.current_node_kind
                      ? <NodeKindBadge kind={s.current_node_kind} />
                      : <span className="text-xs text-slate-400">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-red-600">{s.halt_reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Recent sequence steps */}
      {hasSteps && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-slate-900">Recent sequence steps</h3>
            <span className="text-xs text-slate-400">Last 30 executions</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50">
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Lead</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Step</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Result</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Error</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">When</th>
              </tr>
            </thead>
            <tbody>
              {activity.recent_sequence_steps.map((s, i) => {
                const isCluster = (s.attempt_count ?? 1) > 1;
                const clusterTitle = isCluster
                  ? `${s.attempt_count} attempts — earliest ${fmtRelative(s.earliest_attempted_at)}, latest ${fmtRelative(s.attempted_at)}`
                  : '';
                return (
                  <tr key={`${s.lead_id}-${s.attempted_at}-${i}`} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-2.5"><LeadName ev={s} /></td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <NodeKindBadge kind={s.node_kind} />
                        {isCluster && (
                          <span
                            className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-slate-100 text-slate-600 border border-slate-200"
                            title={clusterTitle}
                          >
                            ×{s.attempt_count}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5"><MiniPill value={s.result} /></td>
                    <td className="px-4 py-2.5 text-xs text-red-500 max-w-[200px] truncate" title={s.error || ''}>
                      {s.error || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-slate-400" title={clusterTitle}>
                      {fmtRelative(s.attempted_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Upcoming scheduled steps */}
      {hasUpcoming && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-200 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-slate-900">Upcoming steps</h3>
            <span className="text-xs text-slate-400">Next 10 scheduled</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50">
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Lead</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Next step</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Scheduled for</th>
              </tr>
            </thead>
            <tbody>
              {activity.upcoming_steps.map((s) => (
                <tr key={s.lead_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2.5"><LeadName ev={s} /></td>
                  <td className="px-4 py-2.5">
                    {s.current_node_kind
                      ? <NodeKindBadge kind={s.current_node_kind} />
                      : <span className="text-xs text-slate-400">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-600">{fmtDatetime(s.next_run_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Recent email pipeline activity feed */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 flex justify-between items-center">
          <h3 className="text-sm font-semibold text-slate-900">Email pipeline activity</h3>
          <span className="text-xs text-slate-400">Last 20 leads by activity</span>
        </div>
        {isLoading ? (
          <div className="px-5 py-4 text-sm text-slate-400">Loading…</div>
        ) : (activity?.recent_events?.length ?? 0) === 0 ? (
          <div className="px-5 py-4 text-sm text-slate-400">No activity yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50">
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Lead</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Research</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Compose</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Send</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Scheduled for</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">Updated</th>
              </tr>
            </thead>
            <tbody>
              {activity.recent_events.map((ev) => (
                <tr key={ev.lead_id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-2.5">
                    <LeadName ev={ev} />
                  </td>
                  <td className="px-4 py-2.5"><MiniPill value={ev.research_status} /></td>
                  <td className="px-4 py-2.5"><MiniPill value={ev.compose_status} /></td>
                  <td className="px-4 py-2.5"><MiniPill value={ev.send_status} /></td>
                  <td className="px-4 py-2.5 text-xs text-slate-500">
                    {ev.send_status === 'scheduled' && ev.scheduled_send_at
                      ? fmtDatetime(ev.scheduled_send_at)
                      : ev.send_status === 'sent' ? '✓ sent' : '—'}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-400">{fmtRelative(ev.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


function LeadEmailModal({ campaignId, lead, onClose }) {
  const { data: detail, isLoading, error } = useQuery({
    queryKey: ['lead-detail', campaignId, lead.id],
    queryFn: () => getLeadDetail(campaignId, lead.id),
  });

  const qualityColors = {
    rich: 'bg-emerald-100 text-emerald-700',
    partial: 'bg-amber-100 text-amber-700',
    low: 'bg-slate-100 text-slate-500',
  };
  const quality = detail?.research_data?.quality || 'low';

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-5">
          <div>
            <h2 className="m-0 text-lg font-semibold text-slate-900">
              {lead.first_name || lead.last_name
                ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
                : lead.email}
            </h2>
            <div className="text-sm text-slate-500 mt-0.5">{lead.email}{lead.company ? ` · ${lead.company}` : ''}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 text-xl font-medium leading-none bg-transparent border-none cursor-pointer p-1"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {isLoading && (
          <div className="py-8 text-center text-sm text-slate-500">Loading…</div>
        )}
        {error && (
          <div className="py-4 text-sm text-red-600">Failed to load lead detail.</div>
        )}
        {detail && (
          <div className="space-y-4">
            {/* Research summary */}
            <div className="p-4 bg-slate-50 rounded-lg border border-slate-200">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Research</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${qualityColors[quality]}`}>
                  {quality}
                </span>
              </div>
              {detail.research_data && (
                <div className="text-xs text-slate-600 space-y-1">
                  {detail.research_data.linkedin_headline && (
                    <div><span className="text-slate-400">Headline:</span> {detail.research_data.linkedin_headline}</div>
                  )}
                  {detail.research_data.company_description && (
                    <div><span className="text-slate-400">Company:</span> {detail.research_data.company_description}</div>
                  )}
                  {(detail.research_data.person_news || []).length > 0 && (
                    <div><span className="text-slate-400">News:</span> {detail.research_data.person_news.slice(0, 2).join(' · ')}</div>
                  )}
                </div>
              )}
              {!detail.research_data && (
                <p className="text-xs text-slate-500">No research data.</p>
              )}
            </div>

            {/* Composed email */}
            {detail.composed_subject || detail.composed_body ? (
              <div className="border border-slate-200 rounded-lg overflow-hidden">
                <div className="bg-slate-50 border-b border-slate-200 px-4 py-2.5">
                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide mr-2">Subject</span>
                  <span className="text-sm text-slate-900 font-medium">{detail.composed_subject || '—'}</span>
                </div>
                <div className="p-4">
                  <pre className="text-sm text-slate-800 whitespace-pre-wrap font-sans leading-relaxed m-0">
                    {detail.composed_body || '—'}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="text-sm text-slate-500 italic py-2">No email composed yet.</div>
            )}

            <div className="flex gap-4 text-xs text-slate-500 pt-1">
              <span>Send status: <strong className="text-slate-700">{detail.send_status}</strong></span>
              {detail.brevo_message_id && (
                <span>Message ID: <code className="text-slate-600">{detail.brevo_message_id}</code></span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


export default function CampaignDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState('overview');
  const [showAddLeads, setShowAddLeads] = useState(false);
  const [viewLead, setViewLead] = useState(null);

  const { data: campaign, isLoading, error } = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => getCampaign(id),
    refetchInterval: (data) => {
      const s = data?.status;
      return (s === 'previewing' || s === 'running') ? 8000 : false;
    },
  });

  const shouldPollProgress = campaign?.status === 'previewing' || campaign?.status === 'running' || campaign?.status === 'paused';
  const { data: progress } = useQuery({
    queryKey: ['preview-progress', id],
    queryFn: () => getPreviewProgress(id),
    enabled: shouldPollProgress,
    refetchInterval: campaign?.status === 'previewing' ? 5000 : campaign?.status === 'running' ? 8000 : false,
  });

  const { data: linkedinAccounts = [] } = useQuery({
    queryKey: ['linkedin-accounts'],
    queryFn: listLinkedInAccounts,
  });

  const linkedinAssignMutation = useMutation({
    mutationFn: (accountId) => updateCampaign(id, { linkedin_account_id: accountId }),
    onSuccess: () => {
      toast.success('LinkedIn account updated');
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Update failed'),
  });

  const pauseMutation = useMutation({
    mutationFn: () => pauseCampaign(id),
    onSuccess: () => {
      toast.success('Campaign paused');
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Pause failed'),
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeCampaign(id),
    onSuccess: () => {
      toast.success('Campaign resumed');
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Resume failed'),
  });

  const launchMutation = useMutation({
    mutationFn: () => approveAllCampaign(id),
    onSuccess: () => {
      toast.success('Campaign launched!');
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
      queryClient.invalidateQueries({ queryKey: ['preview-progress', id] });
      navigate(`/campaigns/${id}/preview`);
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Launch failed'),
  });

  function handlePauseResume() {
    if (campaign?.status === 'running') pauseMutation.mutate();
    else if (campaign?.status === 'paused') resumeMutation.mutate();
  }

  if (isLoading) return <div className="p-8 text-sm text-slate-500">Loading campaign…</div>;
  if (error || !campaign) {
    return (
      <div className="p-8">
        <p className="text-red-600 text-sm mb-4">Failed to load campaign.</p>
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors"
        >
          Back to campaigns
        </button>
      </div>
    );
  }

  return (
    <div className="p-8 max-w-[1100px] mx-auto">
      <div className="mb-3">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="text-sm text-blue-600 hover:text-blue-800 hover:underline bg-transparent border-none cursor-pointer p-0"
        >
          ← All campaigns
        </button>
      </div>
      <div className="flex justify-between items-center gap-3 mb-2">
        <h1 className="text-2xl font-bold text-slate-900 m-0" data-testid="campaign-name">{campaign.name}</h1>
        <div className="flex items-center gap-2">
          {campaign.status === 'previewing' && (
            <button
              type="button"
              onClick={() => navigate(`/campaigns/${id}/preview`)}
              className="px-3 py-1.5 text-sm bg-amber-500 hover:bg-amber-600 text-white font-semibold rounded-lg"
            >
              Review & Launch
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowAddLeads(true)}
            className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            data-testid="add-leads-button"
          >
            + Add leads
          </button>
          <button
            type="button"
            onClick={() => navigate(`/campaigns/${id}/sequence`)}
            className="px-3 py-1.5 text-sm bg-white border border-slate-300 rounded-lg hover:bg-slate-50"
            data-testid="open-sequence-builder"
          >
            Edit sequence
          </button>
          <StatusBadge status={campaign.status} />
        </div>
      </div>

      <div role="tablist" className="flex border-b border-slate-200 mb-6 mt-4">
        {[
          { id: 'overview', label: 'Overview' },
          { id: 'activity', label: 'Activity' },
          { id: 'leads', label: 'Leads' },
          { id: 'analytics', label: 'Analytics' },
        ].map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors bg-transparent cursor-pointer ${
              tab === t.id
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
            data-testid={`tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <OverviewTab
          campaign={campaign}
          progress={progress}
          onPauseToggle={handlePauseResume}
          pauseLoading={pauseMutation.isPending || resumeMutation.isPending}
          onLaunch={() => navigate(`/campaigns/${id}/preview`)}
          launchLoading={launchMutation.isPending}
          linkedinAccounts={linkedinAccounts}
          onSaveLinkedIn={(accountId) => linkedinAssignMutation.mutate(accountId)}
          savingLinkedIn={linkedinAssignMutation.isPending}
        />
      )}
      {tab === 'activity' && (
        <div data-testid="activity-tab">
          <ActivityTab campaignId={id} campaign={campaign} />
        </div>
      )}
      {tab === 'leads' && (
        <div data-testid="leads-tab">
          <LeadTable
            campaignId={id}
            replyTrackingEnabled={!!campaign.connected_account_configured}
            onViewLead={(lead) => setViewLead(lead)}
          />
        </div>
      )}
      {tab === 'analytics' && (
        <div data-testid="analytics-tab">
          <AnalyticsContent id={id} />
        </div>
      )}

      {showAddLeads && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
          onClick={() => setShowAddLeads(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-center mb-5">
              <h2 className="m-0 text-lg font-semibold text-slate-900">Add leads to {campaign.name}</h2>
              <button
                type="button"
                onClick={() => setShowAddLeads(false)}
                className="text-slate-400 hover:text-slate-600 text-xl font-medium leading-none bg-transparent border-none cursor-pointer p-1"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <LeadUpload
              campaignId={id}
              onComplete={(result) => {
                setShowAddLeads(false);
                toast.success(`${result.imported ?? result.count ?? 'Leads'} leads imported — research starting.`);
                queryClient.invalidateQueries({ queryKey: ['campaign', id] });
                queryClient.invalidateQueries({ queryKey: ['leads', id] });
                setTab('leads');
              }}
            />
          </div>
        </div>
      )}

      {viewLead && (
        <LeadEmailModal
          campaignId={id}
          lead={viewLead}
          onClose={() => setViewLead(null)}
        />
      )}
    </div>
  );
}
