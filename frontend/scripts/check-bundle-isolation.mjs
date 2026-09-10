/**
 * Assert that neither application's bundle contains the other's code.
 *
 * Walks the chunk graph reachable from each entry and inspects the MODULES in
 * every chunk it reaches, not the chunk's name or `src`. Chunks shared between
 * the two entries are the whole point: Rollup puts a module imported by both
 * into one, it belongs to neither tree by name, and it is downloaded by both
 * applications. A check that cannot see inside it cannot see the failure it
 * exists to catch.
 *
 * Deliberately not a string search of the built JavaScript either: Rollup
 * minifies identifiers, so a grep can pass because the name it looked for was
 * renamed -- and a check that passes for the wrong reason is worse than none.
 *
 * Symmetric, unlike the one-directional check the spec describes. The lint
 * rules are symmetric and the extra direction costs nothing, so this closes
 * the case where console code reaches shop code by a route eslint cannot see.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const GRAPH = resolve(process.cwd(), 'dist/.vite/bundle-graph.json')

/**
 * Every chunk reachable from one entry, following imports transitively.
 *
 * Dynamic imports count. A lazily-loaded chunk is still the shop serving the
 * console's code to whoever asks.
 *
 * This is also the only layer that catches a dynamic import whose specifier
 * is not a plain string literal -- `import(`../owner/pages/${name}.jsx`)`, or
 * a variable. ESLint's rules match on the literal, so a template literal
 * passes them and reaches only this check. Following static imports alone
 * would leave both layers blind to the same case.
 */
function reachable(chunks, entryFile) {
  const seen = new Set()
  const queue = [entryFile]
  while (queue.length > 0) {
    const file = queue.pop()
    if (seen.has(file)) continue
    seen.add(file)
    const chunk = chunks[file]
    for (const next of [...(chunk?.imports ?? []), ...(chunk?.dynamicImports ?? [])]) {
      queue.push(next)
    }
  }
  return seen
}

let chunks
try {
  chunks = JSON.parse(readFileSync(GRAPH, 'utf8'))
} catch {
  console.error(`Cannot read ${GRAPH}. Run the build first.`)
  process.exit(1)
}

const entries = Object.entries(chunks).filter(([, c]) => c.isEntry)
const find = (name) => entries.find(([, c]) => c.name === name)?.[0]

const store = find('store')
const owner = find('owner')

if (!store || !owner) {
  console.error('Could not find both entry chunks in the bundle graph.')
  console.error(
    `  entry chunks present: ${entries.map(([f, c]) => `${f} (name=${c.name})`).join(', ') || '(none)'}`,
  )
  console.error('  expected chunks named "store" and "owner"')
  process.exit(1)
}

// Module ids are relative to the Vite root (frontend/), so they read
// 'src/owner/pages/AdminPeople.jsx' -- not 'frontend/src/...'. Verified
// against a real build; a prefix with 'frontend/' in it matches nothing and
// the check silently passes everything.
const failures = []
for (const [entryFile, label, forbidden] of [
  [store, 'shop', 'src/owner/'],
  [owner, 'console', 'src/store/'],
]) {
  for (const file of reachable(chunks, entryFile)) {
    for (const module of chunks[file]?.modules ?? []) {
      if (module.includes(forbidden)) {
        failures.push(`${label} bundle reaches ${module} (in chunk ${file})`)
      }
    }
  }
}

if (failures.length > 0) {
  console.error('Bundle isolation FAILED:')
  for (const f of failures) console.error('  ' + f)
  process.exit(1)
}

const counted = new Set([...reachable(chunks, store), ...reachable(chunks, owner)])
console.log(
  `Bundle isolation OK: neither entry reaches the other tree (${counted.size} chunks inspected).`,
)
