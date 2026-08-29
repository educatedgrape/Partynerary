# Design export - Partynerary "Aero-Precision" terminal

Reproduce this design **exactly**. It is a working, shipped visual system, not a
direction to interpret. Every file below is verbatim source: copy it, do not
paraphrase it, do not "modernise" it, do not substitute your own palette,
spacing or component names.

The target is **Next.js App Router with `output: 'export'`**, served from
`web/out` by the Python `ThreadingHTTPServer`. The source below is React 18 +
Vite. The **adaptation notes** in section 9 list every change the port requires -
they are the only permitted deviations.

## What the design is

A dark cockpit HUD. A side-on cutaway of an airport lounge fills the top of the
main column: pixel-art agents walk in from the left, sit on benches, and speak
in retro bubbles while an aircraft idles at the gate on the right. Everything
around it is glass panels on a near-black ground, mono caps labels, one cool sky
accent.

Three ideas carry it, and they are the ones to protect:

1. **Depth is a lit 1px edge, never a drop shadow.** On `#0f1418` a shadow is
   invisible. A panel separates from the page because its top edge catches light
   and its fill is one tonal step brighter.
2. **Every figure on screen is monospaced.** Flight numbers, fares, `cost_ref`s.
   `0` and `O` must never be confusable while a demo is being watched.
3. **The scene is the negotiation.** The lounge is not decoration around the
   product; the agents standing in it *are* the party, and their bubbles carry
   the dereferenced figures.

---

## 1. Fonts and document head

Both families load from Google Fonts. `JetBrains Mono` is `font-pixel`
(everything numeric and every label); `Geist` is `font-hud` (prose).

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark" />
    <meta name="theme-color" content="#0f1418" />
    <title>Partynerary — Terminal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap"
      rel="stylesheet"
    />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

---

## 2. Design tokens - `tailwind.config.js`

**This file is the design system.** Copy it byte for byte. The token *names* are
semantic (`canvas`, `surface`, `ink`, `line`) so no component ever needs to know
which theme it is in.

