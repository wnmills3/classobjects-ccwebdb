import { api } from './api'
import { useRequest } from '../shared/useRequest'

/**
 * The sales platforms, read once when the component mounts.
 *
 * `venues` is empty until they arrive; `loaded` tells "none recorded" from
 * "not read yet", for a caller that must not decide anything from a list
 * that has not arrived. `error` is the failed read's message, or ''.
 */
export function useSalesVenues() {
  const { data, error } = useRequest('sales-venues', () => api.listSalesVenues())
  return { venues: data ?? [], loaded: data !== undefined, error }
}
