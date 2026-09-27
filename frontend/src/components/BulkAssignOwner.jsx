/**
 * "Assign to…" dialog for a bulk-selection bar (Multi-tenancy P3).  Picks a
 * member (or Unassigned) and calls POST /owners/assign for the selected ids.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { assignOwners } from '../api/owners.js';
import { useToast } from './Toast.jsx';
import { Button, Modal } from './ui.jsx';
import { OwnerPicker } from './OwnerPicker.jsx';
import { useMyUserId } from './OwnerAvatar.jsx';

function plural(n, noun) {
  return `${n} ${noun}${n === 1 ? '' : 's'}`;
}

/**
 * @param recordType      'lead' | 'opportunity' | 'activity' | 'campaign' | …
 * @param ids             selected record ids
 * @param noun            singular noun for copy ("lead", "deal")
 * @param invalidateKeys  react-query key prefixes to refresh on success
 * @param onAssigned      called after a successful assign (clear selection)
 */
export default function BulkAssignOwner({
  open, onClose, recordType, ids, noun = 'record', invalidateKeys = [], onAssigned,
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const myId = useMyUserId();
  // undefined = "not chosen yet" → default to me; null = Unassigned.
  const [ownerId, setOwnerId] = useState(undefined);
  const effective = ownerId === undefined ? myId : ownerId;

  const mut = useMutation({
    mutationFn: () => assignOwners(recordType, ids, effective ?? null),
    onSuccess: (res) => {
      const n = res?.updated ?? ids.length;
      toast.success(
        effective ? `Assigned ${plural(n, noun)}` : `Unassigned ${plural(n, noun)}`,
      );
      invalidateKeys.forEach((queryKey) => queryClient.invalidateQueries({ queryKey }));
      setOwnerId(undefined);
      onAssigned?.(res);
      onClose?.();
    },
    onError: (err) => {
      const d = err?.response?.data?.detail;
      toast.error(typeof d === 'string' ? d : 'Assign failed');
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Assign ${plural(ids.length, noun)}`}
      size="sm"
      testId="bulk-assign-modal"
      footer={(
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button
            size="sm"
            onClick={() => mut.mutate()}
            loading={mut.isPending}
            data-testid="bulk-assign-confirm"
          >
            {effective ? 'Assign' : 'Unassign'}
          </Button>
        </>
      )}
    >
      <OwnerPicker
        value={effective ?? null}
        onChange={setOwnerId}
        label="New owner"
        showLabel
        className="w-full"
        testId="bulk-assign-picker"
      />
      <p className="text-xs text-slate-500 mt-2 mb-0">
        The new owner gets one notification for the whole batch.
      </p>
    </Modal>
  );
}
