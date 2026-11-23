import React from "react";
import type { SnapshotPayload } from "../types/valuation";
import { formatDateTime, formatJobLabel } from "../utils/format";

interface DataStatusBarProps {
  status: SnapshotPayload["status"];
}

export const DataStatusBar: React.FC<DataStatusBarProps> = ({ status }) => {
  const failedJobs = status.jobs.filter((j) => j.status === "failed");

  return (
    <footer className="status-bar" aria-label="数据状态栏">
      <div className="status-bar__summary">
        <div className="status-item">
          <span className="status-item__label">上次更新时间</span>
          <span className="status-item__value">
            {formatDateTime(status.last_updated_at)}
          </span>
        </div>
        <div className="status-item">
          <span className="status-item__label">最新数据日期</span>
          <span className="status-item__value">
            {status.latest_data_date ?? "—"}
          </span>
        </div>
        <div className="status-item">
          <span className="status-item__label">近 1 年缺失交易日</span>
          <span
            className={`status-item__value ${status.missing_days_1y > 5 ? "status-warn" : ""}`}
          >
            {status.missing_days_1y} 天
          </span>
        </div>
        <div className="status-item">
          <span className="status-item__label">累计交易日</span>
          <span className="status-item__value">{status.total_trading_days} 天</span>
        </div>
      </div>

      <div className="status-bar__jobs">
        {status.jobs.length === 0 ? (
          <span className="status-empty">尚无任务执行记录</span>
        ) : (
          status.jobs.map((job) => {
            const dotClass =
              job.status === "success"
                ? "dot--ok"
                : job.status === "partial"
                  ? "dot--warn"
                  : job.status === "running"
                    ? "dot--info"
                    : "dot--error";

            const statusText =
              job.status === "success"
                ? "成功"
                : job.status === "partial"
                  ? "部分成功"
                  : job.status === "running"
                    ? "运行中"
                    : "失败";

            return (
              <div
                key={job.id}
                className="job-pill"
                title={
                  job.error
                    ? `错误：${job.error}\n上次成功：${formatDateTime(job.last_success_at)}`
                    : `上次成功：${formatDateTime(job.last_success_at)}`
                }
              >
                <span className={`dot ${dotClass}`} />
                <span className="job-pill__name">{formatJobLabel(job.job)}</span>
                <span className="job-pill__status">{statusText}</span>
              </div>
            );
          })
        )}
      </div>

      {failedJobs.length > 0 && (
        <div className="status-alert" role="status">
          部分数据源最近一次拉取失败（{failedJobs.map((j) => formatJobLabel(j.job)).join("、")}），页面已保留上次成功日期的有效估值数据。
          {failedJobs[0]?.error ? ` 错误详情：${failedJobs[0].error}` : ""}
        </div>
      )}
    </footer>
  );
};
