import { Tag, Tooltip } from 'antd'

const COLOR = { info: 'blue', warning: 'orange', danger: 'red' }

// The server decides when a badge shows (project.licence_badge); this only draws it, as plain text.
export default function LicenceBadge({ project }) {
  const b = project?.licence_badge
  if (!b || !b.label) return null
  return (
    <Tooltip title="A hint, not legal advice.">
      <Tag color={COLOR[b.tone] || 'default'} data-testid="licence-badge" data-tone={b.tone}>
        {String(b.label)}
      </Tag>
    </Tooltip>
  )
}
