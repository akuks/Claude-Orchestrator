import { useEffect, useState } from 'react'
import { Alert, Input, Modal, Radio, Select, Typography } from 'antd'
import { api } from '../api'

// Import a workflow from pasted markdown or a repo file. The server parses and
// creates it; the caller then opens the created workflow in the editor.
export default function ImportWorkflow({ open, onClose, onImported }) {
  const [mode, setMode] = useState('paste')
  const [text, setText] = useState('')
  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState(null)
  const [files, setFiles] = useState([])
  const [reason, setReason] = useState('')
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setError(''); setText(''); setFile(null); setProjectId(null); setFiles([]); setReason('')
    api.listProjects?.().then((p) => setProjects(p || [])).catch(() => {})
  }, [open])

  const pickProject = async (id) => {
    setProjectId(id); setFile(null); setFiles([]); setReason('')
    try {
      const r = await api.workflowRepoFiles(id)
      setFiles(r.files || [])
      setReason(r.reason || '')
    } catch (e) {
      setReason(e.message)
    }
  }

  const go = async () => {
    setBusy(true)
    setError('')
    try {
      const wf = await api.importWorkflow(mode === 'paste' ? { text } : { project_id: projectId, file })
      onImported?.(wf)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const ready = mode === 'paste' ? text.trim() !== '' : !!(projectId && file)

  return (
    <Modal
      title="Import workflow"
      open={open}
      onCancel={onClose}
      onOk={go}
      okText="Import"
      okButtonProps={{ disabled: !ready }}
      confirmLoading={busy}
    >
      <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} style={{ marginBottom: 10 }}>
        <Radio.Button value="paste">Paste</Radio.Button>
        <Radio.Button value="repo">Repo file</Radio.Button>
      </Radio.Group>
      {mode === 'paste' ? (
        <Input.TextArea
          rows={10}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste an exported workflow (markdown with one json block)"
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Select
            placeholder="Project"
            value={projectId}
            onChange={pickProject}
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
          />
          <Select
            placeholder="Workflow file"
            value={file}
            onChange={setFile}
            options={files.map((f) => ({ value: f.name, label: f.name }))}
          />
          {reason && <Typography.Text type="secondary">{reason}</Typography.Text>}
        </div>
      )}
      <Typography.Paragraph type="secondary" style={{ marginTop: 10, marginBottom: 0, fontSize: 12 }}>
        The workflow is created on import; review it in the editor and press Save to keep your changes.
      </Typography.Paragraph>
      {error && <Alert type="error" showIcon message={error} style={{ marginTop: 10 }} />}
    </Modal>
  )
}
