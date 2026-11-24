import { useEffect, useMemo, useRef, useState } from 'react'
import { Popconfirm, Popover, Tooltip } from 'antd'
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  DeleteOutlined,
  FlagOutlined,
  PauseOutlined,
  PlayCircleFilled,
  PlusOutlined,
  RobotOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { KIND_WORD, STATUS_WORD, boxKey, connectorPaths, flowModel, layout, sizeOf } from './flow'
import './flow.css'

const KIND_ICON = { agent: <RobotOutlined />, gate: <CheckCircleOutlined />, approval: <UserOutlined />, fix: <ToolOutlined /> }
const STATUS_ICON = {
  done: <CheckOutlined />,
  failed: <CloseOutlined />,
  held: <PauseOutlined />,
  waiting: <ClockCircleOutlined />,
}

export const ADD_CHOICES = [
  { kind: 'agent', label: 'Agents', icon: <RobotOutlined /> },
  { kind: 'gate', label: 'Check', icon: <CheckCircleOutlined /> },
  { kind: 'approval', label: 'Ask a person', icon: <UserOutlined /> },
]

function Box({ kind, title, sub, mono, retry, handsOver, status, selected, error, label, onClick, mini, step, item, small }) {
  const cls = [
    'co-flow-box',
    `co-flow-k-${kind}`,
    mini && 'co-flow-box-mini',
    small && 'co-flow-box-fixer',
    selected && 'co-flow-sel',
    status && `co-flow-st-${status}`,
    error && 'co-flow-err',
  ]
    .filter(Boolean)
    .join(' ')
  const words = [label, status ? STATUS_WORD[status] : null, error ? 'needs attention' : null].filter(Boolean).join(', ')
  const body = (
    <>
      <span className="co-flow-icon" aria-hidden="true">
        {KIND_ICON[kind]}
      </span>
      <span className="co-flow-text">
        <span className="co-flow-title">{title}</span>
        {!mini && sub ? <span className={`co-flow-sub${mono ? ' co-flow-mono' : ''}`}>{sub}</span> : null}
      </span>
      {!mini && (retry || handsOver > 0) && (
        <span className="co-flow-badges" aria-hidden="true">
          {retry && (
            <span className="co-flow-badge" title={kind === 'gate' ? 'Re-runs only the failures' : 'Tries once more'}>
              ↻
            </span>
          )}
          {handsOver > 0 && (
            <span className="co-flow-badge" title={`Hands over ${handsOver} thing${handsOver === 1 ? '' : 's'}`}>
              ⇢ {handsOver}
            </span>
          )}
        </span>
      )}
      {status && status !== 'next' && (
        <span className={`co-flow-dot co-flow-dot-${status}`} aria-hidden="true">
          {STATUS_ICON[status] || null}
        </span>
      )}
      {error && <span className="co-flow-errdot" aria-hidden="true" title="Needs attention" />}
    </>
  )
  const data = { 'data-testid': 'flow-box', 'data-step': step, 'data-item': item == null ? '' : item, 'data-kind': kind, 'data-status': status || undefined }
  if (!onClick)
    return (
      <div className={cls} title={label} {...data}>
        {body}
      </div>
    )
  return (
    <button type="button" className={cls} aria-label={words} aria-pressed={!!selected} onClick={onClick} {...data}>
      {body}
    </button>
  )
}

function Connector({ nLeft, nRight, height, width, size, state, addAt, onAdd, pulse, openAt, setOpenAt }) {
  const { lines, arrows } = connectorPaths(nLeft, nRight, width, height, size)
  const cls = ['co-flow-link', state && `co-flow-link-${state}`].filter(Boolean).join(' ')
  return (
    <div className={cls} style={{ width, height }} data-testid="flow-link">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" focusable="false">
        {lines.map((d, i) => (
          <path key={i} d={d} className="co-flow-line" />
        ))}
        {arrows.map((p, i) => (
          <polygon key={i} points={p} className="co-flow-arrow" />
        ))}
      </svg>
      {onAdd && (
        <Popover
          trigger="click"
          open={openAt === addAt}
          onOpenChange={(o) => setOpenAt(o ? addAt : null)}
          placement="bottom"
          content={
            <div className="co-flow-add" data-testid="flow-add-menu">
              {ADD_CHOICES.map((c) => (
                <button
                  key={c.kind}
                  type="button"
                  className={`co-flow-add-choice co-flow-k-${c.kind}`}
                  onClick={() => {
                    setOpenAt(null)
                    onAdd(addAt, c.kind)
                  }}
                  data-testid={`flow-add-${c.kind}`}
                >
                  <span className="co-flow-add-icon" aria-hidden="true">
                    {c.icon}
                  </span>
                  <span>{c.label}</span>
                </button>
              ))}
            </div>
          }
        >
          <button
            type="button"
            className={`co-flow-plus${pulse ? ' co-flow-plus-pulse' : ''}`}
            aria-label="Add a step here"
            data-testid="flow-plus"
            data-at={addAt}
          >
            <PlusOutlined />
          </button>
        </Popover>
      )}
    </div>
  )
}

