import { theme } from 'antd'

// "Aurora Sunset" — the one place colours live. Components read them through
// antd tokens (ConfigProvider), CSS variables (var(--co-*)), or, for canvas
// charts that can't resolve CSS variables, modeTokens(mode).

const status = {
  success: '#10b981',
  warning: '#f59e0b',
  danger: '#e11d48',
  info: '#6366f1',
}

// The live-output terminal and code blocks stay dark in both modes.
const terminal = {
  bg: '#120b16',
  elevated: '#1c1222',
  border: '#2e2033',
  text: '#f5e9ef',
  muted: '#9a8592',
  dim: '#8c7886',
  accent: '#7dd3fc',
  error: '#fb7185',
  success: '#34d399',
}

const SUNSET = ['#f59e0b', '#f97316', '#e11d48'] // amber, orange, rose

export const palette = {
  primary: '#f97316',
  amber: '#f59e0b',
  orange: '#f97316',
  rose: '#e11d48',
  gradient: 'linear-gradient(135deg,#e11d48,#f97316)',
  logo: 'conic-gradient(from 210deg,#f59e0b,#f97316,#e11d48,#f59e0b)',
  haze:
    'linear-gradient(90deg, transparent, rgba(245,158,11,.42), rgba(249,115,22,.4), rgba(225,29,72,.36), transparent)',
  glow: 'rgba(249,115,22,.45)',
  radius: 16,
  status,
  terminal,
  light: {
    bg: 'linear-gradient(180deg,#ffffff,#fff7f0 60%,#fff4f6)',
    bgSolid: '#fffaf6',
    surface: 'rgba(255,255,255,.85)',
    surfaceSolid: '#ffffff',
    elevated: '#ffffff',
    header: 'rgba(255,255,255,.72)',
    border: '#fde7d6',
    borderSecondary: '#feefe3',
    shadow: '0 10px 24px rgba(249,115,22,.08)',
    text: '#3b2433',
    textSecondary: '#7d6272',
    textMuted: '#8f7886',
    // Coloured text (stat values, links, placeholders) needs darker shades than
    // the status/brand fills to reach 4.5:1 on white.
    successText: '#047857',
    warningText: '#b45309',
    infoText: '#4f46e5',
    dangerText: '#be123c',
    link: '#c2410c',
    linkHover: '#9a3412',
    placeholder: '#7d6272',
    hover: 'rgba(249,115,22,.06)',
    activeBg: 'rgba(249,115,22,.10)',
    activeBorder: 'rgba(249,115,22,.40)',
    guide: 'rgba(138,111,125,.30)',
    hazeOpacity: '0.9',
    // SVG comets: flow dashes, approval-star fill, head-label text per state.
    cometFlow: '#ffffff',
    cometFlowOpacity: '0.85',
    cometWaitFill: '#fef3c7',
    cometLabelCurrent: '#c2410c',
    cometLabelFailed: '#be123c',
    cometLabelWait: '#b45309',
    // 3D scene (shaders can't read CSS variables): upcoming-stage ring, trail
    // flow highlight, head-spark core, and the particle-field colours.
    starNext: '#c4a9b8',
    sceneFlow: '#ffffff',
    sparkCore: '#ffffff',
    sceneDust: SUNSET,
  },
  night: {
    bg: 'linear-gradient(180deg,#0f0a14,#1a0f1f)',
    bgSolid: '#0f0a14',
    surface: 'rgba(255,255,255,.06)',
    surfaceSolid: '#1d1322',
    elevated: '#21152a',
    header: 'rgba(255,255,255,.04)',
    border: 'rgba(255,255,255,.10)',
    borderSecondary: 'rgba(255,255,255,.06)',
    shadow: '0 10px 24px rgba(0,0,0,.35)',
    text: '#fbeff3',
    textSecondary: '#c9b3bf',
    textMuted: '#93808b',
    successText: status.success,
    warningText: status.warning,
    infoText: status.info,
    dangerText: '#fb7185', // status.danger is too dark on the night surfaces
    hover: 'rgba(249,115,22,.10)',
    activeBg: 'rgba(249,115,22,.16)',
    activeBorder: 'rgba(249,115,22,.50)',
    guide: 'rgba(251,239,243,.22)',
    hazeOpacity: '0.7',
    cometFlow: '#ffffff',
    cometFlowOpacity: '0.5',
    cometWaitFill: '#3a2a10',
    cometLabelCurrent: '#fdba74',
    cometLabelFailed: '#fda4af',
    cometLabelWait: '#fcd34d',
    starNext: '#a8929f',
    sceneFlow: '#fff4e6',
    sparkCore: '#fff7ed',
    sceneDust: [...SUNSET, '#fbeff3'],
  },
}

export const MODES = ['light', 'night']
const norm = (mode) => (mode === 'night' ? 'night' : 'light')

