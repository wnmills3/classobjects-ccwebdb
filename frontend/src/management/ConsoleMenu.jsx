import { useEffect, useId, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

//: The console's menu, grouped by what the work is for rather than listed
//: flat: what is held, buying it, selling it, the reports over all of it,
//: and the settings the rest relies on. Each group is a name and its pages,
//: in the order the work is done -- a purchase is entered before it is
//: received; an item is listed, grouped into a lot or an auction, and
//: then sold. A group of several pages is a button that opens a list of
//: them; a group of one page is a plain link to it, since a list of one
//: would only add a click.
const MENU = [
  [
    'Inventory',
    [
      ['/inventory/coins', 'Coins'],
      ['/inventory/currency', 'Currency'],
      ['/photos', 'Photos'],
      ['/order-lookup', 'Order lookup'],
    ],
  ],
  [
    'Procurement',
    [
      ['/purchases', 'Purchases'],
      ['/receiving', 'Receive'],
    ],
  ],
  [
    'Selling',
    [
      ['/listings', 'Listings'],
      ['/lots', 'Lots'],
      ['/auctions', 'Auctions'],
      ['/sales', 'Sales'],
      ['/platforms', 'Platforms'],
      ['/spot-prices', 'Spot prices'],
    ],
  ],
  ['Reports', [['/reports', 'Reports']]],
  [
    'Settings',
    [
      ['/people', 'People'],
      ['/vocabularies', 'Vocabularies'],
      ['/lists', 'Lists'],
    ],
  ],
]

/** Whether the address shown is this page or something under it. */
function isAt(pathname, to) {
  return pathname === to || pathname.startsWith(`${to}/`)
}

/**
 * One group of the menu: a button, and its pages in a list under it while
 * it is open.
 *
 * The list is in the page only while open, so a closed group adds nothing
 * to the tab order. Down-arrow on the button opens the list on its first
 * page, the arrows move through it, and Escape closes it and returns to the
 * button. The button is marked when the page shown is one of the group's,
 * so a closed menu still says where the console is.
 */
function MenuGroup({ name, links, open, onOpen, onClose }) {
  const { pathname } = useLocation()
  const listId = useId()
  const button = useRef(null)
  const list = useRef(null)
  //: Set when the list was opened from the keyboard: focus goes to its
  //: first page once it is in the document.
  const focusFirst = useRef(false)
  const here = links.some(([to]) => isAt(pathname, to))

  useEffect(() => {
    if (open && focusFirst.current) list.current?.querySelector('a')?.focus()
    focusFirst.current = false
  }, [open])

  function onButtonKey(event) {
    if (event.key !== 'ArrowDown') return
    event.preventDefault()
    focusFirst.current = true
    onOpen()
  }

  function onListKey(event) {
    if (event.key === 'Escape') {
      // Kept from a dialog or page behind the menu: this Escape was for it.
      event.stopPropagation()
      onClose()
      button.current?.focus()
      return
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const pages = [...list.current.querySelectorAll('a')]
    const step = event.key === 'ArrowDown' ? 1 : -1
    const next = pages.indexOf(document.activeElement) + step
    pages[(next + pages.length) % pages.length].focus()
  }

  /** Focus that leaves the group -- tabbing on, say -- closes its list. */
  function onBlur(event) {
    if (open && !event.currentTarget.contains(event.relatedTarget)) onClose()
  }

  return (
    <div className="menu-group" onBlur={onBlur}>
      <button
        ref={button}
        type="button"
        className={here ? 'menu-button current' : 'menu-button'}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => (open ? onClose() : onOpen())}
        onKeyDown={onButtonKey}
      >
        {name}
        <span aria-hidden="true"> ▾</span>
      </button>
      {open && (
        <div
          ref={list}
          id={listId}
          className="menu-list"
          role="group"
          aria-label={name}
          onKeyDown={onListKey}
        >
          {links.map(([to, text]) => (
            <NavLink key={to} to={to} onClick={onClose}>
              {text}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * The console's menu: one row of group names across the top of the page.
 *
 * One list is open at a time. It closes when a page is chosen, on Escape,
 * when focus leaves it, and on a click anywhere outside the menu.
 */
export default function ConsoleMenu() {
  const [open, setOpen] = useState(null)
  const nav = useRef(null)

  useEffect(() => {
    if (open === null) return undefined
    function onMouseDown(event) {
      if (!nav.current?.contains(event.target)) setOpen(null)
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [open])

  return (
    <nav ref={nav} className="nav console-menu" aria-label="Console">
      {MENU.map(([name, links]) =>
        links.length === 1 ? (
          <NavLink key={name} to={links[0][0]} onClick={() => setOpen(null)}>
            {links[0][1]}
          </NavLink>
        ) : (
          <MenuGroup
            key={name}
            name={name}
            links={links}
            open={open === name}
            onOpen={() => setOpen(name)}
            onClose={() => setOpen(null)}
          />
        ),
      )}
    </nav>
  )
}
