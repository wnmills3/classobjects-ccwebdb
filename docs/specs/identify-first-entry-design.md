# Identify first: receiving and new-item entry

2026-09-29

A piece is identified by a handful of facts written the way collectors write
them -- "1921-D $1", "Series 1934-A $5" -- and most of its other classifiers
follow from those facts (`classifier-defaults-design.md`,
`series-classification-design.md`). So entry starts with those facts: the
**Identify** fields come first when staff receive an item in the management
console's Receiving page (`/management/receiving`), and what the facts decide
is filled in from them. An item entered on the Purchases page is entered in
the item editor (`entry-panels-design.md`), where the same facts are filled
when it is saved. Typing less, and typing the facts
before the conclusions, is faster and removes a class of mismatch (a note
whose class contradicts its series).

## The Identify fields

`management/identify.js` (`identifyKeys`) is the one list:

| Kind | Fields, in order |
|---|---|
| Banknote (`currency`) | Series year, Series letter, Denomination, Serial number, Face plate, Back plate |
| Any other kind | Year, Mint (where the kind has one: `fieldFitsKind('mint', kind)`), Denomination |

The kind decides the list, so it is shown first and must be right first. The
fields are the same ones, with the same names, help keys and validation, as
the item editor's and `POST /api/inventory`'s. The denomination picker offers
only the kind's own denominations.

## What the facts decide

| From | Filled | Rule |
|---|---|---|
| Denomination, series year and letter (note) | note class, seal, signatures | `note_issue` (`app.classifier_defaults`) |
| Serial number, series year (Federal Reserve Note) | Reserve Bank | `serial_district`; from Series 1996, $5 and up read the second prefix letter |
| Face plate | printing location | an `FW` prefix is Fort Worth (`app.plates`), applied when the item is saved |
| Denomination, country, year (coin) | metal | `composition` |
| Denomination, year, series letter; seal and class for a note | **design series** | `app.series_classify.suggest_series`, with no text evidence |

The Identify section asks `GET /api/defaults/note` or `/coin` after a 250 ms
pause in typing. The design series is decided by the same rules as the batch pass,
from the facts alone: one candidate that needs no evidence is suggested; a
boundary year (1921 $1: Morgan or Peace) or an evidence-only design is left
open. Text is not read at entry -- a title is the seller's words, and the
save and the batch pass weigh it. The note lookup also returns a `warning`
when no issue of that denomination has that series, naming the series on
record, so a mistyped letter (1953E for 1953B) is caught at once.

Saving the item then runs the same rules for real (`refresh_items` and
`refresh_series`; `series-classification-design.md`, "On save").

## The other half of a star-note pair

A star note whose eight digits are those of an ordinary note in the
collection is a pair to keep together, and easy to own without knowing it.
Wherever a note's serial number is entered -- the Identify section and the
item editor -- `SerialPair` asks `GET /api/inventory/{id}/serial-pairs`
(`app.serial_pairs`) after a 250 ms pause in typing, and says so when there
is one: for a note, the star notes of its digits; for a star note, the notes
without the star. Each is named by item code and serial, with its
denomination and series.

- Only the digits are compared. The letters round them say which Bank and
  block, and a pair across two Banks or two denominations is still told of;
  the denomination and series shown are for judging which it is.
- The serial as typed is what is matched (`serial_number` on the request),
  so the notice shows before the note is saved and stays after. Without the
  parameter the saved serial is.
- Nothing is asked or matched until the serial has exactly eight digits: one
  cut short would match every other cut short the same way.
- Deleted and split items are not offered. A search that fails shows
  nothing: it is a convenience, and never stands in the way of a save.

## Receiving

The receipt dialog (`receiving/ReceiptPanel.jsx`) opens with an **Identify**
section (`IdentifySection.jsx`) above Arrived and Storage location, for a
single item:

- The kind, read-only, then the Identify fields, filled from the item.
- Below them, **From these facts**: what the suggestion endpoint says the
  facts decide (class, seal, signatures, Reserve Bank, metal, series) and the
  no-such-issue warning where there is one. It says what the facts alone
  decide; a value a person set on the item stands when saved.
- **Saved with Receive**, and only with Receive: the changed fields go in one
  `PATCH /api/inventory/{id}` carrying `base` (the values the section
  loaded), before the receipt is posted. Missing and Cancelled mean nobody
  held the piece; Returned means it is going back -- none of them saves
  Identify edits, and the section says so while it has any.
- A refused PATCH stops the receipt: nothing is received, the error is shown
  and every field keeps what was typed. A PATCH that succeeds and a receipt
  that then fails leaves the Identify values saved; the section takes the
  saved values as its new starting point, so a retry does not resend them.
- The for-sale acknowledgement covers both: the PATCH's 409 is answered by
  the same dialog as the receipt's, and the resubmission sends
  `acknowledge_for_sale` with both.
- **Confirm or correct fields** stays live while Identify has unsaved
  edits, and pressing it then says to receive or undo them first instead
  of opening: the full editor never opens over values it cannot see.
  Identify is hidden while the editor is open, and reads the item again when
  it closes, so its `base` is what the editor saved.
- A coin whose years are a range shows its Year box disabled: one box cannot
  hold a range, and the full editor can.
- Nothing typed in Identify is carried to the next line: the facts are the
  piece's, unlike the arrival date and location.

## Tests

Backend: `tests/test_entry_suggestions.py`, `tests/test_series_refresh.py`,
`tests/test_series_classify.py`, `tests/test_serial_pairs.py`. Frontend:
`management/identify.test.js`, `pages/receiving/ReceiptPanel.test.jsx`,
`pages/inventory/SerialPair.test.jsx`.
