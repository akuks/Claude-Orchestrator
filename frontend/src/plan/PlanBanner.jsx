import { useCallback, useEffect, useState } from 'react'
import { Alert } from 'antd'
import { api } from '../api'
import { bannerText } from './plan'

// Top of the app shell: shown only while the signed-in person's plan is at or near its limit.
export default function PlanBanner({ refreshKey }) {
  const [banner, setBanner] = useState(null)
  const load = useCallback(() => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    api.planStatus().then((r) => setBanner(r?.status?.banner || null)).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load, refreshKey])
  if (!banner) return null
  return (
    <Alert
      data-testid="plan-banner"
      type={banner.kind === 'limit' ? 'error' : 'warning'}
      showIcon
      banner
      message={bannerText(banner)}
    />
  )
}
