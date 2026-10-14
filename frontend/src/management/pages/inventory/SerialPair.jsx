import { api } from '../../api'
import { useDebounced } from '../../../shared/useDebounced'
import { useRequest } from '../../../shared/useRequest'

//: How long typing must pause before the collection is searched again.
const PAIR_DELAY_MS = 250
//: A US small-size serial's digits: with fewer or more, nothing is asked.
const SERIAL_DIGITS = 8

/** One match, as a line the owner can go and find: code, serial, what it is. */
function describe(pair) {
  const what = [pair.denomination, pair.series ? `Series ${pair.series}` : '']
    .filter(Boolean)
    .join(', ')
  const said = what ? ` (${what})` : ''
  return `${pair.item_code} ${pair.serial_number}${said}`
}

/**
 * Says so when the collection already holds the other half of a pair: the
 * star note of this note's serial number, or, for a star note, the note
 * without the star (`GET /api/inventory/{id}/serial-pairs`, `app.serial_pairs`).
 *
 * `serial` is the serial as the form shows it, typed or saved, so the match
 * is shown before the note is saved and stays after. Nothing is shown, and
 * nothing asked, until it has eight digits. A convenience: a search that
 * fails shows nothing rather than an error over the form.
 */
export default function SerialPair({ itemId, serial }) {
  const typed = useDebounced((serial ?? '').trim(), PAIR_DELAY_MS)
  const complete = typed.replace(/\D/g, '').length === SERIAL_DIGITS
  const key = itemId != null && complete ? `${itemId}:${typed}` : null
  const found = useRequest(key, () => api.getSerialPairs(itemId, typed))

  // `busy` while the serial has moved on: the last serial's match is not
  // this one's.
  if (key === null || found.busy || found.error) return null
  const pairs = found.data ?? []
  if (pairs.length === 0) return null
  const lead = typed.includes('*')
    ? 'The collection holds this serial number without the star'
    : 'The collection holds the star note of this serial number'
  return (
    <output className="notice serial-pair">
      {lead}: {pairs.map(describe).join('; ')}
    </output>
  )
}
