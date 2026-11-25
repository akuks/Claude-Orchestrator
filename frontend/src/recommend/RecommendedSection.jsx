import { useState } from 'react'
import { Button, Input, Popconfirm, Space, Switch, Tag, Typography } from 'antd'
import { api } from '../api'
import { plainError } from '../plainError'
import './recommended.css'

// Admins add a tool; the item's own pinned preset is posted as a connected tool for this project only.
const NAME_MAX = 120 // the server's limit on a tool's name
const toServer = (preset, projectName) => ({
  name: `${preset.name} (${projectName})`.slice(0, NAME_MAX), // tool names are unique across the server
  transport: preset.transport || 'stdio',
  command: preset.command,
  args: preset.args || [],
  scope: 'project',
  project: projectName,
})

function Item({ it, project, isAdmin, reload }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const add = async () => {
    setBusy(true)
    setErr('')
    try {
      await api.createServer(toServer(it.preset, project.name))
      await reload?.()
    } catch (e) {
      setErr(e?.status === 409 ? 'A tool with this name already exists under Connected tools. Switch it on there.' : plainError(e))
    } finally {
      setBusy(false)
    }
  }
  let action
  if (it.present) action = <Tag color="green" data-testid="rec-added">Added</Tag>
  else if (it.kind === 'skill') action = <a href="#/automation" data-testid="rec-skills-link">Add it under Skills</a>
  else if (isAdmin) {
    action = (
      <Popconfirm title={`Add ${it.name} to this project?`} okText="Add" cancelText="Cancel" onConfirm={add}>
        <Button size="small" loading={busy} data-testid={`rec-add-${it.key}`}>Add</Button>
      </Popconfirm>
    )
  } else action = <span className="co-rec-muted">Ask an admin to add this</span>
  return (
    <div className="co-surface co-rec-item" data-testid={`rec-item-${it.key}`}>
      <div className="co-rec-head">
        <span className="co-rec-name">
          {it.name}
          {it.mark && <Tag color="orange" style={{ marginLeft: 8 }} data-testid="rec-mark">{it.mark}</Tag>}
        </span>
        <span>{action}</span>
      </div>
      <div className="co-rec-why">{it.why}</div>
      {err && <Typography.Text type="danger" className="co-rec-err">{err}</Typography.Text>}
    </div>
  )
}

export default function RecommendedSection({ project, data, reload, canEdit, isAdmin }) {
  const [brand, setBrand] = useState(data?.brand_skill || '')
  const [brandErr, setBrandErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!data?.has_ui) return null

  const save = async (patch, onFail, opts) => {
    setBusy(true)
    try {
      await api.updateProject(project.id, patch)
      await reload?.()
      return true
    } catch (e) {
      onFail(plainError(e, opts))
      return false
    } finally {
      setBusy(false)
    }
  }
  const toggle = async (on) => {
    setErr('')
    await save({ ui_skills: on }, setErr)
  }
  const saveBrand = async () => {
    setBrandErr('')
    await save({ brand_skill: brand.trim() || null }, setBrandErr, { brand: true })
  }
  return (
    <div data-testid="rec-section">
      <p className="co-rec-explain">This project has a user interface. These tools help agents build it and check how it looks. Nothing is added until an admin says so.</p>
      <div className="co-rec-list">
        {data.items.map((it) => (
          <Item key={it.key} it={it} project={project} isAdmin={isAdmin} reload={reload} />
        ))}
      </div>
      <div className="co-surface co-rec-settings">
        <Space>
          <Switch checked={!!data.ui_skills} disabled={!canEdit || busy} onChange={toggle} aria-label="Tell agents to use the design skills" data-testid="rec-ui-skills" />
          <span>Tell agents to use the design skills</span>
        </Space>
        {err && <div><Typography.Text type="danger">{err}</Typography.Text></div>}
        <div className="co-rec-brand">
          <label htmlFor="rec-brand-input">Brand skill name</label>
          <Space.Compact style={{ width: '100%', maxWidth: 360 }}>
            <Input id="rec-brand-input" value={brand} disabled={!canEdit} status={brandErr ? 'error' : undefined} placeholder="Leave empty to let agents look for one" onChange={(e) => setBrand(e.target.value)} data-testid="rec-brand" />
            {canEdit && <Button onClick={saveBrand} loading={busy} data-testid="rec-brand-save">Save</Button>}
          </Space.Compact>
          {brandErr && <div className="co-rec-err" data-testid="rec-brand-error"><Typography.Text type="danger">{brandErr}</Typography.Text></div>}
          {!data.brand_skill && data.brand_skill_found && <div className="co-rec-muted">A brand skill was found in the project and will be used.</div>}
        </div>
      </div>
    </div>
  )
}
