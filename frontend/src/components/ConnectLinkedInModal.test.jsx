import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConnectLinkedInModal from './ConnectLinkedInModal.jsx';

vi.mock('../api/linkedinAccounts.js', () => ({
  createLinkedInAccount: vi.fn(),
  updateLinkedInAccount: vi.fn(),
  testLinkedInAccount: vi.fn(),
  resolveLinkedInChallenge: vi.fn(),
}));

import * as liApi from '../api/linkedinAccounts.js';

const ACCOUNT = {
  id: 'acc-1',
  label: 'My LinkedIn',
  linkedin_email: 'me@example.com',
  proxy_url: 'http://proxy:8080',
  status: 'ok',
  pending_challenge_url: null,
};

function renderNew(props = {}) {
  return render(<ConnectLinkedInModal onClose={vi.fn()} onSaved={vi.fn()} {...props} />);
}

function renderEdit(overrides = {}) {
  return render(
    <ConnectLinkedInModal
      account={{ ...ACCOUNT, ...overrides }}
      onClose={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ConnectLinkedInModal', () => {
  it('renders "Connect LinkedIn account" when no account prop', () => {
    renderNew();
    expect(screen.getByRole('heading', { name: /connect linkedin account/i })).toBeInTheDocument();
  });

  it('renders "Edit LinkedIn account" when an account prop is passed', () => {
    renderEdit();
    expect(screen.getByRole('heading', { name: /edit linkedin account/i })).toBeInTheDocument();
  });

  it('pre-fills form fields from account prop; password is blank', () => {
    renderEdit();
    expect(screen.getByLabelText(/label/i)).toHaveValue(ACCOUNT.label);
    expect(screen.getByLabelText(/linkedin login email/i)).toHaveValue(ACCOUNT.linkedin_email);
    expect(screen.getByLabelText(/proxy url/i)).toHaveValue(ACCOUNT.proxy_url);
    expect(screen.getByLabelText(/^password/i)).toHaveValue('');
  });

  it('save (new account) calls createLinkedInAccount then testLinkedInAccount', async () => {
    const user = userEvent.setup();
    const created = { id: 'new-acc-1' };
    liApi.createLinkedInAccount.mockResolvedValue(created);
    liApi.testLinkedInAccount.mockResolvedValue({ ok: true });

    renderNew();

    await user.type(screen.getByLabelText(/label/i), 'Work LI');
    await user.type(screen.getByLabelText(/linkedin login email/i), 'test@li.com');
    await user.type(screen.getByLabelText(/^password/i), 'mypassword');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(liApi.createLinkedInAccount).toHaveBeenCalled();
    });
    const payload = liApi.createLinkedInAccount.mock.calls[0][0];
    expect(payload.label).toBe('Work LI');
    expect(payload.linkedin_email).toBe('test@li.com');
    expect(payload.password).toBe('mypassword');

    await waitFor(() => {
      expect(liApi.testLinkedInAccount).toHaveBeenCalledWith('new-acc-1');
    });
  });

  it('save (edit) calls updateLinkedInAccount then testLinkedInAccount', async () => {
    const user = userEvent.setup();
    const updated = { id: ACCOUNT.id };
    liApi.updateLinkedInAccount.mockResolvedValue(updated);
    liApi.testLinkedInAccount.mockResolvedValue({ ok: true });

    renderEdit();
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(liApi.updateLinkedInAccount).toHaveBeenCalled();
    });
    const [id] = liApi.updateLinkedInAccount.mock.calls[0];
    expect(id).toBe(ACCOUNT.id);

    await waitFor(() => {
      expect(liApi.testLinkedInAccount).toHaveBeenCalledWith(ACCOUNT.id);
    });
  });

  it('save error shows error message in modal-error', async () => {
    const user = userEvent.setup();
    liApi.createLinkedInAccount.mockRejectedValue({
      response: { data: { detail: 'Email already taken' } },
    });

    renderNew();
    await user.type(screen.getByLabelText(/label/i), 'x');
    await user.type(screen.getByLabelText(/linkedin login email/i), 'x@li.com');
    await user.type(screen.getByLabelText(/^password/i), 'pass');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByTestId('modal-error')).toHaveTextContent('Email already taken');
    });
  });

  it('Test connection button is disabled in new mode', () => {
    renderNew();
    const testBtn = screen.getByRole('button', { name: /test connection/i });
    expect(testBtn).toBeDisabled();
  });

  it('Test connection button is enabled in edit mode', () => {
    renderEdit();
    const testBtn = screen.getByRole('button', { name: /test connection/i });
    expect(testBtn).not.toBeDisabled();
  });

  it('Test connection calls testLinkedInAccount with account.id', async () => {
    const user = userEvent.setup();
    liApi.testLinkedInAccount.mockResolvedValue({ ok: true });

    renderEdit();
    await user.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(liApi.testLinkedInAccount).toHaveBeenCalledWith(ACCOUNT.id);
    });
  });

  it('challenge UI shows when testResult has status="challenged"', async () => {
    const user = userEvent.setup();
    const created = { id: 'new-acc-2' };
    liApi.createLinkedInAccount.mockResolvedValue(created);
    liApi.testLinkedInAccount.mockResolvedValue({
      ok: false,
      status: 'challenged',
      challenge_url: 'https://example.com/challenge',
    });

    renderNew();
    await user.type(screen.getByLabelText(/label/i), 'LI');
    await user.type(screen.getByLabelText(/linkedin login email/i), 'li@example.com');
    await user.type(screen.getByLabelText(/^password/i), 'pw');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByText(/linkedin wants a security check/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: 'https://example.com/challenge' })).toHaveAttribute(
      'href',
      'https://example.com/challenge',
    );
  });

  it('password show/hide toggle works', async () => {
    const user = userEvent.setup();
    renderNew();

    const passwordInput = screen.getByLabelText(/^password/i);
    expect(passwordInput).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: /show password/i }));
    expect(passwordInput).toHaveAttribute('type', 'text');

    await user.click(screen.getByRole('button', { name: /hide password/i }));
    expect(passwordInput).toHaveAttribute('type', 'password');
  });

  it('close button (×) calls onClose', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderNew({ onClose });

    await user.click(screen.getByRole('button', { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('overlay click calls onClose', () => {
    const onClose = vi.fn();
    renderNew({ onClose });

    fireEvent.click(screen.getByTestId('modal-overlay'));
    expect(onClose).toHaveBeenCalled();
  });
});
