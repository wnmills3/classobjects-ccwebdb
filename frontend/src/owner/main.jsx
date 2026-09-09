import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import OwnerApp from './OwnerApp'
import { AuthProvider } from '../shared/auth'
import { ReferenceProvider } from '../shared/reference'
import '../styles.css'

// basename, not a route prefix: every `to="/people"` in the console resolves
// under /owner, so no component needs to know where the console is mounted.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter basename="/owner">
      <AuthProvider>
        <ReferenceProvider>
          <OwnerApp />
        </ReferenceProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
