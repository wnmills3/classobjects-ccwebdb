import { Component } from 'react'

/**
 * The last stop for an error thrown while the shop renders.
 *
 * Without it React unmounts the whole tree and a shopper is left with a
 * blank page and no idea what to do; with it they are told to reload. A
 * class, because only a class component can catch a render error.
 */
export default class ErrorBoundary extends Component {
  /** Nothing has gone wrong yet. */
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  /** Any error thrown below switches to the message. */
  static getDerivedStateFromError() {
    return { failed: true }
  }

  /** Leave the error where a developer will look for it. */
  componentDidCatch(error, info) {
    console.error(error, info?.componentStack)
  }

  /** The shop, or the message once it has failed. */
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <main className="content" role="alert">
        <h1>Something went wrong</h1>
        <p>
          Please <a href={window.location.href}>reload the page</a>.
        </p>
      </main>
    )
  }
}
