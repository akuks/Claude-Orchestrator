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
import { KIND_WORD, STATUS_WORD, badgeWords, boxKey, connectorShapes, flowModel, flowPlan, sizeOf, stackExtras, stepDeps, stepLabel } from './flow'
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

function Box({ aheadMark, word, kind, title, sub, mono, retry, handsOver, outputs, status, selected, error, label, onClick, mini, step, item, small }) {
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
  const badges = mini ? [] : badgeWords(kind, { retry, outputs: outputs || [] })
  const words = [label, word || (status ? STATUS_WORD[status] : null), error ? 'needs attention' : null, aheadMark ? 'approved ahead' : null, ...badges.map((b) => b.toLowerCase())]
    .filter(Boolean)
    .join(', ')
  const retryWord = badges.find((b) => !b.startsWith('Hands over'))
  const handWord = badges.find((b) => b.startsWith('Hands over'))
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
        <span className="co-flow-badges">
          {retry && (
            <Tooltip title={retryWord}>
              <span className="co-flow-badge" role="img" aria-label={retryWord} data-testid="flow-badge">
                ↻
              </span>
            </Tooltip>
          )}
          {handsOver > 0 && (
            <Tooltip title={handWord}>
              <span className="co-flow-badge" role="img" aria-label={handWord} data-testid="flow-badge">
                ⇢ {handsOver}
              </span>
            </Tooltip>
          )}
        </span>
      )}
      {status && status !== 'next' && (
        <span className={`co-flow-dot co-flow-dot-${status}`} aria-hidden="true">
          {STATUS_ICON[status] || null}
        </span>
      )}
      {aheadMark && (!status || status === 'next') && (
        <span className="co-flow-dot co-flow-dot-ahead" data-testid="flow-ahead" aria-hidden="true">
          <CheckOutlined />
        </span>
      )}
      {error && <span className="co-flow-errdot" aria-hidden="true" title="Needs attention" />}
    </>
  )
  const data = { 'data-testid': 'flow-box', 'data-step': step, 'data-item': item == null ? '' : item, 'data-kind': kind, 'data-status': status || undefined }
  if (!onClick)
    return (
      <div className={cls} title={word || label} {...data}>
        {body}
      </div>
    )
  return (
    <button type="button" className={cls} aria-label={words} aria-pressed={!!selected} onClick={onClick} {...data}>
      {body}
    </button>
  )
}

function Connector({ shapes, height, width, top = 0, state, addAt, onAdd, plusY, pulse, openAt, setOpenAt }) {
  const cls = ['co-flow-link', state && `co-flow-link-${state}`].filter(Boolean).join(' ')
  return (
    <div className={cls} style={{ width, height }} data-testid="flow-link">
      <svg
        width={width}
        height={height + top}
        viewBox={`0 ${-top} ${width} ${height + top}`}
        style={top ? { position: 'absolute', left: 0, top: -top } : undefined}
        aria-hidden="true"
        focusable="false"
      >
        {shapes.map((sh, n) => (
          <g key={n} className={sh.state ? `co-flow-link-${sh.state}` : undefined}>
            {sh.lines.map((d, i) => (
              <path key={i} d={d} className="co-flow-line" />
            ))}
            {sh.arrows.map((p, i) => (
              <polygon key={i} points={p} className="co-flow-arrow" />
            ))}
          </g>
        ))}
      </svg>
      {onAdd && (
        <Popover
          trigger="click"
          open={openAt === addAt}
          onOpenChange={(o) => setOpenAt(o ? addAt : null)}
          placement="bottom"
          arrow={{ pointAtCenter: true }}
          align={{ offset: [0, 30] }}
          overlayClassName="co-flow-add-pop"
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
            style={plusY == null ? undefined : { top: plusY }}
          >
            <PlusOutlined />
          </button>
        </Popover>
      )}
    </div>
  )
}