```js
/** @type {import('tailwindcss').Config} */

// Aero-Precision: the dark, glass-layered cockpit theme carried over from the
// Stitch design system (AeroPath Travel Dashboard).
//
// The token NAMES stay semantic - canvas, surface, ink, line - exactly as the
// light build had them, so a component never has to know which theme it is in.
// Only the values below move. `bg-surface2` means "recessed panel" in both
// builds; it just stopped being pale.
//
// Depth here is tonal layering plus a 1px lit edge, not a drop shadow. On a
// #0f1418 ground a shadow is invisible; what separates a panel from the page
// is that its top edge catches light and its fill is one step brighter.
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#0f1418',      // the page itself (also the "on vivid" text)
        surface: '#1b2024',     // panels
        surface2: '#171c20',    // recessed panels, form wells
        surface3: '#252b2e',    // pressed / selected
        surface4: '#303539',    // highest layer
        line: '#2c363d',        // borders
        line2: '#3e484f',       // emphasised borders

        ink: '#dee3e8',         // primary text
        ink2: '#bdc8d1',        // secondary text
        // Muted text sits at 10-11px in most places, so it still has to clear
        // WCAG AA - and not just on the page. #87929a passed on the canvas but
        // measured 4.44:1 on the tinted cards (a moved graph node, an agreed
        // date), which is where captions like a cost_ref actually sit. This
        // clears 4.5:1 on every tint in the palette.
        ink3: '#939ea6',        // muted text, captions

        // Vibrant Sky. 400-600 are all display-bright because every one of them
        // is used as a lit accent on a dark ground; 700 is the pressed state and
        // 100 is the "container" tint behind a selected card.
        brand: {
          700: '#0284c7', 600: '#56c7fa', 500: '#38bdf8',
          400: '#8ed5ff', 300: '#c4e7ff', 100: '#0b2b3a',
        },
        accent: {
          700: '#0891b2', 600: '#22d3ee', 500: '#67e8f9',
          400: '#a5f3fc', 100: '#0a2c33',
        },

        // Functional colours are reserved for flight/negotiation state and are
        // tuned to sit on the dark ground, not on white.
        warn: '#ffc176',
        danger: '#ffb4ab',
        ok: '#5fd39a',

        // Glass: the fill and the lit edge that make a panel read as a pane
        // rather than a rectangle.
        glass: 'rgba(255,255,255,0.03)',
        'glass-edge': 'rgba(255,255,255,0.10)',

        // Scene-only tones for the terminal cutaway, now a night terminal.
        sky: '#16222e',
        sun: '#f1a02b',
        floor: '#1a2126',
      },
      fontFamily: {
        // JetBrains Mono carries every figure on screen - flight numbers,
        // cost_refs, fares - so 0 and O can never be confused in a demo.
        pixel: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
        hud: ['"Geist"', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      boxShadow: {
        // Dark theme uses a lit top edge plus a deep diffuse pool underneath.
        hud: 'inset 0 1px 0 rgba(255,255,255,0.06), 0 8px 32px -12px rgba(0,0,0,0.7)',
        'hud-lg': 'inset 0 1px 0 rgba(255,255,255,0.09), 0 24px 64px -16px rgba(0,0,0,0.85)',
        lift: 'inset 0 1px 0 rgba(255,255,255,0.05), 0 2px 8px -2px rgba(0,0,0,0.6)',
        glow: '0 0 20px rgba(56,189,248,0.18)',
        'glow-lg': '0 0 32px rgba(56,189,248,0.28)',
      },
      keyframes: {
        walk: {
          '0%,100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-3px)' },
        },
        idle: {
          '0%,100%': { transform: 'translateY(0) scaleY(1)' },
          '50%': { transform: 'translateY(-1px) scaleY(1.015)' },
        },
        walkIn: {
          from: { transform: 'translateX(-140px)', opacity: '0' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
        bubblePop: {
          '0%': { transform: 'translateY(6px) scale(0.85)', opacity: '0' },
          '60%': { transform: 'translateY(-2px) scale(1.04)', opacity: '1' },
          '100%': { transform: 'translateY(0) scale(1)', opacity: '1' },
        },
        pulseSoft: {
          '0%,100%': { opacity: '0.45' },
          '50%': { opacity: '1' },
        },
        // The aircraft idles at the gate: a slow drift left and right, not a
        // one-way nudge. Deliberately small - it should read as weight, not as
        // a bouncing sticker.
        taxi: {
          '0%,100%': { transform: 'translateX(-7px)' },
          '50%': { transform: 'translateX(7px)' },
        },
        boardOff: {
          from: { transform: 'translateX(0)', opacity: '1' },
          to: { transform: 'translateX(420px)', opacity: '0' },
        },
        drift: {
          from: { transform: 'translateX(0)' },
          to: { transform: 'translateX(-60px)' },
        },
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(300%)' },
        },
      },
      animation: {
        walk: 'walk 0.42s steps(2, end) infinite',
        idle: 'idle 2.6s ease-in-out infinite',
        walkIn: 'walkIn 0.9s steps(8, end) both',
        bubblePop: 'bubblePop 0.28s ease-out both',
        pulseSoft: 'pulseSoft 2.4s ease-in-out infinite',
        taxi: 'taxi 6.5s ease-in-out infinite',
        boardOff: 'boardOff 1.6s ease-in both',
        drift: 'drift 40s linear infinite',
        sweep: 'sweep 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
```

---

## 3. Base layer and components - `index.css`

The `.hud`, `.btn-*`, `.field`, `.bubble` and `.accent-bar` classes below are
used by every component. Port them as-is.

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  html, body, #root { height: 100%; }
  body {
    @apply bg-canvas text-ink font-hud antialiased;
    /* One cool glow bleeding in from the top right, the way a HUD is lit by the
       instrument it belongs to. Everything else stays flat so the panels are
       what separate from the page. */
    background-image:
      radial-gradient(1200px 620px at 92% -10%, rgba(56,189,248,0.10), transparent 62%),
      radial-gradient(900px 480px at 4% 104%, rgba(34,211,238,0.06), transparent 60%);
    background-attachment: fixed;
  }
  /* Pixel art must never be smoothed - the whole look depends on hard edges. */
  .pixelated { image-rendering: pixelated; shape-rendering: crispEdges; }
}

