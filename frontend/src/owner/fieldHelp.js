/**
 * What a form field means, in the words the owner reads off the item.
 *
 * One place for every form's help text, keyed by the field's API name, so
 * the item editor, Receiving and the Friedberg lookup explain a field the
 * same way. `HelpScope` shows the entry for whichever field has focus; a
 * field opts in with `data-help="<key>"` on its label.
 *
 * Public numismatic fact only -- what is printed where on a note, and what
 * it means. Never a catalogue's numbering or a price guide's values, per
 * CLAUDE.md's reference-data rule.
 */
export const FIELD_HELP = {
  denomination: {
    title: 'Denomination',
    text: 'The face value printed on the note or coin -- $1, $5, a half dollar.',
  },
  note_type: {
    title: 'Note type',
    text:
      'The kind of note, printed across the top of the face: Federal Reserve Note, ' +
      'Silver Certificate, United States Note, and so on.',
  },
  seal_color: {
    title: 'Seal colour',
    text:
      'The colour of the Treasury seal, right of centre on the face: green on a ' +
      'Federal Reserve Note, blue on a Silver Certificate, red on a United States ' +
      'Note; brown, gold and yellow on some older and wartime issues.',
  },
  series_year: {
    title: 'Series year',
    text:
      'The year after the word SERIES on the face, near the signatures -- "SERIES ' +
      '1963 A" is 1963. It is the year the design was adopted, not the year the ' +
      'note was printed: notes of one series are often printed for years afterwards.',
  },
  series_letter: {
    title: 'Series letter',
    text:
      'The letter after the series year -- the A in "SERIES 1963 A". It marks a ' +
      'later printing of the same series, usually because a new Treasurer or ' +
      'Secretary signed it. Leave it blank when the series has no letter, as SERIES ' +
      '1995 does. It is not the letter in the seal: that is the Reserve Bank.',
  },
  signature_combination: {
    title: 'Signatures',
    text:
      'The two signatures printed on the face: the Treasurer of the United States on ' +
      'the left, the Secretary of the Treasury on the right. Each series letter ' +
      'usually has its own pair.',
  },
  fed_district: {
    title: 'Reserve Bank (district)',
    text:
      'The Federal Reserve Bank that issued the note: the letter in the black seal ' +
      'on the left of the face (A Boston to L San Francisco; B is New York). The ' +
      'same bank shows as the number printed four times around the face (2 for ' +
      'New York).',
  },
  serial_number: {
    title: 'Serial number',
    text:
      'The number printed twice on the face, with a letter before and after. On ' +
      'most Federal Reserve Notes the first letter is the Reserve Bank (B for New ' +
      'York); on $5 and higher from Series 1996 the first letter marks the series ' +
      'and the second the bank. A star in place of the last letter marks a ' +
      'replacement note.',
  },
  web_press: {
    title: 'Web press',
    text:
      "Printed on the Bureau of Engraving and Printing's experimental web press, " +
      'from a roll of paper, instead of the usual sheet-fed press. Only some $1 ' +
      'notes of series 1988-A, 1993 and 1995 were. The two printings have ' +
      'different Friedberg numbers. Leave it as not known unless you can tell.',
  },
  fr_number: {
    title: 'Friedberg number',
    text:
      'The Friedberg catalogue number for this type of note, such as 1901-B: a ' +
      'number for the type and, on a Federal Reserve Note, the Reserve Bank ' +
      'letter. Read it off the slab or holder or a reference, or copy it from a ' +
      'match in your own catalogue.',
  },
}
