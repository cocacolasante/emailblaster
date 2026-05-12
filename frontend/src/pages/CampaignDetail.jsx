import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { getCampaign, pauseCampaign, resumeCampaign } from '../api/campaigns.js';
import LeadTable from '../components/LeadTable.jsx';
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


function OverviewTab({ campaign, onPauseToggle, pauseLoading }) {
  const total = campaign.lead_counts?.total ?? 0;
  const sent = campaign.lead_counts?.sent ?? 0;
  const progress = total > 0 ? Math.min(100, (sent / total) * 100) : 0;

  return (
    <div data-testid="overview-tab" className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Left column */}
      <div className="space-y-4">
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
          </div>
          <div className="text-sm text-slate-500 mb-2">{sent} of {total} sent</div>
          <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
            <div className="h-full bg-blue-600 rounded-full transition-all" style={{ width: `${progress}%` }} />
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


export default function CampaignDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState('overview');

  const { data: campaign, isLoading, error } = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => getCampaign(id),
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
        <StatusBadge status={campaign.status} />
      </div>

      <div role="tablist" className="flex border-b border-slate-200 mb-6 mt-4">
        {[
          { id: 'overview', label: 'Overview' },
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
          onPauseToggle={handlePauseResume}
          pauseLoading={pauseMutation.isPending || resumeMutation.isPending}
        />
      )}
      {tab === 'leads' && (
        <div data-testid="leads-tab">
          <LeadTable
            campaignId={id}
            replyTrackingEnabled={!!campaign.connected_account_configured}
          />
        </div>
      )}
      {tab === 'analytics' && (
        <div data-testid="analytics-tab">
          <AnalyticsContent id={id} />
        </div>
      )}
    </div>
  );
}