// Concrete colours for one mode (for canvas charts and other JS consumers).
export function modeTokens(mode) {
  return { ...palette[norm(mode)], primary: palette.primary, status, terminal }
}

const STATUS_TOKEN = {
  completed: 'success',
  failed: 'danger',
  running: 'info',
  awaiting_approval: 'warning',
}

// Task/run status → colour. Neutral states (queued, cancelled, unknown) use
// the mode's muted text colour.
export function statusColor(s, mode = 'light') {
  const key = STATUS_TOKEN[s]
  return key ? status[key] : modeTokens(mode).textMuted
}

const STATUS_VAR = {
  success: 'var(--co-success)',
  danger: 'var(--co-danger)',
  info: 'var(--co-info)',
  warning: 'var(--co-warning)',
}

// Same mapping as statusColor, as a CSS variable (follows the active mode).
export function statusVar(s) {
  const key = STATUS_TOKEN[s]
  return key ? STATUS_VAR[key] : 'var(--co-text-muted)'
}

export const FONT_FAMILY =
  "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
export const MONO_FAMILY = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

export function antdTheme(mode) {
  const m = norm(mode)
  const p = palette[m]
  return {
    algorithm: m === 'night' ? theme.darkAlgorithm : theme.defaultAlgorithm,
    cssVar: false,
    token: {
      colorPrimary: palette.primary,
      colorLink: p.link || palette.primary,
      // Light only: night keeps antd's derived hover and placeholder colours.
      ...(p.linkHover ? { colorLinkHover: p.linkHover, colorLinkActive: p.linkHover } : {}),
      ...(p.placeholder ? { colorTextPlaceholder: p.placeholder } : {}),
      colorInfo: status.info,
      colorSuccess: status.success,
      colorWarning: status.warning,
      colorError: status.danger,
      colorBgBase: p.bgSolid,
      colorBgLayout: 'transparent',
      colorBgContainer: p.surface,
      colorBgElevated: p.elevated,
      colorBorder: p.border,
      colorBorderSecondary: p.borderSecondary,
      colorTextBase: p.text,
      colorText: p.text,
      colorTextSecondary: p.textSecondary,
      colorTextTertiary: p.textMuted,
      fontFamily: FONT_FAMILY,
      fontFamilyCode: MONO_FAMILY,
      fontSize: 14,
      borderRadius: 10,
      borderRadiusLG: palette.radius,
    },
    components: {
      Layout: {
        bodyBg: 'transparent',
        headerBg: p.header,
        headerColor: p.text,
      },
      Card: { boxShadowTertiary: p.shadow },
      // Solid, not glass: tables nest several backgrounds (wrapper, cells,
      // fixed columns) and stacked translucency reads as a grey slab at night.
      Table: { colorBgContainer: p.surfaceSolid, headerBg: p.surfaceSolid },
    },
  }
}

export function cssVars(mode) {
  const m = norm(mode)
  const p = palette[m]
  return {
    '--co-bg': p.bg,
    '--co-bg-solid': p.bgSolid,
    '--co-surface': p.surface,
    '--co-surface-solid': p.surfaceSolid,
    '--co-header': p.header,
    '--co-border': p.border,
    '--co-shadow': p.shadow,
    '--co-radius': `${palette.radius}px`,
    '--co-text': p.text,
    '--co-text-secondary': p.textSecondary,
    '--co-text-muted': p.textMuted,
    '--co-hover': p.hover,
    '--co-active-bg': p.activeBg,
    '--co-active-border': p.activeBorder,
    '--co-guide': p.guide,
    '--co-primary': palette.primary,
    '--co-gradient': palette.gradient,
    '--co-logo': palette.logo,
    '--co-haze': palette.haze,
    '--co-haze-opacity': p.hazeOpacity,
    '--co-glow': palette.glow,
    '--co-comet-flow': p.cometFlow,
    '--co-comet-flow-opacity': p.cometFlowOpacity,
    '--co-comet-wait-fill': p.cometWaitFill,
    '--co-comet-label-current': p.cometLabelCurrent,
    '--co-comet-label-failed': p.cometLabelFailed,
    '--co-comet-label-wait': p.cometLabelWait,
    '--co-success': status.success,
    '--co-success-text': p.successText,
    '--co-warning-text': p.warningText,
    '--co-info-text': p.infoText,
    '--co-danger-text': p.dangerText,
    '--co-warning': status.warning,
    '--co-danger': status.danger,
    '--co-info': status.info,
    '--co-terminal-bg': terminal.bg,
    '--co-terminal-elevated': terminal.elevated,
    '--co-terminal-border': terminal.border,
    '--co-terminal-text': terminal.text,
    '--co-terminal-muted': terminal.muted,
    '--co-terminal-dim': terminal.dim,
    '--co-terminal-accent': terminal.accent,
    '--co-terminal-error': terminal.error,
    '--co-terminal-success': terminal.success,
    '--co-font': FONT_FAMILY,
    '--co-mono': MONO_FAMILY,
  }
}
