import { Tag } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import './published.css'

// Only real web links become clickable; anything else stays plain text.
export const isWebLink = (s) => typeof s === 'string' && /^https?:\/\//i.test(s)

// "mcp__notion__create_page" -> "create page" (a plain fallback when a publication has no title).
export const toolWords = (tool) => {
  const last = String(tool || '').split('__').pop() || ''
  return last.replace(/[_-]+/g, ' ').trim() || 'Something'
}

export const publishedName = (p) => p?.title || toolWords(p?.tool)

// The "Published outside the project" card on the task page. Hidden when the run published nothing.
export default function PublishedLinks({ events }) {
  const items = (events || []).filter((e) => e?.type === 'output' && e.payload).map((e) => e.payload)
  if (!items.length) return null
  return (
    <div className="co-surface co-published" data-testid="published-links">
      <div className="co-published-title">
        <LinkOutlined /> Published outside the project
      </div>
      {items.map((p, i) => (
        <div className="co-published-row" key={`${p.link}:${i}`} data-testid="published-row">
          <div className="co-published-name">{publishedName(p)}</div>
          <div className="co-published-link">
            {isWebLink(p.link) ? (
              <a href={p.link} target="_blank" rel="noopener noreferrer">{p.link}</a>
            ) : (
              <span>{p.link}</span>
            )}
          </div>
          {p.local_file ? (
            <div className="co-published-copy" data-testid="published-copy">Copy in the project: {p.local_file}</div>
          ) : (
            <Tag color="orange" data-testid="published-nocopy">No copy in the project</Tag>
          )}
        </div>
      ))}
    </div>
  )
}
