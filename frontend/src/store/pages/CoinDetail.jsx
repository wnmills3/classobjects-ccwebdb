import { Link, useParams } from 'react-router-dom'

import { api } from '../../shared/api'
import { useReference } from '../../shared/reference-context'
import { useRequest } from '../../shared/useRequest'
import { useCart } from '../cart-context'
import { money } from '../../shared/format'
import { coverImage, describeMember, isLot, summarize } from './lot-entry'

// Classifier values arrive as codes -- 'bullion', 'MS64', 'US' -- because
// codes are the stable contract across installations. The type is shown by
// its label from the `item_kind` vocabulary; the rest are shown as sent.
const FIELDS = [
  ['Item code', 'item_code'],
  ['Type', 'item_kind'],
  ['Country', 'country'],
  ['Year', 'year_start'],
  ['Denomination', 'denomination'],
  // MS65, not the stored 65 and business strike.
  ['Grade', 'grade_display'],
  ['Graded by', 'grading_service'],
  ['Metal', 'metal'],
  ['Fineness', 'fineness'],
  ['Weight (ozt)', 'gross_weight_ozt'],
]

/**
 * One catalog entry: a coin, or a LOT of coins sold as one thing.
 *
 * A lot carries none of the fields above -- no code, no kind, no grade, no
 * year -- because no single value of any of them describes a group, and no
 * photograph of its own. Its `members` say what it is and which coins, and
 * they keep saying it after the lot has sold, which is the case this page has
 * to get right: the detail endpoint serves an ended listing on purpose, so a
 * page someone bookmarked can say the offer is over rather than that it never
 * existed.
 */
export default function CoinDetail() {
  const { id } = useParams()
  const { data: coin, error, busy } = useRequest(id, () => api.getCatalogItem(id))
  const kinds = useReference('item_kind')
  const { add } = useCart()

  // Following a link to another entry changes `id` without remounting, so
  // the previous entry is still in `coin` until the new one arrives.
  if (busy) return <p className="muted">Loading...</p>
  if (error) return <p className="error">{error}</p>

  const kindLabel = (code) => kinds?.find((entry) => entry.code === code)?.label ?? code
  const shown = (key) =>
    key === 'item_kind' ? kindLabel(coin[key]) : String(coin[key])

  // A lot has no picture of its own; one of its coins does. `coverImage`
  // decides which and says so in the alt text.
  const cover = coverImage(coin)

  return (
    <section className="detail">
      <p>
        <Link to="/">&larr; Back to catalog</Link>
      </p>
      <h1>{coin.title}</h1>
      {cover && <img className="detail-image" src={cover.url} alt={cover.alt} />}
      <p className="price large">{money(coin.price, coin.currency)}</p>
      <p className="muted">
        {coin.quantity_available > 0
          ? `${coin.quantity_available} available`
          : 'Sold out'}
      </p>
      {/* How many things this is, before the price is read as one coin's. */}
      {isLot(coin) && <p className="muted">{summarize(coin)}</p>}

      {coin.description && <p>{coin.description}</p>}

      <table className="specs">
        <tbody>
          {FIELDS.map(([label, key]) =>
            coin[key] ? (
              <tr key={key}>
                <th>{label}</th>
                <td>{shown(key)}</td>
              </tr>
            ) : null,
          )}
        </tbody>
      </table>

      {/* Every field above is null for a lot, so that table is empty and the
          coins themselves are the only description there is. Past tense on
          purpose once a lot has ended: these are the coins the group HELD,
          which is exactly what a bookmarked page of a sold lot must still be
          able to say. */}
      {isLot(coin) && (
        <section className="lot-members">
          <h2>What is in this lot</h2>
          <ul className="lot-member-list">
            {coin.members.map((member) => (
              <li key={member.inventory_item_id}>
                {member.thumbnail_url && (
                  <img src={member.thumbnail_url} alt={member.title} loading="lazy" />
                )}
                <div>
                  <strong>{member.title}</strong>
                  <p className="muted small">{describeMember(member)}</p>
                  {member.description && <p>{member.description}</p>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button disabled={coin.quantity_available === 0} onClick={() => add(coin, 1)}>
        {coin.quantity_available === 0 ? 'Sold out' : 'Add to cart'}
      </button>
    </section>
  )
}
