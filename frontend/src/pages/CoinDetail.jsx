import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api'
import { useCart } from '../cart-context'
import { money } from '../format'

// Classifier values arrive as codes -- 'bullion', 'MS64', 'US' -- because
// codes are the stable contract across installations. Human labels come later,
// when the reference tables are exposed to the UI.
const FIELDS = [
  ['Item code', 'item_code'],
  ['Type', 'item_kind'],
  ['Country', 'country'],
  ['Year', 'year_start'],
  ['Denomination', 'denomination'],
  ['Grade', 'grade'],
  ['Graded by', 'grading_service'],
  ['Metal', 'metal'],
  ['Fineness', 'fineness'],
  ['Weight (ozt)', 'gross_weight_ozt'],
]

export default function CoinDetail() {
  const { id } = useParams()
  const [coin, setCoin] = useState(null)
  const [error, setError] = useState('')
  const { add } = useCart()

  useEffect(() => {
    let cancelled = false
    api
      .getCoin(id)
      .then((data) => {
        if (!cancelled) setCoin(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [id])

  if (error) return <p className="error">{error}</p>
  if (!coin) return <p className="muted">Loading...</p>

  return (
    <section className="detail">
      <p>
        <Link to="/">&larr; Back to catalogue</Link>
      </p>
      <h1>{coin.title}</h1>
      {coin.image_url && (
        <img className="detail-image" src={coin.image_url} alt={coin.title} />
      )}
      <p className="price large">{money(coin.price)}</p>
      <p className="muted">
        {coin.quantity_available > 0
          ? `${coin.quantity_available} available`
          : 'Sold out'}
      </p>

      {coin.description && <p>{coin.description}</p>}

      <table className="specs">
        <tbody>
          {FIELDS.map(([label, key]) =>
            coin[key] ? (
              <tr key={key}>
                <th>{label}</th>
                <td>{String(coin[key])}</td>
              </tr>
            ) : null,
          )}
        </tbody>
      </table>

      <button disabled={coin.quantity_available === 0} onClick={() => add(coin, 1)}>
        {coin.quantity_available === 0 ? 'Sold out' : 'Add to cart'}
      </button>
    </section>
  )
}
