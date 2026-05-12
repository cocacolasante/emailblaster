import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  getAnalytics,
  listCampaignErrors,
  retryFailedLeads,
} from '../api/campaigns.js';
import MetricsGrid from '../components/MetricsGrid.jsx';
import LeadTable from '../components/LeadTable.jsx';
import { useToast } from '../components/Toast.jsx';

function pct(v) {
  if (v == null) return '--';
  return `${(v * 100).toFixed(1)}%`;
}

function reputationColor(score) {
  if (score == null) return '#888';
  if (score >= 80) return '#22c55e';
  if (score >= 50) return '#f59e0b';
  return '#dc2626';
}

function ReputationCard({ score, rates }) {
  return (
    <div data-testid="reputation-card" style={cardStyle}>
      <h3 style={{ margin: 0, fontSize: 14, color: '#666' }}>Sender reputation</h3>
      <div
        data-testid="reputation-score"
        style={{
          fontSize: 64,
          fontWeight: 700,
          color: reputationColor(score),
          margin: '12px 0',
        }}
      >
        {score == null ? '—' : score}
      </div>
      <div style={{ display: 'flex', gap: 24, fontSize: 13 }}>
        <div><strong>{pct(rates.delivery_rate)}</strong><br /><span style={{ color: '#666' }}>delivered</span></div>
        <div><strong>{pct(rates.spam_rate)}</strong><br /><span style={{ color: '#666' }}>spam</span></div>
        <div><strong>{pct(rates.bounce_rate)}</strong><br /><span style={{ color: '#666' }}>bounced</span></div>
      </div>
    </div>
  );
}

