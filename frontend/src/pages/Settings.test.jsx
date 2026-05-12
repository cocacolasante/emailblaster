import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings from './Settings.jsx';

vi.mock('../api/connectedAccounts.js', () => ({
  listAccounts: vi.fn(),
  getAccount: vi.fn(),
  createAccount: vi.fn(),
  updateAccount: vi.fn(),
  deleteAccount: vi.fn(),
  testAccount: vi.fn(),
  getAccountStatus: vi.fn(),
}));
vi.mock('../api/settings.js', () => ({
  getApiStatus: vi.fn(),
}));

import * as accountsApi from '../api/connectedAccounts.js';
import * as settingsApi from '../api/settings.js';

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Settings />
    </QueryClientProvider>
  );
}

const sampleAccount = {
  id: 'acc-1',
  label: 'Work Gmail',
  email_address: 'me@gmail.com',
  imap_host: 'imap.gmail.com',
  imap_port: 993,
  imap_use_ssl: true,
  username: 'me@gmail.com',
  last_tested_at: '2026-05-12T10:00:00Z',
  last_test_status: 'ok',
  last_test_error: null,
  last_polled_at: null,
  created_at: '2026-05-12T09:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  accountsApi.listAccounts.mockResolvedValue([]);
  settingsApi.getApiStatus.mockResolvedValue({
    anthropic: true,
    brevo: true,
    apollo: false,
    hunter: false,
  });
});

describe('Settings page tabs', () => {
  it('renders both tabs and starts on Connected inboxes', async () => {
    renderSettings();
    expect(screen.getByRole('tab', { name: /connected inboxes/i })).toHaveAttribute(
      'aria-selected', 'true'
    );
    expect(screen.getByRole('tab', { name: /api status/i })).toHaveAttribute(
      'aria-selected', 'false'
    );
    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    });
  });

  it('switches to API status tab and shows configured/unconfigured rows', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByRole('tab', { name: /api status/i }));

    await waitFor(() => {
      expect(screen.getByTestId('api-row-anthropic')).toBeInTheDocument();
    });
    expect(screen.getByTestId('api-row-anthropic')).toHaveTextContent(/configured/i);
    expect(screen.getByTestId('api-row-brevo')).toHaveTextContent(/configured/i);
    expect(screen.getByTestId('api-row-apollo')).toHaveTextContent(/not configured/i);
    expect(screen.getByTestId('api-row-hunter')).toHaveTextContent(/not configured/i);
  });

  it('marks optional integrations as optional', async () => {
    const user = userEvent.setup();
    renderSettings();
    await user.click(screen.getByRole('tab', { name: /api status/i }));
    await waitFor(() => screen.getByTestId('api-row-apollo'));
    expect(screen.getByTestId('api-row-apollo')).toHaveTextContent(/optional/i);
    expect(screen.getByTestId('api-row-hunter')).toHaveTextContent(/optional/i);
    expect(screen.getByTestId('api-row-anthropic')).not.toHaveTextContent(/optional/i);
  });
});

describe('Connected inboxes list', () => {
  it('renders account cards with status badge', async () => {
    accountsApi.listAccounts.mockResolvedValue([sampleAccount]);
    renderSettings();
    await waitFor(() => screen.getByTestId('account-card'));
    expect(screen.getByText('Work Gmail')).toBeInTheDocument();
    expect(screen.getByText(/me@gmail.com/)).toBeInTheDocument();
    expect(screen.getByTestId('status-badge')).toHaveAttribute('data-status', 'ok');
  });

  it('shows failure error when account is failed', async () => {
    accountsApi.listAccounts.mockResolvedValue([{
      ...sampleAccount,
      last_test_status: 'failed',
      last_test_error: 'authentication failed',
    }]);
    renderSettings();
    await waitFor(() => screen.getByTestId('account-card'));
    expect(screen.getByText(/authentication failed/i)).toBeInTheDocument();
    expect(screen.getByTestId('status-badge')).toHaveAttribute('data-status', 'failed');
  });

  it('opens the modal when "Connect inbox" is clicked', async () => {
    const user = userEvent.setup();
    renderSettings();
    await waitFor(() => screen.getByTestId('empty-state'));
    await user.click(screen.getByRole('button', { name: /connect inbox/i }));
    expect(await screen.findByRole('heading', { name: /connect inbox/i })).toBeInTheDocument();
  });

  it('calls test mutation when Test button clicked', async () => {
    accountsApi.listAccounts.mockResolvedValue([sampleAccount]);
    accountsApi.testAccount.mockResolvedValue({ ok: true, message_count: 5 });
    renderSettings();
    await screen.findByTestId('account-card');

    await act(async () => {
      fireEvent.click(screen.getByText('Test'));
      // Let the react-query mutation queue flush.
      await Promise.resolve();
      await Promise.resolve();
    });

    // React Query 5 passes a context object as the 2nd arg — just check the lead id.
    expect(accountsApi.testAccount).toHaveBeenCalled();
    expect(accountsApi.testAccount.mock.calls[0][0]).toBe('acc-1');
  });
});
