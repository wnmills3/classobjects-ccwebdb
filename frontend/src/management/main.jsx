import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import ManagementApp from './ManagementApp'
import { AuthProvider } from '../shared/auth'
import { ReferenceProvider } from '../shared/reference'
import '../shared/shared.css'
import './styles.css'

// basename, not a route prefix: every `to="/people"` in the console resolves
// under /management, so no component needs to know where the console is mounted.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter basename="/management">
      <AuthProvider>
        <ReferenceProvider>
          <ManagementApp />
        </ReferenceProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
