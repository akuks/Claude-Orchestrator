import { Badge, Tooltip } from 'antd'
import {
  ApiOutlined,
  ClockCircleOutlined,
  DollarOutlined,
  FolderOutlined,
  HomeOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  SecurityScanOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { visiblePages } from './commands'
import { toHash } from './router'

const ICONS = {
  home: <HomeOutlined />,
  tasks: <UnorderedListOutlined />,
  projects: <FolderOutlined />,
  automation: <ClockCircleOutlined />,
  approvals: <SafetyOutlined />,
  usage: <DollarOutlined />,
  security: <SafetyCertificateOutlined />,
  mcp: <ApiOutlined />,
  users: <UserOutlined />,
  teams: <TeamOutlined />,
  guard: <SecurityScanOutlined />,
}

// Pages without their own rail item light up their parent.
const RAIL_PARENT = { runs: 'automation' }

// Slim left icon rail. Items are real links (#/page) so middle-click and
// "copy link" work; the hash router picks the change up. Badges: pending
// approvals on Approvals, open questions the viewer may answer on Home (where
// the Questions section lives).
const BADGES = {
  approvals: { prop: 'approvalCount', testId: 'rail-approvals-badge', label: (n) => `${n} pending` },
  home: { prop: 'questionCount', testId: 'rail-questions-badge', label: (n) => `${n} open question${n === 1 ? '' : 's'}` },
}

export default function Rail({ page, isAdmin, approvalCount = 0, questionCount = 0 }) {
  const counts = { approvalCount, questionCount }
  const active = RAIL_PARENT[page] || page
  return (
    <nav className="co-rail" aria-label="Main">
      <a className="co-rail-logo" href={toHash({ page: 'home' })} aria-label="Claude Orchestrator — Home" title="Claude Orchestrator">
        <span className="co-logo" />
      </a>
      {visiblePages(isAdmin).map((p) => {
        const on = p.page === active
        const badge = BADGES[p.page]
        const n = badge ? counts[badge.prop] : 0
        const icon = badge ? (
          <Badge count={n} size="small" offset={[4, -2]} data-testid={badge.testId}>
            <span className="co-rail-icon">{ICONS[p.page]}</span>
          </Badge>
        ) : (
          <span className="co-rail-icon">{ICONS[p.page]}</span>
        )
        const label = badge && n ? `${p.label} (${badge.label(n)})` : p.label
        return (
          <Tooltip key={p.page} title={label} placement="right" mouseEnterDelay={0.15}>
            <a
              href={toHash({ page: p.page })}
              className={on ? 'co-rail-item co-rail-item-on' : 'co-rail-item'}
              aria-label={label}
              aria-current={on ? 'page' : undefined}
              data-testid={`rail-${p.page}`}
            >
              {icon}
            </a>
          </Tooltip>
        )
      })}
    </nav>
  )
}
