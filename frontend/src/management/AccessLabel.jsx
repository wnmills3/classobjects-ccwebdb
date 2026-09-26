/**
 * A label with its access key's letter underlined.
 *
 * Paired with the `accel` attributes and `useSaveShortcut` hook in
 * `./shortcuts.js` -- see that module for the accelerator scheme these
 * labels advertise. With no `accessKey`, or one whose letter is not in the
 * text, it is the plain label.
 *
 * **One element, always.** Its callers put it straight inside a `.field`,
 * which is `display: grid` with four columns, so loose text around a `<u>`
 * would make each piece of the word its own grid item -- "T      i      tle"
 * -- and push the input out of place. Wrapped in a span, the label is a
 * single item whichever way it renders.
 */
export function AccessLabel({ text, accessKey }) {
  const at = accessKey ? text.toLowerCase().indexOf(accessKey.toLowerCase()) : -1
  if (at < 0) return <span>{text}</span>
  return (
    <span>
      {text.slice(0, at)}
      <u>{text[at]}</u>
      {text.slice(at + 1)}
    </span>
  )
}
