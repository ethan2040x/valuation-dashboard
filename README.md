# 纳指 100 估值看板 (Nasdaq 100 Valuation Dashboard)

基于 [DESIGN.md](./DESIGN.md) 实现的纳指 100 日频估值看板，包含：
- **动态看板**：FastAPI + SQLite + APScheduler + React/TypeScript/ECharts，支持手动刷新与交易日定时更新。
- **离线静态页面**：同一套前端组件构建为单文件 HTML，内嵌快照 JSON，双击即可离线打开。
- **声明式可扩展指标引擎**：新增指标只需在 `backend/app/indicators/` 新增一个指标定义文件，API 和前端零改动。

## 核心指标与口径

| 指标 | ID | 口径与边界规则 |
| --- | --- | --- |
| 前瞻 PE | `pe_fwd` | `1 ÷ Σ(权重 × 未来12个月一致预期EPS ÷ 股价)`；亏损股计入；缺数据股剔除后权重归一；缺数据权重合计 > 5% 标记为“不完整” |
| 滚动 PE | `pe_ttm` | 同上，EPS 为过去 12 个月实际 EPS；过滤异常正值脏数据（隐含 PE < 1x），保留全部亏损股 |
| PE 历史分位 | `pe_percentile` | 窗口内 PE ≤ 当前值的交易日占比（5 年 / 10 年，对滚动 PE 和前瞻 PE 分别计算）；实际历史不足 1 年时不显示；超过 1 年但不满窗口时标注“基于 X 年数据” |

## 快速启动

### 方式一：Docker Compose（一条命令启动）

```bash
docker compose up --build
```

启动后访问 `http://localhost:8000`。SQLite 数据库持久化在 `./data/valuation.db`，首次启动若库为空会自动导入 `data/seed/ndx_pe_history.csv`。

### 方式二：本地开发运行

```bash
# 1. 安装后端依赖（Python >= 3.11）
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"

# 2. 启动后端（含静态托管与 APScheduler）
.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload

# 3. 另开终端启动前端开发服务器（可选，若已 build 前端可直接访问 8000 端口）
cd ../frontend
npm install
npm run dev
```

## 常用任务命令

在仓库根目录执行（使用 `backend/.venv/bin/python`）：

```bash
# 1. 初始化种子历史数据（仅当 raw_series 为空时执行）
backend/.venv/bin/python -m app.jobs seed

# 2. 拉取当日 QQQ 成分股权重 + yfinance 股价与 EPS，落库并重算全部指标
backend/.venv/bin/python -m app.jobs refresh

# 3. 导入自定义历史 CSV（支持宽表 date,pe_ttm,pe_fwd[,source] 或长表 date,series_id,value[,source]）
backend/.venv/bin/python -m app.jobs import-csv path/to/history.csv --source gurufocus_csv

# 4. 重算 / 回填全部已注册指标
backend/.venv/bin/python -m app.jobs compute

# 5. 构建单文件前端模板并导出离线静态页面
npm run --prefix frontend build:static
backend/.venv/bin/python -m app.jobs export --output dist/ndx-valuation-snapshot.html
```

导出完成后，直接双击 `dist/ndx-valuation-snapshot.html` 即可在断网环境下打开，数值与动态看板完全一致，页头显示快照生成时间，不显示“手动刷新”按钮。

## 如何新增一个指标（API & 前端零改动）

以新增 **盈利收益率**（`1 ÷ 滚动 PE × 100%`）为例，只需在 `backend/app/indicators/` 新建 `earnings_yield.py`：

```python
from app.indicators import register
from app.indicators.base import ComputeDeps, Indicator
from app.models import Display, IndicatorPoint, ReferenceLine


@register
class EarningsYield(Indicator):
    id = "earnings_yield"
    name = "盈利收益率"
    unit = "%"
    description = "1 ÷ 滚动 PE × 100%"
    depends_on = ["pe_ttm"]
    display = Display(
        kind="metric_card",
        chartable=True,
        precision=2,
        suffix="%",
        order=25,
        reference_lines=(ReferenceLine(kind="mean", label="区间均值"),),
    )

    def compute(self, deps: ComputeDeps, **params):
        return [
            IndicatorPoint(
                date=pt.date,
                value=round((1.0 / pt.value) * 100.0, 4),
                source=pt.source,
                is_incomplete=pt.is_incomplete,
            )
            for pt in deps["pe_ttm"]
            if pt.value
        ]
```

然后运行 `backend/.venv/bin/python -m app.jobs compute` 回填即可。`GET /api/indicators`、`GET /api/snapshot`、`GET /api/indicators/earnings_yield/series` 以及前端卡片行和走势图会自动展示该指标，无需修改 API 或前端代码。

## 测试

```bash
backend/.venv/bin/pytest backend/tests -v
```

## 数据源说明

- **成分股权重**：优先尝试 Invesco QQQ 官方 CSV 端点，失败自动回退至 Slickcharts Nasdaq-100 权重表，亦支持本地 CSV 文件。
- **股价与 EPS**：通过 `yfinance` 获取成分股 `currentPrice`、`trailingEps`、`forwardEps`。若拉取失败，失败记录写入 `job_runs` 并在页面状态栏提示，历史成功日期与估值保留不变（不会出现空值或 0）。
- **历史序列**：仓库内置 `data/seed/ndx_pe_history.csv` 作为初始 10 年参考种子，可随时通过 `import-csv` 导入 GuruFocus / Bloomberg 等外部历史 CSV；同一天存在多来源时按 `SOURCE_PRIORITY`（默认实时计算优先于 CSV 导入）取值，并在走势图中用实线/虚线区分来源。
