import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';

import IntegrationHealthBanner from './IntegrationHealthBanner.jsx';
import { api, INTEGRATION_PROVIDERS, makeIntegration, server } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const IP_ERROR = 'We have detected you are using an unrecognised IP address 72.78.98.32.';

function renderBanner() {
  return renderRoutes([<Route key="p" path="/" element={<IntegrationHealthBanner />} />]);
}

describe('IntegrationHealthBanner', () => {
  it('renders nothing while every integration is healthy', async () => {
    let fetched = false;
    server.use(http.get(api('/settings/integrations'), () => {
      fetched = true;
      return HttpResponse.json(INTEGRATION_PROVIDERS.map((p) => makeIntegration(p)));
    }));
    renderBanner();
    await waitFor(() => expect(fetched).toBe(true));
    expect(screen.queryByTestId('integration-health-banner')).toBeNull();
  });

  it('shows the provider, its reason and a link to Integrations when a key is rejected', async () => {
    server.use(http.get(api('/settings/integrations'), () => HttpResponse.json(
      INTEGRATION_PROVIDERS.map((p) => makeIntegration(p, p === 'brevo'
        ? { last_test_status: 'failed', last_test_error: IP_ERROR }
        : {})),
    )));
    renderBanner();
    const alert = await screen.findByTestId('integration-health-brevo');
    expect(alert).toHaveTextContent("Brevo isn't accepting this workspace's API key");
    expect(alert).toHaveTextContent(IP_ERROR);
    expect(alert).toHaveTextContent('Emails are on hold');
    expect(screen.getByRole('link', { name: 'Open Integrations' }))
      .toHaveAttribute('href', '/settings?tab=integrations');
    expect(screen.queryByTestId('integration-health-hunter')).toBeNull();
  });

  it('ignores a failed test on a provider that is not configured', async () => {
    let fetched = false;
    server.use(http.get(api('/settings/integrations'), () => {
      fetched = true;
      return HttpResponse.json([
        makeIntegration('unipile', { configured: false, last_test_status: 'failed' }),
      ]);
    }));
    renderBanner();
    await waitFor(() => expect(fetched).toBe(true));
    expect(screen.queryByTestId('integration-health-banner')).toBeNull();
  });
});
