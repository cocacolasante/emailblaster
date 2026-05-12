import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { getCampaign, pauseCampaign, resumeCampaign } from '../api/campaigns.js';
import LeadTable from '../components/LeadTable.jsx';
import { useToast } from '../components/Toast.jsx';
import { AnalyticsContent } from './Analytics.jsx';

const STATUS_COLORS = {
  draft:      { bg: '#e5e7eb', fg: '#374151' },
  previewing: { bg: '#fff3cd', fg: '#7a5a00' },
  approved:   { bg: '#dbeafe', fg: '#1e40af' },
  running:    { bg: '#dcfce7', fg: '#166534' },
  paused:     { bg: '#fef3c7', fg: '#92400e' },
  complete:   { bg: '#e0e7ff', fg: '#3730a3' },
};


function StatusBadge({ status }) {
  const c = STATUS_COLORS[status] || STATUS_COLORS.draft;
  return (
    <span
      data-testid="status-badge"
      data-status={status}
      style={{
        padding: '4px 12px',
        borderRadius: 12,
        fontSize: 13,
        fontWeight: 500,
        background: c.bg,
        color: c.fg,
        textTransform: 'capitalize',
      }}
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
    <div data-testid="overview-tab">
      <section style={cardStyle}>
        <h2 style={sectionH2}>Status</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <StatusBadge status={campaign.status} />
          {campaign.status === 'running' && (
            <button
              type="button"
              onClick={onPauseToggle}
              disabled={pauseLoading}
              style={btnStyle}
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
              style={btnStyle}
              data-testid="pause-resume-button"
            >
              Resume
            </button>
          )}
        </div>
        <div style={{ marginTop: 16, fontSize: 13, color: '#666' }}>
          {sent} of {total} sent
        </div>
        <div style={progressTrackStyle}>
          <div style={{ ...progressFillStyle, width: `${progress}%` }} />
        </div>
      </section>

      <section style={cardStyle}>
        <h2 style={sectionH2}>Quick stats</h2>
        <div style={{ display: 'flex', gap: 32 }}>
          <div>
            <div style={statLabel}>Open rate</div>
            <div style={statValue}>{pct(campaign.stats?.open_rate)}</div>
          </div>
          <div>
            <div style={statLabel}>Click rate</div>
            <div style={statValue}>{pct(campaign.stats?.click_rate)}</div>
          </div>
          <div>
            <div style={statLabel}>Reply rate</div>
            <div style={statValue} data-testid="reply-rate">
              {campaign.connected_account_configured ? pct(campaign.stats?.reply_rate) : '--'}
            </div>
          </div>
          <div>
            <div style={statLabel}>Bounce rate</div>
            <div style={statValue}>{pct(campaign.stats?.bounce_rate)}</div>
          </div>
        </div>
      </section>

      <section style={cardStyle}>
        <h2 style={sectionH2}>Reply tracking</h2>
        {campaign.connected_account_configured && campaign.connected_account ? (
          <p data-testid="reply-tracking-info" style={{ margin: 0 }}>
            <strong>{campaign.connected_account.label}</strong>{' '}
            ({campaign.connected_account.email_address})
          </p>
        ) : (
          <p data-testid="reply-tracking-info" style={{ margin: 0, color: '#666' }}>
            Reply tracking not configured for this campaign.
          </p>
        )}
      </section>

      <section style={cardStyle}>
        <h2 style={sectionH2}>Campaign config</h2>
        <dl style={dlStyle}>
          <dt>Goal</dt><dd>{campaign.goal}</dd>
          <dt>Tone</dt><dd>{campaign.tone}</dd>
          <dt>Sender</dt><dd>{campaign.sender_name} &lt;{campaign.sender_email}&gt;</dd>
          <dt>Research mode</dt><dd>{campaign.research_mode}</dd>
          <dt>Sample count</dt><dd>{campaign.sample_count}</dd>
          <dt>Schedule</dt>
          <dd>
            {campaign.schedule_time_start?.slice(0, 5)} – {campaign.schedule_time_end?.slice(0, 5)}{' '}
            ({campaign.schedule_timezone})
          </dd>
        </dl>
      </section>
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

  if (isLoading) return <div style={{ padding: 24 }}>Loading campaign…</div>;
  if (error || !campaign) {
    return (
      <div style={{ padding: 24 }}>
        <p style={{ color: '#b71c1c' }}>Failed to load campaign.</p>
        <button type="button" onClick={() => navigate('/')} style={btnStyle}>
          Back to campaigns
        </button>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ marginBottom: 8 }}>
        <button type="button" onClick={() => navigate('/')} style={linkBtn}>
          ← All campaigns
        </button>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
        <h1 style={{ margin: 0 }} data-testid="campaign-name">{campaign.name}</h1>
        <StatusBadge status={campaign.status} />
      </div>

      <div role="tablist" style={tabsStyle}>
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
            style={tabBtnStyle(tab === t.id)}
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


const cardStyle = {
  background: 'white',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 16,
  marginBottom: 16,
};

const sectionH2 = {
  marginTop: 0,
  marginBottom: 12,
  fontSize: 14,
  textTransform: 'uppercase',
  color: '#666',
  letterSpacing: 0.5,
};

const tabsStyle = {
  display: 'flex',
  gap: 4,
  borderBottom: '1px solid #e5e7eb',
  marginTop: 16,
  marginBottom: 20,
};

function tabBtnStyle(active) {
  return {
    padding: '10px 18px',
    border: 'none',
    background: 'none',
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: active ? 600 : 400,
    color: active ? '#2563eb' : '#666',
    borderBottom: active ? '2px solid #2563eb' : '2px solid transparent',
    marginBottom: -1,
  };
}

const btnStyle = {
  padding: '6px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const linkBtn = {
  border: 'none',
  background: 'none',
  color: '#2563eb',
  cursor: 'pointer',
  padding: 0,
  fontSize: 13,
};

const progressTrackStyle = {
  height: 8,
  background: '#e5e7eb',
  borderRadius: 999,
  overflow: 'hidden',
  marginTop: 4,
};

const progressFillStyle = {
  height: '100%',
  background: '#2563eb',
  transition: 'width 0.3s',
};

const statLabel = { fontSize: 11, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5 };
const statValue = { fontSize: 22, fontWeight: 600 };

const dlStyle = {
  display: 'grid',
  gridTemplateColumns: '140px 1fr',
  rowGap: 8,
  columnGap: 16,
  margin: 0,
  fontSize: 14,
};
