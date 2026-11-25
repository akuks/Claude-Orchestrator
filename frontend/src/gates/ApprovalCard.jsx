import { useState } from 'react'
import { Button, Input, Popconfirm, Tag, message } from 'antd'
import { api } from '../api'
import { plainError } from '../runs/plainError'
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
export default function ApprovalCard({ runId, index, stage, state, canDecide = true, future = false, onChanged }) {
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
      message.error(plainError(e))
    } finally {
      setBusy(false)
    }
  }

  const ahead = st.ahead && typeof st.ahead === 'object' ? st.ahead : null
  const waitsAhead = future && !st.status && !st.decided_by // not reached, not yet applied
  const showAhead = !!ahead && (waitsAhead || st.status === 'held')
  const stepName = stage?.name || 'this step'

  return (
    <div className="co-approval" data-testid="approval-card">
      <div className={`co-approval-lead co-approval-lead-${st.status || 'waiting'}`} data-testid="approval-lead">
        {waitsAhead && ahead ? 'Approved ahead' : approvalLead(st.status)}
      </div>
      {waitsAhead && ahead && (
        <p className="co-approval-msg" data-testid="approval-ahead-note">The run will go straight past this step.</p>
      )}
      {stage?.message && <p className="co-approval-msg" data-testid="approval-message">{stage.message}</p>}
      {showAhead && (
        <div className="co-approval-ahead" data-testid="approval-ahead">
          <Tag color="success">Approved ahead by {ahead.by_name}</Tag>
          {canDecide && (
            <Button type="link" size="small" disabled={busy} onClick={() => run(() => api.withdrawAhead(runId, index), 'Early approval withdrawn')} data-testid="approval-withdraw">
              Withdraw
            </Button>
          )}
        </div>
      )}
      {waitsAhead && !ahead && stage?.ahead !== true && (
        <div className="co-approval-muted" data-testid="approval-needs-you">
          Needs you when the run gets here
        </div>
      )}
      {waitsAhead && !ahead && stage?.ahead === true && canDecide && (
        <div className="co-approval-actions">
          <Popconfirm
            title={`Approve ${stepName} now? The run will go straight past it.`}
            okText="Approve ahead"
            cancelText="Not now"
            onConfirm={() => run(() => api.approveAhead(runId, index), 'Approved ahead')}
          >
            <Button loading={busy} data-testid="approval-ahead-btn">
              Approve ahead
            </Button>
          </Popconfirm>
        </div>
      )}
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
      {st.decided_by && (
        <div className="co-approval-by" data-testid="approval-by">
          {ahead && st.status === 'approved' ? `Approved ahead by ${ahead.by_name || st.decided_by}` : `Decided by ${st.decided_by}`}
        </div>
      )}
    </div>
  )
}
