import { Component, Suspense, lazy, useCallback, useState } from 'react'
import { useThemeMode } from '../theme/ThemeModeContext'
import CometSvg from './CometSvg'
import TimelineSvg from './TimelineSvg'
import { hasWebGL } from './webgl'

// Chooses the 3D comet scene (three.js, loaded on demand) or its static SVG
// twin. three.js lives only in the lazily imported src/three/* chunk, so
// nothing here pulls it into the entry bundle.
const CometScene = lazy(() => import('../three/CometScene'))
const CometTimeline = lazy(() => import('../three/CometTimeline'))

// 3D only with WebGL and when motion is allowed (OS setting or in-app toggle).
export function use3D({ reduceMotion, webgl }) {
  return Boolean(webgl) && !reduceMotion
}

// A failed chunk load or a render error in the 3D tree shows the SVG instead.
class Fallback extends Component {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch(error) {
    this.props.onError?.(error)
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

export default function Comets3D({
  comets = [],
  overflow = 0,
  onOpen,
  onMore,
  variant = 'scene',
  stages,
  onSelectStage,
  selectedStage,
}) {
  const { mode, reduceMotion } = useThemeMode()
  const [broken, setBroken] = useState(false)
  const onFallback = useCallback(() => setBroken(true), [])
  const threeD = !broken && use3D({ reduceMotion, webgl: hasWebGL() })

  const svg =
    variant === 'timeline' ? (
      <TimelineSvg stages={stages} onSelectStage={onSelectStage} selectedIndex={selectedStage} />
    ) : (
      <CometSvg comets={comets} overflow={overflow} onOpen={onOpen} onMore={onMore} />
    )
  if (!threeD) return svg

  return (
    <Fallback fallback={svg} onError={onFallback}>
      {/* the SVG doubles as the loading state while the three chunk arrives */}
      <Suspense fallback={svg}>
        {variant === 'timeline' ? (
          <CometTimeline
            stages={stages}
            onSelectStage={onSelectStage}
            selectedIndex={selectedStage}
            mode={mode}
            onFallback={onFallback}
          />
        ) : (
          <CometScene
            comets={comets}
            overflow={overflow}
            onOpen={onOpen}
            onMore={onMore}
            mode={mode}
            onFallback={onFallback}
          />
        )}
      </Suspense>
    </Fallback>
  )
}
