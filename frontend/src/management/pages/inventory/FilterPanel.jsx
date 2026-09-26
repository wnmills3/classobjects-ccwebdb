import { useRef } from 'react'

import { AccessLabel } from '../../AccessLabel'
import HelpScope from '../../HelpScope'
import { accel } from '../../shortcuts'
import { SHARED_KEYS } from './specs'

//: For a view that names no placeholder of its own.
const DEFAULT_PLACEHOLDER = 'Search title, description or item code'

/** A label with its underlined letter, or plain text when it has none. */
function Label({ text, letter }) {
  return letter ? <AccessLabel text={text} accessKey={letter} /> : text
}

//: A filter's help topic where it differs from its parameter: a kind is
//: the item's kind, and grade and serial filters take a pattern, which
//: their own topics explain.
const HELP_FOR = {
  kind: 'item_kind',
  grade: 'grade_filter',
  serial_number: 'serial_filter',
  item_code: 'item_code_filter',
}
const helpFor = (param) => HELP_FOR[param] ?? param

/**
 * The search box, facet dropdowns, text filters and year range for an
 * inventory view, plus the "N matching" / "Clear filters" row.
 *
 * The search box has no field syntax to learn -- one term, matched anywhere,
 * ignoring case -- but it has two behaviors nobody guesses: several words are
 * one phrase in that order, and `%` and `_` are wildcards. A placeholder
 * cannot hold that, so the view's `searchExamples` are listed underneath,
 * each one runnable with a click. The box is uncontrolled, so a clicked
 * example is written into it directly before being applied; otherwise the
 * results would change while the box still showed the old text.
 *
 * Every field has an Alt+letter accelerator, underlined in its label. The
 * search box gained a visible "Search" label for that reason: an underline
 * needs somewhere to be.
 */
export default function FilterPanel({
  config,
  current,
  apply,
  facets,
  issues,
  issueDescriptions,
  total,
  busy,
  clear,
}) {
  const box = useRef(null)
  const examples = config.searchExamples ?? []

  function tryExample(term) {
    box.current.value = term
    apply({ q: term })
  }

  return (
    <HelpScope>
      <div className="search-panel">
        <label className="search-row" data-help="search_text">
          <span className="search-label">
            <AccessLabel text="Search" accessKey={SHARED_KEYS.search} />
          </span>
          <input
            ref={box}
            className="search-text"
            placeholder={config.searchPlaceholder ?? DEFAULT_PLACEHOLDER}
            defaultValue={current.q ?? ''}
            onKeyDown={(e) => {
              if (e.key === 'Enter') apply({ q: e.target.value })
            }}
            onBlur={(e) => apply({ q: e.target.value })}
            {...accel(SHARED_KEYS.search)}
          />
        </label>

        {examples.length > 0 && (
          <details className="search-help" data-help="search_tips">
            <summary {...accel(SHARED_KEYS.tips)}>
              <AccessLabel text="Search tips" accessKey={SHARED_KEYS.tips} />
            </summary>
            <ul>
              {examples.map(([term, why]) => (
                <li key={term}>
                  <button
                    type="button"
                    className="link"
                    onClick={() => tryExample(term)}
                    // Names the action; it still contains the visible term, so
                    // speech input that says what it sees still finds it.
                    aria-label={`Search for ${term}`}
                    title={`Search for ${term}`}
                  >
                    <code>{term}</code>
                  </button>
                  {' - '}
                  {why}
                </li>
              ))}
            </ul>
            <p className="muted">
              Case is ignored. Click an example to run it. The dropdowns and year boxes
              below narrow whatever the search finds. Every field has an Alt+letter
              shortcut: the underlined letter in its label.
            </p>
          </details>
        )}

        <div className="filter-grid">
          {config.facetFilters.map(([label, param, facetKey, letter]) => {
            const options = facets[facetKey] ?? []
            return (
              <label key={param} data-help={helpFor(param)}>
                <Label text={label} letter={letter} />
                <select
                  value={current[param] ?? ''}
                  onChange={(e) => apply({ [param]: e.target.value })}
                  disabled={options.length === 0}
                  title={
                    options.length === 0
                      ? `No ${label.toLowerCase()} has been recorded on any matching item yet`
                      : undefined
                  }
                  {...accel(letter)}
                >
                  {/* A disabled control with no explanation reads as broken.
                    Empty here means the field is unrecorded on every
                    matching item -- a gap in the data, not in the filter. */}
                  <option value="">
                    {options.length === 0 ? 'None recorded' : 'Any'}
                  </option>
                  {options.map((o) => (
                    <option key={String(o.value)} value={String(o.value)}>
                      {/* The label is for reading; the value is what the
                        filter compares -- "Cent" shown, usd_coin_0_01 sent. */}
                      {o.label ?? String(o.value)} ({o.count})
                    </option>
                  ))}
                </select>
              </label>
            )
          })}

          {/* Text filters match anywhere in the value and ignore case, so a
            partial serial finds the note. `%` and `_` reach the SQL pattern
            unescaped and work as wildcards -- `_` for one character, which
            is what finds a run of consecutive notes. */}
          {(config.textFilters ?? []).map(([label, param, placeholder, letter]) => (
            <label key={param} data-help={helpFor(param)}>
              <Label text={label} letter={letter} />
              <input
                type="text"
                placeholder={placeholder}
                defaultValue={current[param] ?? ''}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') apply({ [param]: e.target.value })
                }}
                onBlur={(e) => apply({ [param]: e.target.value })}
                {...accel(letter)}
              />
            </label>
          ))}

          <label data-help="year_from">
            <AccessLabel text="Year from" accessKey={SHARED_KEYS.yearFrom} />
            <input
              type="number"
              defaultValue={current.year_min ?? ''}
              onBlur={(e) => apply({ year_min: e.target.value })}
              {...accel(SHARED_KEYS.yearFrom)}
            />
          </label>
          <label data-help="year_to">
            <AccessLabel text="Year to" accessKey={SHARED_KEYS.yearTo} />
            <input
              type="number"
              defaultValue={current.year_max ?? ''}
              onBlur={(e) => apply({ year_max: e.target.value })}
              {...accel(SHARED_KEYS.yearTo)}
            />
          </label>
        </div>

        {/* Named checks, with the size of each job visible before committing
          to it. Counts ignore the selected check, so choosing one does not
          hide what else is left. A check with no hits is not offered: a list
          of a dozen zeroes buries the two that matter. */}
        <div className="issue-checks" data-help="issue_checks">
          {config.issueChecks
            .filter((code) => issues[code])
            .map((code) => (
              <button
                key={code}
                className={current.issue === code ? 'chip chip-on' : 'chip'}
                title={issueDescriptions[code]}
                onClick={() => apply({ issue: current.issue === code ? '' : code })}
              >
                {code.replaceAll('_', ' ')} ({issues[code].toLocaleString()})
              </button>
            ))}
        </div>

        <div className="row">
          <button
            className="link"
            onClick={clear}
            data-help="clear_filters"
            {...accel(SHARED_KEYS.clear)}
          >
            <AccessLabel text="Clear filters" accessKey={SHARED_KEYS.clear} />
          </button>
          <span className="muted">
            {busy ? 'Searching...' : `${total.toLocaleString()} matching`}
          </span>
        </div>
      </div>
    </HelpScope>
  )
}
