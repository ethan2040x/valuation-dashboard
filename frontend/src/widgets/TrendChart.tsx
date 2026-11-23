import React, { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import type {
  IndicatorMeta,
  RangeKey,
  SeriesPayload,
  SeriesPoint,
} from "../types/valuation";
import { formatSourceLabel, formatValue } from "../utils/format";
import { FormulaExplainer } from "./FormulaExplainer";

interface TrendChartProps {
  indicator: IndicatorMeta;
  seriesPayload: SeriesPayload | null;
  range: RangeKey;
  loading: boolean;
  instrumentKind?: "index" | "equity";
  onRangeChange: (range: RangeKey) => void;
}

const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: "1y", label: "1 年" },
  { key: "5y", label: "5 年" },
  { key: "10y", label: "10 年" },
  { key: "20y", label: "20 年" },
  { key: "all", label: "全部" },
];

const SOURCE_STYLE: Record<
  string,
  { color: string; lineType: "solid" | "dashed" | "dotted"; width: number }
> = {
  computed: { color: "#2563eb", lineType: "solid", width: 2.5 },
  "qqq+yfinance": { color: "#2563eb", lineType: "solid", width: 2.5 },
  "spy+yfinance": { color: "#2563eb", lineType: "solid", width: 2.5 },
  "equity+yfinance": { color: "#2563eb", lineType: "solid", width: 2.5 },
  siblis_free: { color: "#0284c7", lineType: "solid", width: 2 },
  multpl: { color: "#0891b2", lineType: "solid", width: 2 },
  shiller: { color: "#0d9488", lineType: "solid", width: 2 },
  sec_edgar: { color: "#0284c7", lineType: "solid", width: 2 },
  yfinance_history: { color: "#0891b2", lineType: "solid", width: 2 },
  csv_import: { color: "#0f766e", lineType: "dashed", width: 2 },
  gurufocus_csv: { color: "#7c3aed", lineType: "dashed", width: 2 },
  macromicro_csv: { color: "#9333ea", lineType: "dashed", width: 2 },
  bloomberg_csv: { color: "#b45309", lineType: "dashed", width: 2 },
  wind_csv: { color: "#be185d", lineType: "dashed", width: 2 },
  siblis_paid: { color: "#c026d3", lineType: "dashed", width: 2 },
  seed_csv: { color: "#64748b", lineType: "dashed", width: 1.8 },
};

function getSourceStyle(source: string) {
  return (
    SOURCE_STYLE[source] ?? {
      color: "#7c3aed",
      lineType: "dotted" as const,
      width: 2,
    }
  );
}

