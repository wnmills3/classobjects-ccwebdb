import { useCallback, useEffect, useState } from 'react'

import { api } from '../api'
import { useAuth } from '../auth-context'

/**
 * People administration, in two tabs.
 *
 * Accounts and customers are separate tables joined by a nullable `user_id`,
 * because a guest checkout creates a customer with no account and an account
 * holder who has never bought anything has no customer record. One tab each
 * keeps that honest -- a merged grid would imply every customer can sign in.
 */

const PHONE_DEFAULT_CC = '+1'

function Tabs({ active, onChange }) {
  return (
    <div className="tabs">
      {[
        ['accounts', 'Accounts'],
        ['customers', 'Customers'],
      ].map(([key, label]) => (
        <button
          key={key}
          className={key === active ? 'tab tab-active' : 'tab'}
          onClick={() => onChange(key)}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

function Accounts({ notify }) {
  const { user: me } = useAuth()
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [pwFor, setPwFor] = useState(null)
  const [pw, setPw] = useState('')

  const load = useCallback(() => {
    api
      .listUsers()
      .then((body) => {
        setRows(body)
        setError('')
      })
      .catch((err) => setError(err.message))
  }, [])

  useEffect(load, [load])

  async function patch(id, payload, what) {
    try {
      await api.updateUser(id, payload)
      notify(what)
      load()
    } catch (err) {
      // The last-administrator refusal arrives here as a 409. It is the
      // whole point of the guard, so it is shown rather than swallowed.
      setError(err.message)
    }
  }

  async function savePassword(id) {
    try {
      await api.setUserPassword(id, pw)
      setPwFor(null)
      setPw('')
      notify('Password changed. That account has been signed out everywhere.')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error) return <p className="error">{error}</p>
  if (!rows) return <p className="muted">Loading...</p>

  return (
    <>
      <p className="muted">
        Accounts are never deleted, only deactivated -- a person who has placed orders
        cannot be removed without breaking that history.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Email</th>
            <th>Name</th>
            <th>Role</th>
            <th>Active</th>
            <th>Password</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => (
            <tr key={u.id}>
              <td className="mono">
                {u.email}
                {u.id === me?.id && <span className="badge">you</span>}
              </td>
              <td>{u.full_name || <span className="muted">-</span>}</td>
              <td>
                <select
                  value={u.role}
                  onChange={(e) =>
                    patch(
                      u.id,
                      { role: e.target.value },
                      `${u.email} is now ${e.target.value}`,
                    )
                  }
                >
                  <option value="customer">customer</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td>
                <button
                  className="link"
                  onClick={() =>
                    patch(
                      u.id,
                      { is_active: !u.is_active },
                      `${u.email} ${u.is_active ? 'deactivated' : 'reactivated'}`,
                    )
                  }
                >
                  {u.is_active ? 'Active' : 'Disabled'}
                </button>
              </td>
              <td>
                {pwFor === u.id ? (
                  <span className="row">
                    <input
                      type="password"
                      autoFocus
                      placeholder="at least 8 characters"
                      value={pw}
                      onChange={(e) => setPw(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && savePassword(u.id)}
                    />
                    <button onClick={() => savePassword(u.id)}>Set</button>
                    <button className="link" onClick={() => setPwFor(null)}>
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    className="link"
                    onClick={() => {
                      setPwFor(u.id)
                      setPw('')
                    }}
                  >
                    Set password
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function AddressForm({ customerId, onSaved, onCancel, notify, setError }) {
  const [form, setForm] = useState({
    line1: '',
    line2: '',
    city: '',
    region: '',
    postal_code: '',
    country: 'US',
  })
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  async function save() {
    try {
      await api.addCustomerAddress(customerId, { ...form, address_kind: 'shipping' })
      notify(
        'Shipping address recorded. The previous one was retired, not overwritten.',
      )
      onSaved()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="search-panel">
      <div className="filter-grid">
        <label>
          Address line 1
          <input value={form.line1} onChange={set('line1')} />
        </label>
        <label>
          Line 2
          <input value={form.line2} onChange={set('line2')} />
        </label>
        <label>
          City
          <input value={form.city} onChange={set('city')} />
        </label>
        <label>
          State / region
          <input value={form.region} onChange={set('region')} />
        </label>
        <label>
          Postal code
          <input value={form.postal_code} onChange={set('postal_code')} />
        </label>
        <label>
          Country
          <input value={form.country} onChange={set('country')} placeholder="US" />
        </label>
      </div>
      <div className="row">
        <button onClick={save}>Save address</button>
        <button className="link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function Customers({ notify }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [addressFor, setAddressFor] = useState(null)
  const [draft, setDraft] = useState({})

  const load = useCallback(() => {
    api
      .listCustomers()
      .then((body) => {
        setRows(body)
        setError('')
      })
      .catch((err) => setError(err.message))
  }, [])

  useEffect(load, [load])

  async function saveEdit(id) {
    try {
      await api.updateCustomer(id, draft)
      setEditing(null)
      notify('Customer updated')
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (error) return <p className="error">{error}</p>
  if (!rows) return <p className="muted">Loading...</p>
  if (rows.length === 0)
    return (
      <p className="muted">
        No customers yet. A customer record is created by a purchase, including a guest
        checkout, so this fills up once the store is selling.
      </p>
    )

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Email</th>
          <th>Phone</th>
          <th>Shipping address</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const shipping = (c.addresses ?? []).find(
            (a) => a.address_kind === 'shipping' && a.is_default,
          )
          const isEditing = editing === c.id
          return (
            <tr key={c.id}>
              <td>
                {isEditing ? (
                  <input
                    value={draft.display_name ?? c.display_name}
                    onChange={(e) =>
                      setDraft({ ...draft, display_name: e.target.value })
                    }
                  />
                ) : (
                  c.display_name
                )}
              </td>
              <td className="mono">
                {isEditing ? (
                  <input
                    value={draft.email ?? c.email ?? ''}
                    onChange={(e) => setDraft({ ...draft, email: e.target.value })}
                  />
                ) : (
                  c.email || <span className="muted">-</span>
                )}
              </td>
              <td className="mono">
                {isEditing ? (
                  <input
                    // Prefilled with the country code rather than storing it
                    // separately: the number is kept whole in E.164 so it is
                    // dialable from anywhere, and two columns could disagree.
                    value={draft.phone ?? c.phone ?? PHONE_DEFAULT_CC}
                    onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
                    placeholder="+12125551234"
                  />
                ) : (
                  c.phone || <span className="muted">-</span>
                )}
              </td>
              <td>
                {shipping ? (
                  `${shipping.line1}, ${shipping.city}${
                    shipping.region ? ' ' + shipping.region : ''
                  } ${shipping.postal_code ?? ''}`
                ) : (
                  <span className="muted">none</span>
                )}
              </td>
              <td>
                {isEditing ? (
                  <span className="row">
                    <button onClick={() => saveEdit(c.id)}>Save</button>
                    <button className="link" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </span>
                ) : (
                  <span className="row">
                    <button
                      className="link"
                      onClick={() => {
                        setEditing(c.id)
                        setDraft({})
                      }}
                    >
                      Edit
                    </button>
                    <button className="link" onClick={() => setAddressFor(c.id)}>
                      New address
                    </button>
                  </span>
                )}
                {addressFor === c.id && (
                  <AddressForm
                    customerId={c.id}
                    notify={notify}
                    setError={setError}
                    onCancel={() => setAddressFor(null)}
                    onSaved={() => {
                      setAddressFor(null)
                      load()
                    }}
                  />
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function AdminPeople() {
  const [tab, setTab] = useState('accounts')
  const [message, setMessage] = useState('')

  return (
    <section>
      <h1>People</h1>
      <Tabs active={tab} onChange={setTab} />
      {message && <p className="notice">{message}</p>}
      {tab === 'accounts' ? (
        <Accounts notify={setMessage} />
      ) : (
        <Customers notify={setMessage} />
      )}
    </section>
  )
}
