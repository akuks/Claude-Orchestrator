import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { ConfigProvider } from 'antd'
import { antdTheme, cssVars } from './tokens'

const MODE_KEY = 'co.mode'
const REDUCE_KEY = 'co.reduceMotion'
const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'
// Reduce motion: follow the OS ("system", the default), or force on/off.
export const MOTION_PREFS = ['system', 'on', 'off']
// Before the tri-state, the setting was stored as a boolean.
const LEGACY_PREF = { true: 'on', false: 'off' }

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

// The stored motion preference; an old boolean is migrated (and rewritten).
export function initialMotionPref(storage) {
  const stored = safeGet(storage, REDUCE_KEY)
  if (MOTION_PREFS.includes(stored)) return stored
  if (stored in LEGACY_PREF) {
    persist(storage, REDUCE_KEY, LEGACY_PREF[stored])
    return LEGACY_PREF[stored]
  }
  return 'system'
}

export function systemPrefersReducedMotion(matchMedia) {
  try {
    return Boolean(matchMedia?.(REDUCE_QUERY)?.matches)
  } catch {
    return false
  }
}

export function resolveReduceMotion(pref, systemReduce) {
  if (pref === 'on') return true
  if (pref === 'off') return false
  return Boolean(systemReduce)
}

export function initialReduceMotion(storage, matchMedia) {
  return resolveReduceMotion(initialMotionPref(storage), systemPrefersReducedMotion(matchMedia))
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
  motionPref: 'system',
  setMotionPref: () => {},
})

export function ThemeModeProvider({ children }) {
  const [mode, setModeState] = useState(() => initialMode(browserStorage()))
  const [motionPref, setMotionPrefState] = useState(() => initialMotionPref(browserStorage()))
  const [systemReduce, setSystemReduce] = useState(() => systemPrefersReducedMotion(browserMatchMedia))
  const reduceMotion = resolveReduceMotion(motionPref, systemReduce)

  // Follow the OS setting live (it only matters while the preference is "system").
  useEffect(() => {
    let mq
    try {
      mq = browserMatchMedia(REDUCE_QUERY)
    } catch {
      mq = null
    }
    if (!mq) return undefined
    const onChange = (e) => setSystemReduce(Boolean(e?.matches ?? mq.matches))
    setSystemReduce(Boolean(mq.matches))
    if (mq.addEventListener) {
      mq.addEventListener('change', onChange)
      return () => mq.removeEventListener('change', onChange)
    }
    mq.addListener?.(onChange) // older Safari
    return () => mq.removeListener?.(onChange)
  }, [])

  const setMode = useCallback((m) => {
    const next = m === 'night' ? 'night' : 'light'
    persist(browserStorage(), MODE_KEY, next)
    setModeState(next)
  }, [])

  const setMotionPref = useCallback((v) => {
    const next = MOTION_PREFS.includes(v) ? v : 'system'
    persist(browserStorage(), REDUCE_KEY, next)
    setMotionPrefState(next)
  }, [])

  useLayoutEffect(() => {
    applyThemeToDocument(document.documentElement, mode, reduceMotion)
  }, [mode, reduceMotion])

  const value = useMemo(
    () => ({ mode, setMode, reduceMotion, motionPref, setMotionPref }),
    [mode, setMode, reduceMotion, motionPref, setMotionPref],
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
