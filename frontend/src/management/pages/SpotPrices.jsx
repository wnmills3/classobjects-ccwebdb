import { useState } from 'react'

import { api } from '../api'
import HelpScope from '../HelpScope'
import { withoutTrailingZeros } from './platform-rates'
import { centsOrZero, fromCents } from '../../shared/cents'
import { dateTime, money } from '../../shared/format'
import { useRequest } from '../../shared/useRequest'

//: A spot price as it may be typed: an amount with up to four decimal places
//: (copper is quoted in cents an ounce), which is what the column holds. The
//: digits before the point may be left out -- `.4553` -- and so may those
//: after it -- `31.` -- but not both.
const PRICE = /^(\d{1,8}(\.\d{0,4})?|\.\d{1,4})$/

/**
 * A price as a person types it, in the form the server takes: `$4,012.50`
 * is `4012.50` and `.4553` is `0.4553`. A dollar sign in front, commas
 * between thousands and a bare decimal point are how a price is written and
 * copied from a quote, and are not what makes it a number. '' for text that
 * is not a price.
 */
function priceOf(text) {
  const plain = text
    .trim()
    .replace(/^\$\s*/, '')
    .replaceAll(',', '')
  if (!PRICE.test(plain)) return ''
  return plain.replace(/^\./, '0.').replace(/\.$/, '')
}

/**
 * A spot price as shown: to the cent, or to four places when it has them.
 *
 * The column holds four places, so `30.0000` arrives for thirty dollars and
 * reads as `$30.00`; only a price that uses the last two -- copper's
 * `0.2875` -- is shown with them.
 */
function shownPrice(value) {
  if (value === null || value === undefined) return '--'
  const [whole, places = ''] = String(value).trim().split('.')
  const beyondCents = withoutTrailingZeros(places.slice(2))
  if (beyondCents !== '') return `$${whole}.${places.padEnd(4, '0').slice(0, 4)}`
  return money(`${whole}.${places.padEnd(2, '0').slice(0, 2)}`)
}

/** Fine troy ounces as shown: three places and the unit. */
function shownOunces(value) {
  if (value === null || value === undefined) return '--'
  return `${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  })} ozt`
}

/**
 * The metals with the most held first, and those with none held last.
 *
 * A price matters by how much of the metal there is to value: the rows worth
 * pricing are at the top, and a metal nothing is held of -- no fine weight
 * recorded, or a weight of zero -- is out of the way at the bottom. Metals
 * that hold the same stay in the order the server sent, which is the
 * vocabulary's own.
 */
function byOuncesHeld(rows) {
  const held = (row) => Number(row.fine_ozt_held ?? 0)
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => held(b.row) - held(a.row) || a.index - b.index)
    .map(({ row }) => row)
}

/**
 * Spot prices: what a troy ounce of each metal is quoted at, entered by hand.
 *
 * One row per metal in use, the most held first: its newest price and when it was recorded, the
 * fine ounces held across the collection and what they melt for at that
 * price, and a box to record a new price. A price is never edited: a new one
 * is recorded and becomes the metal's price, with the one before kept, so a
 * mistyped price is put right by recording the right one.
 *
 * Melt value is worked out from the newest price wherever it is shown -- here
 * and in the "Precious metal" report -- so recording a price revalues
 * everything at once. A metal never quoted has no melt value: that is "not
 * quoted", not "worth nothing".
 */
export default function SpotPrices() {
  const loaded = useRequest('spot-prices', () => api.listMetalPrices())
  // The table as the last recording answered it; the loaded one until then.
  const [recorded, setRecorded] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [saving, setSaving] = useState('')
  const [error, setError] = useState('')
  const [said, setSaid] = useState('')
  const rows = byOuncesHeld(recorded ?? loaded.data ?? [])

  async function record(row) {
    const price = priceOf(drafts[row.metal] ?? '')
    setSaid('')
    if (price === '') {
      // Said, not left as a button that does nothing.
      setError(
        `${row.label}: enter the price of one troy ounce as an amount, like ` +
          '31.50 or 4,012.50 -- up to four decimal places.',
      )
      return
    }
    setSaving(row.metal)
    setError('')
    try {
      setRecorded(
        await api.recordMetalPrice({ metal: row.metal, price_per_ozt: price }),
      )
      setDrafts((current) => ({ ...current, [row.metal]: '' }))
      setSaid(`${row.label} recorded at ${shownPrice(price)} an ounce.`)
    } catch (err) {
      // The price stays in its box: it is corrected, not typed again.
      setError(err.message)
    } finally {
      setSaving('')
    }
  }

  const total = rows.reduce((sum, row) => sum + centsOrZero(row.melt_value), 0)
  const anyValued = rows.some((row) => row.melt_value !== null)

  return (
    <HelpScope>
      <h1>Spot prices</h1>
      <p className="muted">
        What a troy ounce of each metal is quoted at. To set one, click in the
        metal&apos;s <strong>New price</strong> box, type today&apos;s price and press{' '}
        <strong>Record</strong>. That becomes the metal&apos;s price from now on; the
        one before is kept.
      </p>
      {loaded.error && <p className="error">{loaded.error}</p>}
      {error && <p className="error">{error}</p>}
      {said && <output className="notice status-line">{said}</output>}
      {!loaded.error && !loaded.data && <p className="muted">Loading...</p>}
      {rows.length > 0 && (
        <table className="table" aria-label="Spot prices">
          <thead>
            <tr>
              <th>Metal</th>
              <th>Price per troy ounce</th>
              <th>Recorded</th>
              <th>Fine ounces held</th>
              <th>Melt value</th>
              <th>New price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const draft = drafts[row.metal] ?? ''
              return (
                <tr key={row.metal}>
                  <td>{row.label}</td>
                  <td>{shownPrice(row.price_per_ozt)}</td>
                  <td>{row.quoted_at ? dateTime(row.quoted_at) : 'never'}</td>
                  <td>{shownOunces(row.fine_ozt_held)}</td>
                  <td>{row.melt_value === null ? '--' : money(row.melt_value)}</td>
                  <td>
                    <form
                      className="spot-price-entry"
                      onSubmit={(e) => {
                        e.preventDefault()
                        if (!saving) record(row)
                      }}
                    >
                      <input
                        inputMode="decimal"
                        size={10}
                        // Words, not `0.00`: a number here reads as the
                        // metal's price, and the box as not a box at all.
                        placeholder="type a price"
                        data-help="spot_price"
                        aria-label={`New price for ${row.label}`}
                        value={draft}
                        onChange={(e) =>
                          setDrafts((current) => ({
                            ...current,
                            [row.metal]: e.target.value,
                          }))
                        }
                      />
                      {/* Never greyed out for want of a price: a column of
                          dimmed buttons reads as a page that takes no
                          input. Pressed with nothing, or with something
                          that is not a price, it says what to type. */}
                      <button type="submit" disabled={saving !== ''}>
                        {saving === row.metal ? 'Recording...' : 'Record'}
                      </button>
                    </form>
                  </td>
                </tr>
              )
            })}
          </tbody>
          {anyValued && (
            <tfoot>
              <tr>
                <td colSpan={4}>Melt value of everything quoted</td>
                <td>{money(fromCents(total))}</td>
                <td />
              </tr>
            </tfoot>
          )}
        </table>
      )}
    </HelpScope>
  )
}
