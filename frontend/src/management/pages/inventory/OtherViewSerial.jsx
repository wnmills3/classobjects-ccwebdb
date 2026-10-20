import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../../api'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

/**
 * Items on the other view whose serial matches the one typed here.
 *
 * Each view searches its own serials -- a bar's, round's or Goldback's on
 * Coins, a note's on Currency -- so a note's serial typed on Coins finds
 * nothing there, with nothing to say where it is. This counts the other
 * view's matches for the same text and links there with it filled in.
 *
 * The count is kept with the serial it was asked for, so a hint for an
 * earlier serial never shows beside a later one. A count that fails to
 * arrive shows no hint: the search above reports its own errors, and this
 * one only adds a pointer.
 */
export default function OtherViewSerial({ view, serial }) {
  const other = view === COIN_VIEW.view ? CURRENCY_VIEW : COIN_VIEW
  const [found, setFound] = useState(null)

  useEffect(() => {
    if (!serial) return
    let cancelled = false
    api
      .searchInventory(other.view, { serial_number: serial, limit: 1 })
      .then((body) => {
        if (!cancelled) setFound({ serial, total: body.total })
      })
      .catch(() => {
        if (!cancelled) setFound(null)
      })
    return () => {
      cancelled = true
    }
  }, [other.view, serial])

  if (!serial || found?.serial !== serial || !found.total) return null
  const one = found.total === 1
  const address = new URLSearchParams({ serial_number: serial })
  return (
    <p className="notice">
      {found.total} {one ? 'item' : 'items'} in {other.title} {one ? 'has' : 'have'} a
      serial matching “{serial}”.{' '}
      <Link to={`/inventory/${other.view}?${address}`}>
        {one ? 'Show it' : 'Show them'}
      </Link>
    </p>
  )
}
