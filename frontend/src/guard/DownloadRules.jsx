import { useState } from 'react'
import { Button, message } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { api } from '../api'

// "Download the rules as a file": the plain-text rules export (org-wide, or one project's).
export default function DownloadRules({ projectId = null }) {
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const text = await api.exportRules(projectId)
      const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'rules.md'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button icon={<DownloadOutlined />} loading={busy} onClick={download} data-testid="download-rules">
      Download the rules as a file
    </Button>
  )
}
