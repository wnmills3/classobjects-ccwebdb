/**
 * The search box, facet dropdowns, text filters and year range for an
 * inventory view, plus the "N matching" / "Clear filters" row.
 */
export default function FilterPanel({
  config,
  current,
  apply,
  facets,
  // Not rendered until Task 12 -- bound to `_issues` so eslint's
  // convention for a deliberately unused binding applies without
  // changing the prop name callers pass.
  issues: _issues,
  total,
  busy,
  clear,
}) {
  return (
    <div className="search-panel">
      <input
        className="search-text"
        placeholder="Search descriptions and series, e.g. mercury, buffalo, morgan"
        defaultValue={current.q ?? ''}
        onKeyDown={(e) => {
          if (e.key === 'Enter') apply({ q: e.target.value })
        }}
        onBlur={(e) => apply({ q: e.target.value })}
      />

      <div className="filter-grid">
        {config.facetFilters.map(([label, param, facetKey]) => {
          const options = facets[facetKey] ?? []
          return (
            <label key={param}>
              {label}
              <select
                value={current[param] ?? ''}
                onChange={(e) => apply({ [param]: e.target.value })}
                disabled={options.length === 0}
                title={
                  options.length === 0
                    ? `No ${label.toLowerCase()} has been recorded on any matching item yet`
                    : undefined
                }
              >
                {/* A disabled control with no explanation reads as broken.
                    Empty here means the field is unrecorded on every
                    matching item -- a gap in the data, not in the filter. */}
                <option value="">
                  {options.length === 0 ? 'None recorded' : 'Any'}
                </option>
                {options.map((o) => (
                  <option key={String(o.value)} value={String(o.value)}>
                    {String(o.value)} ({o.count})
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
        {(config.textFilters ?? []).map(([label, param, placeholder]) => (
          <label key={param}>
            {label}
            <input
              type="text"
              placeholder={placeholder}
              defaultValue={current[param] ?? ''}
              onKeyDown={(e) => {
                if (e.key === 'Enter') apply({ [param]: e.target.value })
              }}
              onBlur={(e) => apply({ [param]: e.target.value })}
            />
          </label>
        ))}

        <label>
          Year from
          <input
            type="number"
            defaultValue={current.year_min ?? ''}
            onBlur={(e) => apply({ year_min: e.target.value })}
          />
        </label>
        <label>
          Year to
          <input
            type="number"
            defaultValue={current.year_max ?? ''}
            onBlur={(e) => apply({ year_max: e.target.value })}
          />
        </label>
      </div>

      <div className="row">
        <button className="link" onClick={clear}>
          Clear filters
        </button>
        <span className="muted">
          {busy ? 'Searching...' : `${total.toLocaleString()} matching`}
        </span>
      </div>
    </div>
  )
}
