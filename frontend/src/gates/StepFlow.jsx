import { ArrowRightOutlined, CheckCircleOutlined, RobotOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons'
import { stageKind } from './gates'
import './workflows.css'

const KIND_WORDS = { agent: 'Agents do the work', gate: 'Run a check', approval: 'Ask a person' }

function stepIcon(kind, n) {
  if (kind === 'gate') return <CheckCircleOutlined />
  if (kind === 'approval') return <UserOutlined />
  return n > 1 ? <TeamOutlined /> : <RobotOutlined />
}

// A workflow's steps as a short readable flow: an icon per type, the name,
// and how many agents work at once when there are several.
export default function StepFlow({ stages = [] }) {
  if (!stages.length) return <div className="co-wf-flow-empty">No steps yet</div>
  return (
    <ol className="co-wf-flow" aria-label="Steps">
      {stages.map((st, i) => {
        const kind = stageKind(st)
        const n = kind === 'agent' ? st.items?.length || 0 : 0
        const name = st.name || `Step ${i + 1}`
        return (
          <li key={i} className="co-wf-flow-item">
            <span className={`co-wf-pill co-wf-pill-${kind}`} data-kind={kind} title={`${KIND_WORDS[kind]}: ${name}`} data-testid="flow-step">
              {stepIcon(kind, n)}
              <span className="co-wf-sr">{KIND_WORDS[kind]}: </span>
              <span className="co-wf-pill-name">{name}</span>
              {n > 1 && <span className="co-wf-pill-count">{n} at once</span>}
            </span>
            {i < stages.length - 1 && <ArrowRightOutlined className="co-wf-flow-arrow" aria-hidden="true" />}
          </li>
        )
      })}
    </ol>
  )
}
