import { dateTime } from '../../../shared/format'
import { fieldName, fieldValue, shown } from './fieldMerge'

/**
 * Who made a field's latest change, and when, from the item's change log
 * (`last_changes`) -- blank when the log has none, as for a change made by
 * a pass or before the log existed.
 */
function changedBy(change) {
  if (!change) return ''
  return `, changed by ${change.by ?? 'someone'} at ${dateTime(change.at)}`
}

/**
 * The fields edited here that someone else changed since (`conflicts`, see
 * `fieldMerge.js`), each with both values and a choice between them.
 *
 * `onKeepMine(key)` marks the other change as seen, so the edit here is now
 * based on it and will replace it; `onUseTheirs(key)` drops the edit here.
 */
export default function ConflictList({
  item,
  draft,
  conflicts,
  onKeepMine,
  onUseTheirs,
}) {
  return (
    <div className="for-sale" role="alert">
      <strong>Changed elsewhere while you were editing</strong>
      <ul>
        {conflicts.map((key) => (
          <li key={key}>
            {fieldName(key)}: now {shown(fieldValue(item, key))}
            {changedBy(item.last_changes?.[key])}; yours {shown(draft[key])}.{' '}
            <button type="button" onClick={() => onKeepMine(key)}>
              Keep mine
            </button>{' '}
            <button type="button" onClick={() => onUseTheirs(key)}>
              Use theirs
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
