/**
 * The warning shown before changing an item a buyer is looking at.
 *
 * One component for what were two hand-rolled copies, in `ItemEditForm` and
 * `BulkEditBar`, and a third was about to be written for `ErrorsPanel`. The
 * wording names every reason rather than saying "this is for sale": a person
 * with two offers open needs to know which one they are about to change.
 *
 * `uses` is the `sale_state` array an item detail carries. Empty means there
 * is nothing to warn about, and the component renders nothing at all --
 * callers do not need their own conditional.
 *
 * The "a change shows to buyers at once" sentence is this notice's own
 * explanation, and it travels only with the listed reasons. A caller that
 * passes `show` with no `uses` is already showing its own message that says
 * the same thing -- the bulk bar's server refusal reads "A change shows to
 * buyers at once" verbatim -- so repeating the sentence here would stack it
 * twice on screen.
 */
export default function ForSaleNotice({
  uses = [],
  checked,
  onChange,
  action,
  heading = 'This item is for sale',
  show,
}) {
  // `show` is for a caller that knows an item is for sale without holding the
  // reasons: the bulk bar learns it from a refusal it already displays, and
  // repeating that message here would print it twice.
  if (!(show ?? uses.length > 0)) return null
  return (
    <div className="for-sale" role="alert">
      <strong>{heading}</strong>
      {uses.length > 0 ? (
        <>
          : {uses.map((use) => use.text).join(', ')}. A change shows to buyers at once;
          each sale keeps the item as it was sold.
        </>
      ) : (
        '.'
      )}
      <label className="checkbox">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        {/* */}
        {action}
      </label>
    </div>
  )
}
