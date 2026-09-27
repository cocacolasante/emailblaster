import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';

import { server, api } from '../test/server.js';
import { OwnerFilter, OwnerPicker, ScopeToggle } from './OwnerPicker.jsx';
import { OwnerAvatar, initialsFor, withOwnerNames } from './OwnerAvatar.jsx';

const MEMBERS = [
  { user_id: 'user-1', email: 'ada@example.com', name: 'Ada Lovelace', display_name: 'Ada Lovelace', role: 'owner', joined_at: null },
  { user_id: 'user-2', email: 'grace@example.com', name: 'Grace Hopper', display_name: 'Grace Hopper', role: 'member', joined_at: null },
];

function renderWithClient(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  server.use(http.get(api('/team/members'), () => HttpResponse.json(MEMBERS)));
});

describe('OwnerPicker', () => {
  it('lists Unassigned, a Me shortcut, and every member with email', async () => {
    renderWithClient(<OwnerPicker value={null} onChange={() => {}} />);
    const select = screen.getByTestId('owner-picker');
    await within(select).findByRole('option', { name: /Grace Hopper — grace@example.com/ });
    const labels = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(labels[0]).toBe('Unassigned');
    expect(labels[1]).toBe('Me');
    expect(labels).toContain('Ada Lovelace — ada@example.com (you)');
    // Labelled for assistive tech.
    expect(screen.getByLabelText('Owner')).toBe(select);
  });

  it('maps Me → my user id, Unassigned → null, a member → their id', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<OwnerPicker value="user-2" onChange={onChange} />);
    const select = screen.getByTestId('owner-picker');
    await within(select).findByRole('option', { name: 'Me' });

    await user.selectOptions(select, 'Me');
    expect(onChange).toHaveBeenLastCalledWith('user-1');

    await user.selectOptions(select, 'Unassigned');
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('hides Unassigned when allowUnassigned=false and honours disabled', async () => {
    renderWithClient(<OwnerPicker value="user-1" onChange={() => {}} allowUnassigned={false} disabled />);
    const select = screen.getByTestId('owner-picker');
    await within(select).findByRole('option', { name: /Grace Hopper/ });
    expect(within(select).queryByRole('option', { name: 'Unassigned' })).toBeNull();
    // Already me → no redundant Me shortcut.
    expect(within(select).queryByRole('option', { name: 'Me' })).toBeNull();
    expect(select).toBeDisabled();
  });

  it('keeps a removed owner visible as "Former member"', async () => {
    renderWithClient(<OwnerPicker value="user-gone" onChange={() => {}} />);
    const select = screen.getByTestId('owner-picker');
    await within(select).findByRole('option', { name: 'Former member' });
    expect(select).toHaveValue('user-gone');
  });
});

describe('OwnerFilter + ScopeToggle', () => {
  it('OwnerFilter emits the ?owner= wire values', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<OwnerFilter value="" onChange={onChange} />);
    const select = screen.getByTestId('owner-filter');
    await within(select).findByRole('option', { name: 'Grace Hopper' });
    await user.selectOptions(select, 'Me');
    expect(onChange).toHaveBeenLastCalledWith('me');
    await user.selectOptions(select, 'Unassigned');
    expect(onChange).toHaveBeenLastCalledWith('unassigned');
    await user.selectOptions(select, 'Grace Hopper');
    expect(onChange).toHaveBeenLastCalledWith('user-2');
  });

  it('ScopeToggle reports mine/everyone with aria-pressed', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ScopeToggle mine={false} onChange={onChange} />);
    expect(screen.getByTestId('scope-toggle-all')).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByTestId('scope-toggle-mine'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('OwnerAvatar', () => {
  it('renders member initials + name, Unassigned, and Former member states', async () => {
    renderWithClient(
      <>
        <OwnerAvatar ownerId="user-2" testId="a-member" />
        <OwnerAvatar ownerId={null} testId="a-none" />
        <OwnerAvatar ownerId="user-gone" testId="a-former" />
        <OwnerAvatar ownerId="user-2" compact testId="a-compact" />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId('a-member')).toHaveTextContent('Grace Hopper'));
    expect(screen.getByTestId('a-member')).toHaveTextContent('GH');
    expect(screen.getByTestId('a-none')).toHaveTextContent('Unassigned');
    expect(screen.getByTestId('a-former')).toHaveTextContent('Former member');
    expect(screen.getByTestId('a-compact')).toHaveAttribute('aria-label', 'Owner: Grace Hopper');
  });

  it('initialsFor + withOwnerNames helpers', () => {
    expect(initialsFor('Grace Hopper')).toBe('GH');
    expect(initialsFor('sam@x.io')).toBe('SA');
    const cols = [{ key: 'owner', type: 'owner' }, { key: 'count', type: 'number' }];
    const rows = [{ owner: 'user-2', count: 3 }];
    expect(withOwnerNames(cols, rows, (id) => `name:${id}`)).toEqual([{ owner: 'name:user-2', count: 3 }]);
    expect(withOwnerNames([{ key: 'count', type: 'number' }], rows, () => 'x')).toBe(rows);
  });
});
