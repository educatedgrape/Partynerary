'use client'

export default function Pill({ children, tone = 'muted', title }) {
  const tones = {
    muted: 'border-glass-edge bg-white/[0.03] text-ink2',
    ok: 'border-ok/40 bg-ok/10 text-ok',
    warn: 'border-warn/40 bg-warn/10 text-warn',
  }
  return (
    <span title={title}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1
                      font-pixel text-[11px] backdrop-blur-sm ${tones[tone]}`}>
      {children}
    </span>
  )
}
