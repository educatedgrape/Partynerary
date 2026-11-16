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
  content: ['./app/**/*.{js,jsx}', './components/**/*.{js,jsx}'],
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
