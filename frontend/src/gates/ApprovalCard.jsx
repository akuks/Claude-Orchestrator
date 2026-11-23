import { useState } from 'react'
import { Button, Input, message } from 'antd'
import { api } from '../api'
import './workflows.css'

const LEAD = {
  waiting_approval: 'Waiting for someone to approve this step',
  approved: 'Approved',
  rejected: 'Rejected',
  held: 'On hold — waiting until it is ready',
  failed: 'Stopped',
  waiting: 'Not reached yet',
}

// The plain sentence an "Ask a person" step leads with.
export function approvalLead(status) {
  if (!status) return LEAD.waiting
  if (LEAD[status]) return LEAD[status]
  const s = status.replace(/_/g, ' ')
  return s[0].toUpperCase() + s.slice(1)
}

// An "Ask a person" step: what to check, and (while waiting) Approve and Reject.
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
      <div className={`co-approval-lead co-approval-lead-${st.status || 'waiting'}`} data-testid="approval-lead">
        {approvalLead(st.status)}
      </div>
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
            placeholder="Why are you rejecting it? (required)"
            onChange={(e) => setReason(e.target.value)}
            aria-label="Reason for rejecting"
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
            Reject with this reason
          </Button>
        </div>
      )}
      {st.status === 'rejected' && st.reason && <div className="co-approval-reason">Reason: {st.reason}</div>}
      {st.decided_by && <div className="co-approval-by">Decided by {st.decided_by}</div>}
    </div>
  )
}