@layer components {
  /* Glassmorphism. On a dark ground the pane is a near-transparent fill, a 1px
     lit edge, and a blur - the depth comes from the edge catching light, not
     from a shadow nothing can see. */
  .hud {
    @apply relative rounded-lg border border-glass-edge bg-glass
           backdrop-blur-xl shadow-hud;
  }
  .hud-strong {
    @apply relative rounded-lg border border-glass-edge bg-surface/85
           backdrop-blur-2xl shadow-hud-lg;
  }
  /* Section headers are mono caps - the "organised instrument panel" cue that
     does most of the work in this design system. */
  .hud-label {
    @apply font-pixel text-[11px] font-medium uppercase tracking-[0.1em] text-ink3;
  }
  .hud-divider { @apply h-px w-full bg-gradient-to-r from-transparent via-glass-edge to-transparent; }

  .btn {
    @apply inline-flex items-center justify-center gap-2 rounded px-4 py-2
           font-pixel text-[12px] font-medium uppercase tracking-[0.06em]
           transition-all duration-200
           disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none;
  }
  /* Vibrant sky on dark navy text, per the design system. White on #38bdf8
     fails contrast; the navy reads as a lit key rather than a smudge. */
  .btn-primary {
    @apply btn bg-gradient-to-br from-brand-400 to-brand-500 text-canvas shadow-glow
           hover:from-brand-300 hover:to-brand-400 hover:shadow-glow-lg;
  }
  .btn-glow {
    @apply btn bg-gradient-to-br from-accent-500 to-accent-600 text-canvas
           shadow-[0_0_20px_rgba(34,211,238,0.2)]
           hover:from-accent-400 hover:to-accent-500
           hover:shadow-[0_0_32px_rgba(34,211,238,0.3)];
  }
  /* Ghost = slate outline, per the design system. */
  .btn-ghost {
    @apply btn border border-glass-edge bg-white/[0.03] text-ink2 backdrop-blur-sm
           hover:border-brand-500/50 hover:bg-white/[0.07] hover:text-ink;
  }
  .btn-warn { @apply btn bg-warn text-canvas hover:brightness-110; }

  .field {
    @apply w-full rounded border border-glass-edge bg-black/25 px-3 py-2
           text-sm text-ink backdrop-blur-sm placeholder:text-ink3/70
           transition-all focus:border-brand-500 focus:bg-black/40
           focus:outline-none focus:shadow-glow;
  }
  .tag {
    @apply cursor-pointer select-none rounded border px-2 py-1 font-pixel
           text-[11px] font-medium uppercase tracking-wider transition-colors;
  }

  /* A card's state is read off the 4px accent bar down its left edge before
     any of its text is. */
  .accent-bar {
    @apply before:absolute before:inset-y-0 before:left-0 before:w-[3px]
           before:rounded-l before:content-[''];
  }

  /* Retro speech bubble. On dark it becomes a lit slab: a bright rim, a deep
     fill, and a glow in its own tone, so it still reads as a drawn object
     rather than a tooltip. */
  .bubble {
    @apply relative rounded border border-brand-400/70 bg-surface3/95 px-2 py-1
           font-pixel text-[11px] leading-tight text-ink backdrop-blur-md;
    box-shadow: 0 0 14px rgba(56,189,248,0.18), inset 0 1px 0 rgba(255,255,255,0.08);
  }
  .bubble::after {
    content: '';
    @apply absolute left-1/2 h-2 w-2 -translate-x-1/2 border-b border-r
           border-brand-400/70 bg-surface3;
    bottom: -5px;
    transform: translateX(-50%) rotate(45deg);
  }
  .bubble-veto {
    @apply border-danger/80;
    box-shadow: 0 0 14px rgba(255,180,171,0.2), inset 0 1px 0 rgba(255,255,255,0.08);
  }
  .bubble-veto::after { @apply border-danger/80; }
  .bubble-accept {
    @apply border-ok/80;
    box-shadow: 0 0 14px rgba(95,211,154,0.2), inset 0 1px 0 rgba(255,255,255,0.08);
  }
  .bubble-accept::after { @apply border-ok/80; }
}

