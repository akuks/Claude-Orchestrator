import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Input, Modal } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { searchCommands } from './commands'

// ⌘K palette: type to filter, ↑/↓ to move, Enter to run, Esc to close.
export default function CommandPalette({ open, commands, loading, onClose }) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  const results = useMemo(() => searchCommands(commands, query), [commands, query])

  useLayoutEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
  }, [open])

  // The dialog only takes focus a frame or two after ⌘K. Keys pressed before
  // the input has had focus once would be lost, so buffer them until then.
  // After that, a printable key typed elsewhere just refocuses the input.
  const keyRef = useRef(null)
  const focusedOnce = useRef(false)
  useEffect(() => {
    if (!open) return
    focusedOnce.current = false
    const onKey = (e) => {
      const input = inputRef.current?.input
      if (input && document.activeElement === input) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (input && focusedOnce.current) {
        if (e.key.length === 1) input.focus({ preventScroll: true })
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key.length === 1) setQuery((q) => q + e.key)
      else if (e.key === 'Backspace') setQuery((q) => q.slice(0, -1))
      else if (['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) keyRef.current?.(e)
      else return
      e.preventDefault()
      input?.focus({ preventScroll: true })
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, onClose])
  useEffect(() => setActive(0), [query])
  useEffect(() => {
    if (active >= results.length) setActive(Math.max(0, results.length - 1))
  }, [results.length, active])

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: 'nearest' })
  }, [active])

  const runAt = (i) => {
    const cmd = results[i]
    if (!cmd) return
    onClose()
    cmd.run()
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => (results.length ? (a + 1) % results.length : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => (results.length ? (a - 1 + results.length) % results.length : 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      runAt(active)
    }
  }

  keyRef.current = onKeyDown
  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      closable={false}
      width={600}
      style={{ top: 90 }}
      afterOpenChange={(o) => o && inputRef.current?.focus()}
      className="co-palette"
      title={null}
      aria-label="Command palette"
    >
      <Input
        ref={inputRef}
        autoFocus
        size="large"
        variant="borderless"
        prefix={<SearchOutlined />}
        placeholder="Jump to a page, project, task or workflow…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={(e) => {
          focusedOnce.current = true
          const end = e.target.value.length
          e.target.setSelectionRange(end, end)
        }}
        role="combobox"
        aria-expanded="true"
        aria-controls="co-palette-list"
        aria-activedescendant={results[active] ? `co-cmd-${active}` : undefined}
        data-testid="palette-input"
      />
      <div className="co-palette-list" id="co-palette-list" role="listbox" ref={listRef}>
        {results.length === 0 && (
          <div className="co-palette-empty">{loading ? 'Loading…' : 'Nothing matches.'}</div>
        )}
        {results.map((c, i) => (
          <div
            key={c.id}
            id={`co-cmd-${i}`}
            data-index={i}
            role="option"
            aria-selected={i === active}
            className={i === active ? 'co-palette-item co-palette-item-on' : 'co-palette-item'}
            onMouseMove={() => i !== active && setActive(i)}
            onClick={() => runAt(i)}
          >
            <span className="co-palette-label">{c.label}</span>
            <span className="co-palette-group">{c.group}</span>
          </div>
        ))}
      </div>
      <div className="co-palette-hint">
        <kbd>↑</kbd>
        <kbd>↓</kbd> move · <kbd>↵</kbd> run · <kbd>esc</kbd> close
      </div>
    </Modal>
  )
}
