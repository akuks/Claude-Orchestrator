import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState } from 'react'
import { ConfigProvider } from 'antd'
import { antdTheme, cssVars } from './tokens'

const MODE_KEY = 'co.mode'
const REDUCE_KEY = 'co.reduceMotion'

function safeGet(storage, key) {
  try {
    return storage ? storage.getItem(key) : null
  } catch {
    return null
  }
}

export function persist(storage, key, value) {
  try {
    storage?.setItem(key, String(value))
  } catch {
    // private window / blocked storage: the setting just isn't remembered
  }
}

export function initialMode(storage) {
  return safeGet(storage, MODE_KEY) === 'night' ? 'night' : 'light'
}

export function initialReduceMotion(storage, matchMedia) {
  const stored = safeGet(storage, REDUCE_KEY)
  if (stored === 'true') return true
  if (stored === 'false') return false
  try {
    return Boolean(matchMedia?.('(prefers-reduced-motion: reduce)')?.matches)
  } catch {
    return false
  }
}

export function applyThemeToDocument(el, mode, reduceMotion) {
  if (!el) return
  for (const [k, v] of Object.entries(cssVars(mode))) el.style.setProperty(k, v)
  el.dataset.mode = mode
  el.dataset.reduceMotion = String(Boolean(reduceMotion))
  el.style.colorScheme = mode === 'night' ? 'dark' : 'light'
}

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

const browserMatchMedia = (q) => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(q) : null)

const ThemeModeContext = createContext({
  mode: 'light',
  setMode: () => {},
  reduceMotion: false,
  setReduceMotion: () => {},
})

export function ThemeModeProvider({ children }) {
  const [mode, setModeState] = useState(() => initialMode(browserStorage()))
  const [reduceMotion, setReduceState] = useState(() =>
    initialReduceMotion(browserStorage(), browserMatchMedia),
  )

  const setMode = useCallback((m) => {
    const next = m === 'night' ? 'night' : 'light'
    persist(browserStorage(), MODE_KEY, next)
    setModeState(next)
  }, [])

  const setReduceMotion = useCallback((v) => {
    persist(browserStorage(), REDUCE_KEY, Boolean(v))
    setReduceState(Boolean(v))
  }, [])

  useLayoutEffect(() => {
    applyThemeToDocument(document.documentElement, mode, reduceMotion)
  }, [mode, reduceMotion])

  const value = useMemo(
    () => ({ mode, setMode, reduceMotion, setReduceMotion }),
    [mode, setMode, reduceMotion, setReduceMotion],
  )
  const themeConfig = useMemo(() => antdTheme(mode), [mode])

  return (
    <ThemeModeContext.Provider value={value}>
      <ConfigProvider theme={themeConfig}>
        <div className="co-aurora" aria-hidden="true" />
        {children}
      </ConfigProvider>
    </ThemeModeContext.Provider>
  )
}

export function useThemeMode() {
  return useContext(ThemeModeContext)
}