function StepTools({ index, count, onMove, onRemove }) {
  const n = index + 1
  return (
    <div className="co-flow-tools" role="group" aria-label={`Step ${n} controls`}>
      <button type="button" aria-label={`Move step ${n} left`} disabled={index === 0} onClick={() => onMove?.(index, -1)} data-testid="flow-move-left">
        <ArrowLeftOutlined />
      </button>
      <button type="button" aria-label={`Move step ${n} right`} disabled={index >= count - 1} onClick={() => onMove?.(index, 1)} data-testid="flow-move-right">
        <ArrowRightOutlined />
      </button>
      <Popconfirm title="Delete this step?" okText="Delete" okButtonProps={{ danger: true }} cancelText="Keep" onConfirm={() => onRemove?.(index)}>
        <button type="button" className="co-flow-tool-del" aria-label={`Delete step ${n}`} data-testid="flow-remove">
          <DeleteOutlined />
        </button>
      </Popconfirm>
    </div>
  )
}

function FixBranch({ col, label, status, selected, error, onClick }) {
  return (
    <div className="co-flow-branch" data-testid="flow-fix-branch">
      <svg className="co-flow-branch-svg" width="200" height="56" viewBox="0 0 200 56" aria-hidden="true" focusable="false">
        <path d="M88 0 L88 49" className="co-flow-line co-flow-dash" />
        <polygon points="84,49 88,56 92,49" className="co-flow-arrow" />
        <path d="M112 56 L112 7" className="co-flow-line co-flow-dash" />
        <polygon points="108,7 112,0 116,7" className="co-flow-arrow" />
      </svg>
      <span className="co-flow-branch-l">if it fails</span>
      <span className="co-flow-branch-r">then run again</span>
      <Box
        kind="fix"
        small
        step={col.index}
        item="fix"
        title={`Fixer: ${col.fix.title}`}
        label={label}
        status={status}
        selected={selected}
        error={error}
        onClick={onClick}
      />
    </div>
  )
}

