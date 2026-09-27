import { api } from './api'
import { useRequest } from '../shared/useRequest'

/**
 * Where an item is kept: a select over the storage locations, "--" for not
 * recorded. Optional everywhere it is offered.
 *
 * `value` is the location's id as text ('' for none) and `onChange` receives
 * the same; the form turns it into a number or null when it sends it. A list
 * that fails to load leaves only "--": a location is never required.
 */
export default function LocationSelect({ value, onChange, disabled = false }) {
  const locations = useRequest('locations', () => api.listStorageLocations()).data ?? []
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">--</option>
      {locations.map((loc) => (
        <option key={loc.id} value={String(loc.id)}>
          {loc.label}
        </option>
      ))}
    </select>
  )
}
