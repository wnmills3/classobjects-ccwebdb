/**
 * Typed text as a value to send: trimmed, or null when nothing is left.
 *
 * A blank box means "not recorded", which the API stores as null -- never as
 * an empty string, which would read as a value someone chose. `text` is run
 * through `String` first, so a number from a form field is sent as its text,
 * and null or undefined count as blank.
 */
export function orNull(text) {
  const trimmed = String(text ?? '').trim()
  return trimmed === '' ? null : trimmed
}
