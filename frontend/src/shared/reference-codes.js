/**
 * Deriving a vocabulary value's code from what someone typed as its label.
 *
 * Separate from `reference.jsx` -- which exports the `ReferenceSelect` and
 * `ReferenceProvider` components -- because a component module that also
 * exports a plain function breaks Fast Refresh (see eslint's
 * react-refresh/only-export-components rule). Parallel to `reference-match.js`
 * for the same reason.
 */

/** The code a typed label becomes: `Mismatched Serial` -> `mismatched_serial`. */
export function codeFromLabel(label) {
  return label
    .trim()
    .toLowerCase()
    .replace(/['`’]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}
