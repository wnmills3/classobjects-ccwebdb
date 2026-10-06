import { FIELD_HELP } from '../fieldHelp'

/**
 * A vocabulary's own columns, as the forms that add and change a value hold
 * them: text for everything but a switch.
 *
 * Its own module, apart from the form components, so those stay
 * components-only modules; a mixed module defeats Fast Refresh.
 */

/** What each field starts as: a switch off, everything else empty. */
export function blank(fields) {
  return Object.fromEntries(
    fields.map((field) => [field.name, field.kind === 'boolean' ? false : '']),
  )
}

/** What each field holds for a value that exists, from its `extra`. */
export function held(fields, extra) {
  return Object.fromEntries(
    fields.map((field) => {
      const value = extra?.[field.name]
      if (field.kind === 'boolean') return [field.name, Boolean(value)]
      return [field.name, value === null || value === undefined ? '' : String(value)]
    }),
  )
}

/** The help topic for one of a vocabulary's own columns: its own, or the general one. */
export function fieldHelp(name) {
  const own = `vocabulary_field_${name}`
  return FIELD_HELP[own] ? own : 'vocabulary_field'
}

export const filled = (field, value) =>
  field.kind === 'boolean' || String(value).trim() !== ''

/** A field's value as it is sent: a switch as it stands, text trimmed. */
export const sent = (field, value) => (field.kind === 'boolean' ? value : value.trim())
