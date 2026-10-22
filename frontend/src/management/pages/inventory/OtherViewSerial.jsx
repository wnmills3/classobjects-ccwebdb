import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { api } from '../../api'
import { COIN_VIEW, CURRENCY_VIEW } from './specs'

/**
 * Items on the other view whose serial matches the one typed here.
 *
 * Each view searches its own serials -- a bar's, round's or Goldback's on
 * Coins, a note's on Currency -- so a note's serial typed on Coins finds
 * nothing there, with nothing to say where it is. This counts the other
 * view's matches for the same text.
 *
 * When this view found nothing and the serial is the whole search, the
 * other view is where the answer is, so this opens it with the serial
 * filled in and says why it is showing. The step replaces the empty page in
 * history: were it kept, Back would return to it and it would send the
 * reader on again. With other filters set, or matches here too, the page
 * stays and links to the other view's matches instead -- one view's rows
 * never mix with the other's, whose columns differ.
 *
 * The count is kept with the serial it was asked for, so a hint for an
 * earlier serial never shows beside a later one. A count that fails to
 * arrive shows no hint: the search above reports its own errors, and this
 * one only adds a pointer.
 */
export default function OtherViewSerial({ view, serial, emptyOnSerialAlone }) {
  const here = view === COIN_VIEW.view ? COIN_VIEW : CURRENCY_VIEW
  const other = view === COIN_VIEW.view ? CURRENCY_VIEW : COIN_VIEW
  const [found, setFound] = useState(null)
  const navigate = useNavigate()
  const { state } = useLocation()

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

  const counted = Boolean(serial) && found?.serial === serial
  const address = new URLSearchParams({ serial_number: serial ?? '' })
  const target = `/inventory/${other.view}?${address}`
  const switching = counted && found.total > 0 && emptyOnSerialAlone

  useEffect(() => {
    if (!switching) return
    navigate(target, {
      replace: true,
      state: { switchedFrom: here.title, serial, total: found.total },
    })
  }, [switching, navigate, target, here.title, serial, found?.total])

  const arrived = state?.serial === serial && state?.switchedFrom === other.title
  if (arrived) {
    return (
      <p className="notice">
        No items in {other.title} have a serial matching “{serial}”, so this is the{' '}
        {state.total} in {here.title}.
      </p>
    )
  }

  if (!counted || !found.total || switching) return null
  const one = found.total === 1
  return (
    <p className="notice">
      {found.total} {one ? 'item' : 'items'} in {other.title} {one ? 'has' : 'have'} a
      serial matching “{serial}”.{' '}
      <Link to={target}>{one ? 'Show it' : 'Show them'}</Link>
    </p>
  )
}
