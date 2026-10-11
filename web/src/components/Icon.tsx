export default function Icon({
  name,
  size = 20,
}: {
  name: string;
  size?: number;
}) {
  const paths: Record<string, string> = {
    fleet: "M3 8h18M3 16h18M8 3v18M16 3v18",
    aircraft:
      "m12 3 2 7 7 4v2l-7-2-1 5 3 2v1l-4-1-4 1v-1l3-2-1-5-7 2v-2l7-4 2-7Z",
    planning: "M5 4v16M11 4v16M17 4v16M3 8h8M9 14h10M15 19h6",
    lab: "M9 3h6M10 3v7l-6 9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2l-6-9V3M7 15h10",
    agents: "M12 3v3M5 8h14v12H5ZM8 12h1M15 12h1M9 16h6M2 11v5M22 11v5",
    pulse: "M2 12h5l3-7 4 14 3-7h5",
    spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Zm7 11 .9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9L19 14Z",
    arrow: "M7 17 17 7M7 7h10v10",
    search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.pulse} />
    </svg>
  );
}
