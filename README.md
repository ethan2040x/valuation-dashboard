# 美股指数与自选公司估值看板 (Valuation Dashboard)

基于 [DESIGN.md](./DESIGN.md) 实现的日频估值看板，支持 **大盘指数（`NDX` / `SPY`）** 与 **美股自选公司（内置 Top 10 市值，支持扩展 10–50 家）** 双视图：
- **动态看板**：FastAPI + SQLite + APScheduler + React/TypeScript/ECharts，支持手动/定时刷新、添加/删除自选公司与页面上传 CSV。
- **离线静态页面**：同一套前端组件构建为单文件 HTML，内嵌快照 JSON，双击即可离线打开。
- **声明式可扩展指标引擎**：新增指标只需在 `backend/app/indicators/` 新增一个指标定义文件，API 和前端零改动。

## 核心指标与口径

| 指标 | ID | 口径与边界规则 |
| --- | --- | --- |
| 前瞻 PE | `pe_fwd` | 指数按 `1 ÷ Σ(权重 × 未来12个月一致预期EPS ÷ 股价)`（亏损股计入；缺数据权重 > 5% 标“不完整”）；个股按 `股价 ÷ forwardEps` |
| 滚动 PE | `pe_ttm` | 指数同上，采用过去 12 个月实际 EPS；个股按 `股价 ÷ TTM EPS`（亏损标“亏损”，> 300x 极端毛刺过滤） |
| PE 历史分位 | `pe_percentile` | 窗口内 PE ≤ 当前值的交易日占比（5 年 / 10 年 / 20 年，对滚动 PE 和前瞻 PE 分别计算）；实际历史不足 1 年时不显示；超过 1 年但不满窗口时标注“基于 X 年数据” |

## 视图与内置自选公司

顶部固定 **`大盘指数`** 与 **`自选公司`** 两个主 Tab，避免公司数量增加导致导航膨胀：
- **大盘指数**：跟踪纳指 100 (`NDX` / `QQQ`) 与标普 500 (`SPY`)。
- **自选公司**：首次启动自动内置美股 Top 10 市值公司（`NVDA`、`AAPL`、`MSFT`、`AMZN`、`GOOGL`、`META`、`AVGO`、`TSLA`、`BRK.B`、`LLY`），提供搜索、行业筛选、分位排序、总览对比与下钻走势/分位详情，支持页面或 CLI 增删及批量 CSV 导入。

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
# 1. 初始化 / 升级 20 年种子历史数据（NDX 与 SPY）
backend/.venv/bin/python -m app.jobs seed --all

# 2. 拉取 20 年免费公开历史（默认仅使用免费源：Siblis 免费层 / Multpl / Shiller）
backend/.venv/bin/python -m app.jobs backfill-history --all --years 20

# 3. 拉取当日成分股权重 + yfinance 股价与 EPS，落库并重算全部指标
backend/.venv/bin/python -m app.jobs refresh --all

# 4. 自选公司：免费回填 15 年 SEC EDGAR / Yahoo 历史与批量刷新最新行情
backend/.venv/bin/python -m app.jobs backfill-equities --years 15
backend/.venv/bin/python -m app.jobs refresh-equities

# 5. 自选公司管理（增删查）
backend/.venv/bin/python -m app.jobs watchlist list
backend/.venv/bin/python -m app.jobs watchlist add AMD --name "超威半导体" --sector "半导体" --backfill
backend/.venv/bin/python -m app.jobs watchlist remove AMD

# 6. 导入自定义历史 CSV（支持宽表、长表、双列表及带 ticker 列的多公司批量 CSV）
backend/.venv/bin/python -m app.jobs import-csv path/to/history.csv --source gurufocus_csv
backend/.venv/bin/python -m app.jobs import-csv path/to/two_col.csv --source macromicro_csv --series-id pe_fwd
backend/.venv/bin/python -m app.jobs import-csv path/to/equities.csv --instrument AUTO --source gurufocus_csv

# 7. 查看 / 切换数据源优先级（默认 free_first，支持 external_first 或自定义）
backend/.venv/bin/python -m app.jobs sources --instrument NDX
backend/.venv/bin/python -m app.jobs set-priority --profile external_first

# 8. 重算 / 回填全部已注册指标
backend/.venv/bin/python -m app.jobs compute --all

