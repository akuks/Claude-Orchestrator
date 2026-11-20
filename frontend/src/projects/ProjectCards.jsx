import { Tag, Tooltip } from 'antd'
import CometSvg, { stateLabel } from '../comets/CometSvg'
import { layoutComets } from '../comets/layout'
import './projects.css'

// Card grid for the Projects page: name, your role, budget used, task count,
// current work as a mini (SVG) comet from the live endpoint, "idle" when
// nothing runs, "paused" when one of its schedules is blocked.

const money = (c) => `$${Number(c || 0).toFixed(2)}`

// Everything a card shows, derived from the project, the live items and the
// schedules. Pure (tested).
export function projectCardModel(project, live = [], schedules = []) {
  const items = (Array.isArray(live) ? live : []).filter((i) => (i.project_ids || []).includes(project.id))
  // One comet fits a card; attention-first (failed, approval, running) picks it.
  const { comets, overflow } = layoutComets(items, { max: 1 })
  const comet = comets[0] || null
  const head = comet ? comet.stars[comet.headIndex] : null
  const paused = (Array.isArray(schedules) ? schedules : []).filter((s) => s.project_id === project.id && s.blocked_reason)
  const spent = Number(project.total_cost_usd || 0)
  const budget = project.budget_usd != null && project.budget_usd > 0 ? Number(project.budget_usd) : null
  const ratio = budget ? spent / budget : null
  return {
    comets,
    liveCount: items.length,
    more: overflow,
    activity: comet ? `${comet.title} · ${head.name} · ${stateLabel(head.state)}` : null,
    headState: head?.state || null,
    paused: paused.length,
    pausedReason: paused[0]?.blocked_reason || null,
    budgetText: budget ? `${money(spent)} / ${money(budget)}` : money(spent),
    budgetRatio: ratio,
    budgetLevel: ratio == null ? null : ratio >= 1 ? 'over' : ratio >= 0.8 ? 'high' : 'ok',
  }
}

function Card({ project, model, onOpen, actions }) {
  const open = () => onOpen?.(project)
  const pct = model.budgetRatio == null ? null : Math.min(100, Math.round(model.budgetRatio * 100))
  return (
    // The whole card opens the project (mouse); the name is the keyboard /
    // screen-reader control. Actions in the footer don't bubble to the card.
    <article
      className={`co-surface co-pcard${project.archived ? ' co-pcard-archived' : ''}`}
      data-testid="project-card"
      data-project-id={project.id}
      onClick={open}
    >
      <div className="co-pcard-head">
        <button type="button" className="co-pcard-name" onClick={(e) => (e.stopPropagation(), open())}>
          {project.name}
        </button>
        {project.archived && <Tag className="co-pcard-tag">archived</Tag>}
      </div>
      <div className="co-pcard-sub">
        {project.my_role || 'member'} · {project.task_count ?? 0} task{project.task_count === 1 ? '' : 's'}
      </div>

      <div className="co-pcard-work" data-testid="project-card-work">
        {model.comets.length ? (
          <>
            <CometSvg comets={model.comets} compact />
            <div className={`co-pcard-activity co-pcard-activity-${model.headState}`} title={model.activity}>
              {model.activity}
              {model.more > 0 ? ` · +${model.more} more` : ''}
            </div>
          </>
        ) : (
          <div className="co-pcard-quiet">quiet — nothing running</div>
        )}
      </div>

      <footer className="co-pcard-foot">
        <span className="co-pcard-chips">
          {model.liveCount ? (
            <Tag color="orange" className="co-pcard-tag" data-testid="project-card-live">
              {model.liveCount} live
            </Tag>
          ) : (
            <Tag className="co-pcard-tag" data-testid="project-card-idle">
              idle
            </Tag>
          )}
          {model.paused > 0 && (
            <Tooltip title={model.pausedReason}>
              <Tag color="gold" className="co-pcard-tag" data-testid="project-card-paused">
                paused{model.paused > 1 ? ` ×${model.paused}` : ''}
              </Tag>
            </Tooltip>
          )}
          <span
            className={`co-pcard-budget co-num${model.budgetLevel ? ` co-pcard-budget-${model.budgetLevel}` : ''}`}
            title={model.budgetRatio != null ? `${pct}% of budget used` : 'Spent so far (no budget set)'}
            data-testid="project-card-budget"
          >
            {model.budgetText}
            {pct != null && (
              <span className="co-pcard-bar" aria-hidden="true">
                <span style={{ width: `${pct}%` }} />
              </span>
            )}
          </span>
        </span>
        {actions && (
          <span className="co-pcard-actions" onClick={(e) => e.stopPropagation()}>
            {actions}
          </span>
        )}
      </footer>
    </article>
  )
}

export default function ProjectCards({ projects = [], live = [], schedules = [], onOpen, renderActions }) {
  return (
    <div className="co-pcards" data-testid="project-cards">
      {projects.map((p) => (
        <Card
          key={p.id}
          project={p}
          model={projectCardModel(p, live, schedules)}
          onOpen={onOpen}
          actions={renderActions?.(p)}
        />
      ))}
    </div>
  )
}
