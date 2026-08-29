'use client'

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
        <g className="animate-pulseSoft">
          <Px x={2} y={0} w={12} h={1} fill="#fb7185" />
        </g>
      )}
    </svg>
  )
}
