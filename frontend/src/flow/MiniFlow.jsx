import FlowCanvas from './FlowCanvas'

// The small canvas on a card. The whole drawing opens the full-size view
// (mouse, Enter or Space).
export default function MiniFlow({ steps, agents, label, onOpen }) {
  if (!steps?.length) return <div className="co-wf-flow-empty">No steps yet</div>
  const keyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onOpen?.()
    }
  }
  return (
    <div className="co-flow-open" role="button" tabIndex={0} aria-label={label} onClick={onOpen} onKeyDown={keyDown} data-testid="flow-open">
      <FlowCanvas size="mini" steps={steps} agents={agents} label={label} />
    </div>
  )
}
