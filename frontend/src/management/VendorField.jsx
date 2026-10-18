import { useState } from 'react'

import { api } from './api'
import { siteName, withScheme } from './site-name'
import { useInlineAdd } from './useInlineAdd'
import { ADD, useAddOption } from '../shared/add-option'
import { ReferenceSelect } from '../shared/reference'

const BLANK_VENDOR_DRAFT = { name: '', vendor_kind: '', url: '' }

/**
 * The vendor picker: a plain select over `listVendors()`, opening with a
 * "+ Add a vendor..." option that opens an inline web address / name / kind
 * form -- the same idea as `ReferenceSelect`'s inline add, but vendors are
 * rows with their own id, not a reference table's codes, so this is its own
 * small component rather than a reuse of that one.
 *
 * `onChange` receives the chosen vendor's id, or null for the blank
 * option, which reads `blankLabel`. `onVendorAdded` receives a vendor
 * made here, for the caller to add to `vendors`.
 */
export default function VendorField({
  vendors,
  value,
  onChange,
  onVendorAdded,
  blankLabel = '--',
  //: The select's accessible name: an `aria-label` outranks the `<label>`
  //: the picker sits in, so a caller that names the field otherwise -- the
  //: Platforms form's "Purchase source" -- passes that name here.
  label = 'Vendor',
  //: Keyboard accelerator attributes (`accel(...)`), passed to the select.
  accessKey,
  'aria-keyshortcuts': ariaKeyshortcuts,
}) {
  const addOption = useAddOption()
  // Add stays live: a press with no name says so, rather than a dimmed
  // button that says nothing.
  const [askedEmpty, setAskedEmpty] = useState(false)
  const { adding, setAdding, draft, setDraft, error, add, wrapper, ready } =
    useInlineAdd({
      blank: BLANK_VENDOR_DRAFT,
      ready: (d) => Boolean(d.name.trim()),
      create: (d) =>
        api.createVendor({
          name: d.name.trim(),
          vendor_kind: d.vendor_kind || null,
          url: withScheme(d.url),
        }),
      onCreated: (created) => {
        onVendorAdded(created)
        onChange(created.id)
      },
    })

  if (adding) {
    return (
      <div className="add-reference" ref={wrapper}>
        {/* The address first: a vendor is named for its site, so the address
            proposes the name. Only a name the address gave follows it, or
            an empty one, which is nobody's choice; one typed by hand is
            left alone. */}
        <input
          placeholder="https://"
          aria-label="Vendor web address"
          value={draft.url}
          autoFocus
          onChange={(e) => {
            const url = e.target.value
            const proposed = draft.name === '' || draft.name === siteName(draft.url)
            setDraft({ ...draft, url, name: proposed ? siteName(url) : draft.name })
          }}
        />
        <input
          placeholder="Vendor name"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <ReferenceSelect
          table="vendor_kind"
          value={draft.vendor_kind}
          onChange={(e) => setDraft({ ...draft, vendor_kind: e.target.value })}
          placeholder="vendor kind"
        />
        <button
          type="button"
          onClick={() => {
            setAskedEmpty(!ready)
            if (ready) add()
          }}
        >
          Add
        </button>
        <button type="button" className="link" onClick={() => setAdding(false)}>
          Cancel
        </button>
        {askedEmpty && !ready && (
          <span className="error">Type the vendor&apos;s name first.</span>
        )}
        {error && <span className="error">{error}</span>}
      </div>
    )
  }

  return (
    <select
      value={value ?? ''}
      onKeyDown={addOption.onKeyDown}
      onChange={(e) => {
        if (e.target.value !== ADD) {
          onChange(e.target.value === '' ? null : Number(e.target.value))
          return
        }
        // An arrow steps over Add; only choosing it opens the form.
        const meant = addOption.steppedOver(e.target)
        if (meant === undefined) setAdding(true)
        else if (meant !== null) onChange(Number(meant))
      }}
      aria-label={label}
      accessKey={accessKey}
      aria-keyshortcuts={ariaKeyshortcuts}
    >
      <option value="">{blankLabel}</option>
      <option value={ADD}>+ Add a vendor...</option>
      {vendors.map((v) => (
        <option key={v.id} value={v.id}>
          {v.name}
        </option>
      ))}
    </select>
  )
}
