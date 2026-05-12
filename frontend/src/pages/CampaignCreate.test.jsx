import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CampaignCreate from './CampaignCreate.jsx';

vi.mock('../api/connectedAccounts.js', () => ({
  listAccounts: vi.fn(),
  createAccount: vi.fn(),
  updateAccount: vi.fn(),
  deleteAccount: vi.fn(),
  testAccount: vi.fn(),
}));
vi.mock('../api/campaigns.js', () => ({
  createCampaign: vi.fn(),
  getPreview: vi.fn(),
  getPreviewProgress: vi.fn(),
  uploadLeadsPreview: vi.fn(),
  confirmLeadsUpload: vi.fn(),
}));

import * as accountsApi from '../api/connectedAccounts.js';
import * as campaignsApi from '../api/campaigns.js';

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CampaignCreate />
      </MemoryRouter>
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
  last_test_status: 'ok',
};

beforeEach(() => {
  vi.clearAllMocks();
  accountsApi.listAccounts.mockResolvedValue([]);
  campaignsApi.getPreview.mockResolvedValue({ samples: [], all_ready: false });
  campaignsApi.getPreviewProgress.mockResolvedValue({
    total_leads: 0, researched: 0, composed: 0, sent: 0, failed: 0,
  });
});


describe('Step 1 — campaign details', () => {
  it('renders the form with default tone, sample count, and research mode', async () => {
    renderPage();
    await screen.findByText(/campaign details/i);
    expect(screen.getByLabelText(/^name$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^goal$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^tone$/i)).toHaveValue('Professional');
    expect(screen.getByLabelText(/sample count/i)).toHaveValue(5);
    expect(screen.getByRole('radio', { name: /^fast/i })).toHaveAttribute('aria-checked', 'true');
  });

  it('shows "no inboxes" prompt when no accounts exist', async () => {
    renderPage();
    expect(await screen.findByTestId('no-inbox-prompt')).toBeInTheDocument();
  });

  it('shows inbox dropdown when accounts exist', async () => {
    accountsApi.listAccounts.mockResolvedValue([sampleAccount]);
    renderPage();
    const dropdown = await screen.findByLabelText(/reply tracking inbox/i);
    expect(dropdown).toBeInTheDocument();
    // First option is "No reply tracking", then the account
    expect(dropdown).toHaveValue('');
    expect(screen.getByText(/work gmail/i)).toBeInTheDocument();
  });

  it('shows inbox status badge when an account is selected', async () => {
    accountsApi.listAccounts.mockResolvedValue([sampleAccount]);
    renderPage();
    const dropdown = await screen.findByLabelText(/reply tracking inbox/i);
    fireEvent.change(dropdown, { target: { value: 'acc-1' } });
    await waitFor(() => {
      expect(screen.getByTestId('inbox-status')).toHaveTextContent(/connected/i);
    });
  });

  it('switches research mode to Deep when clicked', async () => {
    renderPage();
    await screen.findByText(/campaign details/i);
    fireEvent.click(screen.getByRole('radio', { name: /^deep/i }));
    expect(screen.getByRole('radio', { name: /^deep/i })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /^fast/i })).toHaveAttribute('aria-checked', 'false');
  });
});


describe('Submitting step 1', () => {
  async function fillForm(user) {
    await user.type(screen.getByLabelText(/^name$/i), 'Spring outreach');
    await user.type(screen.getByLabelText(/^goal$/i), 'Book a discovery call');
    await user.type(screen.getByLabelText(/sender name/i), 'Anthony');
    await user.type(screen.getByLabelText(/sender email/i), 'me@example.com');
  }

  it('createCampaign is called with form data and step advances to 2', async () => {
    const user = userEvent.setup();
    campaignsApi.createCampaign.mockResolvedValue({ id: 'new-campaign-1' });
    renderPage();
    await screen.findByText(/campaign details/i);

    await fillForm(user);
    fireEvent.click(screen.getByTestId('step1-submit'));

    await waitFor(() => expect(campaignsApi.createCampaign).toHaveBeenCalled());
    const payload = campaignsApi.createCampaign.mock.calls[0][0];
    expect(payload.name).toBe('Spring outreach');
    expect(payload.goal).toBe('Book a discovery call');
    expect(payload.sender_email).toBe('me@example.com');
    expect(payload.research_mode).toBe('fast');
    expect(payload.schedule_time_start).toBe('09:00:00');
    expect(payload.schedule_time_end).toBe('17:00:00');
    expect(payload.connected_account_id).toBeNull();

    // Step 2 mounts the LeadUpload component
    await waitFor(() => expect(screen.getByTestId('lead-upload')).toBeInTheDocument());
  });

  it('shows error on create failure', async () => {
    const user = userEvent.setup();
    campaignsApi.createCampaign.mockRejectedValue({
      response: { data: { detail: 'time order' } },
    });
    renderPage();
    await screen.findByText(/campaign details/i);
    await fillForm(user);
    fireEvent.click(screen.getByTestId('step1-submit'));

    expect(await screen.findByTestId('step1-error')).toHaveTextContent(/time order/i);
  });
});


describe('Step 3 — progress polling', () => {
  it('renders progress UI and shows composed/researched counts', async () => {
    const user = userEvent.setup();
    campaignsApi.createCampaign.mockResolvedValue({ id: 'c1' });
    campaignsApi.uploadLeadsPreview.mockResolvedValue({
      columns: ['Email'], preview_rows: [{ Email: 'a@x.com' }],
      suggested_mapping: { Email: 'email' }, total_rows: 1,
    });
    campaignsApi.confirmLeadsUpload.mockResolvedValue({
      total: 1, suppressed: 0, duplicates_removed: 0, samples_selected: 1,
    });
    campaignsApi.getPreviewProgress.mockResolvedValue({
      total_leads: 10, researched: 4, composed: 2, sent: 0, failed: 0,
    });

    renderPage();
    await screen.findByText(/campaign details/i);
    await user.type(screen.getByLabelText(/^name$/i), 'x');
    await user.type(screen.getByLabelText(/^goal$/i), 'g');
    await user.type(screen.getByLabelText(/sender name/i), 's');
    await user.type(screen.getByLabelText(/sender email/i), 's@x.com');
    fireEvent.click(screen.getByTestId('step1-submit'));

    // Step 2: upload
    await screen.findByTestId('lead-upload');
    const file = new File(['Email\na@x.com\n'], 'leads.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file] } });
    await screen.findByTestId('mapping-table');
    fireEvent.click(screen.getByRole('button', { name: /import 1 leads/i }));

    // Step 3: progress
    await screen.findByTestId('step3');
    await waitFor(() => {
      expect(screen.getByText(/2 of 10 composed/i)).toBeInTheDocument();
    });
    // Bar width reflects composed/total
    const bar = screen.getByTestId('progress-bar');
    expect(bar.style.width).toBe('20%');
  });
});
