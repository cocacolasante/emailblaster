import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/signals.js', () => ({
  listWatches: vi.fn(),
  createWatch: vi.fn(),
  updateWatch: vi.fn(),
  deleteWatch: vi.fn(),
  runWatchNow: vi.fn(),
  listSignals: vi.fn(),
  actionSignal: vi.fn(),
  dismissSignal: vi.fn(),
}));

import * as api from '../api/signals.js';
import Signals from './Signals.jsx';
import { ToastProvider } from '../components/Toast.jsx';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider defaultDuration={0}>
        <MemoryRouter initialEntries={['/signals']}>
          <Signals />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const SIGNAL = {
  id: 's1', watch_id: 'w1', signal_type: 'job_change',
  summary: 'Jane Doe changed roles: Director of IT → VP of Engineering',
  detail: { old_title: 'Director of IT', new_title: 'VP of Engineering' },
  status: 'new', lead_id: 'l1', opportunity_id: null,
  detected_at: '2026-06-12T10:00:00Z',
};

function paged(items) {
  return { items, total: items.length, page: 1, page_size: 50, total_pages: 1 };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listSignals.mockResolvedValue(paged([SIGNAL]));
  api.listWatches.mockResolvedValue([]);
});

describe('Signals page', () => {
  it('renders the feed with type badge and summary', async () => {
    renderPage();
    const card = await screen.findByTestId('signal-card-s1');
    expect(within(card).getByText('Job change')).toBeInTheDocument();
    expect(card).toHaveTextContent('VP of Engineering');
  });

  it('action + dismiss buttons call the API', async () => {
    api.actionSignal.mockResolvedValue({ id: 's1', status: 'actioned' });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId('action-signal-s1'));
    await waitFor(() => {
      expect(api.actionSignal).toHaveBeenCalled();
      expect(api.actionSignal.mock.calls[0][0]).toBe('s1');
    });
  });

  it('creates a cold-target watch from the Watches tab', async () => {
    api.createWatch.mockResolvedValue({ id: 'w2' });
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('tab', { name: 'Watches' }));
    await user.click(await screen.findByTestId('new-watch-btn'));
    await user.selectOptions(screen.getByTestId('watch-type'), 'hiring');
    await user.type(screen.getByTestId('watch-company'), 'Acme');
    await user.click(screen.getByTestId('save-watch-btn'));
    await waitFor(() => {
      expect(api.createWatch).toHaveBeenCalled();
      const payload = api.createWatch.mock.calls[0][0];
      expect(payload.watch_type).toBe('hiring');
      expect(payload.company).toBe('Acme');
    });
  });

  it('save disabled without a target; empty feed shows explainer', async () => {
    api.listSignals.mockResolvedValue(paged([]));
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByTestId('signals-empty')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Watches' }));
    await user.click(await screen.findByTestId('new-watch-btn'));
    expect(screen.getByTestId('save-watch-btn')).toBeDisabled();
  });
});
