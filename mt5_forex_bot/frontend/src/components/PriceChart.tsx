/**
 * Candlestick chart (TradingView Lightweight Charts) with the strategy's EMA
 * overlays, buy/sell markers from the trade history and price lines for the
 * open position's entry, stop loss and take profit.
 */

import { useEffect, useRef } from 'react';
import {
  ColorType, CrosshairMode, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time,
} from 'lightweight-charts';
import type { ChartPayload, Position, Trade } from '../lib/types';

interface Props {
  data: ChartPayload | null;
  positions: Position[];
  trades: Trade[];
}

export function PriceChart({ data, positions, trades }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const fastRef = useRef<ISeriesApi<'Line'> | null>(null);
  const slowRef = useRef<ISeriesApi<'Line'> | null>(null);
  const priceLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);

  // -- create once ---------------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return undefined;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#97a1c0',
        fontFamily: "'JetBrains Mono', 'SF Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 39, 64, 0.55)' },
        horzLines: { color: 'rgba(30, 39, 64, 0.55)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#7c5cff', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#7c5cff' },
        horzLine: { color: '#7c5cff', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#7c5cff' },
      },
      rightPriceScale: { borderColor: '#1e2740', scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: '#1e2740', timeVisible: true, secondsVisible: false, rightOffset: 6 },
      handleScroll: true,
      handleScale: true,
    });

    candleRef.current = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#f43f5e',
      borderUpColor: '#22c55e', borderDownColor: '#f43f5e',
      wickUpColor: '#22c55e', wickDownColor: '#f43f5e',
    });
    fastRef.current = chart.addLineSeries({ color: '#22d3ee', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    slowRef.current = chart.addLineSeries({ color: '#f59e0b', lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    chartRef.current = chart;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) chart.resize(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, []);

  // -- candles + EMAs ------------------------------------------------
  useEffect(() => {
    if (!data || !candleRef.current || !fastRef.current || !slowRef.current) return;
    candleRef.current.setData(
      data.candles.map((candle) => ({
        time: candle.time as Time,
        open: candle.open, high: candle.high, low: candle.low, close: candle.close,
      })),
    );
    fastRef.current.setData(data.ema_fast.map((point) => ({ time: point.time as Time, value: point.value })));
    slowRef.current.setData(data.ema_slow.map((point) => ({ time: point.time as Time, value: point.value })));
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  // -- trade markers -------------------------------------------------
  useEffect(() => {
    if (!candleRef.current || !data) return;
    const symbol = data.symbol;
    const markers: SeriesMarker<Time>[] = [];

    for (const trade of trades) {
      if (trade.symbol !== symbol || !trade.opened_at) continue;
      const openedAt = Math.floor(new Date(trade.opened_at).getTime() / 1000);
      markers.push({
        time: openedAt as Time,
        position: trade.direction === 'BUY' ? 'belowBar' : 'aboveBar',
        color: trade.direction === 'BUY' ? '#22c55e' : '#f43f5e',
        shape: trade.direction === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: `${trade.direction} ${trade.volume}`,
      });
      if (trade.closed_at) {
        markers.push({
          time: Math.floor(new Date(trade.closed_at).getTime() / 1000) as Time,
          position: trade.direction === 'BUY' ? 'aboveBar' : 'belowBar',
          color: trade.profit >= 0 ? '#4ade80' : '#fb7185',
          shape: 'circle',
          text: `${trade.profit >= 0 ? '+' : ''}${trade.profit.toFixed(2)}`,
        });
      }
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    candleRef.current.setMarkers(markers);
  }, [trades, data]);

  // -- entry / SL / TP lines of the open position --------------------
  useEffect(() => {
    const series = candleRef.current;
    if (!series || !data) return;
    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = [];

    const position = positions.find((row) => row.symbol === data.symbol);
    if (!position) return;

    const add = (price: number | null | undefined, color: string, title: string, style: LineStyle) => {
      if (!price) return;
      priceLinesRef.current.push(
        series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }),
      );
    };
    add(position.entry_price, '#a78bfa', `ENTRY ${position.direction}`, LineStyle.Solid);
    add(position.stop_loss, '#f43f5e', 'SL', LineStyle.Dashed);
    add(position.take_profit, '#22c55e', 'TP', LineStyle.Dashed);
  }, [positions, data]);

  return (
    <div className="chart-wrap">
      {data && (
        <div className="chart-legend">
          <div className="legend-row">
            <strong>{data.symbol}</strong>
            <span className="faint">{data.timeframe}</span>
            <span>{data.last_price?.toFixed(data.digits)}</span>
          </div>
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: '#22d3ee' }} />
            <span className="faint">EMA {data.ema_fast_period}</span>
            <span>{data.ema_fast.at(-1)?.value.toFixed(data.digits) ?? '--'}</span>
          </div>
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: '#f59e0b' }} />
            <span className="faint">EMA {data.ema_slow_period}</span>
            <span>{data.ema_slow.at(-1)?.value.toFixed(data.digits) ?? '--'}</span>
          </div>
        </div>
      )}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  );
}
