# Identify first: receiving and new-item entry

2026-09-26

A piece is identified by a handful of facts written the way collectors write
them -- "1921-D $1", "Series 1934-A $5" -- and most of its other classifiers
follow from those facts (`classifier-defaults-design.md`,
`series-classification-design.md`). Entry therefore starts with those facts:
the **Identify** fields come first when an item is received and when one is
entered, and what they decide is filled in from them.

## The Identify fields

| Kind | Fields, in order |
|---|---|
| Banknote (`currency`) | Series year, Series letter, Denomination, Serial number, Face plate, Back plate |
| Any other kind | Year, Mint (where the kind has one: `fieldFitsKind('mint', kind)`), Denomination |

The kind decides the list, so it is shown first and must be right first; the
fields are the same ones, with the same names, help keys and validation, as
the item editor's and `POST /api/inventory`'s.

## What the facts decide

| From | Filled | Rule |
|---|---|---|
| Denomination, series year and letter (note) | note class, seal, signatures | `note_issue` (`app.classifier_defaults`) |
| Serial number, series year (Federal Reserve Note) | Reserve Bank | `serial_district`; from Series 1996, $5 and up read the second prefix letter |
| Face plate | printing facility | an `FW` prefix is Fort Worth (`app.plates`) |
| Denomination, country, year (coin) | metal | `composition` |
| Denomination, year, series letter; seal and class for a note | **design series** | `app.series_classify.decide`, with no text evidence |

The design series is new to entry. The same rules as the batch pass decide
it, from the facts alone: one candidate that needs no evidence is assigned;
a boundary year (1921 $1: Morgan or Peace) or an evidence-only design is left
open. Text is not read at entry -- a title is the seller's words, and the
batch pass still reads it later.

`GET /api/defaults/note` and `/coin` return `series` beside what they return
now. Both take the facts only, as today.

### On save

Creating or editing an item already refreshes its classifier defaults
(`refresh_items`). It now also refreshes its **series**, per item, in the same
transaction (`app.series_classify.refresh_series`):

1. An item whose series was derived by `series_classify`, or filled by an
   entry suggestion the person accepted, and which the facts now rule out or
   no longer support, has that series cleared and its record removed -- the
   machine takes back its own guess.
2. An item with no series, and no `held` record for `series_id`, is decided
   by `decide()` with its own text as evidence (as the batch does); a design
   found is written and recorded as `series_classify`.

A series a person chose, or one `series_match` read from the text, is never
cleared by this; one the facts contradict is left for the batch pass's
disagreement report. A series the person emptied is recorded as `held` and
stays empty through every save and both batch passes. Cases the refresh
cannot decide (a boundary year, text naming a design the facts rule out) are
left unassigned without a message; the batch pass's report lists them.

## Receiving

The receipt dialog (`ReceiptPanel.jsx`) opens with an **Identify** section
above Arrived and Storage location, for a single item:

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
- **Confirm or correct fields** is disabled while Identify has unsaved edits
  (saying why), so the full editor never opens over values it cannot see.
  Identify is hidden while the editor is open, and reads the item again when
  it closes, so its `base` is what the editor saved.
- A coin whose years are a range shows its Year box disabled: one box cannot
  hold a range, and the full editor can.
- Nothing typed in Identify is carried to the next line: the facts are the
  piece's, unlike the arrival date and location.

## New item

`NewItemForm.jsx` is reordered so the facts come first:

1. Kind.
2. The Identify fields for the kind (a coin's Year keeps its range checkbox).
   For a note, **Printed at** follows the back plate.
3. What the facts fill, marked *suggested*: series, then the note's class,
   seal, signatures and Reserve Bank, or the coin's metal.
4. Country, then grading: strike type, grade, grade designation, grading
   service, certificate number; set form and variety where the kind has them.
5. The purchase line: title, seller's item id, pieces, item cost, shipping,
   status, description.

`series` joins the suggested fields for both kinds and is sent in
`suggested` when accepted. **Save and add another** focuses the first
Identify field that was cleared: the Year for a coin, the Serial number for a
note (whose series year, letter and denomination are kept). The title stays
required: it is what the seller called the piece, and is not made up from the
facts.

## Testing

- Backend: series in both suggestion endpoints (a single candidate, a
  boundary year, a needs-evidence design with and without its seal);
  `refresh_series` on create and PATCH (assigns; retracts its own guess when
  the year changes; leaves a person's and a `series_match` value; respects
  `held`); the batch pass skipping `held`.
- Frontend: the Identify fields per kind and in order; only changed fields
  sent, with `base`, before the receipt, and only for Receive; a refused
  PATCH posts no receipt; a failed receipt after a saved PATCH does not
  resend; Confirm or correct fields disabled while dirty; the new form's DOM
  order per kind; series suggested; add-another focus.
