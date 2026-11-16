'use client'

import { useEffect, useRef } from 'react'
import { Panel } from './Panel'

export default function StatusStream({ logTail }) {
  const scrollRef = useRef(null)
  const entries = Array.isArray(logTail) ? logTail : []

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries.length])

  return (
    <Panel title="Status stream" accent={`${entries.length} entries`}>
      <div
        ref={scrollRef}
        className="max-h-[200px] overflow-y-auto font-pixel text-[11px] leading-relaxed"
      >
        {entries.length === 0 && (
          <div className="py-3 text-center text-ink3">Awaiting activity...</div>
        )}
        {entries.map((entry, i) => (
          <div key={i} className="flex gap-2 border-b border-glass-edge py-1 last:border-0">
            <span className="shrink-0 text-ink3/50">{String(i).padStart(3, '0')}</span>
            <span className="text-ink2">{typeof entry === 'string' ? entry : JSON.stringify(entry)}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}
