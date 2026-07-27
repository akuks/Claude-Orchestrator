import { useEffect, useState } from 'react'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Upload,
  message,
} from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { api } from '../api'

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
        tags: values.tags || [],
        claude_md: values.project_id ? undefined : values.claude_md || undefined,
        input_files,
      })
      message.success('Task created')
      form.resetFields()
      setFileList([])
      onCreated?.()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setSubmitting(false)
    }
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
        </div>
        <Form.Item name="tags" label="Tags">
          <Select mode="tags" placeholder="Add tags" tokenSeparators={[',']} />
        </Form.Item>
        <Form.Item name="claude_md" label="CLAUDE.md (optional, injected into workspace)">
          <Input.TextArea rows={2} placeholder="Project instructions for this task" />
        </Form.Item>
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
