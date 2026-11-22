import { useState } from 'react'
import { Dropdown, Button } from 'antd'
import { DownOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { api } from '../api'
import { canContinue } from './resume'
import './resume.css'

// Continue a stopped main-line step (never a completed one, never an agent's
// child). The server has the last word: a 409 is shown as its message.
export default function ContinueButton({ task, user, thread, onContinued }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const can = canContinue(task, user, thread)
  if (!task || task.status === 'completed' || (!can.resume && !can.fresh)) return null

  const go = async (mode) => {
    setBusy(true)
    setError(null)
    try {
      const child = await api.continueTask(task.id, mode)
      onContinued?.(child)
    } catch (e) {
      setError(e.status === 409 ? `Cannot continue now: ${e.message}` : e.message)
    } finally {
      setBusy(false)
    }
  }
  const items = [
    { key: 'resume', label: 'Continue (resume)', disabled: !can.resume },
    { key: 'fresh', label: 'Continue fresh with the handoff', disabled: !can.fresh },
  ]
  return (
    <span data-testid="continue-wrap">
      <Dropdown menu={{ items, onClick: ({ key }) => go(key) }} trigger={['click']} disabled={busy}>
        <Button icon={<PlayCircleOutlined />} loading={busy} data-testid="continue-button">
          Continue <DownOutlined />
        </Button>
      </Dropdown>
      {error && (
        <div className="co-resume-error" data-testid="continue-error">
          {error}
        </div>
      )}
    </span>
  )
}
