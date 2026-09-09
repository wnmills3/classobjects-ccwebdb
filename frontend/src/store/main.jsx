import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { AuthProvider } from '../shared/auth'
import { CartProvider } from './cart'
import { ReferenceProvider } from '../shared/reference'
import StoreApp from './StoreApp'
import '../shared/shared.css'
import './styles.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <CartProvider>
          <ReferenceProvider>
            <StoreApp />
          </ReferenceProvider>
        </CartProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