function StepTools({ index, count, onMove, onRemove, blocked }) {
  const n = index + 1
  return (
    <div className="co-flow-tools" role="group" aria-label={`Step ${n} controls`}>
      <button
        type="button"
        aria-label={`Move step ${n} left`}
        title={blocked?.(index, -1) || undefined}
        disabled={index === 0 || !!blocked?.(index, -1)}
        onClick={() => onMove?.(index, -1)}
        data-testid="flow-move-left"
      >
        <ArrowLeftOutlined />
      </button>
      <button
        type="button"
        aria-label={`Move step ${n} right`}
        title={blocked?.(index, 1) || undefined}
        disabled={index >= count - 1 || !!blocked?.(index, 1)}
        onClick={() => onMove?.(index, 1)}
        data-testid="flow-move-right"
      >
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
  center = false,
  usageOf,
  wordOf,
  aheadOf,
  moveBlocked,
}) {
  const mini = size === 'mini'
  const edit = mode === 'edit' && !mini
  const run = mode === 'run'
  const model = useMemo(() => flowModel(steps, agents), [steps, agents])
  const plan = useMemo(() => flowPlan(steps, model, { size, mode: edit ? 'edit' : mode }), [steps, model, size, edit, mode])
  const { geo } = plan
  const s = sizeOf(size)
  const linkW = edit ? s.editLink : s.link
  const [openAt, setOpenAt] = useState(null)
  const scroller = useRef(null)
  // Which edges have more to scroll to (for the fade).
  const [more, setMore] = useState({ left: false, right: false })
  useEffect(() => {
    const sc = scroller.current
    if (!sc) return undefined
    const check = () => {
      const left = sc.scrollLeft > 1
      const right = sc.scrollLeft + sc.clientWidth < sc.scrollWidth - 1
      setMore((m) => (m.left === left && m.right === right ? m : { left, right }))
    }
    check()
    sc.addEventListener('scroll', check, { passive: true })
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(check) : null
    ro?.observe(sc)
    if (sc.firstElementChild) ro?.observe(sc.firstElementChild)
    return () => {
      sc.removeEventListener('scroll', check)
      ro?.disconnect()
    }
  }, [model.length])

  // Keep the focused or selected step fully in view (with room for its badge and the edge
  // fade). Only the canvas scrolls, never the page.
  const keepStep = selected && selected.step != null ? selected.step : focusStep
  useEffect(() => {
    if (keepStep == null || mini) return
    const sc = scroller.current
    const col = sc?.querySelector(`[data-col="${keepStep}"]`)
    if (!sc || !col) return
    const room = 24
    const left = col.offsetLeft
    const w = col.offsetWidth
    if (left - room >= sc.scrollLeft && left + w + room <= sc.scrollLeft + sc.clientWidth) return
    const to = Math.max(0, left + w / 2 - sc.clientWidth / 2)
    if (typeof sc.scrollTo === 'function') sc.scrollTo({ left: to, behavior: 'smooth' })
    else sc.scrollLeft = to
  }, [keepStep, mini, model.length])

  const stepStatus = (i) => (run && statusOf ? statusOf(i) : null)
  // A line is "reached" once the step it leads to has started (Done: once its step is done).
  const edgeState = (e) => {
    if (!run) return null
    if (e.to === 'end') return e.from !== 'start' && stepStatus(e.from) === 'done' ? 'reached' : 'muted'
    return stepStatus(e.to) && stepStatus(e.to) !== 'next' ? 'reached' : 'muted'
  }
  const isSel = (step, item) =>
    !!selected && selected.step === step && (selected.item === undefined ? item !== 'fix' : selected.item === item)
  const hasErr = (step, item) => !!errors && errors.has(boxKey(step, item))
  const click = onSelect ? (step, item) => () => onSelect({ step, item }) : null

  const connector = (k) => {
    const shapes = connectorShapes(plan, k, linkW, size).map((sh) => ({ ...sh, state: edgeState(sh) }))
    const states = new Set(shapes.map((sh) => sh.state))
    const direct = shapes.filter((sh) => sh.kind !== 'pass')
    const last = k === plan.count
    // "+" sits on the line when there is one; at the end it adds after every last step.
    const single = direct.length === 1 && direct[0].kind === 'direct'
    const showAdd = edit && onInsert && (single || last)
    return (
      <Connector
        shapes={shapes}
        height={geo.height}
        width={linkW}
        top={plan.top}
        state={states.size === 1 ? [...states][0] : null}
        addAt={last ? model.length : plan.groups[k][0]}
        onAdd={showAdd ? onInsert : null}
        plusY={single ? direct[0].y : null}
        pulse={pulseAdd && model.length === 0}
        openAt={openAt}
        setOpenAt={setOpenAt}
      />
    )
  }

  const slotExtras = (col) => stackExtras(col, size, mode)

  const renderCol = (i) => {
    const col = model[i]
    const n = i + 1
    const many = col.boxes.length > 1
    const deps = stepDeps(steps, i)
    const dflt = deps.length === (i > 0 ? 1 : 0) && (i === 0 || deps[0] === i - 1)
    const waits = dflt ? '' : deps.length ? `, waits for ${deps.map((j) => stepLabel(steps, j)).join(', ')}` : ', starts at once'
    return (
    <div className={`co-flow-col co-flow-col-${col.kind}`} data-col={i} data-testid="flow-step" data-kind={col.kind}>
      {edit && <StepTools index={i} count={model.length} onMove={onMove} onRemove={onRemove} blocked={moveBlocked} />}
      {col.boxes.map((b, k) => {
        const status = run ? (statusOf ? statusOf(i, b.item) : null) || 'next' : null
        const label =
          col.kind === 'agent'
            ? `Step ${n}, ${many ? `Agent ${k + 1} of ${col.boxes.length}` : 'Agent'}: ${b.title}`
            : `Step ${n}, ${KIND_WORD[col.kind]}: ${col.name}`
        const full = label + waits
        const word = run && wordOf ? wordOf(i, b.item) : null
        return (
          <Box
            key={k}
            word={word}
            aheadMark={run && aheadOf ? !!aheadOf(i) : false}
            kind={col.kind}
            mini={mini}
            step={i}
            item={b.item}
            title={b.title}
            sub={b.sub}
            mono={b.mono}
            retry={b.retry}
            handsOver={b.handsOver}
            outputs={b.outputs}
            status={status}
            selected={isSel(i, b.item)}
            error={hasErr(i, b.item)}
            label={full}
            onClick={click ? click(i, b.item) : null}
          />
        )
      })}
      {run && !mini && usageOf && usageOf(i) && (
        <div className="co-flow-usage" data-testid="flow-usage">
          {usageOf(i)}
        </div>
      )}
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

    )
  }

  const startPill = (
    <div className={`co-flow-pill co-flow-start${mini ? ' co-flow-pill-mini' : ''}`} data-testid="flow-start">
      <PlayCircleFilled aria-hidden="true" /> <span>Start</span>
    </div>
  )

  return (
    <div
      ref={scroller}
      className={[
        'co-flow',
        `co-flow-${mini ? 'mini' : 'full'}`,
        `co-flow-mode-${mode}`,
        center && 'co-flow-center',
        more.left && 'co-flow-more-left',
        more.right && 'co-flow-more-right',
      ]
        .filter(Boolean)
        .join(' ')}
      role="group"
      aria-label={label}
      data-testid="flow-canvas"
    >
      <div className="co-flow-track" style={mini ? (plan.top ? { paddingTop: 2 + plan.top } : undefined) : { paddingTop: 14 + geo.padTop + plan.laneRoom, paddingBottom: 14 + geo.padBottom + (run && usageOf ? 22 : 0) }} data-testid="flow-track">
        {input ? (
          <Tooltip title={`Running on: ${input}`}>
            <span className="co-flow-pill-wrap" tabIndex={0} aria-label={`Start, running on: ${input}`}>
              {startPill}
            </span>
          </Tooltip>
        ) : (
          startPill
        )}
        {plan.groups.map((g, k) => (
          <div key={k} className="co-flow-seg">
            {connector(k)}
            {g.length === 1 ? (
              renderCol(g[0])
            ) : (
              <div className="co-flow-group" style={{ gap: s.gap }} data-testid="flow-group">
                {g.map((i) => {
                  const ex = slotExtras(model[i])
                  return (
                    <div key={i} className="co-flow-slot" style={{ paddingTop: ex.above, paddingBottom: ex.below }}>
                      {renderCol(i)}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ))}
        <div className="co-flow-seg">
          {connector(plan.count)}
          <div className={`co-flow-pill co-flow-end${mini ? ' co-flow-pill-mini' : ''}`} data-testid="flow-end">
            <FlagOutlined aria-hidden="true" /> <span>Done</span>
          </div>
        </div>
      </div>
    </div>
  )
}
