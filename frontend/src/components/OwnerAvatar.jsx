/**
 * Record-owner display (Multi-tenancy P3).  Every record carries an
 * `owner_id` (uuid | null).  These components resolve that id against the
 * cached workspace member list (`useMembers()` → ['team-members']) so a
 * table of 50 rows costs one request, not 50.
 *
 *  - null owner            → muted "Unassigned"
 *  - id not in the members → "Former member" (the user was removed)
 *  - members still loading → neutral placeholder (no "Former member" flash)
 */
import { useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';

import useMembers from '../hooks/useMembers.js';
import { authMeQueryOptions } from '../hooks/useAuth.js';

export const UNASSIGNED_LABEL = 'Unassigned';
export const FORMER_MEMBER_LABEL = 'Former member';

/** Best human label for a member row. */
export function memberName(m) {
  if (!m) return '';
  return m.display_name || m.name || m.email || '';
}

/** Up to two initials from a name / email. */
export function initialsFor(label) {
  const s = String(label || '').trim();
  if (!s) return '?';
  const base = s.includes('@') && !s.includes(' ') ? s.split('@')[0] : s;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

// Deterministic per-user tint so the same person reads the same everywhere.
const TONES = [
  'bg-sky-100 text-sky-700',
  'bg-violet-100 text-violet-700',
  'bg-emerald-100 text-emerald-700',
  'bg-amber-100 text-amber-800',
  'bg-rose-100 text-rose-700',
  'bg-indigo-100 text-indigo-700',
  'bg-teal-100 text-teal-700',
];

function toneFor(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

/** The signed-in user's id.  Reads the shared `auth-me` query directly
 *  (not useAuth) so owner widgets render outside a Router too. */
export function useMyUserId() {
  const { data: me } = useQuery(authMeQueryOptions);
  return me?.user?.id || null;
}

/**
 * Resolve owner ids → display info.  Returns `resolve(id)` →
 * `{ state: 'unassigned' | 'member' | 'former' | 'loading', label, member, isMe }`.
 */
export function useOwnerLookup() {
  const { members, isLoading } = useMembers();
  const myId = useMyUserId();

  const resolve = useCallback((ownerId) => {
    if (!ownerId) return { state: 'unassigned', label: UNASSIGNED_LABEL, member: null, isMe: false };
    const member = members.find((m) => m.user_id === ownerId) || null;
    if (member) {
      return { state: 'member', label: memberName(member), member, isMe: ownerId === myId };
    }
    if (isLoading) return { state: 'loading', label: '…', member: null, isMe: false };
    return { state: 'former', label: FORMER_MEMBER_LABEL, member: null, isMe: false };
  }, [members, isLoading, myId]);

  return { resolve, members, myId, isLoading };
}

const SIZES = {
  xs: 'w-5 h-5 text-[9px]',
  sm: 'w-6 h-6 text-[10px]',
  md: 'w-7 h-7 text-xs',
};

/** Just the circle.  `info` is a resolve() result. */
function AvatarCircle({ ownerId, info, size = 'sm' }) {
  const dim = SIZES[size] || SIZES.sm;
  if (info.state === 'unassigned') {
    return (
      <span
        aria-hidden="true"
        className={`${dim} shrink-0 inline-flex items-center justify-center rounded-full border border-dashed border-slate-300 text-slate-300`}
      >
        ?
      </span>
    );
  }
  if (info.state === 'former' || info.state === 'loading') {
    return (
      <span
        aria-hidden="true"
        className={`${dim} shrink-0 inline-flex items-center justify-center rounded-full bg-slate-100 text-slate-400 font-semibold`}
      >
        {info.state === 'former' ? '–' : ''}
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className={`${dim} shrink-0 inline-flex items-center justify-center rounded-full font-semibold ${toneFor(ownerId)}`}
    >
      {initialsFor(info.label)}
    </span>
  );
}

/**
 * Initials avatar + (optionally) the owner's name.
 *
 * @param ownerId   uuid | null
 * @param compact   avatar only (name in the tooltip / aria-label) — kanban cards
 * @param size      'xs' | 'sm' | 'md'
 */
export function OwnerAvatar({ ownerId, compact = false, size, className = '', testId }) {
  const { resolve } = useOwnerLookup();
  const info = resolve(ownerId);
  const title = info.state === 'member'
    ? `Owner: ${info.label}${info.isMe ? ' (you)' : ''}`
    : info.state === 'loading' ? 'Owner' : `Owner: ${info.label}`;

  if (compact) {
    return (
      <span
        className={`inline-flex ${className}`}
        title={title}
        aria-label={title}
        role="img"
        data-testid={testId}
        data-owner-state={info.state}
      >
        <AvatarCircle ownerId={ownerId} info={info} size={size || 'xs'} />
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 min-w-0 ${className}`}
      title={title}
      data-testid={testId}
      data-owner-state={info.state}
    >
      <AvatarCircle ownerId={ownerId} info={info} size={size || 'sm'} />
      <span
        className={`truncate text-sm ${
          info.state === 'member' ? 'text-slate-700' : 'text-slate-400 italic'
        }`}
      >
        {info.label}
        {info.isMe && <span className="text-slate-400 not-italic"> (you)</span>}
      </span>
    </span>
  );
}

/** Table-cell flavour — the full avatar + name. */
export function OwnerCell({ ownerId, testId }) {
  return <OwnerAvatar ownerId={ownerId} testId={testId} />;
}

export default OwnerAvatar;

/** Report-builder result columns that carry a user id (render as a name). */
export function isOwnerColumn(col) {
  return !!col && (col.type === 'owner' || col.key === 'owner');
}

/** `label(id)` → member name for report cells / chart labels.  Handles the
 *  report-filter literal "me" and null (Unassigned). */
export function useOwnerLabel() {
  const { resolve } = useOwnerLookup();
  return useCallback((id) => {
    if (id === 'me') return 'Me';
    return resolve(id).label;
  }, [resolve]);
}

/** Replace owner-id cells with member names — for report tables, charts and
 *  CSV.  Returns the original array when there is no owner column. */
export function withOwnerNames(columns, rows, label) {
  const keys = (columns || []).filter(isOwnerColumn).map((c) => c.key);
  if (!keys.length) return rows;
  return (rows || []).map((r) => {
    const next = { ...r };
    for (const k of keys) next[k] = label(r[k]);
    return next;
  });
}
