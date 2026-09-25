/**
 * Merging the facts' suggestions into the New item form.
 *
 * `suggested` maps a field to the code the facts filled in, for as long as the
 * person has not changed it. See docs/specs/classifier-defaults-design.md.
 */

/** `marks` without `key`, unchanged if it had none. */
export function without(marks, key) {
  if (!(key in marks)) return marks
  const rest = { ...marks }
  delete rest[key]
  return rest
}

/**
 * The form with the facts' suggestions applied.
 *
 * Fills a field that is empty or was filled by an earlier suggestion, and
 * clears a suggestion the new facts no longer support. A value the person
 * picked is never touched.
 */
export function withSuggestions({ form, suggested }, found) {
  const nextForm = { ...form }
  let nextMarks = { ...suggested }
  for (const [key, code] of Object.entries(found)) {
    if (form[key] && !(key in suggested)) continue
    if (code) {
      nextForm[key] = code
      nextMarks[key] = code
    } else if (key in suggested) {
      nextForm[key] = ''
      nextMarks = without(nextMarks, key)
    }
  }
  return { form: nextForm, suggested: nextMarks }
}
