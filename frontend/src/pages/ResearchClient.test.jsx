import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../api/researchClient.js', () => ({
  researchClient: vi.fn(),
}));

import * as api from '../api/researchClient.js';
import ResearchClient from './ResearchClient.jsx';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ResearchClient />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // Avoid bleed from a prior test.
  try { localStorage.removeItem('researchClient.senderName'); } catch { /* noop */ }
});


describe('ResearchClient page', () => {
  it('renders form fields with sensible defaults', () => {
    renderPage();
    expect(screen.getByTestId('input-linkedin-url')).toBeInTheDocument();
    expect(screen.getByTestId('input-goal')).toBeInTheDocument();
    // DM is the default output kind so the char limit should default to 300.
    expect(screen.getByTestId('input-char-limit')).toHaveValue(300);
    // Default depth is fast.
    expect(screen.getByTestId('mode-fast')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('mode-deep')).toHaveAttribute('aria-pressed', 'false');
  });

  it('swaps the default char limit when output kind toggles to email', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId('output-email'));
    expect(screen.getByTestId('input-char-limit')).toHaveValue(600);
    await user.click(screen.getByTestId('output-dm'));
    expect(screen.getByTestId('input-char-limit')).toHaveValue(300);
  });

  it('preserves a user-supplied char limit when output kind toggles', async () => {
    const user = userEvent.setup();
    renderPage();
    const charInput = screen.getByTestId('input-char-limit');
    await user.clear(charInput);
    await user.type(charInput, '450');
    await user.click(screen.getByTestId('output-email'));
    // User-edited value should NOT be overwritten by the email default.
    expect(charInput).toHaveValue(450);
  });

  it('submit is disabled until url + goal are filled', async () => {
    const user = userEvent.setup();
    renderPage();
    const btn = screen.getByTestId('submit-research');
    expect(btn).toBeDisabled();
    await user.type(screen.getByTestId('input-linkedin-url'), 'https://www.linkedin.com/in/jane/');
    expect(btn).toBeDisabled();
    await user.type(screen.getByTestId('input-goal'), 'Book a call');
    expect(btn).toBeEnabled();
  });

  it('submits the form and renders the composed message', async () => {
    const user = userEvent.setup();
    api.researchClient.mockResolvedValue({
      profile: {
        first_name: 'Jane', last_name: 'Doe',
        headline: 'Founder', company: 'Acme',
        company_website: 'acme.io', job_title: 'CEO',
        industry: 'SaaS', found: true, quality: 'rich',
      },
      research: {
        person_news: ['raised Series B'],
        company_news: ['shipped Acme Pro'],
        company_description: 'B2B platform',
        recent_updates: [], industry: 'SaaS',
      },
      subject: 'Congrats on the raise',
      body: 'Hi Jane, congrats on the raise.',
      char_count: 30,
      duration_ms: 8200,
    });

    renderPage();
    await user.type(screen.getByTestId('input-linkedin-url'), 'https://www.linkedin.com/in/jane-doe/');
    await user.type(screen.getByTestId('input-goal'), 'Book a discovery call');
    await user.click(screen.getByTestId('submit-research'));

    await waitFor(() => {
      expect(api.researchClient).toHaveBeenCalledTimes(1);
    });
    expect(api.researchClient.mock.calls[0][0]).toMatchObject({
      linkedin_url: 'https://www.linkedin.com/in/jane-doe/',
      goal: 'Book a discovery call',
      output_kind: 'linkedin_dm',
      research_mode: 'fast',
      char_limit: 300,
    });

    await waitFor(() => {
      expect(screen.getByTestId('result-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('result-body')).toHaveValue('Hi Jane, congrats on the raise.');
    // DM also surfaces a subject (thread topic) — same UI as email.
    expect(screen.getByTestId('result-subject')).toHaveValue('Congrats on the raise');
    expect(screen.getByTestId('char-count')).toHaveTextContent('30 / 300');
  });

  it('hides the subject input when the server returns an empty subject', async () => {
    const user = userEvent.setup();
    api.researchClient.mockResolvedValue({
      profile: {
        first_name: 'X', last_name: 'Y', headline: '',
        company: '', company_website: '', job_title: '',
        industry: '', found: false, quality: 'low',
      },
      research: { person_news: [], company_news: [], company_description: '' },
      subject: '',  // model couldn't produce one; UI should silently omit
      body: 'Hi.',
      char_count: 3,
      duration_ms: 1000,
    });

    renderPage();
    await user.type(screen.getByTestId('input-linkedin-url'), 'https://www.linkedin.com/in/x/');
    await user.type(screen.getByTestId('input-goal'), 'ping');
    await user.click(screen.getByTestId('submit-research'));

    await waitFor(() => {
      expect(screen.getByTestId('result-panel')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('result-subject')).not.toBeInTheDocument();
  });

  it('shows the email subject on the email output path', async () => {
    const user = userEvent.setup();
    api.researchClient.mockResolvedValue({
      profile: {
        first_name: 'Jane', last_name: '', headline: '',
        company: 'Acme', company_website: '', job_title: '',
        industry: '', found: true, quality: 'partial',
      },
      research: { person_news: [], company_news: [], company_description: '' },
      subject: 'Quick thought',
      body: 'Hi Jane.',
      char_count: 8,
      duration_ms: 1000,
    });

    renderPage();
    await user.click(screen.getByTestId('output-email'));
    await user.type(screen.getByTestId('input-linkedin-url'), 'https://www.linkedin.com/in/jane/');
    await user.type(screen.getByTestId('input-goal'), 'Book a call');
    await user.click(screen.getByTestId('submit-research'));

    await waitFor(() => {
      expect(screen.getByTestId('result-subject')).toHaveValue('Quick thought');
    });
  });

  it('surfaces backend error detail', async () => {
    const user = userEvent.setup();
    api.researchClient.mockRejectedValue({
      response: { data: { detail: 'URL must be a LinkedIn profile URL' } },
      message: 'Request failed with status code 400',
    });

    renderPage();
    await user.type(screen.getByTestId('input-linkedin-url'), 'https://example.com/');
    await user.type(screen.getByTestId('input-goal'), 'ping');
    await user.click(screen.getByTestId('submit-research'));

    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toHaveTextContent(/LinkedIn profile URL/);
    });
  });
});
