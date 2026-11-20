import { useMemo, useState } from 'react'
import { Alert, Button, Skeleton, Tag, Tooltip, message } from 'antd'
import { CheckOutlined, KeyOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { api } from '../api'
import { layoutComets } from '../comets/layout'
import CometSvg from '../comets/CometSvg'
import { displayName, greetingFor, parseTime, useHomeData } from './useHomeData'
import './home.css'

dayjs.extend(relativeTime)

const KIND_TAG = {
  credential: { color: 'warning', text: 'set up' },
  approval: { color: 'magenta', text: 'approve' },
  failed: { color: 'error', text: 'failed' },
  schedule: { color: 'gold', text: 'paused' },
}
const RECENT_TAG = {
  completed: { color: 'success', text: 'done' },
  failed: { color: 'error', text: 'failed' },
  cancelled: { color: 'default', text: 'cancelled' },
}

const when = (d) => dayjs(parseTime(d))
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

function Panel({ title, testId, extra, children, className = '' }) {
  return (
    <section className={`co-surface co-home-panel ${className}`} data-testid={testId} aria-label={title}>
      <header className="co-home-panel-head">
        <h2>{title}</h2>
        {extra}
      </header>
      {children}
    </section>
  )
}

function InlineError({ text, onRetry }) {
  return (
    <Alert
      className="co-home-error"
      type="error"
      showIcon
      message={text}
      action={
        <Button size="small" onClick={onRetry}>
          Retry
        </Button>
      }
    />
  )
}

function Empty({ title, text, children }) {
  return (
    <div className="co-home-empty">
      <div className="co-home-empty-title">{title}</div>
      {text && <div className="co-home-empty-text">{text}</div>}
      {children}
    </div>
  )
}

export default function HomeView({ user, navigate, onOpenCredentials, onApprovalsChanged, refreshSignal }) {
  const { needsYou, live, recent, stats, loading, errors, reload } = useHomeData(user, { refreshSignal })
  const [approving, setApproving] = useState(null)
  const { comets, overflow } = useMemo(() => layoutComets(live), [live])

  const approve = async (item) => {
    setApproving(item.id)
    try {
      await api.approveTask(item.id)
      message.success(`Approved “${item.title}”`)
      onApprovalsChanged?.()
      await reload()
    } catch (e) {
      message.error(e.message)
    } finally {
      setApproving(null)
    }
  }

  const openItem = (item) => {
    if (item.kind === 'credential') onOpenCredentials?.()
    else if (item.kind === 'schedule') navigate({ page: 'automation' })
    else navigate({ page: 'tasks', id: item.id })
  }
  const openComet = (c) => navigate(c.kind === 'run' ? { page: 'runs', id: c.id } : { page: 'tasks', id: c.id })

  const summary = [
    needsYou.length
      ? `${plural(needsYou.length, 'thing needs', 'things need')} you`
      : errors.needsYou
        ? null
        : 'Nothing needs you',
    stats.liveCount != null ? `${stats.liveCount} live` : null,
    stats.costToday != null ? `$${stats.costToday.toFixed(2)} today` : null,
  ].filter(Boolean)

  return (
    <div className="co-home" data-testid="home">
      <div className="co-home-greeting">
        <h1 data-testid="home-greeting">
          {greetingFor()}, {displayName(user)}
        </h1>
        <div className="co-home-summary co-num" data-testid="home-summary">
          {loading ? <Skeleton.Input active size="small" style={{ width: 260, height: 16 }} /> : summary.join(' · ')}
        </div>
      </div>

      <div className="co-home-grid">
        <div className="co-home-col">
          <Panel title="Needs you" testId="home-needs-you">
            {errors.needsYou && <InlineError text="Couldn't load everything that needs you." onRetry={reload} />}
            {loading ? (
              <Skeleton active paragraph={{ rows: 3 }} title={false} />
            ) : needsYou.length === 0 ? (
              // An all-clear only when every source behind it loaded.
              errors.needsYou ? null : <Empty title="You're all caught up" text="Approvals, failed tasks and paused schedules will show up here." />
            ) : (
              <ul className="co-home-rows">
                {needsYou.map((item) => (
                  <li key={item.key} className="co-home-row" data-testid={`needs-${item.kind}`}>
                    <div className="co-home-row-main">
                      <Tag color={KIND_TAG[item.kind].color} bordered={false}>
                        {KIND_TAG[item.kind].text}
                      </Tag>
                      <div className="co-home-row-text">
                        <div className="co-home-row-title" title={item.title}>
                          {item.title}
                        </div>
                        {(item.detail || item.at) && (
                          <div className="co-home-row-detail" title={item.detail || undefined}>
                            {[item.detail, item.at ? when(item.at).fromNow() : null].filter(Boolean).join(' · ')}
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="co-home-row-actions">
                      {item.kind === 'approval' && (
                        <Button
                          size="small"
                          type="primary"
                          icon={<CheckOutlined />}
                          loading={approving === item.id}
                          disabled={approving != null && approving !== item.id}
                          onClick={() => approve(item)}
                        >
                          Approve
                        </Button>
                      )}
                      {item.kind === 'credential' ? (
                        <Button size="small" type="primary" icon={<KeyOutlined />} onClick={() => openItem(item)}>
                          My credentials
                        </Button>
                      ) : (
                        <Button size="small" onClick={() => openItem(item)}>
                          Open
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Recent" testId="home-recent">
            {errors.recent && <InlineError text="Couldn't load your recent tasks." onRetry={reload} />}
            {loading ? (
              <Skeleton active paragraph={{ rows: 3 }} title={false} />
            ) : recent.length === 0 ? (
              errors.recent ? null : <Empty title="Nothing finished yet" text="Your last finished tasks will appear here." />
            ) : (
              <ul className="co-home-rows">
                {recent.map((t) => {
                  const tag = RECENT_TAG[t.status] || { color: 'default', text: t.status }
                  return (
                    <li key={t.id} className="co-home-row" data-testid="recent-item">
                      <div className="co-home-row-main">
                        <Tag color={tag.color} bordered={false}>
                          {tag.text}
                        </Tag>
                        <a className="co-home-row-title" href={`#/tasks/${encodeURIComponent(t.id)}`} title={t.title}>
                          {t.title}
                        </a>
                      </div>
                      <Tooltip title={when(t.completed_at || t.created_at).format('YYYY-MM-DD HH:mm')}>
                        <span className="co-home-row-time">{when(t.completed_at || t.created_at).fromNow()}</span>
                      </Tooltip>
                    </li>
                  )
                })}
              </ul>
            )}
          </Panel>
        </div>

        <Panel
          title="Live workflows"
          testId="home-live"
          className="co-home-live"
          extra={
            !loading && comets.length > 0 ? <span className="co-home-hint">Click a comet to open it</span> : null
          }
        >
          {errors.live && <InlineError text="Couldn't load live workflows." onRetry={reload} />}
          {loading ? (
            <Skeleton active paragraph={{ rows: 7 }} title={false} />
          ) : comets.length === 0 ? (
            errors.live ? null : <Empty title="No workflows running right now" text="Start a workflow and watch it move through its stages here.">
              <Button icon={<PlusOutlined />} onClick={() => navigate({ page: 'automation' })}>
                Start a workflow
              </Button>
            </Empty>
          ) : (
            <CometSvg
              comets={comets}
              overflow={overflow}
              onOpen={openComet}
              onMore={() => navigate({ page: 'automation' })}
            />
          )}
        </Panel>
      </div>
    </div>
  )
}
