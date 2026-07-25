import { theme } from 'antd'

// "warmtone" — a warm, low-glare dark palette built around Claude's terracotta
// accent. Warm browns replace the stock cold grays across surfaces, borders,
// and the live-output terminal.
export const warm = {
  bgBase: '#17120f',
  bgContainer: '#1e1815',
  bgElevated: '#241d19',
  bgTerminal: '#140f0c',
  border: '#3a2f28',
  primary: '#d97757',
  text: '#f0e9e3',
  textSecondary: '#b8a99d',
  textMuted: '#8a7a6d',
}

export const warmTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: warm.primary,
    colorInfo: warm.primary,
    colorBgBase: warm.bgBase,
    colorBgContainer: warm.bgContainer,
    colorBgElevated: warm.bgElevated,
    colorBorder: warm.border,
    colorBorderSecondary: '#2c2420',
    colorText: warm.text,
    colorTextSecondary: warm.textSecondary,
    borderRadius: 8,
  },
  components: {
    Layout: {
      headerBg: '#1a1512',
      bodyBg: warm.bgBase,
    },
  },
}
