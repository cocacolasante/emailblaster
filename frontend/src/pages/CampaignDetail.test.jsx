import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../components/Toast.jsx';
import CampaignDetail from './CampaignDetail.jsx';

vi.mock('../api/campaigns.js', () => ({
  getCampaign: vi.fn(),
  pauseCampaign: vi.fn(),
  resumeCampaign: vi.fn(),
  listCampaignLeads: vi.fn().mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 50, total_pages: 0,
  }),
  getAnalytics: vi.fn().mockResolvedValue({
    campaign_id: 'c1',
    overview: { total_leads: 0, sent: 0, delivered: 0, opened: 0, clicked: 0, replied: 0, bounced: 0, spam_complaints: 0, unsubscribed: 0 },
    rates: { open_rate: null, click_rate: null, reply_rate: null, bounce_rate: null, spam_rate: null, unsub_rate: null, delivery_rate: null },
    reply_tracking_enabled: false,
    timeline: [],
    research_quality_breakdown: [],
    sender_reputation_score: null,
    best_subject_lines: [],
  }),
  listCampaignErrors: vi.fn().mockResolvedValue([]),
  retryFailedLeads: vi.fn(),
}));

// Recharts stub
vi.mock('recharts', () => {
  const noop = ({ children }) => <div>{children}</div>;
  return {
    LineChart: ({ children }) => <div data-testid="line-chart">{children}</div>,
    Line: () => <div />,
    XAxis: noop, YAxis: noop, CartesianGrid: noop, Tooltip: noop, Legend: noop,
    ResponsiveContainer: noop,
  };
});

import * as api from '../api/campaigns.js';

const RUNNING_CAMPAIGN = {
  id: 'c1', name: 'Spring outreach', status: 'running',
  goal: 'Book demos', tone: 'Direct',
  sender_name: 'Anthony', sender_email: 'a@x.com',
  research_mode: 'fast', sample_count: 5,
  schedule_time_start: '09:00:00', schedule_time_end: '17:00:00',
  schedule_timezone: 'UTC',
  connected_account_configured: true,
  connected_account: { id: 'a1', label: 'Work Gmail', email_address: 'work@x.com' },
  lead_counts: { total: 100, pending: 50, scheduled: 0, sent: 50, failed: 0 },
  stats: { open_rate: 0.5, click_rate: 0.2, reply_rate: 0.05, bounce_rate: 0.01 },
  created_at: '2026-05-01T12:00:00Z',
};


function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider defaultDuration={0}>
        <MemoryRouter initialEntries={['/campaigns/c1']}>
          <Routes>
            <Route path="/campaigns/:id" element={<CampaignDetail />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getCampaign.mockResolvedValue(RUNNING_CAMPAIGN);
});


describe('CampaignDetail', () => {
  it('renders campaign name and status', async () => {
    renderPage();
    expect(await screen.findByTestId('campaign-name')).toHaveTextContent('Spring outreach');
    // Status badge appears in both the page header and the Overview tab.
    const badges = screen.getAllByTestId('status-badge');
    expect(badges.length).toBeGreaterThan(0);
    badges.forEach((b) => expect(b).toHaveAttribute('data-status', 'running'));
  });

  it('Overview is the default tab', async () => {
    renderPage();
    expect(await screen.findByTestId('overview-tab')).toBeInTheDocument();
    expect(screen.getByTestId('tab-overview')).toHaveAttribute('aria-selected', 'true');
  });

  it('shows connected inbox info on Overview', async () => {
    renderPage();
    const info = await screen.findByTestId('reply-tracking-info');
    expect(info).toHaveTextContent(/work gmail/i);
    expect(info).toHaveTextContent(/work@x.com/i);
  });

  it('shows "not configured" when no inbox attached', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN,
      connected_account_configured: false,
      connected_account: null,
    });
    renderPage();
    const info = await screen.findByTestId('reply-tracking-info');
    expect(info).toHaveTextContent(/not configured/i);
  });

  it('Pause button on running campaign calls pauseCampaign', async () => {
    api.pauseCampaign.mockResolvedValue({});
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('pause-resume-button'));
    await waitFor(() => expect(api.pauseCampaign).toHaveBeenCalledWith('c1'));
  });

  it('Resume button on paused campaign calls resumeCampaign', async () => {
    api.getCampaign.mockResolvedValue({ ...RUNNING_CAMPAIGN, status: 'paused' });
    api.resumeCampaign.mockResolvedValue({});
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('pause-resume-button'));
    await waitFor(() => expect(api.resumeCampaign).toHaveBeenCalledWith('c1'));
  });

  it('No pause/resume button on draft campaign', async () => {
    api.getCampaign.mockResolvedValue({ ...RUNNING_CAMPAIGN, status: 'draft' });
    renderPage();
    await screen.findByTestId('overview-tab');
    expect(screen.queryByTestId('pause-resume-button')).not.toBeInTheDocument();
  });

  it('Switching to Leads tab renders the LeadTable', async () => {
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('tab-leads'));
    expect(await screen.findByTestId('leads-tab')).toBeInTheDocument();
    expect(screen.getByTestId('lead-table')).toBeInTheDocument();
  });

  it('Switching to Analytics tab renders analytics content', async () => {
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByTestId('analytics-tab')).toBeInTheDocument();
    // Analytics content shows metric tiles
    await waitFor(() => {
      expect(screen.getByTestId('metric-sent')).toBeInTheDocument();
    });
  });

  it('campaign config card shows goal/tone/sender/schedule', async () => {
    renderPage();
    await screen.findByTestId('overview-tab');
    expect(screen.getByText(/book demos/i)).toBeInTheDocument();
    expect(screen.getByText(/direct/i)).toBeInTheDocument();
    expect(screen.getByText(/anthony/i)).toBeInTheDocument();
    expect(screen.getByText(/09:00.*17:00.*utc/i)).toBeInTheDocument();
  });
});
