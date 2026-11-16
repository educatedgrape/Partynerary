'use client'

import { useState } from 'react'
import { Panel } from './Panel'

export default function AddAgentModal({ open, onClose, onSubmit }) {
  const [name, setName] = useState('')
  const [budget, setBudget] = useState('')
  const [preferences, setPreferences] = useState('')
  const [icsText, setIcsText] = useState('')

  if (!open) return null

  const handleIcsUpload = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => setIcsText(ev.target.result)
    reader.readAsText(file)
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!name.trim()) return
    const data = { name: name.trim() }
    if (budget) data.budget = Number(budget)
    if (preferences.trim()) data.preferences = preferences.trim()
    if (icsText.trim()) data.ics_text = icsText.trim()
    onSubmit(data)
    setName('')
    setBudget('')
    setPreferences('')
    setIcsText('')
    onClose()
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="hud-strong w-full max-w-md p-0">
        <Panel title="Add agent" step="+">
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="hud-label mb-1 block">Name</label>
              <input
                className="field"
                type="text"
                placeholder="e.g. Marcus"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div>
              <label className="hud-label mb-1 block">Budget ceiling</label>
              <input
                className="field"
                type="number"
                placeholder="e.g. 800"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
            </div>
            <div>
              <label className="hud-label mb-1 block">Preferences</label>
              <textarea
                className="field min-h-[64px]"
                placeholder="e.g. beaches, temples, street food"
                value={preferences}
                onChange={(e) => setPreferences(e.target.value)}
              />
            </div>
            <div>
              <label className="hud-label mb-1 block">ICS calendar</label>
              <input
                className="field text-[11px]"
                type="file"
                accept=".ics,text/calendar"
                onChange={handleIcsUpload}
              />
              <textarea
                className="field mt-1.5 min-h-[60px] font-pixel text-[11px]"
                placeholder="...or paste ICS content here"
                value={icsText}
                onChange={(e) => setIcsText(e.target.value)}
              />
              {icsText && (
                <p className="mt-1 font-pixel text-[10px] text-ok">
                  Calendar loaded ({icsText.split('VEVENT').length - 1} events)
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" className="btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={!name.trim()}>
                + Agent
              </button>
            </div>
          </form>
        </Panel>
      </div>
    </div>
  )
}