export const TrendChart: React.FC<TrendChartProps> = ({
  indicator,
  seriesPayload,
  range,
  loading,
  instrumentKind = "index",
  onRangeChange,
}) => {
  const chartRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  const chartModel = useMemo(() => {
    if (!seriesPayload || seriesPayload.series.length === 0) {
      return null;
    }

    const points: SeriesPoint[] = seriesPayload.series;
    const dates = points.map((p) => p.date);
    const pointByDate = new Map(points.map((p) => [p.date, p]));
    const distinctSources = Array.from(
      new Set(points.map((p) => p.source || "unknown"))
    );

    // Split into per-source series so computed vs CSV-imported data use distinct line styles.
    // Bridge adjacent transitions by repeating the boundary point in the incoming source series.
    const seriesList: echarts.SeriesOption[] = distinctSources.map((src, sIdx) => {
      const style = getSourceStyle(src);
      const data: Array<number | null> = points.map((pt, idx) => {
        if (pt.source === src) return pt.value;
        // Connect transition cleanly when the next point switches into this source
        if (idx + 1 < points.length && points[idx + 1].source === src) {
          return pt.value;
        }
        return null;
      });

      const isPrimaryOverlayHolder = sIdx === 0;
      const markLineData: Array<Record<string, unknown>> = [];
      const markAreaData: Array<[Record<string, unknown>, Record<string, unknown>]> = [];

      if (isPrimaryOverlayHolder && seriesPayload.stats.mean !== null) {
        for (const ref of indicator.display.reference_lines) {
          if (ref.kind === "mean") {
            markLineData.push({
              yAxis: seriesPayload.stats.mean,
              name: ref.label || "均值",
              lineStyle: { color: "#f59e0b", type: "dashed", width: 1.5 },
              label: {
                formatter: `${ref.label || "均值"} ${formatValue(seriesPayload.stats.mean, indicator.display)}`,
                position: "insideEndTop",
                color: "#b45309",
                fontSize: 11,
                backgroundColor: "rgba(255,255,255,0.85)",
                padding: [2, 4],
                borderRadius: 3,
              },
            });
          } else if (
            ref.kind === "std_band" &&
            seriesPayload.stats.std !== null &&
            seriesPayload.stats.std > 0
          ) {
            const sigma = ref.sigma ?? 1;
            const upper = seriesPayload.stats.mean + sigma * seriesPayload.stats.std;
            const lower = seriesPayload.stats.mean - sigma * seriesPayload.stats.std;
            markAreaData.push([
              {
                name: ref.label || `±${sigma}σ`,
                yAxis: lower,
                itemStyle: { color: "rgba(37, 99, 235, 0.08)" },
              },
              { yAxis: upper },
            ]);
          } else if (ref.kind === "fixed" && ref.value !== null && ref.value !== undefined) {
            markLineData.push({
              yAxis: ref.value,
              name: ref.label,
              lineStyle: { color: "#94a3b8", type: "dotted", width: 1.2 },
              label: {
                formatter: `${ref.label} ${formatValue(ref.value, indicator.display)}`,
                position: "insideEndTop",
                color: "#475569",
                fontSize: 11,
              },
            });
          }
        }
      }

      return {
        name: formatSourceLabel(src),
        type: "line",
        data,
        showSymbol: points.length <= 25,
        symbolSize: 5,
        smooth: false,
        connectNulls: false,
        lineStyle: {
          color: style.color,
          width: style.width,
          type: style.lineType,
        },
        itemStyle: { color: style.color },
        markLine:
          markLineData.length > 0
            ? { silent: true, symbol: ["none", "none"], data: markLineData }
            : undefined,
        markArea:
          markAreaData.length > 0
            ? {
                silent: true,
                label: {
                  show: true,
                  position: "insideTopLeft",
                  color: "#3b82f6",
                  fontSize: 11,
                },
                data: markAreaData,
              }
            : undefined,
      };
    });

    return { dates, seriesList, pointByDate, distinctSources };
  }, [indicator, seriesPayload]);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    const chart = instanceRef.current;

    if (!chartModel || !seriesPayload) {
      chart.clear();
      return;
    }

    const option: echarts.EChartsOption = {
      animation: true,
      animationDuration: 300,
      grid: { top: 44, left: 56, right: 24, bottom: 38 },
      legend: {
        top: 6,
        right: 12,
        textStyle: { color: "#475569", fontSize: 12 },
        itemWidth: 22,
        itemHeight: 10,
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(15, 23, 42, 0.94)",
        borderColor: "#334155",
        textStyle: { color: "#f8fafc", fontSize: 12 },
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const first = items[0] as { axisValue?: string } | undefined;
          const dt = first?.axisValue;
          if (!dt) return "";
          const pt = chartModel.pointByDate.get(dt);
          if (!pt) return dt;
          const incLine = pt.is_incomplete
            ? `<div style="color:#fbbf24;margin-top:4px;">⚠ 数据不完整（缺数据权重 &gt; 5%）</div>`
            : "";
          return [
            `<div style="font-weight:600;margin-bottom:4px;">${dt}</div>`,
            `<div>${indicator.name}：<strong>${formatValue(pt.value, indicator.display)}</strong></div>`,
            `<div style="color:#cbd5e1;margin-top:2px;">来源：${formatSourceLabel(pt.source)}</div>`,
            incLine,
          ].join("");
        },
      },
      xAxis: {
        type: "category",
        data: chartModel.dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#cbd5e1" } },
        axisLabel: { color: "#64748b", fontSize: 11 },
      },
      yAxis: {
        type: "value",
        scale: true,
        splitLine: { lineStyle: { color: "#f1f5f9" } },
        axisLabel: {
          color: "#64748b",
          fontSize: 11,
          formatter: (val: number) => `${val}${indicator.display.suffix}`,
        },
      },
      series: chartModel.seriesList,
    };

    chart.setOption(option, true);

    const handleResize = () => chart.resize();
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, [chartModel, indicator, seriesPayload]);

  useEffect(() => {
    return () => {
      instanceRef.current?.dispose();
      instanceRef.current = null;
    };
  }, []);

  const stats = seriesPayload?.stats;

  return (
    <section className="panel" aria-label="估值走势图">
      <div className="panel__header">
        <div>
          <h2 className="panel__title">
            {indicator.name} 历史走势
            <span className="panel__subtitle">{indicator.description}</span>
          </h2>
        </div>
        <div className="range-tabs" role="tablist" aria-label="时间范围">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              role="tab"
              aria-selected={range === opt.key}
              className={`range-tab ${range === opt.key ? "range-tab--active" : ""}`}
              onClick={() => onRangeChange(opt.key)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <FormulaExplainer
        formula={
          instrumentKind === "equity"
            ? (indicator.equity_formula ?? indicator.formula)
            : indicator.formula
        }
        defaultOpen={false}
      />

      {stats && stats.count > 0 && (
        <div className="chart-stats-bar">
          <div className="stat-chip">
            <span className="stat-chip__label">区间均值</span>
            <span className="stat-chip__value">
              {formatValue(stats.mean, indicator.display)}
            </span>
          </div>
          <div className="stat-chip">
            <span className="stat-chip__label">±1σ 区间</span>
            <span className="stat-chip__value">
              {formatValue(stats.minus_1std, indicator.display)} ~{" "}
              {formatValue(stats.plus_1std, indicator.display)}
            </span>
          </div>
          <div className="stat-chip">
            <span className="stat-chip__label">中位数</span>
            <span className="stat-chip__value">
              {formatValue(stats.median, indicator.display)}
            </span>
          </div>
          <div className="stat-chip">
            <span className="stat-chip__label">最低 / 最高</span>
            <span className="stat-chip__value">
              {formatValue(stats.min, indicator.display)} /{" "}
              {formatValue(stats.max, indicator.display)}
            </span>
          </div>
          <div className="stat-chip">
            <span className="stat-chip__label">样本交易日</span>
            <span className="stat-chip__value">{stats.count}</span>
          </div>
        </div>
      )}

      <div className="chart-container">
        {loading && <div className="chart-overlay">加载走势数据…</div>}
        {!loading && (!seriesPayload || seriesPayload.series.length === 0) && (
          <div className="chart-overlay">暂无历史序列数据</div>
        )}
        <div ref={chartRef} className="chart-canvas" />
      </div>

      {seriesPayload && seriesPayload.sources.length > 1 && (
        <p className="chart-footnote">
          注：实线为实时成分股计算或免费公开历史口径，虚线为外部 CSV 导入或内置 20 年基准种子序列，不同来源的统计口径可能存在细微差异。
        </p>
      )}
    </section>
  );
};
