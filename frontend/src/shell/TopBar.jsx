import { Button, Dropdown, Space, Tag, Tooltip } from 'antd'
import {
  CheckOutlined,
  KeyOutlined,
  SlidersOutlined,
  LockOutlined,
  LogoutOutlined,
  MoonOutlined,
  PauseCircleOutlined,
  SearchOutlined,
  SunOutlined,
} from '@ant-design/icons'
import { useThemeMode } from '../theme/ThemeModeContext.jsx'
import { IS_MAC } from './commands'

export const SHORTCUT_LABEL = IS_MAC ? '⌘K' : 'Ctrl K'

const MOTION_CHOICES = [
  { value: 'system', label: 'System' },
  { value: 'on', label: 'On' },
  { value: 'off', label: 'Off' },
]

// Top bar: ⌘K trigger, Claude credential tag, night toggle, avatar menu.
export default function TopBar({ user, claudeInfo, isAdmin, onOpenPalette, onOpenCredentials, onOpenPreferences, onChangePassword, onLogout }) {
  const { mode, setMode, motionPref, setMotionPref } = useThemeMode()
  const night = mode === 'night'
  const initials = (user.name || user.email || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <header className="co-topbar">
      <button
        type="button"
        className="co-cmd"
        data-testid="palette-trigger"
        aria-label={`Ask or jump (${SHORTCUT_LABEL})`}
        aria-haspopup="dialog"
        onClick={() => onOpenPalette()} // a <button>: Enter and Space click it too
      >
        <SearchOutlined className="co-cmd-icon" />
        <span className="co-cmd-text">Ask or jump…</span>
        <kbd>{SHORTCUT_LABEL}</kbd>
      </button>

      <Space size={10} className="co-topbar-right">
        {claudeInfo && <ClaudeTag info={claudeInfo} isAdmin={isAdmin} onClick={onOpenCredentials} />}
        <Tooltip title={night ? 'Day mode' : 'Night aurora'}>
          <Button
            shape="circle"
            icon={night ? <SunOutlined /> : <MoonOutlined />}
            onClick={() => setMode(night ? 'light' : 'night')}
            aria-label={night ? 'Switch to day mode' : 'Switch to night mode'}
            aria-pressed={night}
            data-testid="toggle-night"
          />
        </Tooltip>
        <Dropdown
          trigger={['click']}
          placement="bottomRight"
          menu={{
            items: [
              {
                key: 'me',
                disabled: true,
                label: (
                  <span>
                    {user.name}{' '}
                    <Tag style={{ marginInlineEnd: 0 }} color={user.role === 'admin' ? 'gold' : 'default'}>
                      {user.role}
                    </Tag>
                  </span>
                ),
              },
              { type: 'divider' },
              {
                key: 'motion',
                type: 'group',
                label: (
                  <span>
                    <PauseCircleOutlined /> Reduce motion
                  </span>
                ),
                children: MOTION_CHOICES.map((c) => ({
                  key: `motion:${c.value}`,
                  onClick: () => setMotionPref(c.value),
                  label: (
                    <Space
                      data-checked={motionPref === c.value}
                      data-testid={`motion-${c.value}`}
                      style={{ width: '100%', justifyContent: 'space-between' }}
                    >
                      {c.value === 'system' ? 'System (follow OS)' : c.label}
                      {motionPref === c.value ? <CheckOutlined aria-label="selected" /> : null}
                    </Space>
                  ),
                })),
              },
              { type: 'divider' },
              { key: 'creds', icon: <KeyOutlined />, label: 'My credentials', onClick: onOpenCredentials },
              { key: 'prefs', icon: <SlidersOutlined />, label: 'My preferences', onClick: onOpenPreferences },
              { key: 'pw', icon: <LockOutlined />, label: 'Change password', onClick: onChangePassword },
              { key: 'out', icon: <LogoutOutlined />, label: 'Log out', onClick: onLogout },
            ],
          }}
        >
          <button type="button" className="co-avatar" data-testid="user-menu" title={user.name} aria-label="User menu">
            {initials}
          </button>
        </Dropdown>
      </Space>
    </header>
  )
}

// Whether *you* can run tasks (your own Claude credential, the org fallback,
// or none), plus a server-side note if the CLI binary is missing.
export function ClaudeTag({ info, isAdmin, onClick }) {
  if (!info.found)
    return (
      <Tag color="red" title="The claude CLI isn't installed on the server (check CO_CLAUDE_BIN).">
        claude CLI not found
      </Tag>
    )
  const you = info.you?.claude
  const version = info.version?.split(' ')[0]
  const title = version ? `claude ${version} on the server` : undefined
  if (you === 'own')
    return (
      <Tag color="green" title={title} style={{ cursor: 'pointer' }} onClick={onClick}>
        Claude: {info.you.claude_kind === 'api_key' ? 'your API key' : 'your token'}
      </Tag>
    )
  if (you === 'fallback')
    return (
      <Tag color="blue" title={title} style={{ cursor: 'pointer' }} onClick={onClick}>
        Claude: org fallback
      </Tag>
    )
  return (
    <Tag
      color="red"
      data-testid="claude-not-set-up"
      style={{ cursor: 'pointer' }}
      onClick={onClick}
      title={
        isAdmin
          ? 'Add your own credential under My credentials, or set an org fallback key on the Users page'
          : 'Add your own credential under My credentials (or ask an admin to set an org fallback key)'
      }
    >
      Claude: not set up
    </Tag>
  )
}
