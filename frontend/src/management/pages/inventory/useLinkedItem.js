import { api } from '../../api'
import { useRequest } from '../../../shared/useRequest'

/**
 * The item a link names by its code (`CC-001234`), as `{ id, problem }`.
 *
 * Found through the search's item-code filter, which matches part of a
 * code, so only the row whose code is exactly the one asked for counts:
 * `CC-00001` must not open `CC-000012`. `problem` says why nothing opened.
 * With no `code` it asks nothing and finds nothing.
 */
export function useLinkedItem(view, code) {
  const wanted = code?.trim()
  const request = useRequest(wanted ? `${view}:${wanted}` : null, () =>
    api
      .searchInventory(view, { item_code: wanted, limit: 200 })
      .then(
        (body) =>
          body.rows.find((row) => row.item_code.toLowerCase() === wanted.toLowerCase())
            ?.id ?? null,
      ),
  )
  if (!wanted || request.busy) return { id: null, problem: '' }
  if (request.error) return { id: null, problem: request.error }
  if (request.data == null) return { id: null, problem: `No item ${code} found.` }
  return { id: request.data, problem: '' }
}
