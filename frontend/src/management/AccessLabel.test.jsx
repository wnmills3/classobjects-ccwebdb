import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AccessLabel } from './AccessLabel'

describe('AccessLabel', () => {
  it('underlines the access key letter inside the word', () => {
    const { container } = render(<AccessLabel text="Title" accessKey="t" />)
    expect(container.querySelector('u')).toHaveTextContent('T')
    expect(container).toHaveTextContent('Title')
  })

  it('matches the letter case-insensitively, anywhere in the word', () => {
    render(<AccessLabel text="Description" accessKey="C" />)
    expect(screen.getByText('c').tagName).toBe('U')
  })

  it('renders the label as ONE element, not loose text around the letter', () => {
    // The callers put this straight inside `.field`, which is a four-column
    // CSS grid. As a bare fragment its three children each became a grid
    // item and the word was torn apart across the columns -- "T      i
    // tle". One element is the layout contract, not a detail.
    const { container } = render(<AccessLabel text="Title" accessKey="t" />)
    expect(container.childNodes).toHaveLength(1)
    expect(container.firstChild.nodeType).toBe(Node.ELEMENT_NODE)
    expect(container.firstChild).toHaveTextContent('Title')
  })

  it('is one element even when the letter is not in the label at all', () => {
    // This branch used to return a bare string, which is a single text node
    // and so looked right -- exactly why the bug hit only some labels.
    const { container } = render(<AccessLabel text="Title" accessKey="z" />)
    expect(container.childNodes).toHaveLength(1)
    expect(container.firstChild.nodeType).toBe(Node.ELEMENT_NODE)
    expect(container.firstChild).toHaveTextContent('Title')
    expect(container.querySelector('u')).toBeNull()
  })

  it('is the plain label for a control with no access key', () => {
    const { container } = render(<AccessLabel text="Seal" accessKey={null} />)
    expect(container.childNodes).toHaveLength(1)
    expect(container.firstChild).toHaveTextContent('Seal')
    expect(container.querySelector('u')).toBeNull()
  })
})
