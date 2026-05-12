import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  approveAll as approveAllCampaign,
  getCampaign,
  getPreview,
  rejectPreview,
  updateSample,
} from '../api/campaigns.js';
import EmailPreviewCard from '../components/EmailPreviewCard.jsx';

export default function Preview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const previewQuery = useQuery({
    queryKey: ['preview', id],
    queryFn: () => getPreview(id),
  });
  const campaignQuery = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => getCampaign(id),
  });

  const approveAllMutation = useMutation({
    mutationFn: () => approveAllCampaign(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
      navigate(`/campaigns/${id}`);
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => rejectPreview(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaign', id] });
      navigate(`/campaigns/${id}`);
    },
  });

  async function handleSampleSave(leadId, payload) {
    const updated = await updateSample(id, leadId, payload);
    // Patch the cached preview without a full refetch.
    queryClient.setQueryData(['preview', id], (prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        samples: prev.samples.map((s) =>
          s.lead_id === leadId ? { ...s, ...updated } : s,
        ),
      };
    });
  }

  async function handleApprove(leadId, approved) {
    await handleSampleSave(leadId, { approved });
  }

  if (previewQuery.isLoading || campaignQuery.isLoading) {
    return <div style={{ padding: 24 }}>Loading preview…</div>;
  }
  if (previewQuery.error || campaignQuery.error) {
    return (
      <div style={{ padding: 24, color: '#b71c1c' }}>
        Failed to load preview.
      </div>
    );
  }

  const preview = previewQuery.data;
  const campaign = campaignQuery.data;
  const samples = preview?.samples || [];
  const approvedCount = samples.filter((s) => s.sample_approved === true).length;
  const total = samples.length;

  return (
    <div style={{ padding: 24, maxWidth: 880, margin: '0 auto', paddingBottom: 120 }}>
      <h1>Preview</h1>

      <div data-testid="campaign-context" style={contextStripStyle}>
        <div><strong>Goal:</strong> {campaign?.goal}</div>
        <div><strong>Tone:</strong> {campaign?.tone}</div>
      </div>

      {samples.length === 0 ? (
        <p style={{ padding: 40, textAlign: 'center', color: '#666' }}>
          No samples available yet. Wait for research and composition to complete.
        </p>
      ) : (
        samples.map((sample) => (
          <EmailPreviewCard
            key={sample.lead_id}
            sample={sample}
            remainingSamples={total - 1}
            onSave={handleSampleSave}
            onApprove={handleApprove}
          />
        ))
      )}

      {/* Sticky bottom action bar */}
      <div style={actionBarStyle}>
        <span data-testid="approval-counter" style={{ fontSize: 14 }}>
          <strong>{approvedCount}</strong> of {total} approved
        </span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            onClick={() => rejectMutation.mutate()}
            disabled={rejectMutation.isPending}
            style={btnStyle}
          >
            Reject and reconfigure
          </button>
          <button
            type="button"
            onClick={() => approveAllMutation.mutate()}
            disabled={approveAllMutation.isPending || total === 0}
            style={primaryBtn}
          >
            {approveAllMutation.isPending ? 'Launching…' : 'Approve and launch campaign'}
          </button>
        </div>
      </div>
    </div>
  );
}

const contextStripStyle = {
  background: '#f9fafb',
  border: '1px solid #e5e7eb',
  padding: 12,
  borderRadius: 6,
  marginBottom: 20,
  display: 'flex',
  gap: 32,
  fontSize: 14,
};

const actionBarStyle = {
  position: 'fixed',
  bottom: 0,
  left: 0,
  right: 0,
  background: 'white',
  borderTop: '1px solid #e5e7eb',
  padding: '12px 24px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  boxShadow: '0 -2px 8px rgba(0,0,0,0.05)',
};

const btnStyle = {
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};

const primaryBtn = {
  ...btnStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};
