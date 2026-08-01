import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  message,
} from 'antd'
import { api } from '../api'

const MODEL_OPTS = [
  { value: 'opus', label: 'Opus (deepest — recommended)' },
  { value: 'sonnet', label: 'Sonnet' },
  { value: 'haiku', label: 'Haiku' },
]

// Build a VAPT / STQC-style security-review prompt. Read-only; never mutates the repo.
function buildPrompt({ branch, base, scope }) {
  const gather =
    scope === 'full'
      ? `1. Run: git fetch --all --quiet
2. Create an ISOLATED read-only checkout without disturbing the working tree:
   git worktree add --detach /tmp/vapt-review-${branch.replace(/[^a-zA-Z0-9]/g, '_')} origin/${branch}
   Review the code under that path (full-branch audit). Focus on entry points,
   auth/session code, API/route handlers, DB access, crypto, config, file/OS
   operations, and dependency manifests.
3. When finished: git worktree remove --force <that path>`
      : `1. Run: git fetch --all --quiet
2. Review the change set on the branch versus base:
   git diff origin/${base}...origin/${branch}   (and git diff --stat for an overview)
3. Read full file context as needed with: git show origin/${branch}:<path>`

  return `SECURITY CODE REVIEW for STQC / VAPT compliance. This is a NON-INTERACTIVE, READ-ONLY audit: never modify, commit, push, switch the working branch, or open/merge PRs. Do not ask questions — perform the audit and produce the report.

Repository: this project's directory (a git clone). Target branch: "${branch}". Base branch: "${base}".
Scope: ${scope === 'full' ? 'FULL branch audit' : 'changes on the branch vs base'}.

Gather the code (read-only git):
${gather}

Audit for security vulnerabilities aligned with OWASP Top 10, SANS/CWE Top 25, and common VAPT findings:
- Injection: SQL, command/OS, LDAP, XSS, template, path traversal
- Broken authentication, session management, and access control (IDOR, missing authz, privilege escalation)
- Cryptographic failures: weak/deprecated algorithms, hardcoded keys/IVs, insecure randomness, plaintext storage
- Hardcoded secrets, credentials, API tokens, private keys
- Sensitive data exposure and insecure logging (PII, secrets in logs)
- Insecure deserialization, SSRF, XXE
- Security misconfiguration, unsafe defaults, debug/admin endpoints, permissive CORS
- Missing input validation / output encoding
- Vulnerable or outdated dependencies (inspect manifests if changed)
- Unsafe file handling, race conditions, and insecure use of subprocess/eval

Produce a structured VAPT-style report:

## Summary
- Overall risk rating: Critical / High / Medium / Low
- Findings count by severity.

## Findings
For EACH finding, include:
- Severity: Critical | High | Medium | Low | Info
- Category: OWASP A0x:2021 and/or CWE-<id>
- Location: <file>:<line>
- Description — what the issue is
- Impact — how it could be exploited
- Remediation — a concrete, actionable fix

If no security issues are found, say so explicitly and list what you inspected.
Reminder: report only — make no code changes and no GitHub writes.`
}

export default function CodeReviewModal({ open, projects = [], onClose, onCreated }) {
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (open) {
      form.resetFields()
      form.setFieldsValue({ base: 'main', scope: 'changed', model: 'opus', max_turns: 60 })
    }
  }, [open])

  const submit = async () => {
    let v
    try {
      v = await form.validateFields()
    } catch {
      return
    }
    setSubmitting(true)
    try {
      // Unique, readable title so repeated runs of the same branch don't collide.
      const now = new Date()
      const stamp = `${now.toLocaleDateString()} ${now.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      })}`
      const scopeTag = v.scope === 'full' ? 'full' : 'diff'
      await api.createTask({
        title: `Security review: ${v.branch} (${scopeTag}) — ${stamp}`,
        prompt: buildPrompt({ branch: v.branch, base: v.base || 'main', scope: v.scope }),
        project_id: v.project_id,
        model: v.model,
        max_turns: v.max_turns,
        tags: ['security', 'vapt', 'code-review'],
      })
      message.success('Security review started')
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
      title="🔒 Security Code Review (STQC / VAPT)"
      open={open}
      onCancel={onClose}
      width={620}
      footer={[
        <Button key="c" onClick={onClose}>
          Cancel
        </Button>,
        <Button key="r" type="primary" loading={submitting} onClick={submit}>
          Start Review
        </Button>,
      ]}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Read-only security audit. Reviews the branch for OWASP/CWE vulnerabilities and produces a findings report by severity. Never modifies code or GitHub."
      />
      <Form form={form} layout="vertical">
        <Form.Item
          name="project_id"
          label="Repository (Project)"
          rules={[{ required: true, message: 'Pick the repo to review' }]}
          extra="The review runs in this project's local clone."
        >
          <Select
            placeholder="Select a project"
            options={projects
              .filter((p) => !p.archived)
              .map((p) => ({ value: p.id, label: p.name }))}
          />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item
            name="branch"
            label="Branch to review"
            style={{ flex: 1 }}
            rules={[{ required: true, message: 'Enter the branch name' }]}
          >
            <Input placeholder="e.g. feature/login" />
          </Form.Item>
          <Form.Item name="base" label="Base branch" style={{ width: 160 }}>
            <Input placeholder="main" />
          </Form.Item>
        </div>
        <Form.Item name="scope" label="Scope">
          <Radio.Group>
            <Radio value="changed">Changed files (branch vs base)</Radio>
            <Radio value="full">Full branch audit</Radio>
          </Radio.Group>
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="model" label="Model" style={{ flex: 1 }}>
            <Select options={MODEL_OPTS} />
          </Form.Item>
          <Form.Item
            name="max_turns"
            label="Max turns"
            style={{ width: 140 }}
            extra="Higher = more thorough"
          >
            <InputNumber min={10} max={200} style={{ width: '100%' }} />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  )
}
