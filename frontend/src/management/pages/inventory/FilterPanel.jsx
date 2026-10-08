import { useEffect, useRef } from 'react'

import { AccessLabel } from '../../AccessLabel'
import HelpScope from '../../HelpScope'
import { accel } from '../../shortcuts'
import { missingLabel } from './missingFields'
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
  sellers_item_id: 'sellers_item_id_filter',
}
/** The help topic for a filter parameter: its own name unless listed above. */
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
 * The typed boxes are uncontrolled, and two things keep them true to the
 * filters in force (`current`). Leaving a box applies it only if its text
 * differs from the filter it shows: applying returns to the first page, so
 * a box merely passed through must change nothing. And when the filters
 * change from elsewhere -- Clear filters, Back -- each box takes the new
 * value, except the one being typed in: text left behind in a box would be
 * applied again the next time it lost focus.
 *
 * Nearly every field has an Alt+letter accelerator, underlined in its label;
 * a filter whose spec gives no letter has none. The search box has a visible
 * "Search" label for that reason: an underline needs somewhere to be.
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
  const panel = useRef(null)
  const examples = config.searchExamples ?? []

  // The filters in force, as one string: the effect below runs when they
  // change, not on every render.
  const inForce = JSON.stringify(current)
  useEffect(() => {
    const applied = JSON.parse(inForce)
    for (const input of panel.current.querySelectorAll('input[data-param]')) {
      const value = applied[input.dataset.param] ?? ''
      if (input !== document.activeElement && input.value !== value) {
        input.value = value
      }
    }
  }, [inForce])

  /** Apply a box's text on leaving it, unless it already is the filter. */
  const applyOnLeaving = (param) => (e) => {
    if (e.target.value !== (current[param] ?? '')) apply({ [param]: e.target.value })
  }

  function tryExample(term) {
    box.current.value = term
    apply({ q: term })
  }

  return (
    <HelpScope>
      <div className="search-panel" ref={panel}>
        <label className="search-row" data-help="search_text">
          <span className="search-label">
            <AccessLabel text="Search" accessKey={SHARED_KEYS.search} />
          </span>
          <input
            ref={box}
            className="search-text"
            placeholder={config.searchPlaceholder ?? DEFAULT_PLACEHOLDER}
            data-param="q"
            defaultValue={current.q ?? ''}
            onKeyDown={(e) => {
              if (e.key === 'Enter') apply({ q: e.target.value })
            }}
            onBlur={applyOnLeaving('q')}
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
              below narrow whatever the search finds. A field with an underlined letter
              in its label has an Alt+letter shortcut.
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

          {/* Text filters other than Grade match anywhere in the value and
            ignore case, so a partial serial finds the note. `%` and `_` reach
            the SQL pattern unescaped and work as wildcards -- `_` for one
            character, which is what finds a run of consecutive notes. */}
          {(config.textFilters ?? []).map(([label, param, placeholder, letter]) => (
            <label key={param} data-help={helpFor(param)}>
              <Label text={label} letter={letter} />
              <input
                type="text"
                placeholder={placeholder}
                data-param={param}
                defaultValue={current[param] ?? ''}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') apply({ [param]: e.target.value })
                }}
                onBlur={applyOnLeaving(param)}
                {...accel(letter)}
              />
            </label>
          ))}

          <label data-help="year_from">
            <AccessLabel text="Year from" accessKey={SHARED_KEYS.yearFrom} />
            <input
              type="number"
              data-param="year_min"
              defaultValue={current.year_min ?? ''}
              onBlur={applyOnLeaving('year_min')}
              {...accel(SHARED_KEYS.yearFrom)}
            />
          </label>
          <label data-help="year_to">
            <AccessLabel text="Year to" accessKey={SHARED_KEYS.yearTo} />
            <input
              type="number"
              data-param="year_max"
              defaultValue={current.year_max ?? ''}
              onBlur={applyOnLeaving('year_max')}
              {...accel(SHARED_KEYS.yearTo)}
            />
          </label>
        </div>

        {/* A `missing=` filter has no control of its own -- it arrives from a
          report's drill-down -- so without this chip the list would be
          narrowed with nothing on screen saying so. Clicking removes it. */}
        {current.missing && (
          <div className="issue-checks" data-help="missing_filter">
            <button
              type="button"
              className="chip chip-on"
              aria-label={`Remove filter ${missingLabel(current.missing)}`}
              title="Remove this filter"
              onClick={() => apply({ missing: '' })}
            >
              {missingLabel(current.missing)} <span aria-hidden="true">✕</span>
            </button>
          </div>
        )}

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
