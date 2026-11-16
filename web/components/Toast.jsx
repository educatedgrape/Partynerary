'use client'

import { useEffect } from 'react'

export default function Toast({ toast, onDone }) {
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(onDone, 4200)
    return () => clearTimeout(t)
  }, [toast, onDone])
  if (!toast) return null
  const tone = toast.kind === 'error'
    ? 'border-danger/50 bg-danger/10 text-danger'
    : 'border-ok/50 bg-ok/10 text-ok'
  return (
    <div className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg border
                     px-4 py-2.5 text-sm font-medium backdrop-blur-xl
                     shadow-hud-lg ${tone}`}>
      {toast.text}
    </div>
  )
}
