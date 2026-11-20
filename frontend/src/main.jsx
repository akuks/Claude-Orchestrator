import React from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './theme/aurora.css'
import App from './App.jsx'
import { ThemeModeProvider } from './theme/ThemeModeContext.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeModeProvider>
      <App />
    </ThemeModeProvider>
  </React.StrictMode>,
)
