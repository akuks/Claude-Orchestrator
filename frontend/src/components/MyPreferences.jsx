import { useEffect, useState } from 'react'
import { Alert, Button, Collapse, Input, Modal, Space, Spin, Switch, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../api'

const { Paragraph, Text } = Typography
export const MAX_ITEMS = 12
export const MAX_CHARS = 240

// One line only: newlines (typed or pasted) become single spaces.
const oneLine = (v) => String(v || '').replace(/[\r\n\v\f\u0085\u2028\u2029]+/g, ' ')
const snap = (enabled, items) => JSON.stringify([enabled, items.map((i) => [i.id || null, i.text, i.on])])

// "My preferences": short standing notes the signed-in person writes for their own tasks.
export default function MyPreferences({ open, onClose }) {
  const [enabled, setEnabled] = useState(true)
  const [items, setItems] = useState([])
  const [preview, setPreview] = useState(null)
  const [base, setBase] = useState(null) // snapshot of the last loaded or saved state
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(false)

  const apply = (r) => {
    const en = r.enabled !== false
    const its = (r.items || []).map((i) => ({ id: i.id, text: i.text, on: i.on !== false }))
    setEnabled(en)
    setItems(its)
    setBase(snap(en, its))
    setPreview(r.preview || null)
  }

  useEffect(() => {
    if (!open) return
    setError(null)
    setDone(false)
    setLoaded(false)
    setBase(null)
    setLoading(true)
    api
      .getMyPreferences()
      .then((r) => { apply(r); setLoaded(true) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [open])

  const edit = (idx, patch) => {
    setDone(false)
    setItems((xs) => xs.map((x, i) => (i === idx ? { ...x, ...patch } : x)))
  }
  const add = () => {
    setDone(false)
    setItems((xs) => (xs.length >= MAX_ITEMS ? xs : [...xs, { text: '', on: true }]))
  }
  const remove = (idx) => {
    setDone(false)
    setItems((xs) => xs.filter((_, i) => i !== idx))
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setDone(false)
    try {
      const r = await api.saveMyPreferences({
        enabled,
        items: items.map((i) => (i.id ? { id: i.id, text: i.text, on: i.on } : { text: i.text, on: i.on })),
      })
      apply(r)
      setDone(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="My preferences"
      open={open}
      onCancel={onClose}
      width={640}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={onClose} data-testid="pref-cancel">Cancel</Button>
          <Button type="primary" loading={saving} disabled={loading || !loaded} onClick={save} data-testid="pref-save">Save</Button>
        </Space>
      }
    >
      <Spin spinning={loading}>
        <Paragraph type="secondary">
          Short notes about how you like to work. They are added to every task you start, in any project.
          Project rules and decisions come first.
        </Paragraph>
        <Space style={{ marginBottom: 12 }}>
          <Switch checked={enabled} onChange={(v) => { setEnabled(v); setDone(false) }} data-testid="pref-master" aria-label="Add my preferences to my tasks" />
          <Text>Add my preferences to my tasks</Text>
        </Space>
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          {items.map((it, idx) => (
            <div key={it.id || `new-${idx}`} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <Switch size="small" style={{ marginTop: 8 }} checked={it.on} onChange={(v) => edit(idx, { on: v })} data-testid={`pref-on-${idx}`} aria-label="Use this preference" />
              <div style={{ flex: 1 }}>
                <Input.TextArea
                  data-testid={`pref-text-${idx}`}
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  maxLength={MAX_CHARS}
                  value={it.text}
                  placeholder="For example: keep answers short"
                  onChange={(e) => edit(idx, { text: oneLine(e.target.value) })}
                  onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault() }}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>{it.text.length} / {MAX_CHARS}</Text>
              </div>
              <Button type="text" icon={<DeleteOutlined />} aria-label="Delete this preference" onClick={() => remove(idx)} data-testid={`pref-delete-${idx}`} />
            </div>
          ))}
        </Space>
        <div style={{ marginTop: 12 }}>
          <Space>
            <Button icon={<PlusOutlined />} onClick={add} disabled={items.length >= MAX_ITEMS} data-testid="pref-add">Add a preference</Button>
            <Text type="secondary">{items.length} of {MAX_ITEMS}</Text>
          </Space>
        </div>
        <Collapse
          ghost
          style={{ marginTop: 12 }}
          items={[{
            key: 'seen',
            forceRender: true,
            label: 'What the agent sees',
            children: (
              <>
                {loaded && snap(enabled, items) !== base && <Paragraph type="secondary">Save to update.</Paragraph>}
                {preview ? <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }} data-testid="pref-preview">{preview}</pre> : <Text type="secondary">Nothing would be added.</Text>}
              </>
            ),
          }]}
        />
        {done && <Alert style={{ marginTop: 12 }} type="success" showIcon message="Saved. Your next tasks will use these." data-testid="pref-done" />}
        {error && <Alert style={{ marginTop: 12 }} type="error" showIcon message={error} data-testid="pref-error" />}
      </Spin>
    </Modal>
  )
}
