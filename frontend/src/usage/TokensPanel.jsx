import { useState } from 'react'
import { DownOutlined, RightOutlined } from '@ant-design/icons'
import { revealHidden } from '../guard/guard'
import './usage.css'

const clean = (v) => revealHidden(v).text
const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0).toLocaleString('en-US')
const FIELDS = [
  ['messages', 'messages'],
  ['input', 'input'],
  ['cache_write', 'cache write'],
  ['cache_read', 'cache read'],
  ['output', 'output'],
]
const entries = (o) => (o && typeof o === 'object' ? Object.entries(o) : [])

function Buckets({ rows, testid, rowTestid }) {
  return (
    <table className="co-usage-table" data-testid={testid}>
      <thead>
        <tr>
          <th />
          {FIELDS.map(([k, label]) => (
            <th key={k}>{label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, b]) => (
          <tr key={name} data-testid={rowTestid}>
            <td className="co-usage-name">{clean(name)}</td>
            {FIELDS.map(([k]) => (
              <td key={k}>{n(b?.[k])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// "Where the tokens went": the step's main and helper buckets, per model, the
// largest tool results, co-orchestrator's own injected text and the tips.
// Collapsed by default.
export default function TokensPanel({ report }) {
  const [open, setOpen] = useState(false)
  const bd = report?.step?.breakdown
  const tips = Array.isArray(report?.tips) ? report.tips : []
  if (!bd && !tips.length) return null
  const buckets = bd ? [['main', bd.main || {}], ...entries(bd.helpers).map(([kind, b]) => [`helper: ${kind}`, b])] : []
  const models = bd ? entries(bd.models) : []
  const largest = bd && Array.isArray(bd.largest_results) ? bd.largest_results : []
  return (
    <section className="co-usage-panel" data-testid="tokens-panel" aria-label="Where the tokens went">
      <button
        type="button"
        className="co-usage-toggle"
        data-testid="tokens-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <DownOutlined /> : <RightOutlined />} Where the tokens went
      </button>
      {open && (
        <div className="co-usage-body" data-testid="tokens-body">
          {buckets.length > 0 && <Buckets rows={buckets} testid="tokens-buckets" rowTestid="tokens-bucket" />}
          {models.length > 0 && (
            <>
              <div className="co-usage-sub">Per model</div>
              <Buckets rows={models} testid="tokens-models" rowTestid="tokens-model" />
            </>
          )}
          {largest.length > 0 && (
            <>
              <div className="co-usage-sub">Largest tool results</div>
              <ul className="co-usage-list" data-testid="tokens-largest">
                {largest.map((r, i) => (
                  <li key={`${r.id || ''}-${i}`}>
                    <span className="co-usage-mono">{clean(r.tool || 'tool')}</span> — {n(r.chars)} chars
                  </li>
                ))}
              </ul>
            </>
          )}
          {bd && (
            <div className="co-usage-note" data-testid="tokens-injected">
              co-orchestrator added {n(bd.injected_chars)} characters of its own notes to this step.
            </div>
          )}
          {tips.length > 0 && (
            <>
              <div className="co-usage-sub">Tips</div>
              <ul className="co-usage-list" data-testid="tokens-tips">
                {tips.map((t, i) => (
                  <li key={i}>{clean(t)}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </section>
  )
}
