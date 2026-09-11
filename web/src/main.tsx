import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './styles.css'

const container = document.getElementById('root')
if (!container) throw new Error('#root is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Lets the desktop shell (and any future mobile wrapper) tell "the bundle
// loaded and React mounted" apart from "the WebView is showing a blank page".
container.dataset.appReady = 'true'
