import ModalDialog from '../../ModalDialog'
import ItemEditForm from './ItemEditForm'

/**
 * The edit form, opened over the results rather than below them.
 *
 * The shell -- opening, Escape, the parent holding what is open -- is
 * `ModalDialog`, whose docstring records why any of this is modal. This is
 * the pairing of that shell with the item form, kept as its own component so
 * the results table names what it opens rather than assembling it inline.
 */
export default function ItemEditDialog({ itemId, onSaved, onChanged, onClose }) {
  return (
    <ModalDialog label="Edit item" onClose={onClose}>
      <ItemEditForm
        itemId={itemId}
        onSaved={onSaved}
        onChanged={onChanged}
        onClose={onClose}
      />
    </ModalDialog>
  )
}
