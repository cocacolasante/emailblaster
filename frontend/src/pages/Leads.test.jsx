import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/campaigns.js', () => ({
  listAllLeads: vi.fn(),
  listCampaigns: vi.fn(),
  getLeadDetail: vi.fn(),
  getLeadById: vi.fn(),
  updateLeadEmail: vi.fn(),
  ignoreLead: vi.fn(),
  unignoreLead: vi.fn(),
}));

import * as api from '../api/campaigns.js';
import Leads from './Leads.jsx';
import { ToastProvider } from '../components/Toast.jsx';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <Leads />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function leadsPayload(items, overrides = {}) {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 50,
    total_pages: 1,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listCampaigns.mockResolvedValue([
    { id: 'c1', name: 'Q2 outreach' },
    { id: 'c2', name: 'C-suite blast' },
  ]);
});

describe('Leads page', () => {
  it('renders leads with campaign + notes columns', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      {
        id: 'l1', campaign_id: 'c1', campaign_name: 'Q2 outreach',
        first_name: 'Ada', last_name: 'Lovelace',
        email: 'ada@example.com', company: 'Analytical',
        send_status: 'sent',
        has_notes: true, notes: 'Met at conference',
      },
      {
        id: 'l2', campaign_id: 'c2', campaign_name: 'C-suite blast',
        first_name: 'Grace', last_name: 'Hopper',
        email: 'grace@example.com', company: 'Navy',
        send_status: 'pending',
        has_notes: false, notes: null,
      },
    ]));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('ada@example.com')).toBeInTheDocument();
      expect(screen.getByText('grace@example.com')).toBeInTheDocument();
    });
    expect(screen.getByText('Met at conference')).toBeInTheDocument();
    expect(screen.getAllByText('Q2 outreach').length).toBeGreaterThan(0);
  });

  it('filters by campaign and has-notes toggle', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([]));
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(api.listAllLeads).toHaveBeenCalled());
    // Wait for the campaigns dropdown to hydrate with options.
    await screen.findByRole('option', { name: 'C-suite blast' });

    await user.selectOptions(screen.getByLabelText(/Filter by campaign/i), 'c2');
    await user.click(screen.getByLabelText(/Has notes/i));

    await waitFor(() => {
      const lastCall = api.listAllLeads.mock.calls.at(-1)[0];
      expect(lastCall.campaign_id).toBe('c2');
      expect(lastCall.has_notes).toBe(true);
    });
  });

  it('passes search text through to the API', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([]));
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText(/Search leads/i), 'ada');

    await waitFor(() => {
      const lastCall = api.listAllLeads.mock.calls.at(-1)[0];
      expect(lastCall.search).toBe('ada');
    });
  });

  it('opens a modal on row click and saves notes via updateLeadEmail', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      {
        id: 'l1', campaign_id: 'c1', campaign_name: 'Q2 outreach',
        first_name: 'Ada', last_name: 'Lovelace',
        email: 'ada@example.com', company: 'Analytical',
        send_status: 'sent', has_notes: false, notes: null,
      },
    ]));
    // New rich-detail endpoint is the primary data source for the modal.
    api.getLeadById.mockResolvedValue({
      id: 'l1', campaign_id: 'c1',
      composed_subject: 'Hello Ada',
      composed_body: 'Saw your work on the engine.',
      notes: '',
      history: [],
      history_counts: {},
      research_summary: {},
    });
    api.updateLeadEmail.mockResolvedValue({ id: 'l1' });

    const user = userEvent.setup();
    renderPage();

    const row = await screen.findByTestId('lead-row-l1');
    await user.click(row);

    const modal = await screen.findByTestId('lead-crm-modal');
    expect(within(modal).getByText('Hello Ada')).toBeInTheDocument();

    const textarea = await screen.findByTestId('lead-notes-textarea');
    await user.type(textarea, 'Followed up via LinkedIn');

    await user.click(screen.getByTestId('save-notes-btn'));

    await waitFor(() => {
      expect(api.updateLeadEmail).toHaveBeenCalledWith(
        'c1', 'l1', { notes: 'Followed up via LinkedIn' },
      );
    });
  });

  it('renders LinkedIn link + contact-info section from the rich detail endpoint', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l2', campaign_id: 'c1', campaign_name: 'Q2', first_name: 'J',
        last_name: 'Doe', email: 'j@x.com', company: 'Acme',
        send_status: 'pending', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l2', campaign_id: 'c1',
      first_name: 'J', last_name: 'Doe',
      email: 'j@x.com',
      phone: '+1-555-0100',
      linkedin_url: 'https://www.linkedin.com/in/j-doe/',
      linkedin_connection_status: 'connected',
      company_website: 'acme.io',
      job_title: 'CFO',
      company: 'Acme',
      notes: '', history: [], history_counts: {},
      research_summary: { industry: 'SaaS', size_hint: 'growth' },
    });

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l2'));

    const modal = await screen.findByTestId('lead-crm-modal');
    // LinkedIn link points at the profile URL.
    const liLink = await within(modal).findByTestId('lead-linkedin-link');
    expect(liLink).toHaveAttribute('href', 'https://www.linkedin.com/in/j-doe/');
    expect(liLink).toHaveAttribute('target', '_blank');
    // Connection state shows up next to the link.
    expect(within(modal).getByText(/Connected/)).toBeInTheDocument();
    // Contact info card lists phone + website + industry/size.
    const contact = within(modal).getByTestId('lead-contact-section');
    expect(within(contact).getByText('+1-555-0100')).toBeInTheDocument();
    expect(within(contact).getByText(/acme\.io/)).toBeInTheDocument();
    expect(within(contact).getByText('SaaS')).toBeInTheDocument();
    expect(within(contact).getByText('growth')).toBeInTheDocument();
  });

  it('renders the activity history list with one row per event', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l3', campaign_id: 'c1', campaign_name: 'Q2', first_name: 'B',
        last_name: 'X', email: 'b@x.com', company: 'X',
        send_status: 'sent', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l3', campaign_id: 'c1',
      email: 'b@x.com',
      notes: '',
      history: [
        { at: '2026-06-09T14:00:00Z', kind: 'event', action: 'Email opened',
          status: 'success', icon: '👀', detail: null, external_id: null },
        { at: '2026-06-09T13:00:00Z', kind: 'execution', action: 'Sent email',
          status: 'success', icon: '📧', detail: null, external_id: 'brevo-1' },
        { at: '2026-06-08T10:00:00Z', kind: 'execution',
          action: 'Sent LinkedIn connection request',
          status: 'success', icon: '🤝', detail: null, external_id: 'inv-9' },
      ],
      history_counts: { email: 1, opened: 1, linkedin_connect: 1 },
      research_summary: {},
    });

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l3'));

    const modal = await screen.findByTestId('lead-crm-modal');
    const list = within(modal).getByTestId('lead-history-list');
    // Each action label appears in the list.
    expect(within(list).getByText('Email opened')).toBeInTheDocument();
    expect(within(list).getByText('Sent email')).toBeInTheDocument();
    expect(within(list).getByText('Sent LinkedIn connection request')).toBeInTheDocument();
    // Header roll-up pill for opens.
    const pills = within(modal).getByTestId('lead-stat-pills');
    expect(within(pills).getByText(/1 open/)).toBeInTheDocument();
  });

  it('Ignore button calls ignoreLead after user confirms', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l5', campaign_id: 'c1', campaign_name: 'Q2', first_name: 'I',
        last_name: 'G', email: 'ig@x.com', company: 'X',
        send_status: 'pending', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l5', campaign_id: 'c1', email: 'ig@x.com',
      notes: '', history: [], history_counts: {}, research_summary: {},
      is_suppressed: false, suppression_reason: null,
    });
    api.ignoreLead.mockResolvedValue({
      suppressed: true, already_suppressed: false,
      leads_halted: 1, campaigns_affected: ['c1'],
    });

    // Auto-confirm the window.confirm dialog.
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l5'));

    const modal = await screen.findByTestId('lead-crm-modal');
    const ignoreBtn = await within(modal).findByTestId('lead-ignore-btn');
    await user.click(ignoreBtn);

    await waitFor(() => {
      expect(api.ignoreLead).toHaveBeenCalledWith('l5');
    });
  });

  it('Ignore button does NOT call API when user cancels the confirm', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l6', campaign_id: 'c1', campaign_name: 'Q2', email: 'no@x.com',
        send_status: 'pending', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l6', campaign_id: 'c1', email: 'no@x.com',
      notes: '', history: [], history_counts: {}, research_summary: {},
      is_suppressed: false,
    });
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l6'));
    const modal = await screen.findByTestId('lead-crm-modal');
    await user.click(await within(modal).findByTestId('lead-ignore-btn'));

    expect(api.ignoreLead).not.toHaveBeenCalled();
  });

  it('Suppressed lead shows the badge + swaps Ignore button for Un-ignore', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l7', campaign_id: 'c1', campaign_name: 'Q2', email: 'sup@x.com',
        send_status: 'pending', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l7', campaign_id: 'c1', email: 'sup@x.com',
      notes: '', history: [], history_counts: {}, research_summary: {},
      is_suppressed: true, suppression_reason: 'manual',
    });
    api.unignoreLead.mockResolvedValue();
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l7'));
    const modal = await screen.findByTestId('lead-crm-modal');

    // Badge present.
    expect(await within(modal).findByTestId('lead-suppressed-badge')).toBeInTheDocument();
    // Ignore button is gone; Un-ignore button is shown.
    expect(within(modal).queryByTestId('lead-ignore-btn')).toBeNull();
    const unignoreBtn = within(modal).getByTestId('lead-unignore-btn');
    expect(unignoreBtn).toBeInTheDocument();

    // Clicking it calls unignoreLead.
    await user.click(unignoreBtn);
    await waitFor(() => expect(api.unignoreLead).toHaveBeenCalledWith('l7'));
  });

  it('history section shows an empty hint when the timeline is empty', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([
      { id: 'l4', campaign_id: 'c1', campaign_name: 'Q2', first_name: 'E',
        last_name: 'M', email: 'e@x.com', company: 'X',
        send_status: 'pending', has_notes: false, notes: null },
    ]));
    api.getLeadById.mockResolvedValue({
      id: 'l4', campaign_id: 'c1', email: 'e@x.com',
      notes: '', history: [], history_counts: {}, research_summary: {},
    });

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('lead-row-l4'));
    const modal = await screen.findByTestId('lead-crm-modal');
    expect(within(modal).getByText(/hasn't moved through any sequence/i)).toBeInTheDocument();
  });

  it('shows an empty-state row when no leads come back', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([]));
    renderPage();
    await waitFor(() => expect(screen.getByText(/No leads\./i)).toBeInTheDocument());
  });
});
