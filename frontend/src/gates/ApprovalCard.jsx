import { useState } from 'react'
import { Button, Input, message } from 'antd'
import { api } from '../api'

// An approval stage: the message, and (while waiting) Approve and Reject.
export default function ApprovalCard({ runId, index, stage, state, canDecide = true, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const st = state || {}
  const waiting = st.status === 'waiting_approval'
  const trimmed = reason.trim()

  const run = async (fn, ok) => {
    setBusy(true)
    try {
      await fn()
      message.success(ok)
      setRejecting(false)
      setReason('')
      await onChanged?.()
    } catch (e) {
      message.error(e?.message || 'Could not record the decision')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="co-approval" data-testid="approval-card">
      {stage?.message && <p className="co-approval-msg" data-testid="approval-message">{stage.message}</p>}
      {waiting && canDecide && (
        <div className="co-approval-actions">
          <Button type="primary" loading={busy} onClick={() => run(() => api.approveWorkflowStage(runId, index), 'Approved')} data-testid="approval-approve">
            Approve
          </Button>
          <Button danger disabled={busy} onClick={() => setRejecting(true)} data-testid="approval-reject">
            Reject
          </Button>
        </div>
      )}
      {waiting && rejecting && (
        <div className="co-approval-reject">
          <Input.TextArea
            value={reason}
            maxLength={500}
            rows={2}
            placeholder="Why is it rejected? (required)"
            onChange={(e) => setReason(e.target.value)}
            data-testid="approval-reason"
          />
          <Button
            danger
            type="primary"
            disabled={!trimmed}
            loading={busy}
            onClick={() => run(() => api.rejectWorkflowStage(runId, index, trimmed), 'Rejected')}
            data-testid="approval-confirm-reject"
          >
            Confirm reject
          </Button>
        </div>
      )}
      {!waiting && st.status && <div className="co-approval-status">{st.status.replace('_', ' ')}</div>}
      {st.status === 'rejected' && st.reason && <div className="co-approval-reason">Reason: {st.reason}</div>}
      {st.decided_by && <div className="co-approval-by">Decided by {st.decided_by}</div>}
    </div>
  )
}
