import { Fragment } from 'react'

// A name as plain React text with a break opportunity after each underscore (never an HTML string).
export function breakable(name) {
  const parts = String(name ?? '').split('_')
  return parts.map((p, i) => (
    <Fragment key={i}>
      {i < parts.length - 1 ? `${p}_` : p}
      {i < parts.length - 1 && <wbr />}
    </Fragment>
  ))
}
