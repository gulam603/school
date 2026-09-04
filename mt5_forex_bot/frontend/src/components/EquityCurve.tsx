/** Lightweight inline SVG equity curve - no chart library needed for a line. */

export function EquityCurve({
  points, initialBalance,
}: {
  points: { time: string; equity: number }[];
  initialBalance: number;
}) {
  if (points.length < 2) return <div className="empty">Not enough data for an equity curve</div>;

  const width = 1000;
  const height = 220;
  const padding = { top: 12, right: 10, bottom: 18, left: 54 };
  const values = points.map((point) => point.equity);
  const min = Math.min(...values, initialBalance);
  const max = Math.max(...values, initialBalance);
  const span = max - min || 1;

  const x = (index: number) =>
    padding.left + (index / (points.length - 1)) * (width - padding.left - padding.right);
  const y = (value: number) =>
    padding.top + (1 - (value - min) / span) * (height - padding.top - padding.bottom);

  const line = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.equity).toFixed(1)}`).join(' ');
  const area = `${line} L${x(points.length - 1).toFixed(1)},${y(min)} L${x(0).toFixed(1)},${y(min)} Z`;
  const profitable = values[values.length - 1] >= initialBalance;
  const stroke = profitable ? '#22c55e' : '#f43f5e';

  const ticks = [min, min + span / 2, max];

  return (
    <svg className="equity-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>

      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)}
            stroke="rgba(30,39,64,0.8)" strokeWidth="1"
          />
          <text x={padding.left - 8} y={y(tick) + 3} fill="#626d8c" fontSize="10" textAnchor="end">
            {tick.toFixed(0)}
          </text>
        </g>
      ))}

      <line
        x1={padding.left} x2={width - padding.right}
        y1={y(initialBalance)} y2={y(initialBalance)}
        stroke="#7c5cff" strokeWidth="1" strokeDasharray="4 4" opacity="0.8"
      />

      <path d={area} fill="url(#equity-fill)" />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />

      <text x={padding.left} y={height - 4} fill="#626d8c" fontSize="10">{points[0].time.slice(0, 10)}</text>
      <text x={width - padding.right} y={height - 4} fill="#626d8c" fontSize="10" textAnchor="end">
        {points[points.length - 1].time.slice(0, 10)}
      </text>
    </svg>
  );
}