# 9. 构建单文件前端模板并导出离线静态页面
npm run --prefix frontend build:static
backend/.venv/bin/python -m app.jobs export --output dist/ndx-valuation-snapshot.html
```

导出完成后，直接双击 `dist/ndx-valuation-snapshot.html` 即可在断网环境下打开，数值与动态看板完全一致，页头显示快照生成时间，不显示“手动刷新”按钮。

## GitHub Pages 自动部署

仓库已内置工作流 [.github/workflows/deploy-pages.yml](.github/workflows/deploy-pages.yml)，支持将单文件静态快照自动发布到 GitHub Pages，并按美股交易日自动刷新：

1. 将代码推送到 `main` 分支。
2. 在 GitHub 仓库页面打开 **Settings → Pages → Build and deployment → Source**，选择 **GitHub Actions**。
3. 工作流会在以下时机自动构建并发布站点：
   - 每次推送到 `main` 分支；
   - 每个美股交易日收盘后（周一至周五 `23:30 UTC`，对应美东 `18:30 EST` / `19:30 EDT`），通过 GitHub Actions Cache 保留历史累积库，自动拉取最新 QQQ 持仓与 `yfinance` 数据并重生成快照；
   - 在 **Actions** 页面手动点击 **Run workflow**。
4. 部署完成后访问：`https://<username>.github.io/valuation-dashboard/`（同时提供 `/ndx-valuation-snapshot.html` 直链）。

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

## 数据源与优先级说明

看板支持 **实时计算源、免费公开历史源、外部导入 CSV 与可选付费 API**，同日存在多来源时按优先级自动合并，默认配置为 **`free_first`（免费源优先）**：

1. **实时免费计算**：`computed`（指数：`qqq+yfinance` / `spy+yfinance`；个股：`equity+yfinance`，优先复用当日指数成分股快照）。
2. **免费公开历史源**：
   - `siblis_free`：Siblis Research 免密钥公开历史接口（支持 `NDX`）。
   - `multpl`：Multpl S&P 500 百年月频 PE 表（结合 `^GSPC` 收盘价展开为日频，离线时按 NYSE 交易日历线性插值）。
   - `shiller`：Robert Shiller 公开数据集（`SP500 / Earnings`，作为 Multpl 备源）。
   - `sec_edgar`：SEC EDGAR 官方 CompanyFacts XBRL（`10-Q`/`10-K` 稀释 EPS + 历史拆股对齐 + Yahoo 日收盘价，支持美股 10–15 年滚动 PE）。
   - `yfinance_history`：Yahoo Finance 季度/年度利润表 EPS 历史（约 4–5 年免费兜底）。
3. **外部 CSV 导入源**：`csv_import`、`gurufocus_csv`、`macromicro_csv`、`bloomberg_csv`、`wind_csv`（支持页面弹窗上传或 `import-csv` 导入，含 `--instrument AUTO` 多公司批量导入）。
4. **可选付费 API**：`siblis_paid`（默认关闭，仅当设置 `SIBLIS_API_KEY` 且显式传 `--include-paid` / `include_paid=true` 时启用）。
5. **内置 20 年基准种子**：`seed_csv`（`data/seed/ndx_pe_history.csv` 与 `data/seed/spy_pe_history.csv`，覆盖 `2006-01-03` 至今约 20 年 NYSE 交易日，零网络兜底）。

### 相关 REST API

- `GET /api/instruments` / `POST /api/instruments` / `PATCH /api/instruments/{symbol}` / `DELETE /api/instruments/{symbol}`：管理大盘指数与美股自选公司
- `GET /api/watchlist/overview?kind=equity`：单次聚合自选公司最新价、PE、5Y/10Y 分位与历史跨度
- `GET /api/indicators/{id}/series?instrument=NDX&range=20y`（支持 `1y` / `5y` / `10y` / `20y` / `all`）
- `GET /api/sources?instrument=NDX`：查看数据源目录、优先级顺序与当前数据库覆盖区间
- `PUT /api/sources/priority`：切换优先级预设（`free_first` / `external_first`）或提交自定义顺序并自动重算指标
- `POST /api/jobs/backfill-history?instrument=NDX&years=20&include_paid=false`：拉取单个标的免费历史数据
- `POST /api/jobs/refresh-equities` & `POST /api/jobs/backfill-equities`：批量刷新 / 15 年回填自选公司（含 `GET /api/jobs/{job_id}` 进度查询）
- `POST /api/import/csv`：导入单公司或带 `ticker` 列的多公司批量 CSV