/* Thin scrollbars. */
* { scrollbar-width: thin; scrollbar-color: #303539 transparent; }
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-thumb { background: #303539; border-radius: 9999px; }
*::-webkit-scrollbar-thumb:hover { background: #87929a; }
*::-webkit-scrollbar-track { background: transparent; }
```

---

## 4. The pixel avatars - `PixelChar.jsx`

SVG rects on a fixed 16x24 grid, **not** a sprite sheet - agents are created at
runtime so palettes must be generated per member. Side profile means **one** eye,
**one** arm, **one** leg; drawing two of anything makes the cutaway read as
front-facing. The palette is hashed from the member's name so an agent looks the
same across reloads without the backend storing a colour.

```jsx
/**
 * Side-profile pixel characters, drawn as SVG rects on a fixed 16x24 grid.
 *
 * SVG rather than sprite sheets: every agent is user-created at runtime, so
 * palettes have to be generated per member. A sheet would mean pre-baking every
 * possible colourway, and there is no fixed set of them.
 *
 * Side-profile means ONE eye, one visible arm and one visible leg. Drawing two
 * of anything is the fastest way to make a cutaway view read as front-facing.
 */

const P = 4 // logical pixel size

// Deterministic palette from the member's name, so an agent always looks the
// same across reloads and reconnects without the backend storing a colour.
const PALETTES = [
  { skin: '#f2c49b', hair: '#3f2a1d', top: '#2563eb', legs: '#1e293b' },
  { skin: '#c98d63', hair: '#141821', top: '#06b6d4', legs: '#0f172a' },
  { skin: '#8d5a3b', hair: '#2a1a12', top: '#f59e0b', legs: '#1e293b' },
  { skin: '#fadcc0', hair: '#a8582b', top: '#a855f7', legs: '#111827' },
  { skin: '#e8b48c', hair: '#6b7280', top: '#10b981', legs: '#1f2937' },
  { skin: '#7a4a2e', hair: '#0b0f18', top: '#ef4444', legs: '#111827' },
  { skin: '#f7d3b0', hair: '#facc15', top: '#3b82f6', legs: '#0f172a' },
  { skin: '#a86a44', hair: '#4c1d95', top: '#f472b6', legs: '#1e293b' },
]

export function paletteFor(name = '') {
  let hash = 0
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) | 0
  return PALETTES[Math.abs(hash) % PALETTES.length]
}

function Px({ x, y, w = 1, h = 1, fill }) {
  return <rect x={x * P} y={y * P} width={w * P} height={h * P} fill={fill} />
}

/**
 * @param pose   'walk' | 'idle' | 'sit'
 * @param facing 1 = walking right (into the lounge), -1 = facing left
 */
export default function PixelChar({
  name = '',
  pose = 'idle',
  facing = 1,
  frame = 0,
  scale = 1,
  vetoed = false,
}) {
  const c = paletteFor(name)
  const step = frame % 2 === 0

  // Legs differ per pose. Sitting folds them forward; walking swings one back.
  const legs =
    pose === 'sit' ? (
      <>
        <Px x={6} y={19} w={4} h={2} fill={c.legs} />
        <Px x={9} y={21} w={2} h={3} fill={c.legs} />
        <Px x={9} y={23} w={4} h={1} fill="#0b1220" />
      </>
    ) : pose === 'walk' ? (
      <>
        <Px x={6} y={19} w={2} h={4} fill={c.legs} />
        <Px x={step ? 9 : 5} y={19} w={2} h={4} fill={c.legs} />
        <Px x={step ? 9 : 4} y={23} w={3} h={1} fill="#0b1220" />
        <Px x={6} y={23} w={3} h={1} fill="#0b1220" />
      </>
    ) : (
      <>
        <Px x={6} y={19} w={2} h={4} fill={c.legs} />
        <Px x={8} y={19} w={2} h={4} fill={c.legs} />
        <Px x={5} y={23} w={3} h={1} fill="#0b1220" />
        <Px x={8} y={23} w={3} h={1} fill="#0b1220" />
      </>
    )

  const bodyY = pose === 'sit' ? 2 : 0
  const animation =
    pose === 'walk' ? 'animate-walk' : pose === 'idle' ? 'animate-idle' : ''

  return (
    <svg
      className={`pixelated ${animation}`}
      width={16 * P * scale}
      height={24 * P * scale}
      viewBox={`0 0 ${16 * P} ${24 * P}`}
      style={{ transform: `scaleX(${facing})` }}
      aria-label={`${name} pixel avatar`}
    >
      <g transform={`translate(0 ${bodyY * P})`}>
        {/* hair / head back */}
        <Px x={5} y={2} w={7} h={2} fill={c.hair} />
        <Px x={4} y={4} w={2} h={4} fill={c.hair} />
        {/* head */}
        <Px x={6} y={4} w={6} h={5} fill={c.skin} />
        {/* one eye - side profile */}
        <Px x={10} y={6} fill="#0b1220" />
        {/* nose nub, the thing that sells the profile */}
        <Px x={12} y={6} w={1} h={1} fill={c.skin} />
        {/* neck */}
        <Px x={7} y={9} w={3} h={1} fill={c.skin} />
        {/* torso */}
        <Px x={5} y={10} w={7} h={7} fill={c.top} />
        <Px x={5} y={10} w={7} h={1} fill="#ffffff22" />
        {/* single visible arm, swinging on the walk */}
        <Px
          x={pose === 'walk' && !step ? 11 : 10}
          y={pose === 'sit' ? 11 : 11}
          w={2}
          h={pose === 'sit' ? 4 : 5}
          fill={c.top}
        />
        <Px
          x={pose === 'walk' && !step ? 11 : 10}
          y={pose === 'sit' ? 15 : 16}
          w={2}
          h={1}
          fill={c.skin}
        />
        {/* hips */}
        <Px x={5} y={17} w={7} h={2} fill={c.legs} />
        {legs}
      </g>
      {vetoed && (
        <g className="animate-pulseGlow">
          <Px x={2} y={0} w={12} h={1} fill="#fb7185" />
        </g>
      )}
    </svg>
  )
}
```

---

## 5. The lounge scene - `Terminal.jsx`

The signature component. Absolute positioning against a fixed floor line at
`FLOOR = 74`px. Agents stand on it, the aircraft sits beyond it, and the whole
scene composes left-to-right so a spawning agent walks in from off-screen and
boarding carries them out to the right.

```jsx
/**
 * The side-profile cutaway: a terminal lounge seen edge-on, with the aircraft
 * waiting at the gate on the right.
 *
 * Layout is absolute positioning against a fixed floor line. Agents stand ON
 * that line; the plane sits beyond it. Everything is composed left-to-right so
 * a spawning agent can walk in from off-screen and the boarding animation can
 * carry them out to the right.
 */
import { useEffect, useRef, useState } from 'react'
import PixelChar from './PixelChar'
// Imported rather than dropped in public/: the demo is served by the Python
// API, which only exposes dist/assets, so the bundler has to emit it there.
import planeStill from './assets/plane-still.png'

const FLOOR = 74 // px from the bottom of the stage to the lounge floor

/* ------------------------------------------------------------------ plane */
// The source art arrived as a 640x640 GIF with an opaque white ground, which
// would have rendered as a white square on the night apron. `key_plane.py`
// keyed the pure-white background to alpha, cropped to the aircraft, and threw
// away the drop shadow - which was invisible on a dark ground but occupied the
// bottom quarter of the frame and left the aeroplane hovering. What is imported
// here is 507x175 of aircraft and nothing else, so the image bottom IS the
// undercarriage and no offset has to compensate for empty pixels.
//
// The fuselage survived the key because it is #F6F6F5 - a few levels off the
// pure white it sat on.
//
// Movement is CSS, not frames: a 9KB still that sways beats a 632KB animation.
const PLANE_W = 300               // rendered width; 507x175 -> 300x104

function Aircraft({ status }) {
  // status: 'waiting' | 'ready' | 'boarded'
  const ready = status === 'ready'
  const boarded = status === 'boarded'
  return (
    <div
      className={`absolute right-4 ${boarded ? 'animate-boardOff' : 'animate-taxi'}
                  motion-reduce:animate-none`}
      style={{ bottom: FLOOR - 4 }}
    >
      {/* Boarding glow, pooled on the apron under the aircraft. The old hull was
          drawn, so "ready" could light its own cabin windows; fixed artwork
          cannot change, so the state is told by the light around it instead. */}
      {ready && (
        <div className="pointer-events-none absolute -bottom-1 left-1/2 h-5 w-60
                        -translate-x-1/2 rounded-[50%] bg-accent-600/25 blur-md
                        animate-pulseSoft motion-reduce:animate-none" />
      )}
      <img
        src={planeStill}
        alt=""
        aria-hidden="true"
        draggable="false"
        className="relative block"
        style={{
          width: PLANE_W,
          height: 'auto',
          maxWidth: 'none',
          // drop-shadow follows the artwork's own alpha, so the contact shadow
          // traces the aircraft instead of boxing it. It also replaces the
          // baked-in shadow that was cropped out of the asset.
          filter: ready
            ? 'drop-shadow(0 0 16px rgba(34,211,238,0.45)) drop-shadow(0 10px 8px rgba(0,0,0,0.55))'
            : 'drop-shadow(0 10px 8px rgba(0,0,0,0.55))',
        }}
      />
    </div>
  )
}

/* -------------------------------------------------------------- furniture */
function Bench({ left }) {
  return (
    <div className="absolute" style={{ left, bottom: FLOOR - 2 }}>
      <svg width="150" height="42" viewBox="0 0 150 42" className="pixelated">
        <rect x="0" y="14" width="150" height="7" fill="#2c363d" />
        <rect x="0" y="12" width="150" height="3" fill="#38bdf8" opacity="0.55" />
        <rect x="6" y="21" width="6" height="20" fill="#232b31" />
        <rect x="138" y="21" width="6" height="20" fill="#232b31" />
        <rect x="70" y="21" width="6" height="20" fill="#232b31" />
      </svg>
    </div>
  )
}

function DepartureBoard({ date, destination, status }) {
  return (
    <div className="absolute left-6 top-6 w-56 overflow-hidden rounded
                    border border-brand-500/30 bg-black/70 p-2 font-pixel
                    text-[10px] shadow-[0_0_24px_-6px_rgba(56,189,248,0.5)]
                    backdrop-blur-sm">
      {/* Signage, not a panel: a lit board bolted to the wall of the lounge.
          It keeps its own frame so it never reads as another HUD card. */}
      <div className="mb-1 flex items-center justify-between tracking-[0.1em]
                      text-brand-400">
        <span>DEPARTURES</span>
        <span className="h-1.5 w-1.5 rounded-full bg-ok animate-pulseSoft" />
      </div>
      <div className="mb-1.5 h-px w-full bg-brand-500/40" />
      <div className="flex justify-between text-ink">
        <span>{destination || '— — —'}</span>
        <span className="text-brand-400">{date || '--/--'}</span>
      </div>
      <div className="mt-1 text-[9px] uppercase tracking-widest text-warn">
        {status}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- speech bubble */
function Bubble({ text, amount, tone, costRef, unresolved, voiced }) {
  const toneClass =
    tone === 'veto' || tone === 'withdraw' ? 'bubble-veto'
      : tone === 'accept' ? 'bubble-accept' : ''
  // The badge shows the value of ONE Atlas field, which is rarely the same
  // number as a per-person trip total. Naming the field stops the two reading
  // as a contradiction when they are simply different quantities.
  const field = costRef ? costRef.split('.').pop() : null
  return (
    <div className={`bubble ${toneClass} animate-bubblePop max-w-[210px]`}>
      {text}
      {/* Which lines a model wrote is visible on camera. The words may be
          generated; the move and the figure beside it never are. */}
      {voiced && (
        <span className="ml-1 rounded-sm border border-current px-1 text-[9px] opacity-60"
              title="wording by the member's voice model">
          voiced
        </span>
      )}
      {unresolved && (
        <span className="ml-1 rounded-sm bg-danger px-1 text-canvas">
          unresolved ref
        </span>
      )}
      {amount != null && (
        <span className="ml-1 whitespace-nowrap rounded-sm bg-warn px-1 text-canvas">
          {Number(amount).toFixed(2)}
          {field && <span className="ml-1 opacity-70">{field}</span>}
        </span>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------- agent */
function Agent({ member, index, total, bubble, vetoed, spawnedAt }) {
  const [frame, setFrame] = useState(0)
  const [arrived, setArrived] = useState(false)
  const walkedIn = useRef(false)

  // Walk in once, then settle. Re-running this on every render would leave
  // agents permanently jogging on the spot.
  useEffect(() => {
    if (walkedIn.current) return
    walkedIn.current = true
    const t = setTimeout(() => setArrived(true), 900)
    return () => clearTimeout(t)
  }, [spawnedAt])

  useEffect(() => {
    const id = setInterval(() => setFrame((f) => f + 1), 210)
    return () => clearInterval(id)
  }, [])

  // Spread agents across the lounge floor, leaving the gate end clear.
  const slot = total <= 1 ? 0.5 : index / Math.max(1, total - 1)
  const left = 120 + slot * 300

  const seated = arrived && index % 2 === 1

  return (
    <div
      className={`absolute flex flex-col items-center ${arrived ? '' : 'animate-walkIn'}`}
      style={{ left, bottom: seated ? FLOOR + 6 : FLOOR, zIndex: 20 + index }}
    >
      {bubble && (
        <div className="mb-2">
          <Bubble text={bubble.text} amount={bubble.amount} tone={bubble.move}
                  costRef={bubble.cost_ref} unresolved={bubble.unresolved}
                  voiced={bubble.voiced} />
        </div>
      )}
      <PixelChar
        name={member.member}
        pose={!arrived ? 'walk' : seated ? 'sit' : 'idle'}
        frame={frame}
        facing={1}
        scale={0.62}
        vetoed={vetoed}
      />
      <div className="mt-0.5 rounded border border-glass-edge bg-black/60 px-1.5
                      font-pixel text-[9px] tracking-wide backdrop-blur-sm">
        <span className="text-ink">{member.member}</span>
        <span className="ml-1 text-brand-400">{member.ceiling}</span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- stage */
export default function Terminal({ state, bubbleFor, planeStatus }) {
  const members = state.members || []
  return (
    <div className="hud relative h-[420px] overflow-hidden">
      {/* night beyond the glass */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#0a1119]
                      via-[#0f1720] to-[#141b21]" />
      {/* apron floodlight low on the horizon */}
      <div className="pointer-events-none absolute right-24 top-16 h-40 w-56
                      rounded-full bg-sun opacity-[0.14] blur-3xl" />
      {/* window mullions - the wall is glass, so the frames are what you see */}
      <div className="absolute inset-x-0 top-10 h-32">
        <div className="flex h-full gap-3 px-4">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="flex-1 rounded-sm border border-white/10
                                    bg-gradient-to-b from-brand-500/[0.07]
                                    to-transparent" />
          ))}
        </div>
      </div>
      {/* cloud drifting past the glass, backlit rather than white */}
      <div className="pointer-events-none absolute left-0 top-16 flex gap-24
                      opacity-40 animate-drift">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-6 w-24 rounded-full bg-white/10 blur-[3px]" />
        ))}
      </div>

      <DepartureBoard
        date={state.date?.agreed}
        destination={state.chosen_graph?.destination_name}
        status={planeStatus === 'boarded' ? 'BOARDED'
          : planeStatus === 'ready' ? 'NOW BOARDING' : 'AWAITING CONSENSUS'}
      />

      {/* floor */}
      <div className="absolute inset-x-0 border-t border-glass-edge bg-floor"
           style={{ bottom: 0, height: FLOOR }} />
      {/* tile seams - what makes the floor read as receding rather than flat */}
      <div className="absolute inset-x-0 flex" style={{ bottom: 0, height: FLOOR }}>
        {Array.from({ length: 22 }).map((_, i) => (
          <div key={i} className="h-full flex-1 border-r border-white/[0.04]" />
        ))}
      </div>
      <div className="absolute inset-x-0" style={{ bottom: FLOOR - 2 }}>
        <div className="h-0.5 w-full bg-brand-500
                        shadow-[0_0_12px_rgba(56,189,248,0.55)]" />
      </div>

      <Bench left={140} />
      <Bench left={330} />

      <Aircraft status={planeStatus} />

      {members.map((m, i) => (
        <Agent
          key={m.member}
          member={m}
          index={i}
          total={members.length}
          bubble={bubbleFor(m.member)}
          vetoed={(state.impact?.breached || []).includes(m.member)}
          spawnedAt={m.member}
        />
      ))}

      {members.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="hud-strong px-6 py-4 text-center">
            <div className="hud-label mb-1">Terminal empty</div>
            <p className="text-sm text-ink2">
              No agents yet. Create one and they will walk in.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
```

**Asset:** `web/src/assets/plane-still.png` - 507x175, rendered at 300px wide.
Copy the file across unchanged. It is pre-keyed: the original had an opaque white
ground and a baked drop shadow that left the aircraft hovering on a dark apron.
The white was keyed to alpha and the shadow cropped, so **the image bottom is the
undercarriage** and no offset compensates for empty pixels. Motion is CSS
(`animate-taxi`), not frames - a 9KB still that sways beats a 632KB GIF.

---

## 6. Chrome components

### Panel - the wrapper for every card

```jsx
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
```

### Pill - status chip

Mono, bordered, no fill unless it is carrying a warning.

```jsx
function Pill({ children, tone = 'muted', title }) {
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
```

### Rail - the stage navigation

Not decorative. Every row is a real pipeline stage, lit from `state.stage`, and
anchors to the panel that owns it, so a watcher sees where the run is without
being told.

```jsx
const STAGES = [
  { id: 'feed', label: 'Discover', icon: '◎' },
  { id: 'date', label: 'Date consensus', icon: '◷' },
  { id: 'scout', label: 'Scout shortlist', icon: '❖' },
  { id: 'inventory', label: 'Atlas inventory', icon: '⌖' },
  { id: 'graph', label: 'Itinerary graph', icon: '⬡' },
  { id: 'authority', label: 'Authority', icon: '⚿' },
  { id: 'receipt', label: 'Receipt', icon: '≡' },
]

function Rail({ state }) {
  const at = stageIndex(state)
  return (
    <nav className="flex flex-col gap-1">
      {STAGES.map((s, i) => {
        const active = i === at
        const done = i < at
        return (
          <a key={s.id} href={`#panel-${s.id}`}
             className={`group flex items-center gap-3 rounded px-3 py-2
                         transition-all duration-200
               ${active
                 ? 'border border-brand-500/30 bg-brand-500/10 text-brand-400 shadow-glow'
                 : 'border border-transparent text-ink3 hover:bg-white/5 hover:text-ink'}`}>
            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-sm
                              font-pixel text-[11px]
              ${active ? 'text-brand-400'
                       : done ? 'text-ok' : 'text-ink3/70'}`}>
              {done ? '✓' : s.icon}
            </span>
            <span className="font-pixel text-[11px] uppercase tracking-[0.1em]">
              {s.label}
            </span>
            {active && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-400
                               animate-pulseSoft" />
            )}
          </a>
        )
      })}
    </nav>
  )
}
```

### Toast

```jsx
function Toast({ toast, onDone }) {
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
```

---

## 7. Application shell

Fixed 264px sidebar on `lg+`, sticky 64px top bar, then a two-column grid whose
right rail is a fixed 380px.

```jsx
<div className="flex min-w-0 flex-1 flex-col lg:ml-[264px]">
  <header className="sticky top-0 z-30 flex h-16 items-center gap-3
                     border-b border-glass-edge bg-canvas/70 px-4
                     backdrop-blur-2xl lg:px-6">
    ...
  </header>

  <main className="mx-auto w-full max-w-[1400px] p-4 lg:p-6">
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">

      <div className="space-y-4">   {/* left column */}
        <Terminal />                {/* the lounge, h-[420px] */}
        <div className="hud p-3">   {/* "Flight deck" button row */}
        <Panel id="panel-feed"      title="Atlas proposes - before anybody has agreed anything" step="0" />
        <Panel                      title="Change - Propagate - Re-plan" step="8" />
        <div className="grid gap-4 md:grid-cols-2">
          <Panel id="panel-graph"     title="The itinerary graph" step="4" />
          <Panel id="panel-inventory" title="Atlas proposes - constraints filter" step="2" />
        </div>
      </div>

      <div className="space-y-4">   {/* right rail, 380px */}
        <Panel                      title="Passengers" accent="N checked in" />
        <Panel id="panel-scout"     title="Scout - interests to places" step="1.5" />
        <Panel id="panel-date"      title="Date consensus" step="1" />
        <Panel id="panel-authority" title="Authority" />
        <Panel                      title="Move the departure" step="8" />
        <Panel id="panel-receipt"   title="Receipt" step="7" />
      </div>

    </div>
  </main>
</div>
```

Below `lg` the sidebar disappears, so the provenance pill, Reset and + Agent move
into the top bar. **The provenance badge is a claim made on camera and is never
the thing dropped to make a narrow layout fit.**

---

## 8. Copy voice

The wording is part of the design. It is plain, slightly wry, and never markets.

- Buttons: `Ask Atlas where we can go`, `Confirm & board`, `Next round`,
  `Run to agreement`.
- Empty terminal: *"No agents yet. Create one and they will walk in."*
- Feed: *"The terminal is empty, and these flights are here anyway."*
- Failed consensus: *"No date works - Marcus, Ana hit their limit and withdrew.
  The group is told, not sold."*
- Propagation: *"Not 'the flight got dearer' - the change is walked through the
  dependency graph to find who it breaks."*

Section titles use a middle-dot separator and stay lowercase after it:
*"Atlas proposes - constraints filter"*, *"Scout - interests to places"*.

---

## 9. Next.js adaptation notes - the only permitted deviations

1. **Tailwind v3.** The config in section 2 is v3 syntax. Install
   `tailwindcss@^3.4.17`, `postcss`, `autoprefixer`. Do **not** take Tailwind v4
   with its CSS-first config - the `@layer components` block and the `extend`
   theme will not port cleanly and the design will drift.
2. **`content`** becomes
   `['./app/**/*.{js,jsx,ts,tsx}', './components/**/*.{js,jsx,ts,tsx}']`.
3. **Fonts.** Keep the Google Fonts `<link>` in `app/layout.jsx`, or use
   `next/font/google` for `Geist` and `JetBrains_Mono` bound to CSS variables the
   config reads. Either is acceptable; the rendered result must be identical.
4. **`'use client'`** at the top of every component that holds state or polls -
   `Terminal`, the App shell, every modal. There is no server-side data fetching:
   the Python API is not running at build time.
5. **The plane image.** `next/image` is disabled under static export
   (`images: { unoptimized: true }`). Use a plain `<img>` exactly as the source
   does, importing the asset so the bundler emits it into the served output.
6. **Anchor navigation.** The Rail uses `href="#panel-x"` with `scroll-mt-20`.
   Keep plain `<a>`; do not convert to `next/link`, which intercepts the hash.
7. **Output directory.** `output: 'export'` writes `web/out`. Point
   `src/ui/dashboard.py` at it - three places currently reference `web/dist`.
8. **`motion-reduce:animate-none`** is on every looping animation in the source.
   Preserve it on all of them.

---

## 10. Acceptance

Screenshot the running app beside the original. These must match, not
approximate:

- Page ground `#0f1418` with two radial glows - sky top-right, cyan bottom-left -
  and `background-attachment: fixed`.
- Panels: translucent white fill, 1px lit edge, `backdrop-blur-xl`. **No drop
  shadow doing the separating.**
- Lounge 420px tall: 12 window mullions, 22 floor tile seams, a `#38bdf8` floor
  line with a 12px glow, benches at `left: 140` and `left: 330`.
- Agents: 16x24 pixel grid at `scale={0.62}`, walking in over 900ms, odd indices
  seated, name plate showing name and ceiling.
- Aircraft: 300px wide, `animate-taxi` sway, cyan drop-shadow glow when ready.
- Every number in a bubble, card or receipt rendered in JetBrains Mono.
