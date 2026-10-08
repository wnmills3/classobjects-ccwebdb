import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { api } from '../api'
import HelpScope from '../HelpScope'
import { useRequest } from '../../shared/useRequest'
import ItemEditDialog from './inventory/ItemEditDialog'
import { useLinkedItem } from './inventory/useLinkedItem'
import ReportForm from './reports/ReportForm'
import ReportView from './reports/ReportView'
import { refusalText } from './reports/values'

/**
 * Reports: read-only answers from the whole collection
 * (`docs/specs/reporting-design.md`, *Console: the Reports page*).
 *
 * The catalog comes from the API, grouped as it lists it; nothing here knows
 * one report from another. Choosing one shows its purpose and its parameters
 * -- a form built from the catalog's description of them -- and runs it.
 *
 * **The address is the source of truth.** `?report=pr_outstanding&
 * overdue_days=30` names the report and its parameters, so a report can be
 * bookmarked, reopened or linked to, and opening the address runs it. Only
 * parameters that differ from their defaults are written there; Run writes
 * them, and pressing Run on an unchanged form runs the report again.
 *
 * Everything in the address but `report` is handed to the API as it stands,
 * which validates it: a bad value, or a parameter the report does not have,
 * comes back as a 422 and is shown beside the form by the parameter's label.
 *
 * **A row naming one item opens its editor here**, over the report, so
 * closing it returns to the report rather than to an inventory page; a save
 * runs the report again, since the item may no longer belong in it.
 */
export default function Reports() {
  const [search, setSearch] = useSearchParams()
  const catalog = useRequest('catalog', () => api.listReports())
  const reportId = search.get('report')
  const report = catalog.data?.find((entry) => entry.id === reportId) ?? null

  const sent = Object.fromEntries(
    [...search.entries()].filter(([key]) => key !== 'report'),
  )
  const runKey = report ? search.toString() : null
  const run = useRequest(runKey, () =>
    api.runReport(report.id, sent).catch((err) => {
      throw new Error(refusalText(err, report))
    }),
  )

  function runWith(draft) {
    const next = new URLSearchParams({ report: report.id })
    for (const param of report.params) {
      const value = String(draft[param.name] ?? '').trim()
      if (value !== '' && value !== String(param.default ?? ''))
        next.set(param.name, value)
    }
    if (next.toString() === search.toString()) run.reload()
    else setSearch(next)
  }

  // The item a row's link asked for, `{ view, code }`, while its editor is
  // open or being found.
  const [opening, setOpening] = useState(null)
  const opened = useLinkedItem(opening?.view, opening?.code)

  const values = report
    ? Object.fromEntries(
        report.params.map((param) => [param.name, formValue(param, search)]),
      )
    : {}
  const result = runKey && !run.busy && !run.error ? run.data : null

  return (
    <HelpScope>
      <section className="reports">
        <h1 className="screen-only">Reports</h1>
        {catalog.error && <p className="error">{catalog.error}</p>}
        {catalog.busy && <p className="muted">Loading...</p>}

        <div className="reports-layout">
          {catalog.data && <Catalog entries={catalog.data} current={reportId} />}

          <div className="reports-main">
            {reportId && catalog.data && !report && (
              <p className="error">There is no report named {reportId}.</p>
            )}
            {!reportId && catalog.data && (
              <p className="muted">Choose a report to run it.</p>
            )}
            {report && (
              <>
                <div className="screen-only">
                  <h2>{report.title}</h2>
                  <p className="muted">{report.purpose}</p>
                </div>
                <ReportForm
                  key={runKey}
                  report={report}
                  values={values}
                  error={run.error}
                  busy={run.busy}
                  ranAt={result?.run_at}
                  onRun={runWith}
                />
                {result && (
                  <ReportView
                    key={`${runKey}|${result.run_at}`}
                    report={report}
                    result={result}
                    sent={sent}
                    onOpenItem={setOpening}
                  />
                )}
                {opened.problem && <p className="error">{opened.problem}</p>}
              </>
            )}
          </div>
        </div>
        {opened.id && (
          <ItemEditDialog
            key={opened.id}
            itemId={opened.id}
            onSaved={() => {
              setOpening(null)
              run.reload()
            }}
            onChanged={run.reload}
            onClose={() => setOpening(null)}
          />
        )}
      </section>
    </HelpScope>
  )
}

/**
 * What the form starts a parameter at: the address's value, or its default.
 *
 * A dropdown can only show one of its own choices, so a choice the address
 * names that is not among them starts at the default too -- otherwise the
 * dropdown would show its first option while Run went on sending the value
 * that was refused.
 */
function formValue(param, search) {
  const fallback = String(param.default ?? '')
  const asked = search.get(param.name)
  if (asked === null) return fallback
  if (param.type === 'choice' && !param.choices.map(String).includes(asked))
    return fallback
  return asked
}

/** The catalog, one labeled group of links per group, in the API's order. */
function Catalog({ entries, current }) {
  const byGroup = new Map()
  for (const entry of entries) {
    if (!byGroup.has(entry.group)) byGroup.set(entry.group, [])
    byGroup.get(entry.group).push(entry)
  }
  const groups = [...byGroup].map(([name, members]) => ({ name, entries: members }))
  return (
    <nav className="report-catalog" aria-label="Reports" data-help="report_catalog">
      {groups.map((group, index) => (
        <div key={group.name} role="group" aria-labelledby={`report-group-${index}`}>
          <h2 id={`report-group-${index}`}>{group.name}</h2>
          <ul>
            {group.entries.map((entry) => (
              <li key={entry.id}>
                <Link
                  to={`?report=${encodeURIComponent(entry.id)}`}
                  aria-current={entry.id === current ? 'page' : undefined}
                >
                  {entry.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}
