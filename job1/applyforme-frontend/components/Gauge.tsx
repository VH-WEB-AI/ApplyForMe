// The signature element: a cockpit-style arc gauge. Used anywhere a score
// (0-100) needs to read at a glance, the way an instrument panel dial does.

const SIZE = 120;
const STROKE = 10;
const RADIUS = (SIZE - STROKE) / 2;
const ARC_DEGREES = 270; // 3/4 sweep, like a real gauge
const CIRCUMFERENCE = 2 * Math.PI * RADIUS * (ARC_DEGREES / 360);

function colorForScore(score: number) {
  if (score >= 80) return "#5EEAD4"; // mint
  if (score >= 60) return "#FF8A3D"; // signal
  return "#FF6B5C"; // danger
}

export default function Gauge({
  score,
  label,
  sublabel,
}: {
  score: number;
  label: string;
  sublabel?: string;
}) {
  const clamped = Math.max(0, Math.min(100, score));
  const filled = (clamped / 100) * CIRCUMFERENCE;
  const color = colorForScore(clamped);
  const rotation = 135; // start angle so the 270deg arc opens at the bottom

  const ticks = Array.from({ length: 10 }, (_, i) => i);

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        <g transform={`rotate(${rotation} ${SIZE / 2} ${SIZE / 2})`}>
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke="#1D3E37"
            strokeWidth={STROKE}
            strokeDasharray={`${CIRCUMFERENCE} ${2 * Math.PI * RADIUS}`}
            strokeLinecap="round"
          />
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke={color}
            strokeWidth={STROKE}
            strokeDasharray={`${filled} ${2 * Math.PI * RADIUS}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 0.6s ease" }}
          />
          {ticks.map((t) => (
            <line
              key={t}
              x1={SIZE / 2}
              y1={STROKE / 2}
              x2={SIZE / 2}
              y2={STROKE / 2 + 4}
              stroke="#0A1412"
              strokeWidth={1}
              transform={`rotate(${t * (ARC_DEGREES / 9)} ${SIZE / 2} ${SIZE / 2})`}
            />
          ))}
        </g>
        <text
          x="50%"
          y="48%"
          textAnchor="middle"
          className="font-mono"
          fontSize="26"
          fontWeight={600}
          fill="#F1F5F3"
        >
          {clamped}
        </text>
        <text x="50%" y="62%" textAnchor="middle" className="font-mono" fontSize="10" fill="#8FA39C">
          / 100
        </text>
      </svg>
      <div className="text-center">
        <p className="font-display text-sm font-semibold leading-tight">{label}</p>
        {sublabel && <p className="text-xs text-muted mt-0.5">{sublabel}</p>}
      </div>
    </div>
  );
}
