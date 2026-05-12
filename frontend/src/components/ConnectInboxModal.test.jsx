import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConnectInboxModal from './ConnectInboxModal.jsx';

vi.mock('../api/connectedAccounts.js', () => ({
  createAccount: vi.fn(),
  updateAccount: vi.fn(),
  testAccount: vi.fn(),
}));

import * as accountsApi from '../api/connectedAccounts.js';

beforeEach(() => {
  vi.clearAllMocks();
});

function renderModal(props = {}) {
  const onClose = props.onClose || vi.fn();
  const onSaved = props.onSaved || vi.fn();
  const utils = render(
    <ConnectInboxModal account={props.account ?? null} onClose={onClose} onSaved={onSaved} />
  );
  return { ...utils, onClose, onSaved };
}

describe('ConnectInboxModal', () => {
  it('renders empty form for new inbox', () => {
    renderModal();
    expect(screen.getByRole('heading', { name: /connect inbox/i })).toBeInTheDocument();
    const portInput = screen.getByLabelText(/^port$/i);
    expect(portInput).toHaveValue(993);
  });

  it('Gmail preset fills imap_host and port', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('button', { name: /use gmail preset/i }));
    expect(screen.getByLabelText(/imap host/i)).toHaveValue('imap.gmail.com');
    expect(screen.getByLabelText(/^port$/i)).toHaveValue(993);
  });

  it('Outlook preset fills imap_host to outlook.office365.com', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('button', { name: /use outlook preset/i }));
    expect(screen.getByLabelText(/imap host/i)).toHaveValue('outlook.office365.com');
  });

  it('Yahoo preset fills imap_host to imap.mail.yahoo.com', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('button', { name: /use yahoo preset/i }));
    expect(screen.getByLabelText(/imap host/i)).toHaveValue('imap.mail.yahoo.com');
  });

  it('auto-fills username from email address', async () => {
    const user = userEvent.setup();
    renderModal();
    const emailInput = screen.getByLabelText(/email address/i);
    await user.type(emailInput, 'me@example.com');
    expect(screen.getByLabelText(/username/i)).toHaveValue('me@example.com');
  });

  it('toggles password visibility', async () => {
    const user = userEvent.setup();
    renderModal();
    const passwordInput = screen.getByLabelText(/^password/i);
    expect(passwordInput).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: /show password/i }));
    expect(passwordInput).toHaveAttribute('type', 'text');
    await user.click(screen.getByRole('button', { name: /hide password/i }));
    expect(passwordInput).toHaveAttribute('type', 'password');
  });

  it('save calls createAccount for new inbox, then runs test', async () => {
    const user = userEvent.setup();
    accountsApi.createAccount.mockResolvedValue({ id: 'new-acc' });
    accountsApi.testAccount.mockResolvedValue({ ok: true, message_count: 7 });

    const { onSaved } = renderModal();
    await user.type(screen.getByLabelText(/label/i), 'Work Gmail');
    await user.type(screen.getByLabelText(/email address/i), 'me@example.com');
    await user.type(screen.getByLabelText(/imap host/i), 'imap.gmail.com');
    await user.type(screen.getByLabelText(/^password/i), 'app-pass');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(accountsApi.createAccount).toHaveBeenCalled();
    });
    const payload = accountsApi.createAccount.mock.calls[0][0];
    expect(payload.label).toBe('Work Gmail');
    expect(payload.email_address).toBe('me@example.com');
    expect(payload.password).toBe('app-pass');

    await waitFor(() => {
      expect(accountsApi.testAccount).toHaveBeenCalledWith('new-acc');
    });
    await waitFor(() => {
      expect(onSaved).toHaveBeenCalled();
    });
    expect(screen.getByTestId('test-result')).toHaveTextContent(/connection ok/i);
  });

  it('save calls updateAccount when editing and omits empty password', async () => {
    const user = userEvent.setup();
    accountsApi.updateAccount.mockResolvedValue({ id: 'existing-1' });
    accountsApi.testAccount.mockResolvedValue({ ok: true });

    renderModal({
      account: {
        id: 'existing-1',
        label: 'Existing',
        email_address: 'old@x.com',
        imap_host: 'imap.x.com',
        imap_port: 993,
        imap_use_ssl: true,
        username: 'old@x.com',
      },
    });
    // Don't touch password — should be omitted from payload.
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(accountsApi.updateAccount).toHaveBeenCalled();
    });
    const [id, payload] = accountsApi.updateAccount.mock.calls[0];
    expect(id).toBe('existing-1');
    expect(payload.password).toBeUndefined();
  });

  it('renders Gmail App Password helper link', () => {
    renderModal();
    const link = screen.getByRole('link', { name: /app password/i });
    expect(link).toHaveAttribute('href', 'https://myaccount.google.com/apppasswords');
  });

  it('shows error when save fails', async () => {
    const user = userEvent.setup();
    accountsApi.createAccount.mockRejectedValue({
      response: { data: { detail: 'Bad payload' } },
    });
    renderModal();
    await user.type(screen.getByLabelText(/label/i), 'x');
    await user.type(screen.getByLabelText(/email address/i), 'x@y.com');
    await user.type(screen.getByLabelText(/imap host/i), 'h');
    await user.type(screen.getByLabelText(/^password/i), 'p');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByTestId('modal-error')).toHaveTextContent(/bad payload/i);
    });
  });

  it('closes when overlay is clicked', async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.click(screen.getByTestId('modal-overlay'));
    expect(onClose).toHaveBeenCalled();
  });
});
