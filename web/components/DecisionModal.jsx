'use client'

import { Panel } from './Panel'
import Pill from './Pill'

export default function DecisionModal({ state, onDecide, onConfirm }) {
  const synthesis = state?.synthesis
  if (!synthesis) return null

  const options = [synthesis.option1, synthesis.option2].filter(Boolean)
  const decided = state.decision != null

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="hud-strong w-full max-w-lg p-0">
        <Panel title="Authority - choose the route" step="5">
          <div className="space-y-3">
            {options.map((opt, i) => {
              const label = i === 0 ? 'option1' : 'option2'
              const selected = state.decision === label
              return (
                <button
                  key={label}
                  onClick={() => !decided && onDecide(label)}
                  disabled={decided}
                  className={`hud relative w-full p-3 text-left transition-all
                    ${selected
                      ? 'border-brand-500/50 bg-brand-500/10 shadow-glow'
                      : 'hover:border-brand-500/30 hover:bg-white/5'}
                    ${decided && !selected ? 'opacity-40' : ''}`}
                >
                  <div className="mb-1 flex items-center gap-2">
                    <Pill tone={selected ? 'ok' : 'muted'}>
                      {selected ? 'SELECTED' : label}
                    </Pill>
                    {opt.total != null && (
                      <span className="font-pixel text-[12px] text-warn">
                        {Number(opt.total).toFixed(2)}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-ink2">
                    {opt.summary || opt.description || JSON.stringify(opt)}
                  </p>
                  {opt.cost_ref && (
                    <div className="mt-1 font-pixel text-[11px] text-ink3">
                      cost_ref: {opt.cost_ref}
                    </div>
                  )}
                </button>
              )
            })}

            {decided && (
              <div className="pt-2 text-center">
                <button className="btn-primary" onClick={onConfirm}>
                  Confirm &amp; board
                </button>
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  )
}
