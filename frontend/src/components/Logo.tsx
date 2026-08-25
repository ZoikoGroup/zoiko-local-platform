// Single source of truth for the logo <img> markup - was previously
// hand-duplicated across 8 call sites in 6 files (Sidebar, AuthLayout x2,
// choose-plan, join/[roomName], pricing x2, status), so a future asset
// swap or accessibility fix had to be applied 8 times by hand instead of
// once here.
export default function Logo({ size = 36, className = "" }: { size?: number; className?: string }) {
  return (
    <img
      src="/logo-icon.svg"
      alt="Zoiko Local"
      width={size}
      height={size}
      className={`rounded-lg shrink-0 ${className}`}
      style={{ width: size, height: size }}
    />
  );
}
