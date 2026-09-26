import { date } from '../../../shared/format'
import { useRequest } from '../../../shared/useRequest'
import { api } from '../../api'

/**
 * Every sale of the item, each as it was sold.
 *
 * A returned item may be corrected and sold again; each sale keeps the
 * item's name, grade and price from the day it sold. Nothing is shown for an
 * item never sold, or when its sales could not be read.
 *
 * A sale made inside a lot is shown as this coin's own share of the line,
 * not the line's `quantity` and `unit_price` -- those are the whole group's,
 * so a three-coin lot sold for 1,000.00 would otherwise claim the full
 * 1,000.00 against each of its coins on the one screen that answers "what
 * happened to this coin".
 */
export default function SaleHistory({ itemId }) {
  const { data: sales, error } = useRequest(itemId, () => api.getItemSales(itemId))

  if (error || !sales || sales.length === 0) return null
  return (
    <div className="sale-history">
      <h3>Sales</h3>
      <ul>
        {sales.map((sale) => {
          const sold = sale.snapshot?.item
          const inLot = sale.sales_lot_id != null
          return (
            <li key={`${sale.order_id}-${sale.placed_at}`}>
              Order #{sale.order_id}, {date(sale.placed_at)}, {sale.status}:{' '}
              {inLot
                ? `${sale.share_amount ?? sale.unit_price} of lot #${sale.sales_lot_id}`
                : `${sale.quantity} at ${sale.unit_price}`}{' '}
              to {sale.customer_name}
              {sold && (
                <span className="muted">
                  {' '}
                  -- sold as {sold.source_title}
                  {sold.grade_display ? `, ${sold.grade_display}` : ''}
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
