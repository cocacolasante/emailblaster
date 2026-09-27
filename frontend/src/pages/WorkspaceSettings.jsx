import { Fragment, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createInvite,
  listInvites,
  removeMember,
  revokeInvite,
  updateMemberRole,
  updateWorkspace,
} from '../api/team.js';
import { errorDetail } from '../api/auth.js';
import { AUTH_ME_KEY, useAuth } from '../hooks/useAuth.js';
import { TEAM_MEMBERS_KEY, useMembers } from '../hooks/useMembers.js';
import { useToast } from '../components/Toast.jsx';
import { Badge, Button, Card, Field, Input, Select } from '../components/ui.jsx';
import { formatDate } from '../utils/format.js';

const TEAM_INVITES_KEY = ['team-invites'];

const ROLE_LABEL = { owner: 'Owner', admin: 'Admin', member: 'Member' };
const ROLE_BADGE = { owner: 'info', admin: 'success', member: 'neutral' };

function InlineError({ children, testId }) {
  if (!children) return null;
  return (
    <p role="alert" data-testid={testId} className="text-xs text-danger-600 mt-2 mb-0">
      {children}
    </p>
  );
}

function SectionTitle({ title, subtitle }) {
  return (
    <div className="mb-3">
      <h2 className="text-base font-semibold text-slate-900 m-0">{title}</h2>
      {subtitle && <p className="text-xs text-slate-500 mt-0.5 mb-0">{subtitle}</p>}
    </div>
  );
}

// --------------------------------------------------------------------------
// Workspace name
// --------------------------------------------------------------------------

