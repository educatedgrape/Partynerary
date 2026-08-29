'use client'

export function Panel({ title, step, children, accent, id, className = '' }) {
  return (
    <section id={id} className={`hud scroll-mt-20 p-4 ${className}`}>
      <header className="mb-3 flex items-center gap-2">
        {step != null && (
          <span className="grid h-5 w-5 place-items-center rounded-sm border
                           border-brand-500/40 bg-brand-100 font-pixel text-[10px]
                           font-medium text-brand-400">
            {step}
          </span>
        )}
        <h3 className="hud-label">{title}</h3>
        {accent && (
          <span className="ml-auto font-pixel text-[11px] text-ink3">{accent}</span>
        )}
      </header>
      {children}
    </section>
  )
}