// A workflow drawn left to right: Start → one column per step → Done.
// Agents of one step stack in their column with the lines fanning out and
// back in. A check's fixer hangs below it on a dashed loop.
//
// mode: 'edit' (add/move/delete controls), 'view', 'run' (statuses).
// selected: { step, item? } — item is an agent index, null for a single-box
// step, 'fix' for a fixer; without item every box of the step is selected.
// statusOf(step, item?) → 'done' | 'running' | 'waiting' | 'failed' | 'held' | 'next'.
// errors: a Set of boxKey(step, item) to mark with a red dot.
export default function FlowCanvas({
  steps = [],
  agents = [],
  selected = null,
  onSelect,
  onInsert,
  onMove,
  onRemove,
  onAddAgent,
  statusOf,
  mode = 'view',
  size = 'full',
  errors,
  input,
  focusStep = null,
  pulseAdd = false,
  label = 'Workflow steps',
}) {
  const mini = size === 'mini'
  const edit = mode === 'edit' && !mini
  const run = mode === 'run'
  const model = useMemo(() => flowModel(steps, agents), [steps, agents])
  const geo = layout(model, { size, mode: edit ? 'edit' : mode })
  const s = sizeOf(size)
  const linkW = edit ? s.editLink : s.link
  const [openAt, setOpenAt] = useState(null)
  const scroller = useRef(null)

  // Keep the focused step (the run's current step, or the one being edited) in view.
  useEffect(() => {
    if (focusStep == null || mini) return
    const sc = scroller.current
    const col = sc?.querySelector(`[data-col="${focusStep}"]`)
    if (!sc || !col) return
    const left = col.offsetLeft
    const w = col.offsetWidth
    if (left >= sc.scrollLeft && left + w <= sc.scrollLeft + sc.clientWidth) return
    const to = Math.max(0, left - (sc.clientWidth - w) / 2)
    if (typeof sc.scrollTo === 'function') sc.scrollTo({ left: to, behavior: 'smooth' })
    else sc.scrollLeft = to
  }, [focusStep, mini, model.length])

  const stepStatus = (i) => (run && statusOf ? statusOf(i) : null)
  const linkState = (i) => {
    if (!run) return null
    if (i < model.length) return stepStatus(i) && stepStatus(i) !== 'next' ? 'reached' : 'muted'
    return model.length && stepStatus(model.length - 1) === 'done' ? 'reached' : 'muted'
  }
  const isSel = (step, item) =>
    !!selected && selected.step === step && (selected.item === undefined ? item !== 'fix' : selected.item === item)
  const hasErr = (step, item) => !!errors && errors.has(boxKey(step, item))
  const click = onSelect ? (step, item) => () => onSelect({ step, item }) : null

  const connector = (i, nLeft, nRight) => (
    <Connector
      nLeft={nLeft}
      nRight={nRight}
      height={geo.height}
      width={linkW}
      size={size}
      state={linkState(i)}
      addAt={i}
      onAdd={edit && onInsert ? onInsert : null}
      pulse={pulseAdd && model.length === 0}
      openAt={openAt}
      setOpenAt={setOpenAt}
    />
  )

  const startPill = (
    <div className={`co-flow-pill co-flow-start${mini ? ' co-flow-pill-mini' : ''}`} data-testid="flow-start">
      <PlayCircleFilled aria-hidden="true" /> <span>Start</span>
    </div>
  )

  return (
    <div
      ref={scroller}
      className={`co-flow co-flow-${mini ? 'mini' : 'full'} co-flow-mode-${mode}`}
      role="group"
      aria-label={label}
      data-testid="flow-canvas"
    >
      <div className="co-flow-track" style={mini ? undefined : { paddingTop: 14 + geo.padTop, paddingBottom: 14 + geo.padBottom }}>
        {input ? (
          <Tooltip title={`Running on: ${input}`}>
            <span className="co-flow-pill-wrap" tabIndex={0} aria-label={`Start, running on: ${input}`}>
              {startPill}
            </span>
          </Tooltip>
        ) : (
          startPill
        )}
        {model.map((col, i) => {
          const prevN = i === 0 ? 1 : model[i - 1].boxes.length
          const n = i + 1
          const many = col.boxes.length > 1
          return (
            <div key={i} className="co-flow-seg">
              {connector(i, prevN, col.boxes.length)}
              <div className={`co-flow-col co-flow-col-${col.kind}`} data-col={i} data-testid="flow-step" data-kind={col.kind}>
                {edit && <StepTools index={i} count={model.length} onMove={onMove} onRemove={onRemove} />}
                {col.boxes.map((b, k) => {
                  const status = run ? (statusOf ? statusOf(i, b.item) : null) || 'next' : null
                  const label =
                    col.kind === 'agent'
                      ? `Step ${n}, ${many ? `Agent ${k + 1} of ${col.boxes.length}` : 'Agent'}: ${b.title}`
                      : `Step ${n}, ${KIND_WORD[col.kind]}: ${col.name}`
                  return (
                    <Box
                      key={k}
                      kind={col.kind}
                      mini={mini}
                      step={i}
                      item={b.item}
                      title={b.title}
                      sub={b.sub}
                      mono={b.mono}
                      retry={b.retry}
                      handsOver={b.handsOver}
                      status={status}
                      selected={isSel(i, b.item)}
                      error={hasErr(i, b.item)}
                      label={label}
                      onClick={click ? click(i, b.item) : null}
                    />
                  )
                })}
                {col.kind === 'agent' && col.boxes.length > 0 && !mini && (
                  <div className="co-flow-colname" aria-hidden="true" title={col.name}>
                    {many ? `${col.name} · together` : col.name}
                  </div>
                )}
                {col.fix && !mini && (
                  <FixBranch
                    col={col}
                    label={`Step ${n}, Fixer: ${col.fix.title}`}
                    status={run ? (statusOf ? statusOf(i, 'fix') : null) || 'next' : null}
                    selected={isSel(i, 'fix')}
                    error={hasErr(i, 'fix')}
                    onClick={click ? click(i, 'fix') : null}
                  />
                )}
                {edit && col.kind === 'agent' && onAddAgent && (
                  <button type="button" className="co-flow-chip" onClick={() => onAddAgent(i)} aria-label={`Add an agent to step ${n}`} data-testid="flow-add-agent">
                    <PlusOutlined aria-hidden="true" /> agent
                  </button>
                )}
              </div>
            </div>
          )
        })}
        <div className="co-flow-seg">
          {connector(model.length, model.length ? model[model.length - 1].boxes.length : 1, 1)}
          <div className={`co-flow-pill co-flow-end${mini ? ' co-flow-pill-mini' : ''}`} data-testid="flow-end">
            <FlagOutlined aria-hidden="true" /> <span>Done</span>
          </div>
        </div>
      </div>
    </div>
  )
}