function WorkspaceNameCard() {
  const { workspace, isManager } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(workspace?.name || '');

  useEffect(() => { setName(workspace?.name || ''); }, [workspace?.name]);

  const mutation = useMutation({
    mutationFn: updateWorkspace,
    onSuccess: (ws) => {
      queryClient.setQueryData(AUTH_ME_KEY, (me) => {
        if (!me) return me;
        return {
          ...me,
          workspace: { ...me.workspace, ...ws },
          memberships: (me.memberships || []).map((m) => (
            m.tenant_id === ws.id ? { ...m, tenant_name: ws.name } : m
          )),
        };
      });
      toast.success('Workspace renamed');
    },
  });

  const trimmed = name.trim();
  const dirty = trimmed && trimmed !== workspace?.name;

  return (
    <Card className="p-5" data-testid="workspace-name-card">
      <SectionTitle title="Workspace" subtitle={isManager ? 'The name your team sees across the app.' : undefined} />
      {isManager ? (
        <form
          className="flex items-end gap-3"
          onSubmit={(e) => { e.preventDefault(); if (dirty) mutation.mutate({ name: trimmed }); }}
        >
          <Field label="Workspace name" className="flex-1">
            {(p) => (
              <Input {...p} value={name} onChange={(e) => setName(e.target.value)} data-testid="workspace-name-input" />
            )}
          </Field>
          <Button type="submit" disabled={!dirty} loading={mutation.isPending} data-testid="workspace-name-save">
            Save
          </Button>
        </form>
      ) : (
        <div className="text-sm text-slate-800" data-testid="workspace-name-readonly">{workspace?.name}</div>
      )}
      <InlineError testId="workspace-name-error">
        {mutation.isError ? errorDetail(mutation.error) : null}
      </InlineError>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Members
// --------------------------------------------------------------------------

function RemoveConfirm({ member, isSelf, others, onCancel, onConfirm, pending }) {
  const [reassignTo, setReassignTo] = useState('');
  return (
    <div className="mt-2 p-3 rounded-lg bg-slate-50 border border-slate-200" data-testid={`remove-confirm-${member.user_id}`}>
      <p className="text-sm text-slate-700 mt-0 mb-2">
        {isSelf
          ? 'Leave this workspace? You will lose access until someone invites you again.'
          : `Remove ${member.display_name || member.email} from this workspace?`}
      </p>
      {others.length > 0 && (
        <Field label="Reassign their records to" optional className="mb-3 max-w-xs">
          {(p) => (
            <Select {...p} value={reassignTo} onChange={(e) => setReassignTo(e.target.value)} data-testid="remove-reassign">
              <option value="">Don&apos;t reassign</option>
              {others.map((o) => (
                <option key={o.user_id} value={o.user_id}>{o.display_name || o.email}</option>
              ))}
            </Select>
          )}
        </Field>
      )}
      <div className="flex gap-2">
        <Button variant="danger" size="sm" loading={pending} onClick={() => onConfirm(reassignTo || undefined)}
          data-testid="remove-confirm-submit">
          {isSelf ? 'Leave workspace' : 'Remove'}
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function MembersCard() {
  const { user, role: myRole, isManager, memberships, workspace, switchWorkspace, logout } = useAuth();
  const { members, isLoading, error } = useMembers();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirming, setConfirming] = useState(null); // user_id
  const [rowError, setRowError] = useState({}); // user_id -> message

  const setErr = (uid, msg) => setRowError((cur) => ({ ...cur, [uid]: msg }));

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }) => updateMemberRole(userId, role),
    onMutate: ({ userId }) => setErr(userId, null),
    onError: (err, { userId }) => setErr(userId, errorDetail(err)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: TEAM_MEMBERS_KEY }),
  });

  const removeMutation = useMutation({
    mutationFn: ({ userId, reassignTo }) => removeMember(userId, reassignTo),
    onMutate: ({ userId }) => setErr(userId, null),
    onError: (err, { userId }) => setErr(userId, errorDetail(err)),
    onSuccess: async (_d, { userId }) => {
      setConfirming(null);
      if (userId === user?.id) {
        // Left the active workspace — hop to another one if we have it,
        // otherwise there's nothing left to show: log out.
        const other = memberships.find((m) => m.tenant_id !== workspace?.id);
        toast.success('You left the workspace');
        if (other) {
          try {
            await switchWorkspace(other.tenant_id);
            return;
          } catch { /* fall through to logout */ }
        }
        await logout();
        return;
      }
      toast.success('Member removed');
      queryClient.invalidateQueries({ queryKey: TEAM_MEMBERS_KEY });
    },
  });

  if (isLoading) return <Card className="p-5"><p className="text-sm text-slate-500 m-0">Loading members…</p></Card>;
  if (error) {
    return (
      <Card className="p-5">
        <p className="text-sm text-danger-600 m-0">Failed to load members: {errorDetail(error)}</p>
      </Card>
    );
  }

  const roleOptions = myRole === 'owner' ? ['owner', 'admin', 'member'] : ['admin', 'member'];

  return (
    <Card className="p-5" data-testid="members-card">
      <SectionTitle title="Members" subtitle={`${members.length} ${members.length === 1 ? 'person' : 'people'} in this workspace`} />
      <div className="overflow-auto rounded-card border border-slate-200">
        <table className="w-full text-sm" data-testid="members-table">
          <thead className="bg-slate-50">
            <tr className="border-b border-slate-200">
              {['Name', 'Email', 'Role', ''].map((h, i) => (
                <th key={i} className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const isSelf = m.user_id === user?.id;
              // Admins can't touch owners; owners can edit anyone.
              const canEditRole = isManager && (myRole === 'owner' || m.role !== 'owner');
              const canRemove = isSelf || canEditRole;
              const showExtra = confirming === m.user_id || rowError[m.user_id];
              return (
                <Fragment key={m.user_id}>
                <tr className="border-b border-slate-100 last:border-0 align-top" data-testid={`member-row-${m.user_id}`}>
                  <td className="px-4 py-3 text-slate-900">
                    <span className="font-medium">{m.display_name || m.name || '—'}</span>
                    {isSelf && <span className="ml-1.5 text-xs text-slate-400">(you)</span>}
                  </td>
                  <td className="px-4 py-3 text-slate-600">{m.email}</td>
                  <td className="px-4 py-3">
                    {canEditRole ? (
                      <Select
                        aria-label={`Role for ${m.email}`}
                        value={m.role}
                        disabled={roleMutation.isPending}
                        onChange={(e) => roleMutation.mutate({ userId: m.user_id, role: e.target.value })}
                        className="w-32 py-1"
                        data-testid={`member-role-${m.user_id}`}
                      >
                        {(roleOptions.includes(m.role) ? roleOptions : [m.role, ...roleOptions]).map((r) => (
                          <option key={r} value={r}>{ROLE_LABEL[r] || r}</option>
                        ))}
                      </Select>
                    ) : (
                      <Badge variant={ROLE_BADGE[m.role] || 'neutral'}>{ROLE_LABEL[m.role] || m.role}</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    {canRemove && confirming !== m.user_id && (
                      <Button
                        variant="secondary"
                        size="sm"
                        className={isSelf ? '' : 'text-danger-600'}
                        onClick={() => { setErr(m.user_id, null); setConfirming(m.user_id); }}
                        data-testid={isSelf ? 'leave-workspace' : `remove-member-${m.user_id}`}
                      >
                        {isSelf ? 'Leave workspace' : 'Remove'}
                      </Button>
                    )}
                  </td>
                </tr>
                {showExtra && (
                  <tr>
                    <td colSpan={4} className="px-4 pb-3">
                      {confirming === m.user_id && (
                        <RemoveConfirm
                          member={m}
                          isSelf={isSelf}
                          others={members.filter((o) => o.user_id !== m.user_id)}
                          pending={removeMutation.isPending}
                          onCancel={() => setConfirming(null)}
                          onConfirm={(reassignTo) => removeMutation.mutate({ userId: m.user_id, reassignTo })}
                        />
                      )}
                      <InlineError testId={`member-error-${m.user_id}`}>{rowError[m.user_id]}</InlineError>
                    </td>
                  </tr>
                )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Invites (managers only)
// --------------------------------------------------------------------------

function CopyLink({ url }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="flex gap-2">
      <Input readOnly value={url} onFocus={(e) => e.target.select()} data-testid="invite-link" aria-label="Invite link" />
      <Button type="button" variant="secondary" onClick={copy} data-testid="invite-link-copy">
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  );
}

function InviteCard() {
  const { role: myRole } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('member');
  const [shareLink, setShareLink] = useState(null); // { email, url }

  const invitesQuery = useQuery({ queryKey: TEAM_INVITES_KEY, queryFn: listInvites });

  const createMutation = useMutation({
    mutationFn: createInvite,
    onSuccess: (inv) => {
      setEmail('');
      if (inv.emailed) {
        setShareLink(null);
        toast.success(`Invite sent to ${inv.email}`);
      } else {
        setShareLink({ email: inv.email, url: inv.invite_url });
      }
      queryClient.invalidateQueries({ queryKey: TEAM_INVITES_KEY });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: revokeInvite,
    onSettled: () => queryClient.invalidateQueries({ queryKey: TEAM_INVITES_KEY }),
  });

  const roleOptions = myRole === 'owner' ? ['member', 'admin', 'owner'] : ['member', 'admin'];
  const invites = invitesQuery.data || [];

  return (
    <Card className="p-5" data-testid="invite-card">
      <SectionTitle title="Invite teammates" subtitle="They'll get a link to join this workspace." />
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) createMutation.mutate({ email: email.trim(), role });
        }}
      >
        <Field label="Email" className="flex-1 min-w-[14rem]">
          {(p) => (
            <Input {...p} type="email" placeholder="teammate@company.com" value={email}
              onChange={(e) => setEmail(e.target.value)} data-testid="invite-email" />
          )}
        </Field>
        <Field label="Role" className="w-36">
          {(p) => (
            <Select {...p} value={role} onChange={(e) => setRole(e.target.value)} data-testid="invite-role">
              {roleOptions.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </Select>
          )}
        </Field>
        <Button type="submit" loading={createMutation.isPending} disabled={!email.trim()} data-testid="invite-submit">
          Send invite
        </Button>
      </form>
      <InlineError testId="invite-error">{createMutation.isError ? errorDetail(createMutation.error) : null}</InlineError>

      {shareLink && (
        <div className="mt-4 p-3 rounded-lg bg-warning-50 border border-warning-200" data-testid="invite-share">
          <p className="text-sm text-warning-700 mt-0 mb-2">
            Email isn&apos;t configured — share this link with <span className="font-medium">{shareLink.email}</span>:
          </p>
          <CopyLink url={shareLink.url} />
        </div>
      )}

      <div className="mt-5">
        <h3 className="text-sm font-semibold text-slate-800 mt-0 mb-2">Pending invites</h3>
        {invitesQuery.isLoading ? (
          <p className="text-sm text-slate-500 m-0">Loading invites…</p>
        ) : invitesQuery.isError ? (
          <p className="text-sm text-danger-600 m-0">Failed to load invites: {errorDetail(invitesQuery.error)}</p>
        ) : invites.length === 0 ? (
          <p className="text-sm text-slate-400 m-0" data-testid="invites-empty">No pending invites.</p>
        ) : (
          <ul className="list-none p-0 m-0 divide-y divide-slate-100 border border-slate-200 rounded-card" data-testid="invites-list">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center gap-3 px-4 py-2.5" data-testid={`invite-row-${inv.id}`}>
                <span className="flex-1 min-w-0 text-sm text-slate-800 truncate">{inv.email}</span>
                <Badge variant={ROLE_BADGE[inv.role] || 'neutral'}>{ROLE_LABEL[inv.role] || inv.role}</Badge>
                <span className="text-xs text-slate-400 whitespace-nowrap">Expires {formatDate(inv.expires_at)}</span>
                <Button variant="ghost" size="sm" className="text-danger-600"
                  loading={revokeMutation.isPending && revokeMutation.variables === inv.id}
                  onClick={() => revokeMutation.mutate(inv.id)} data-testid={`revoke-invite-${inv.id}`}>
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
        <InlineError testId="revoke-error">{revokeMutation.isError ? errorDetail(revokeMutation.error) : null}</InlineError>
      </div>
    </Card>
  );
}

export default function WorkspaceTab() {
  const { isManager } = useAuth();
  return (
    <div className="space-y-5" data-testid="workspace-tab">
      <WorkspaceNameCard />
      <MembersCard />
      {isManager && <InviteCard />}
    </div>
  );
}
