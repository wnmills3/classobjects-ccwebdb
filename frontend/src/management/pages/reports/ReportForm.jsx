import { useState } from 'react'

import { choiceText, paramHelp } from './values'

/**
 * A report's parameters, built from the catalog's description of them, and
 * Run.
 *
 * A choice is a dropdown, an integer a number box, anything else a text box.
 * The form holds its own draft; the page keys it by the address, so a new
 * address -- another report, Back, a link -- starts it again from there.
 * `error` is why the last run was refused, shown beside the fields it names.
 */
export default function ReportForm({ report, values, error, onRun }) {
  const [draft, setDraft] = useState(values)
  const [emptyError, setEmptyError] = useState('')

  function submit(e) {
    e.preventDefault()
    // An emptied field would otherwise run silently with that parameter's
    // default (`runWith` drops a blank value) -- indistinguishable from
    // asking for the default on purpose, so it is refused here instead. A
    // date is the one exception (Ruling P2-3): emptying it means no bound on
    // that side, not a request for its (null) default, so it runs as-is.
    const blank = report.params.find(
      (param) => param.type !== 'date' && String(draft[param.name] ?? '').trim() === '',
    )
    if (blank) {
      setEmptyError(`Enter a value for ${blank.label}.`)
      return
    }
    setEmptyError('')
    onRun(draft)
  }

  return (
    <form className="search-panel report-form" onSubmit={submit}>
      {report.params.length > 0 && (
        <div className="filter-grid">
          {report.params.map((param) => (
            <label key={param.name} data-help={paramHelp(param.name)}>
              {param.label}
              <ParamInput
                param={param}
                value={draft[param.name] ?? ''}
                onChange={(value) => setDraft({ ...draft, [param.name]: value })}
              />
            </label>
          ))}
        </div>
      )}
      {(emptyError || error) && <p className="error">{emptyError || error}</p>}
      <button type="submit" data-help="report_run">
        Run
      </button>
    </form>
  )
}

function ParamInput({ param, value, onChange }) {
  if (param.type === 'choice') {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {param.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choiceText(choice)}
          </option>
        ))}
      </select>
    )
  }
  if (param.type === 'date') {
    return (
      <input type="date" value={value} onChange={(e) => onChange(e.target.value)} />
    )
  }
  return (
    <input
      type={param.type === 'integer' ? 'number' : 'text'}
      step={param.type === 'integer' ? 1 : undefined}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
