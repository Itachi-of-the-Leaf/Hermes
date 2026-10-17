/** The Saarathi mark: a chariot wheel. Takes its colour from the surrounding text. */
export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round">
        <circle cx="32" cy="32" r="22" />
        <path d="M32 10v44M10 32h44M16.4 16.4l31.2 31.2M47.6 16.4L16.4 47.6" />
      </g>
      <circle cx="32" cy="32" r="7" fill="currentColor" />
      <circle cx="32" cy="32" r="2.8" fill="var(--logo-hub, #FCF1D0)" />
    </svg>
  )
}
