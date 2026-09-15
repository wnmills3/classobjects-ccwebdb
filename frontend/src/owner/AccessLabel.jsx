/**
 * A label with its access key's letter underlined.
 *
 * Paired with the `accel` attributes and `useSaveShortcut` hook in
 * `./shortcuts.js` -- see that module for the accelerator scheme these
 * labels advertise.
 */
export function AccessLabel({ text, accessKey }) {
  const at = text.toLowerCase().indexOf(accessKey.toLowerCase())
  if (at < 0) return text
  return (
    <>
      {text.slice(0, at)}
      <u>{text[at]}</u>
      {text.slice(at + 1)}
    </>
  )
}
