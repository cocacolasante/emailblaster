import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/crm.js', () => ({
  listOpportunities: vi.fn(),
  getOpportunity: vi.fn(),
  createOpportunity: vi.fn(),
  updateOpportunity: vi.fn(),
  deleteOpportunity: vi.fn(),
  getPipelineSummary: vi.fn(),
  listActivities: vi.fn(),
  createActivity: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

import * as api from '../api/crm.js';
import Opportunities from './Opportunities.jsx';
import { ToastProvider } from '../components/Toast.jsx';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider defaultDuration={0}>
        <MemoryRouter initialEntries={['/opportunities']}>
          <Routes>
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/opportunities/:id" element={<div data-testid="detail-page" />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const OPP = {
  id: 'o1', name: 'Acme — managed IT', stage: 'qualification',
  amount: 25000, close_date: '2026-07-15', probability: 25,
  description: null, closed_at: null, loss_reason: null,
  first_name: 'Jane', last_name: 'Doe', email: 'jane@acme.io',
  phone: null, company: 'Acme', job_title: 'CFO', linkedin_url: null,
  source_lead_id: 'l1', created_at: '2026-06-11T10:00:00Z',
  updated_at: '2026-06-11T10:00:00Z',
  activity_count: 1, open_task_count: 2,
};

function paged(items) {
  return { items, total: items.length, page: 1, page_size: 500, total_pages: 1 };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listOpportunities.mockResolvedValue(paged([OPP]));
  api.getPipelineSummary.mockResolvedValue([
    { stage: 'prospecting', count: 0, total_amount: 0 },
    { stage: 'qualification', count: 1, total_amount: 25000 },
    { stage: 'proposal', count: 0, total_amount: 0 },
    { stage: 'negotiation', count: 0, total_amount: 0 },
    { stage: 'closed_won', count: 0, total_amount: 0 },
    { stage: 'closed_lost', count: 0, total_amount: 0 },
  ]);
  api.listActivities.mockResolvedValue(paged([]));
});

describe('Opportunities page', () => {
  it('renders the pipeline board with the deal card in its stage column', async () => {
    renderPage();
    const col = await screen.findByTestId('stage-column-qualification');
    const card = within(col).getByTestId('opp-card-o1');
    expect(within(card).getByText('Acme — managed IT')).toBeInTheDocument();
    expect(within(card).getByText(/\$25,000/)).toBeInTheDocument();
    expect(within(card).getByText(/2 open tasks/)).toBeInTheDocument();
    // Stage header shows the pipeline roll-up.
    expect(within(col).getByText(/1 · \$25,000/)).toBeInTheDocument();
  });

  it('closed columns hidden by default; toggle shows them', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('pipeline-board');
    expect(screen.queryByTestId('stage-column-closed_won')).toBeNull();
    await user.click(screen.getByTestId('show-closed-toggle'));
    expect(screen.getByTestId('stage-column-closed_won')).toBeInTheDocument();
  });

  it('clicking a card navigates to the opportunity detail page', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('opp-card-o1'));
    // The card routes to /opportunities/:id — full record page, not a modal.
    expect(await screen.findByTestId('detail-page')).toBeInTheDocument();
  });

  it('creates a new opportunity via the modal', async () => {
    api.createOpportunity.mockResolvedValue({ ...OPP, id: 'o2', name: 'Fresh deal' });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('new-opportunity-btn'));
    await user.type(screen.getByTestId('new-opp-name'), 'Fresh deal');
    await user.type(screen.getByTestId('new-opp-amount'), '5000');
    await user.click(screen.getByTestId('new-opp-save'));
    await waitFor(() => {
      const payload = api.createOpportunity.mock.calls[0][0];
      expect(payload.name).toBe('Fresh deal');
      expect(payload.amount).toBe(5000);
    });
  });
});