function QualityBreakdown({ items }) {
  if (!items || items.length === 0) return null;
  const labels = { rich: 'Rich', partial: 'Partial', low: 'Generic' };
  return (
    <div data-testid="quality-breakdown" style={cardStyle}>
      <h3 style={{ margin: 0, fontSize: 14, color: '#666' }}>Research quality vs. open rate</h3>
      <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
        {items.map((item) => (
          <div key={item.quality} style={{ flex: 1, padding: 12, background: '#f9fafb', borderRadius: 6 }}>
            <div style={{ fontSize: 13, color: '#666' }}>{labels[item.quality] || item.quality}</div>
            <div style={{ fontSize: 20, fontWeight: 600 }}>{item.count}</div>
            <div style={{ fontSize: 12, color: '#666' }}>Open: {pct(item.open_rate)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BestSubjects({ subjects }) {
  if (!subjects || subjects.length === 0) return null;
  return (
    <div data-testid="best-subjects" style={cardStyle}>
      <h3 style={{ margin: 0, fontSize: 14, color: '#666' }}>Best subject lines</h3>
      <table style={{ width: '100%', marginTop: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={subjThStyle}>Subject</th>
            <th style={{ ...subjThStyle, width: 80 }}>Sent</th>
            <th style={{ ...subjThStyle, width: 100 }}>Open rate</th>
          </tr>
        </thead>
        <tbody>
          {subjects.map((s) => (
            <tr key={s.subject}>
              <td style={subjTdStyle}>{s.subject}</td>
              <td style={subjTdStyle}>{s.sent}</td>
              <td style={subjTdStyle}>{pct(s.open_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Timeline({ points, replyTrackingEnabled }) {
  if (!points || points.length === 0) {
    return (
      <div style={cardStyle}>
        <h3 style={{ margin: 0, fontSize: 14, color: '#666' }}>Timeline</h3>
        <p style={{ fontSize: 13, color: '#888' }}>No events yet.</p>
      </div>
    );
  }
  return (
    <div data-testid="timeline-chart" style={cardStyle}>
      <h3 style={{ margin: 0, fontSize: 14, color: '#666' }}>Last 30 days</h3>
      <LineChart width={680} height={220} data={points} style={{ marginTop: 12 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="date" />
        <YAxis />
        <Tooltip />
        <Legend />
        <Line type="monotone" dataKey="opens" stroke="#14b8a6" strokeWidth={2} />
        <Line type="monotone" dataKey="clicks" stroke="#8b5cf6" strokeWidth={2} />
        {replyTrackingEnabled && (
          <Line type="monotone" dataKey="replies" stroke="#f59e0b" strokeWidth={2} />
        )}
      </LineChart>
    </div>
  );
}

function FailedLeadsBanner({ id }) {
  const queryClient = useQueryClient();
  const toast = useToast();

  const { data: errors = [] } = useQuery({
    queryKey: ['campaign-errors', id],
    queryFn: () => listCampaignErrors(id),
    refetchInterval: 30000,
  });

  const retryMutation = useMutation({
    mutationFn: () => retryFailedLeads(id),
    onSuccess: (data) => {
      const total =
        (data?.research_retried || 0) +
        (data?.compose_retried || 0) +
        (data?.send_retried || 0);
      toast.success(`Re-queued ${total} failed lead${total === 1 ? '' : 's'}`);
      queryClient.invalidateQueries({ queryKey: ['campaign-errors', id] });
      queryClient.invalidateQueries({ queryKey: ['analytics', id] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Retry failed'),
  });

  if (errors.length === 0) return null;

  return (
    <div data-testid="failed-leads-banner" style={failedBannerStyle}>
      <div>
        <strong>{errors.length}</strong> lead{errors.length === 1 ? '' : 's'} failed in this campaign.
      </div>
      <button
        type="button"
        onClick={() => retryMutation.mutate()}
        disabled={retryMutation.isPending}
        style={retryBtnStyle}
        data-testid="retry-failed-button"
      >
        {retryMutation.isPending ? 'Retrying…' : 'Retry failed'}
      </button>
    </div>
  );
}


export function AnalyticsContent({ id, includeLeadTable = true }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['analytics', id],
    queryFn: () => getAnalytics(id),
    refetchInterval: 30000,
  });

  if (isLoading) return <div>Loading analytics…</div>;
  if (error) return <div style={{ color: '#b71c1c' }}>Failed to load analytics</div>;
  if (!data) return null;

  const replyTracking = data.reply_tracking_enabled;

  const topRow = [
    { label: 'Sent', value: data.overview.sent },
    { label: 'Delivered', value: data.overview.delivered },
    { label: 'Open rate', value: pct(data.rates.open_rate) },
    { label: 'Click rate', value: pct(data.rates.click_rate) },
  ];

  const secondRow = [
    {
      label: 'Reply rate',
      value: replyTracking ? pct(data.rates.reply_rate) : '--',
      tooltip: replyTracking ? null : 'Connect an inbox to track replies',
    },
    { label: 'Bounce rate', value: pct(data.rates.bounce_rate) },
    { label: 'Spam complaints', value: data.overview.spam_complaints },
    { label: 'Unsubscribed', value: data.overview.unsubscribed },
  ];

  return (
    <div>
      <FailedLeadsBanner id={id} />

      <MetricsGrid metrics={topRow} />
      <MetricsGrid metrics={secondRow} />

      {!replyTracking && (
        <div data-testid="reply-tracking-banner" style={bannerStyle}>
          Reply tracking is not configured for this campaign.{' '}
          <a href="/settings" style={{ color: '#2563eb' }}>Connect an inbox</a>{' '}
          in Settings to track replies on future campaigns.
        </div>
      )}

      <Timeline points={data.timeline} replyTrackingEnabled={replyTracking} />

      <ReputationCard score={data.sender_reputation_score} rates={data.rates} />

      <QualityBreakdown items={data.research_quality_breakdown} />

      <BestSubjects subjects={data.best_subject_lines} />

      {includeLeadTable && (
        <div style={{ marginTop: 24 }}>
          <h2>Leads</h2>
          <LeadTable campaignId={id} replyTrackingEnabled={replyTracking} />
        </div>
      )}
    </div>
  );
}


export default function Analytics() {
  const { id } = useParams();
  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <h1>Analytics</h1>
      <AnalyticsContent id={id} />
    </div>
  );
}

const cardStyle = {
  padding: 16,
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  background: 'white',
  marginBottom: 16,
};

const bannerStyle = {
  background: '#fffbeb',
  border: '1px solid #fde68a',
  padding: 12,
  borderRadius: 6,
  fontSize: 13,
  color: '#7a5a00',
  marginBottom: 16,
};

const failedBannerStyle = {
  background: '#fef2f2',
  border: '1px solid #fecaca',
  borderRadius: 6,
  padding: 12,
  marginBottom: 16,
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontSize: 14,
  color: '#991b1b',
};

const retryBtnStyle = {
  padding: '6px 14px',
  border: '1px solid #b71c1c',
  borderRadius: 4,
  background: '#b71c1c',
  color: 'white',
  cursor: 'pointer',
  fontSize: 13,
  fontWeight: 500,
};

const subjThStyle = {
  textAlign: 'left',
  padding: '8px 10px',
  borderBottom: '2px solid #e5e7eb',
  fontSize: 12,
  color: '#666',
};

const subjTdStyle = {
  padding: '8px 10px',
  borderBottom: '1px solid #eee',
  fontSize: 14,
};
