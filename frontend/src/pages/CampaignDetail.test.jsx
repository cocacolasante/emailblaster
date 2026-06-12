import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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
  updateCampaign: vi.fn().mockResolvedValue({}),
  applySignature: vi.fn().mockResolvedValue({ updated: 3 }),
  updateLeadEmail: vi.fn().mockResolvedValue({}),
  getLeadDetail: vi.fn(),
  getDeliverability: vi.fn(),
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

const DELIVERABILITY = {
  window_hours: 24, sample: 40, delivered: 38,
  hard_bounces: 1, soft_bounces: 0, spam: 0, opens: 12,
  bounce_rate: 0.025, spam_rate: 0, open_rate: 0.31,
  breaker: { enabled: true, min_sample: 20, bounce_threshold: 0.05, spam_threshold: 0.001 },
  domain: {
    domain: 'x.com', hour_used: 12, hour_cap: 100, hour_remaining: 88,
    day_used: 40, day_cap: 500, day_remaining: 460,
  },
  auto_paused_at: null, auto_pause_reason: null,
  send_time_optimization: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getCampaign.mockResolvedValue(RUNNING_CAMPAIGN);
  api.getDeliverability.mockResolvedValue(DELIVERABILITY);
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

  it('shows an auto-paused note when paused at the LinkedIn cap', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN, status: 'paused',
      auto_paused_until: '2026-05-27T13:00:00Z',
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    expect(screen.getByTestId('auto-paused-note')).toHaveTextContent(/auto-paused.*linkedin daily cap/i);
  });

  it('no auto-paused note for a manual pause', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN, status: 'paused', auto_paused_until: null,
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    expect(screen.queryByTestId('auto-paused-note')).not.toBeInTheDocument();
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

  it('Leads tab signature editor saves via updateCampaign', async () => {
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('tab-leads'));
    const editor = await screen.findByTestId('signature-editor');
    fireEvent.change(within(editor).getByRole('textbox'), {
      target: { value: 'Anthony\n555-1234\nacme.com' },
    });
    fireEvent.click(screen.getByTestId('save-signature-btn'));
    await waitFor(() => expect(api.updateCampaign).toHaveBeenCalledWith('c1', {
      signature: 'Anthony\n555-1234\nacme.com',
    }));
  });

  it('Schedule editor renders a read-only summary on overview', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN,
      schedule_days: [1, 2, 3, 4, 5],
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    const editor = await screen.findByTestId('schedule-editor');
    const summary = within(editor).getByTestId('schedule-summary');
    expect(summary.textContent).toMatch(/09:00.*17:00.*UTC/);
    expect(summary.textContent).toMatch(/Mon, Tue, Wed, Thu, Fri/);
  });

  it('Schedule editor saves a new window via updateCampaign on a running campaign', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN,
      schedule_days: [1, 2, 3, 4, 5],
      min_delay_seconds: 60,
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(await screen.findByTestId('schedule-editor-toggle'));

    fireEvent.change(screen.getByTestId('schedule-time-start'), { target: { value: '08:00' } });
    fireEvent.change(screen.getByTestId('schedule-time-end'),   { target: { value: '20:00' } });
    fireEvent.change(screen.getByTestId('schedule-timezone'),   { target: { value: 'America/New_York' } });
    fireEvent.click(screen.getByTestId('day-6'));  // add Saturday

    fireEvent.click(screen.getByTestId('save-schedule-btn'));
    await waitFor(() => expect(api.updateCampaign).toHaveBeenCalledTimes(1));
    const [cid, payload] = api.updateCampaign.mock.calls[0];
    expect(cid).toBe('c1');
    expect(payload).toMatchObject({
      schedule_time_start: '08:00:00',
      schedule_time_end: '20:00:00',
      schedule_timezone: 'America/New_York',
      schedule_days: [1, 2, 3, 4, 5, 6],
      min_delay_seconds: 60,
    });
  });

  it('Schedule editor blocks save when start >= end', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN, schedule_days: [1, 2, 3, 4, 5],
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(await screen.findByTestId('schedule-editor-toggle'));

    fireEvent.change(screen.getByTestId('schedule-time-start'), { target: { value: '18:00' } });
    fireEvent.change(screen.getByTestId('schedule-time-end'),   { target: { value: '09:00' } });

    expect(screen.getByTestId('schedule-validation-error').textContent)
      .toMatch(/start time must be before end time/i);
    expect(screen.getByTestId('save-schedule-btn')).toBeDisabled();
  });

  it('Apply-to-all calls applySignature when a saved signature exists', async () => {
    api.getCampaign.mockResolvedValue({ ...RUNNING_CAMPAIGN, signature: 'Saved sig' });
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('tab-leads'));
    await screen.findByTestId('signature-editor');
    const apply = screen.getByTestId('apply-signature-btn');
    expect(apply).not.toBeDisabled();  // saved sig, not dirty
    fireEvent.click(apply);
    await waitFor(() => expect(api.applySignature).toHaveBeenCalledWith('c1'));
  });

  it('editing a composed email saves via updateLeadEmail', async () => {
    api.listCampaignLeads.mockResolvedValue({
      items: [{
        id: 'L1', email: 'l@x.com', first_name: 'Jane', last_name: 'Doe',
        company: 'Acme', research_status: 'done', compose_status: 'done',
        send_status: 'pending',
      }],
      total: 1, page: 1, page_size: 50, total_pages: 1,
    });
    api.getLeadDetail.mockResolvedValue({
      composed_subject: 'Hi', composed_body: 'Body here',
      send_status: 'pending', research_data: { quality: 'low' },
    });
    renderPage();
    await screen.findByTestId('overview-tab');
    fireEvent.click(screen.getByTestId('tab-leads'));
    fireEvent.click(await screen.findByText('View email'));
    fireEvent.click(await screen.findByTestId('edit-email-btn'));
    const editor = await screen.findByTestId('email-editor');
    // [0] = subject input, [1] = body textarea
    const bodyField = within(editor).getAllByRole('textbox')[1];
    fireEvent.change(bodyField, { target: { value: 'Body here\n\nAnthony\n555-1234' } });
    fireEvent.click(screen.getByTestId('save-email-btn'));
    await waitFor(() => expect(api.updateLeadEmail).toHaveBeenCalledWith(
      'c1', 'L1', expect.objectContaining({ composed_body: 'Body here\n\nAnthony\n555-1234' }),
    ));
  });
});

