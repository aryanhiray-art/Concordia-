// The club's shared icon language for "how it works" — one source so the
// logged-out landing (AuthGate) and the wizard's rules step can never drift
// apart again. 24x24, stroke 1.8, teal gradient stroke via per-instance defs
// (CSS can't cross-reference SVG paints).

export const HOW_ICONS = {
  // ballot dropping into a slotted box
  vote: (
    <>
      <rect x="9" y="3.5" width="6" height="9.5" rx="1.2" />
      <path d="M10.6 8.2l1.1 1.1 1.9-2.3" />
      <path d="M7 13H5.6A1.6 1.6 0 0 0 4 14.6v4.3a1.6 1.6 0 0 0 1.6 1.6h12.8a1.6 1.6 0 0 0 1.6-1.6v-4.3A1.6 1.6 0 0 0 18.4 13H17" />
    </>
  ),
  // a whole with one slice marked out — "only that slice ever trades"
  stake: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M12 12V3.8" />
      <path d="M12 12l5.9 5.7" />
    </>
  ),
  // shield with check — your account stays yours
  yours: (
    <>
      <path d="M12 3.6l6.4 2.5v5c0 4.4-2.7 7.3-6.4 8.7-3.7-1.4-6.4-4.3-6.4-8.7v-5z" />
      <path d="M9.4 11.9l1.8 1.8 3.4-3.8" />
    </>
  ),
  // switch — you're always in control (wizard's fourth card)
  control: (
    <>
      <rect x="3" y="8" width="18" height="8" rx="4" />
      <circle cx="16" cy="12" r="2.4" />
    </>
  ),
} as const;

/** 44px teal-glass chip with a gradient-stroked icon. `uid` must be unique on
 *  the page (it namespaces the SVG gradient id). */
export function HowChip({ icon, uid }: { icon: React.ReactNode; uid: string }) {
  return (
    <span className="hiw flex-none" aria-hidden>
      <svg viewBox="0 0 24 24">
        <defs>
          <linearGradient id={`hiwg-${uid}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#6EF0DC" />
            <stop offset="1" stopColor="#17B8A2" />
          </linearGradient>
        </defs>
        <g stroke={`url(#hiwg-${uid})`}>{icon}</g>
      </svg>
    </span>
  );
}
