import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './landing.css'
import { Landing } from './Landing.tsx'

// the dashboard used to live at /, so old links such as /#/tasks still open the right dashboard page
if (window.location.hash.startsWith('#/')) window.location.replace(`/dashboard/${window.location.hash}`)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
)
