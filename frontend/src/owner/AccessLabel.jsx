/**
 * A label with its access key's letter underlined.
 *
 * Paired with the `accel` attributes and `useSaveShortcut` hook in
 * `./shortcuts.js` -- see that module for the accelerator scheme these
 * labels advertise.
 *
 * **One element, always.** This returned a bare fragment of three children
 * ("T", `<u>i</u>`, "tle"), and its callers put it straight inside a
 * `.field`, which is `display: grid` with four columns. Each piece of the
 * word therefore became its own grid item -- "T" in the 9rem first column,
 * the underlined letter in the 12-24rem second, the rest in the third --
 * rendering "T      i      tle" and pushing the input out of place. A label
 * whose letter was not found returned a plain string, one item, and looked
 * correct, which is why only some of them broke. Wrapping in a span makes
 * the label a single item either way.
 */
export function AccessLabel({ text, accessKey }) {
  const at = text.toLowerCase().indexOf(accessKey.toLowerCase())
  if (at < 0) return <span>{text}</span>
  return (
    <span>
      {text.slice(0, at)}
      <u>{text[at]}</u>
      {text.slice(at + 1)}
    </span>
  )
}
