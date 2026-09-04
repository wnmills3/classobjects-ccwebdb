import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api'
import { useCart } from '../cart'
import { money } from '../format'

const FIELDS = [
  ['SKU', 'sku'],
  ['Type', 'kind'],
  ['Country', 'country'],
  ['Year', 'year'],
  ['Denomination', 'denomination'],
  ['Composition', 'composition'],
  ['Grade', 'grade'],
  ['Certification', 'certification'],
  ['Mint mark', 'mint_mark'],
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
      <p className="price large">{money(coin.price)}</p>
      <p className="muted">
        {coin.quantity > 0 ? `${coin.quantity} available` : 'Sold out'}
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

      <button disabled={coin.quantity === 0} onClick={() => add(coin, 1)}>
        {coin.quantity === 0 ? 'Sold out' : 'Add to cart'}
      </button>
    </section>
  )
}
