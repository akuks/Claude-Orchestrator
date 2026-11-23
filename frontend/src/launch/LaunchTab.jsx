import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Empty, Input, Popconfirm, Space, Tag, Typography } from 'antd'
import { api } from '../api'
import { revealHidden } from '../guard/guard'
import { ACTION_STATUS, STALE_MSG, bySection, rowActions } from './launch'

const day = (s) => (s ? String(s).slice(0, 10) : '')
const LABEL = { start: 'Start', done: 'Done', drop: 'Drop', reopen: 'Reopen', edit: 'Edit', move: 'Move', delete: 'Delete' }
const COLOR = { open: 'default', started: 'blue', done: 'green', dropped: 'default' }

function Row({ item, canEdit, linkedDone, act }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(item.title)
  return (
    <div data-testid={`launch-item-${item.number}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--co-border, #f0f0f0)' }}>
      <Space wrap size={6}>
        <b>#{item.number}</b>
        <Tag data-testid="launch-status" color={COLOR[item.status]} bordered={false}>{item.status}</Tag>
        {editing ? (
          <>
            <Input size="small" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 260 }} data-testid={`launch-edit-input-${item.number}`} maxLength={200} />
            <Button size="small" type="primary" data-testid={`launch-edit-save-${item.number}`} onClick={async () => { if (await act(item, { title })) setEditing(false) }}>Save</Button>
            <Button size="small" onClick={() => { setEditing(false); setTitle(item.title) }}>Cancel</Button>
          </>
        ) : (
          <span>{revealHidden(item.title).text}</span>
        )}
        {item.started_at && <Typography.Text type="secondary" style={{ fontSize: 12 }}>started {day(item.started_at)}</Typography.Text>}
        {item.done_at && <Typography.Text type="secondary" style={{ fontSize: 12 }}>done {day(item.done_at)}</Typography.Text>}
      </Space>
      <div style={{ marginTop: 4 }}>
        <Space size={4} wrap>
          {rowActions(item, canEdit).map((a) => {
            const id = `launch-${a}-${item.number}`
            if (a === 'edit') return <Button key={a} size="small" type="link" data-testid={id} onClick={() => setEditing(true)}>{LABEL[a]}</Button>
            if (a === 'move')
              return (
                <Button key={a} size="small" type="link" data-testid={id} onClick={() => act(item, { section: item.section === 'before' ? 'after' : 'before' })}>
                  {item.section === 'before' ? 'Move to after' : 'Move to before'}
                </Button>
              )
            if (a === 'delete')
              return (
                <Popconfirm key={a} title="Delete this item?" onConfirm={() => act(item, null)}>
                  <Button size="small" type="link" danger data-testid={id}>{LABEL[a]}</Button>
                </Popconfirm>
              )
            return <Button key={a} size="small" type="link" data-testid={id} onClick={() => act(item, { status: ACTION_STATUS[a] })}>{LABEL[a]}</Button>
          })}
        </Space>
        {linkedDone && item.status !== 'done' && item.status !== 'dropped' && (
          <Typography.Text type="success" style={{ fontSize: 12 }} data-testid={`launch-hint-${item.number}`}> linked tasks done — mark done?</Typography.Text>
        )}
      </div>
    </div>
  )
}

function Section({ title, section, items, canEdit, linked, act, add }) {
  const [text, setText] = useState('')
  const [adding, setAdding] = useState(false)
  const doAdd = async () => {
    if (adding || !text.trim()) return
    setAdding(true)
    try {
      if (await add(section, text.trim())) setText('')
    } finally {
      setAdding(false)
    }
  }
  const rows = bySection(items, section)
  return (
    <div data-testid={`launch-section-${section}`} style={{ marginBottom: 20 }}>
      <Typography.Title level={5}>{title}</Typography.Title>
      {rows.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Nothing here yet" />}
      {rows.map((i) => <Row key={i.id} item={i} canEdit={canEdit} linkedDone={linked[String(i.number)]} act={act} />)}
      <Space.Compact style={{ marginTop: 8, width: '100%' }}>
        <Input value={text} maxLength={200} placeholder={`Add an item ${section} launch`} onChange={(e) => setText(e.target.value)} data-testid={`launch-add-input-${section}`} onPressEnter={doAdd} />
        <Button disabled={!text.trim() || adding} data-testid={`launch-add-${section}`} onClick={doAdd}>Add</Button>
      </Space.Compact>
    </div>
  )
}

// The project's launch list: date, critical paths and the before/after items. `canEdit` = maintainer or admin;
// contributors may add items and move them forward. The server has the last word on every move.
export default function LaunchTab({ project, canEdit }) {
  const pid = project.id
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [date, setDate] = useState('')
  const [paths, setPaths] = useState('')

  // `drafts`: also reset the date and paths inputs (first load, after Save, after a 409); other reloads keep them.
  const load = useCallback(async (drafts = false) => {
    try {
      const d = await api.getLaunch(pid)
      setData(d)
      if (drafts) {
        setDate(d?.launch?.launch_date || '')
        setPaths((d?.launch?.critical_paths || []).join('\n'))
      }
      return true
    } catch (e) {
      setError(e.message)
      return false
    }
  }, [pid])
  useEffect(() => { load(true) }, [load])

  const guard = async (fn, resetDrafts = false) => {
    setError(null)
    try {
      await fn()
      await load(resetDrafts)
      return true
    } catch (e) {
      if (e.status === 409) {
        await load(true)
        setError(STALE_MSG)
      } else setError(e.message)
      return false
    }
  }
  const add = (section, title) => guard(() => api.addLaunchItem(pid, { title, section }))
  const act = (item, body) =>
    guard(() => (body === null ? api.deleteLaunchItem(pid, item.number) : api.updateLaunchItem(pid, item.number, { version: item.version, ...body })))
  const save = () =>
    guard(() =>
      api.saveLaunch(pid, {
        launch_date: date.trim() || null,
        critical_paths: paths.split('\n').map((x) => x.trim()).filter(Boolean),
        version: data?.launch?.version ?? null,
      }),
      true,
    )

  if (!data) return error ? <Alert type="error" showIcon message={error} data-testid="launch-error" /> : null
  const items = data.items || []
  const linked = data.all_linked_done || {}
  return (
    <div data-testid="launch-tab">
      {error && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={error} data-testid="launch-error" />}
      <div style={{ marginBottom: 16 }}>
        <Typography.Text strong>Launch date</Typography.Text>{' '}
        {canEdit ? (
          <Input size="small" style={{ width: 140 }} value={date} placeholder="YYYY-MM-DD" onChange={(e) => setDate(e.target.value)} data-testid="launch-date" />
        ) : (
          <span data-testid="launch-date-text">{data.launch?.launch_date || 'not set'}</span>
        )}
        <div style={{ marginTop: 8 }}>
          <Typography.Text strong>Critical paths</Typography.Text>
          {canEdit ? (
            <>
              <Input.TextArea rows={3} value={paths} placeholder="One glob per line, e.g. src/billing/**" onChange={(e) => setPaths(e.target.value)} data-testid="launch-paths" />
              <Button size="small" type="primary" style={{ marginTop: 6 }} onClick={save} data-testid="launch-save">Save</Button>
            </>
          ) : (
            <div>{(data.launch?.critical_paths || []).map((p) => <code key={p} style={{ marginRight: 6 }}>{revealHidden(p).text}</code>)}</div>
          )}
        </div>
      </div>
      <Section title="Before launch" section="before" items={items} canEdit={canEdit} linked={linked} act={act} add={add} />
      <Section title="After launch" section="after" items={items} canEdit={canEdit} linked={linked} act={act} add={add} />
    </div>
  )
}
