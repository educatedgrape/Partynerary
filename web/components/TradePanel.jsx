'use client'

import { Panel } from './Panel'

export default function TradePanel({ state }) {
  const missed = state?.missed
  if (!missed || missed.length === 0) return null

  return (
    <Panel id="panel-trade" title="Trade-offs - missed destinations" step="!">
      <div className="space-y-2">
        {missed.map((m, i) => (
          <div key={i} className="hud p-3">
            <div className="flex items-center justify-between">
              <span className="font-pixel text-[12px] text-ink">
                {m.destination || m.route || `missed ${i}`}
              </span>
              {m.cost_ref && (
                <span className="font-pixel text-[11px] text-warn">
                  {m.cost_ref}
                </span>
              )}
            </div>
            {m.cheapest != null && (
              <div className="mt-1 font-pixel text-[11px] text-ink3">
                cheapest: {Number(m.cheapest).toFixed(2)}
              </div>
            )}
            {m.reason && (
              <p className="mt-1 text-[11px] text-danger">{m.reason}</p>
            )}
            {m.breached_by && (
              <p className="mt-1 text-[11px] text-ink3">
                ceiling breach: <span className="text-danger">{m.breached_by}</span>
              </p>
            )}
          </div>
        ))}
      </div>
    </Panel>
  )
}