describe('Deliverability guard', () => {
  it('renders the deliverability strip with rates + domain headroom', async () => {
    renderPage();
    const strip = await screen.findByTestId('deliverability-strip');
    expect(within(strip).getByTestId('deliv-bounce-rate')).toHaveTextContent('2.5%');
    const headroom = within(strip).getByTestId('domain-headroom');
    expect(headroom).toHaveTextContent('x.com');
    expect(headroom).toHaveTextContent('88/100 this hour');
    expect(headroom).toHaveTextContent('460/500 today');
  });

  it('shows the breaker banner with the reason on an auto-paused campaign', async () => {
    api.getCampaign.mockResolvedValue({
      ...RUNNING_CAMPAIGN,
      status: 'paused',
      auto_paused_at: '2026-06-12T10:00:00Z',
      auto_pause_reason: 'Hard-bounce rate 10.0% over the last 24h (4/40 sends) crossed the 5% threshold',
    });
    renderPage();
    const banner = await screen.findByTestId('breaker-banner');
    expect(banner).toHaveTextContent(/deliverability breaker/i);
    expect(banner).toHaveTextContent(/10\.0%/);
    expect(banner).toHaveTextContent(/will not resume on its own/i);
    // Manual resume stays available right above the banner.
    expect(screen.getByTestId('pause-resume-button')).toHaveTextContent('Resume');
  });

  it('saving the send-time-optimization toggle includes it in the PATCH', async () => {
    api.updateCampaign.mockResolvedValue({});
    renderPage();
    await screen.findByTestId('schedule-editor');
    fireEvent.click(screen.getByTestId('schedule-editor-toggle'));
    const toggle = screen.getByTestId('send-time-optimization-toggle');
    fireEvent.click(within(toggle).getByRole('checkbox'));
    fireEvent.click(screen.getByTestId('save-schedule-btn'));
    await waitFor(() => {
      expect(api.updateCampaign).toHaveBeenCalled();
      const payload = api.updateCampaign.mock.calls[0][1];
      expect(payload.send_time_optimization).toBe(true);
    });
  });
});
