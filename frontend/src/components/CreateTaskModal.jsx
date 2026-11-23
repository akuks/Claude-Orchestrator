import { useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Switch,
  Upload,
  message,
} from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { api } from '../api'
import BriefFields from '../briefs/BriefFields'
import ScopeNudge from '../launch/ScopeNudge'
import { isOpen, titleFromPrompt } from '../launch/launch'
import { BRIEF_LABELS, briefMissing, briefPayload } from '../briefs/brief'

// Read a File into base64 (strips the data: URL prefix).
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

export default function CreateTaskModal({
  open,
  onClose,
  onCreated,
  projects = [],
  activeProjectId = null,
}) {
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [fileList, setFileList] = useState([])
  const [briefError, setBriefError] = useState('')
  const [nudge, setNudge] = useState(null) // {open, would_be, values, brief}: waiting for a choice
  const [items, setItems] = useState([])
  const projectId = Form.useWatch('project_id', form)

  // Open and started items of the chosen project, for the "Launch item" select. Errors: no list.
  useEffect(() => {
    let live = true
    setItems([])
    if (open && projectId) {
      Promise.resolve()
        .then(() => api.getLaunch(projectId))
        .then((d) => live && setItems((d?.items || []).filter(isOpen)))
        .catch(() => {})
    }
    return () => {
      live = false
    }
  }, [open, projectId])

  useEffect(() => {
    if (open) form.setFieldsValue({ project_id: activeProjectId || undefined })
  }, [open, activeProjectId])

  const submit = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    if (!items.some((i) => i.id === values.launch_item_id)) values = { ...values, launch_item_id: undefined }
    const brief = briefPayload(values.brief)
    const missing = briefMissing(values.brief)
    if (missing.length) {
      setBriefError(`The brief needs: ${missing.map((k) => BRIEF_LABELS[k]).join(', ')} (${missing.join(', ')})`)
      return
    }
    setBriefError('')
    setNudge(null)
    if (values.project_id) {
      // Never blocks: an error from the check means no nudge.
      let check = null
      setSubmitting(true)
      try {
        check = await api.scopeCheck(values.project_id, values.prompt)
      } catch {
        check = null
      }
      if (check?.new_scope) {
        setSubmitting(false)
        setNudge({ open: check.open, would_be: check.would_be, values, brief })
        return
      }
      setSubmitting(false)
    }
    await create(values, brief, values.launch_item_id)
  }

  const create = async (values, brief, launchItemId) => {
    setSubmitting(true)
    try {
      const input_files = await Promise.all(
        fileList.map(async (f) => ({
          name: f.name,
          content_base64: await fileToBase64(f.originFileObj),
        }))
      )
      await api.createTask({
        prompt: values.prompt,
        title: values.title || undefined,
        project_id: values.project_id || undefined,
        model: values.model || undefined, // undefined => inherit project default
        priority: values.priority,
        max_turns: values.max_turns,
        max_budget_usd: values.max_budget_usd ?? undefined,
        tags: values.tags || [],
        claude_md: values.project_id ? undefined : values.claude_md || undefined,
        requires_approval: !!values.requires_approval,
        input_files,
        ...(brief ? { brief } : {}),
        ...(launchItemId ? { launch_item_id: launchItemId } : {}),
      })
      message.success('Task created')
      form.resetFields()
      setBriefError('')
      setNudge(null)
      setFileList([])
      onCreated?.()
      onClose()
    } catch (e) {
      if (brief) setBriefError(e.message)
      message.error(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  // "Add to launch list" / "After launch": make the item from the prompt, then the task linked to it.
  const addAndCreate = async (section) => {
    const { values, brief } = nudge
    setSubmitting(true)
    let item
    try {
      item = await api.addLaunchItem(values.project_id, { title: titleFromPrompt(values.prompt), section })
    } catch (e) {
      message.error(e.message)
      setSubmitting(false)
      return
    }
    setSubmitting(false)
    await create(values, brief, item?.id)
  }

  return (
    <Modal
      title="New Task"
      open={open}
      onCancel={onClose}
      width={640}
      footer={[
        <Button key="cancel" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="run" type="primary" loading={submitting} onClick={submit}>
          Run Task
        </Button>,
      ]}
    >
      {nudge && (
        <ScopeNudge
          open={nudge.open}
          wouldBe={nudge.would_be}
          busy={submitting}
          onAdd={() => addAndCreate('before')}
          onAfter={() => addAndCreate('after')}
          onRunAnyway={() => create(nudge.values, nudge.brief, nudge.values.launch_item_id)}
        />
      )}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ priority: 'normal', max_turns: 25 }}
      >
        <Form.Item
          name="prompt"
          label="Prompt"
          rules={[{ required: true, message: 'A prompt is required' }]}
        >
          <Input.TextArea rows={4} placeholder="What should Claude do?" />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="title" label="Title (optional)" style={{ flex: 1 }}>
            <Input placeholder="Defaults to first line of prompt" />
          </Form.Item>
          <Form.Item
            name="project_id"
            label="Project (optional)"
            style={{ flex: 1 }}
            extra="Runs in the project's directory with its memory & context."
          >
            <Select
              allowClear
              placeholder="No project (sandbox)"
              options={projects
                .filter((p) => !p.archived)
                .map((p) => ({ value: p.id, label: p.name }))}
            />
          </Form.Item>
        </div>
        {projectId && items.length > 0 && (
          <Form.Item name="launch_item_id" label="Launch item (optional)">
            <Select
              allowClear
              placeholder="Not linked"
              options={items.map((i) => ({ value: i.id, label: `#${i.number} ${i.title}` }))}
            />
          </Form.Item>
        )}
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item
            name="model"
            label="Model"
            style={{ flex: 1 }}
            extra="Leave blank to use the project default or your Claude Code default."
          >
            <Select
              allowClear
              placeholder="Default"
              options={[
                { value: 'sonnet', label: 'Sonnet' },
                { value: 'opus', label: 'Opus' },
                { value: 'haiku', label: 'Haiku' },
              ]}
            />
          </Form.Item>
          <Form.Item name="priority" label="Priority" style={{ flex: 1 }}>
            <Select
              options={[
                { value: 'low', label: 'Low' },
                { value: 'normal', label: 'Normal' },
                { value: 'high', label: 'High' },
                { value: 'urgent', label: 'Urgent' },
              ]}
            />
          </Form.Item>
          <Form.Item name="max_turns" label="Max Turns" style={{ flex: 1 }}>
            <InputNumber min={1} max={200} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="max_budget_usd"
            label="Budget cap"
            style={{ flex: 1 }}
            tooltip="Hard spend limit ($). Claude stops before exceeding it."
          >
            <InputNumber min={0} step={0.5} prefix="$" style={{ width: '100%' }} placeholder="none" />
          </Form.Item>
        </div>
        <Form.Item name="tags" label="Tags">
          <Select mode="tags" placeholder="Add tags" tokenSeparators={[',']} />
        </Form.Item>
        <Form.Item
          name="requires_approval"
          label="Require approval before running"
          valuePropName="checked"
          extra="Task waits in the Approval Inbox until you approve it."
        >
          <Switch />
        </Form.Item>
        <Form.Item name="claude_md" label="CLAUDE.md (optional, injected into workspace)">
          <Input.TextArea rows={2} placeholder="Project instructions for this task" />
        </Form.Item>
        <BriefFields error={briefError} />
        <Form.Item label="Input files (optional)">
          <Upload
            multiple
            beforeUpload={() => false}
            fileList={fileList}
            onChange={({ fileList }) => setFileList(fileList)}
          >
            <Button icon={<UploadOutlined />}>Attach files</Button>
          </Upload>
        </Form.Item>
      </Form>
    </Modal>
  )
}
