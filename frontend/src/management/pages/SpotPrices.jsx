import { useState } from 'react'

import { api } from '../api'
import HelpScope from '../HelpScope'
import { dateTime, money } from '../../shared/format'
import { useRequest } from '../../shared/useRequest'

//: A spot price as it may be typed: an amount with up to four decimal places
//: (copper is quoted in cents an ounce), which is what the column holds.
const PRICE = /^\d{1,8}(\.\d{1,4})?$/

/** Whether `text` is a spot price the server will take. */
function isPrice(text) {
  return PRICE.test(text.trim())
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
  const beyondCents = places.slice(2).replace(/0+$/, '')
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
 * Spot prices: what a troy ounce of each metal is quoted at, entered by hand.
 *
 * One row per metal in use: its newest price and when it was recorded, the
 * fine ounces held across the collection and what they melt for at that
 * price, and a box to record a new price. A price is never edited: a new one
 * is recorded and becomes the metal's price, with the one before kept, so a
 * mistyped price is put right by recording the right one.
 *
 * Melt value is worked out from the newest price wherever it is shown -- here
 * and in the "Metal by form" report -- so recording a price revalues
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
  const rows = recorded ?? loaded.data ?? []

  async function record(row) {
    const price = (drafts[row.metal] ?? '').trim()
    setSaving(row.metal)
    setError('')
    setSaid('')
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

  const total = rows.reduce(
    (sum, row) => (row.melt_value === null ? sum : sum + Number(row.melt_value)),
    0,
  )
  const anyValued = rows.some((row) => row.melt_value !== null)

  return (
    <HelpScope>
      <h1>Spot prices</h1>
      <p className="muted">
        What a troy ounce of each metal is quoted at. Recording a price makes it the
        metal&apos;s price from now on; the one before is kept.
      </p>
      {loaded.error && <p className="error">{loaded.error}</p>}
      {error && <p className="error">{error}</p>}
      {said && (
        <p className="notice" role="status">
          {said}
        </p>
      )}
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
                      className="row"
                      onSubmit={(e) => {
                        e.preventDefault()
                        if (isPrice(draft) && !saving) record(row)
                      }}
                    >
                      <input
                        inputMode="decimal"
                        size={10}
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
                      <button type="submit" disabled={!isPrice(draft) || saving !== ''}>
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
                <td>{money(total.toFixed(2))}</td>
                <td />
              </tr>
            </tfoot>
          )}
        </table>
      )}
    </HelpScope>
  )
}
