import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/campaigns.js', () => ({
  listAllLeads: vi.fn(),
  listCampaigns: vi.fn(),
  getLeadDetail: vi.fn(),
  updateLeadEmail: vi.fn(),
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
    api.getLeadDetail.mockResolvedValue({
      id: 'l1', campaign_id: 'c1',
      composed_subject: 'Hello Ada',
      composed_body: 'Saw your work on the engine.',
      notes: '',
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

  it('shows an empty-state row when no leads come back', async () => {
    api.listAllLeads.mockResolvedValue(leadsPayload([]));
    renderPage();
    await waitFor(() => expect(screen.getByText(/No leads\./i)).toBeInTheDocument());
  });
});
