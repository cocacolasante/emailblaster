import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteCampaign,
  listCampaigns,
  pauseCampaign,
  resumeCampaign,
} from '../api/campaigns.js';
import { useToast } from '../components/Toast.jsx';

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
        padding: '4px 10px',
        borderRadius: 12,
        fontSize: 12,
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


function CampaignCard({ campaign }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();

  const pauseMutation = useMutation({
    mutationFn: () => pauseCampaign(campaign.id),
    onSuccess: () => {
      toast.success(`"${campaign.name}" paused`);
      queryClient.invalidateQueries({ queryKey: ['campaigns'] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Pause failed'),
  });
  const resumeMutation = useMutation({
    mutationFn: () => resumeCampaign(campaign.id),
    onSuccess: () => {
      toast.success(`"${campaign.name}" resumed`);
      queryClient.invalidateQueries({ queryKey: ['campaigns'] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Resume failed'),
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteCampaign(campaign.id),
    onSuccess: () => {
      toast.success(`"${campaign.name}" deleted`);
      queryClient.invalidateQueries({ queryKey: ['campaigns'] });
    },
    onError: (e) => toast.error(e?.response?.data?.detail || e.message || 'Delete failed'),
  });

  const total = campaign.lead_counts?.total ?? 0;
  const sent = campaign.lead_counts?.sent ?? 0;
  const progress = total > 0 ? Math.min(100, (sent / total) * 100) : 0;
  const replyTracking = campaign.connected_account_configured;

  return (
    <div data-testid="campaign-card" data-campaign-id={campaign.id} style={cardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16 }}>{campaign.name}</h3>
        <StatusBadge status={campaign.status} />
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: '#666' }}>
        {sent} of {total} sent
      </div>
      <div style={progressTrackStyle}>
        <div
          data-testid="progress-bar"
          style={{ ...progressFillStyle, width: `${progress}%` }}
        />
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 12, fontSize: 13 }}>
        <div>
          <div style={statLabel}>Open</div>
          <div style={statValue}>{pct(campaign.stats?.open_rate)}</div>
        </div>
        <div>
          <div style={statLabel}>Click</div>
          <div style={statValue}>{pct(campaign.stats?.click_rate)}</div>
        </div>
        <div>
          <div style={statLabel}>Reply</div>
          <div
            style={statValue}
            data-testid="reply-rate"
            title={replyTracking ? '' : 'Connect an inbox to track replies'}
          >
            {replyTracking ? pct(campaign.stats?.reply_rate) : '--'}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 11, color: '#888', marginTop: 12 }}>
        Created {new Date(campaign.created_at).toLocaleDateString()}
      </div>

      <div style={{ display: 'flex', gap: 6, marginTop: 12 }}>
        <button
          type="button"
          onClick={() => navigate(`/campaigns/${campaign.id}`)}
          style={btnStyle}
        >
          View
        </button>
        {campaign.status === 'running' && (
          <button
            type="button"
            onClick={() => pauseMutation.mutate()}
            disabled={pauseMutation.isPending}
            style={btnStyle}
            data-testid="pause-button"
          >
            Pause
          </button>
        )}
        {campaign.status === 'paused' && (
          <button
            type="button"
            onClick={() => resumeMutation.mutate()}
            disabled={resumeMutation.isPending}
            style={btnStyle}
            data-testid="resume-button"
          >
            Resume
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            if (confirm(`Delete campaign "${campaign.name}"? This cannot be undone.`)) {
              deleteMutation.mutate();
            }
          }}
          disabled={deleteMutation.isPending}
          style={{ ...btnStyle, color: '#b71c1c' }}
          data-testid="delete-button"
        >
          Delete
        </button>
      </div>
    </div>
  );
}


function Skeleton() {
  return (
    <div style={gridStyle} data-testid="loading-skeleton">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} style={{ ...cardStyle, opacity: 0.4 }}>
          <div style={{ background: '#e5e7eb', height: 16, width: '60%', borderRadius: 4 }} />
          <div style={{ background: '#e5e7eb', height: 12, width: '80%', borderRadius: 4, marginTop: 16 }} />
          <div style={{ background: '#e5e7eb', height: 24, width: '100%', borderRadius: 4, marginTop: 16 }} />
        </div>
      ))}
    </div>
  );
}


function EmptyState() {
  return (
    <div data-testid="empty-state" style={{ padding: 48, textAlign: 'center', background: '#f9fafb', borderRadius: 8 }}>
      <h2 style={{ marginTop: 0 }}>No campaigns yet</h2>
      <p style={{ color: '#666' }}>
        Create your first campaign to start sending personalized cold emails.
      </p>
      <Link to="/campaigns/new" style={primaryLinkStyle}>+ New campaign</Link>
    </div>
  );
}


export default function Campaigns() {
  const { data: campaigns, isLoading, error } = useQuery({
    queryKey: ['campaigns'],
    queryFn: listCampaigns,
  });

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1>Campaigns</h1>
        <Link to="/campaigns/new" style={primaryLinkStyle}>
          + New campaign
        </Link>
      </div>

      {isLoading && <Skeleton />}
      {error && (
        <div style={{ color: '#b71c1c', padding: 16 }}>
          Failed to load campaigns: {String(error?.message)}
        </div>
      )}
      {!isLoading && !error && campaigns?.length === 0 && <EmptyState />}
      {!isLoading && !error && campaigns?.length > 0 && (
        <div style={gridStyle}>
          {campaigns.map((c) => (
            <CampaignCard key={c.id} campaign={c} />
          ))}
        </div>
      )}
    </div>
  );
}


const gridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
  gap: 16,
};

const cardStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 16,
  background: 'white',
};

const progressTrackStyle = {
  height: 6,
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
const statValue = { fontSize: 16, fontWeight: 600 };

const btnStyle = {
  padding: '6px 12px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const primaryLinkStyle = {
  padding: '8px 16px',
  borderRadius: 4,
  background: '#2563eb',
  color: 'white',
  textDecoration: 'none',
  fontSize: 14,
  fontWeight: 500,
};
