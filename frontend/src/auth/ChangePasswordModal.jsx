import { useEffect, useState } from 'react'
import { Form, Input, Modal, message } from 'antd'
import { api } from '../api'

export default function ChangePasswordModal({ open, onClose }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) form.resetFields()
  }, [open])
  const submit = async () => {
    const v = await form.validateFields().catch(() => null)
    if (!v) return
    setBusy(true)
    try {
      await api.changePassword(v.current, v.next)
      message.success('Password changed')
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title="Change password" open={open} onCancel={onClose} onOk={submit} confirmLoading={busy} okText="Change" destroyOnHidden>
      <Form form={form} layout="vertical">
        <Form.Item name="current" label="Current password" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="next" label="New password" rules={[{ required: true, min: 8, message: 'At least 8 characters' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
