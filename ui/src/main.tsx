import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import Terminal from './pages/Terminal'
import './base.css'

// The terminal on your own machine: the engine serves this page on 127.0.0.1 and everything runs locally.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Terminal />
  </StrictMode>,
)
