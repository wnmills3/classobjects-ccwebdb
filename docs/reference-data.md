# Reference data: what may be shipped

*2026-10-02*

The catalog is sold, so reference data shipped inside the product is
redistributed with it. Before any retrieved data goes into the repository, the
seed files under `backend/data/reference/`, or the database, establish that it
is free to use. This is enforced, not advisory.

## Fact versus arrangement

The line is between a **fact** and a publisher's **arrangement** of facts.

| Safe to ship | Not safe to ship |
|---|---|
| Who held an office and when (Treasurer, Secretary of the Treasury) | **Friedberg** numbering, the arrangement published in *Paper Money of the United States* |
| Design series names and their year spans | **Pick** numbering for world notes |
| Mint specifications and legislated compositions | A vendor's price-guide values |
| Collector nicknames in common use | Any catalog's mapping of attributes to its own numbers |

That a note carries a given pair of signatures is a fact. That the same note
has a particular catalog number is one publisher's numbering scheme.

## What follows from it

- **A catalog number is recorded, never shipped.** The `friedberg_number`
  table models the concept and ships empty. A number is entered per note by
  the owner, from the note's holder or the owner's own copy of the catalog,
  exactly as a certification number is. Rows recorded that way are reused by
  later lookups.
- **The software never fetches, parses or stores search results.** The console
  can build the text of a web search for a note; what the owner reads there
  and types in is theirs to record.
- **The format of a number is not the catalog.** Normalising how a typed
  number is written (`app/fr_format.py`,
  `frontend/src/management/friedberg-format.js`) restates no mapping.
- **Tests use synthetic numbers.** Friedberg numbers in tests and fixtures are
  in the 9900s, past any real number.
- **A seed file says where its figures came from.** Each file under
  `backend/data/reference/` carries a `_comment` naming the source.
- **A garbled retrieval is not a source.** Partial and correct beats complete
  and invented; a vocabulary is seeded with what is known and extended by hand.
