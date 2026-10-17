# Bullion serial numbers and the identifying-numbers report

2026-10-17

## Why

Many bars, some rounds and every Goldback carry a serial number unique to
the piece. The owner wants it recorded and searchable, chiefly so that a
stolen piece can be described to the police or an insurer by a number no
other piece shares. A graded coin's certificate number serves the same
purpose: it is printed on the holder and visible in every sale listing.

## Goals

- A bullion item holds one serial number, typed in the item editor, stored
  exactly as written.
- The Coins screen (where bullion lives) finds an item by its serial and can
  show and sort by it.
- A note that becomes bullion keeps its serial, and a bullion item that
  becomes a note keeps it too: a serial is never dropped by a kind change.
- One report lists every held item that a number identifies -- a serial, a
  certificate, or both -- ready to hand over after a theft.

## Non-goals

- No uniqueness rule and no duplicate-serial issue check: a repeated serial
  is not proof of a duplicate (owner's ruling).
- No serial on coins, sets, medals or tokens (my call; bullion is what was
  asked for, and widening later is one guard).
- No serial-pattern attributes (solid, radar, repeater, fancy) on bullion:
  those attributes are note-side vocabulary and the pattern pass reads notes
  only. A Goldback's fancy serial is still recorded as its serial.
- Nothing in the public catalog: a serial is never shown to shoppers.
- Receiving's identify step is unchanged.

## Anti-goals

- A serial lost silently: on a kind change, an edit, or a save sent without
  the field.
- A serial changed in form: no upper-casing, trimming or dropped leading
  zeros. It is stored as sent, as a note's is; only a blank is no serial.

## Storage

`coin_detail.serial_number`, `String(64)`, nullable, indexed -- the same shape
as `currency_detail.serial_number`. `coin_detail` is the row every non-note
item already has. One migration adds the column and index; the
`coin_inventory` view is not changed (no application code reads it).

## Rules

- Accepted only where the item is (or, in the same request, becomes)
  bullion. On a coin, set, medal or token it is a 422 naming the field, as a
  note field on a coin is today. A blank sent for any kind is no change.
- The API field is the one name `serial_number` for both notes and bullion;
  the server routes it by the item's resulting kind: to `currency_detail` for
  a note, to `coin_detail` for bullion.
- Blank or whitespace alone is stored as NULL (`_serial_or_none`, shared
  with notes). A change is logged in History like any other field and moves the
  item's version; sent unchanged, it moves nothing.
- Single-item edit and create only; bulk edit refuses it, as it does mint
  and variety.
- Kind change:
  - note to bullion: the note's serial moves to the bullion row. The note's
    other facts still refuse the change until cleared, as today.
  - bullion to note: the serial moves to the note row.
  - bullion to any other non-note kind: refused while a serial is held,
    naming it; clearing it in the same request lets the change through.
- The suggested description of a bullion item ends with `S/N <serial>`, as a
  note's does.

## Search

The Coins view gains a `serial_number` column (off by default), a
case-insensitive contains filter "Serial number" with a free Alt letter, and
the sort. Free-text search is unchanged.

## Report: Serial and certificate numbers

Collection group, `cb_id_numbers`, status/disposition parameters
(`LiveParams`, default received and held). One row per live item holding a
serial number (note or bullion) or at least one certificate number. Columns:
item code, description, kind, metal, fine weight (ozt), serial number,
grading service, certificate numbers (several joined with ", "), photo
(yes/no). Ordered by item code; each row opens the item's editor. Totals: the
count of items and the fine weight.

## Left to the builder

The editor row's place among the bullion facts, the help text, the Alt
letter, and the exact refusal wording, following the code around them.
