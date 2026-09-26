import { fitsKind, sideFor } from '../../../shared/kinds'
import { ReferenceSelect } from '../../../shared/reference'
import { useReference } from '../../../shared/reference-context'

//: Where a derived attribute was read, as its mark's tooltip says it.
const ATTRIBUTE_FROM = {
  serial_pattern: 'Read from the serial number',
  rating: 'Read from the rating',
  attribute_rule: "Follows from the note's class and series",
}

//: `item_attribute.attribute_group`'s members, for the add form's group
//: picker. The reference API has no endpoint listing an enum's values --
//: only each existing row's own `extra.attribute_group` -- so this mirrors
//: `AttributeGroup` in backend/app/models/reference.py by hand; keep the two
//: in step if that enum changes.
const ATTRIBUTE_GROUPS = [
  { code: 'serial', label: 'Serial' },
  { code: 'variety', label: 'Variety' },
  { code: 'release', label: 'Release' },
  { code: 'verification', label: 'Verification' },
  { code: 'qualifier', label: 'Qualifier' },
]

/**
 * What the item is beyond its grade: Star Note, No Motto, First Strike.
 *
 * The whole set is saved with the item, so a stale form is a 409 like any
 * other field. Removing one a rule read (marked "read") keeps it removed:
 * the rule does not add it back. Only attributes for this kind of item are
 * offered -- a star note is not a coin's.
 */
export default function AttributesField({ item, codes, onChange, kind }) {
  const vocabulary = useReference('item_attribute') ?? []
  const byCode = new Map(vocabulary.map((entry) => [entry.code, entry]))
  const held = new Map((item.attributes ?? []).map((a) => [a.code, a]))

  return (
    <div className="field" data-help="attributes">
      <span>Attributes</span>
      <div className="attribute-list">
        {codes.map((code) => {
          const label = byCode.get(code)?.label ?? held.get(code)?.label ?? code
          const read = held.get(code)?.source === 'derived' ? held.get(code) : null
          return (
            <span key={code} className="chip alias-chip">
              {label}
              {read && (
                <span
                  className="suggested"
                  title={ATTRIBUTE_FROM[read.derived_by] ?? 'Read by a rule'}
                >
                  read
                </span>
              )}
              <button
                type="button"
                aria-label={`Remove ${label}`}
                onClick={() => onChange(codes.filter((c) => c !== code))}
              >
                ×
              </button>
            </span>
          )
        })}
        {/* Only once loaded: until then the picker is a text box, and each
            keystroke would add a partial code. */}
        {vocabulary.length > 0 && (
          <ReferenceSelect
            table="item_attribute"
            value=""
            allowAdd
            labelOnly
            // No top-level `applies_to` field: the API reads it from `extra`
            // (`ReferenceValueCreate`). A value added with none would fit no
            // kind and vanish from this very picker the moment it appeared
            // -- see `fitsKind`, whose exact inverse `sideFor` is.
            // `attribute_group` is NOT NULL with no database default, and the
            // owner chose to ask rather than have one picked silently:
            // `groupField`/`groupOptions` put a required group picker in the
            // add form instead.
            addFields={{ applies_to: sideFor(kind) }}
            groupField="attribute_group"
            groupOptions={ATTRIBUTE_GROUPS}
            filter={(entry) => fitsKind(entry, kind) && !codes.includes(entry.code)}
            onChange={(e) => {
              if (e.target.value) onChange([...codes, e.target.value])
            }}
          />
        )}
      </div>
      <span />
      <span />
    </div>
  )
}
