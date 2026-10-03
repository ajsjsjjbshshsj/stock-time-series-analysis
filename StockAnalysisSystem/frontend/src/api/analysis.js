/**
 * 分析建模接口层（对齐 stock-analysis-app）。
 * 后端目前只有 Python API/Streamlit，无 REST；以下路径为设计约定。
 */
import { request } from './client'
import * as mock from '../mock/analysis'
import { STOCK_POOL, getBars, getDailyBasic, getIndicators, FIXED_TODAY } from '../mock/generator'

/** GET /api/analysis/stocks — 股票池（stock_basic 子集） */
export function getStockPool() {
  return request(() => STOCK_POOL.map(({ ts_code, symbol, name, area, industry, list_date }) => ({ ts_code, symbol, name, area, industry, list_date })), '/api/analysis/stocks')
}

/** GET /api/analysis/kline/:tsCode — 日线 OHLCV（stock_daily） */
export function getKline(tsCode) {
  return request(() => ({ ts_code: tsCode, bars: getBars(tsCode) }), `/api/analysis/kline/${tsCode}`)
}

/** GET /api/analysis/daily-basic/:tsCode/:tradeDate — 估值与换手（stock_daily_basic） */
export function getDailyBasicRow(tsCode, tradeDate = FIXED_TODAY) {
  return request(() => getDailyBasic(tsCode, tradeDate), `/api/analysis/daily-basic/${tsCode}/${tradeDate}`)
}

/** GET /api/analysis/features/catalog — 特征目录（FEATURE_REGISTRY） */
export function getFeatureCatalog() {
  return request(() => mock.getFeatureCatalog(), '/api/analysis/features/catalog')
}

/** GET /api/analysis/features/:tsCode — stock_features 行（v2 列名，含横截面） */
export function getFeatureRows(tsCode, limit = 30) {
  return request(() => mock.getFeatureRows(tsCode, limit), `/api/analysis/features/${tsCode}?limit=${limit}`)
}

/** GET /api/analysis/indicators/:tsCode — 全量指标序列（内部列名，供图表） */
export function getIndicatorSeries(tsCode) {
  return request(() => getIndicators(tsCode), `/api/analysis/indicators/${tsCode}`)
}

/** GET /api/analysis/statistics/:tsCode — 描述统计/直方图/回撤/波动率/相关性/趋势 */
export function getStatisticalAnalysis(tsCode) {
  return request(() => mock.getStatisticalAnalysis(tsCode), `/api/analysis/statistics/${tsCode}`)
}

/** GET /api/analysis/models — 模型注册表（model_registry） */
export function getModels() {
  return request(() => mock.getModels(), '/api/analysis/models')
}

/** GET /api/analysis/models/:name/importance — 特征重要性 TopN */
export function getFeatureImportance(modelName, topN = 15) {
  return request(() => mock.getFeatureImportance(modelName, topN), `/api/analysis/models/${modelName}/importance?top=${topN}`)
}

/** GET /api/analysis/prediction/:tsCode — 预测 vs 实际序列 */
export function getPrediction(tsCode, modelName) {
  return request(() => mock.getPrediction(tsCode, modelName), `/api/analysis/prediction/${tsCode}?model=${modelName}`)
}

/** GET /api/analysis/results/:tsCode — analysis_result 表预览 */
export function getAnalysisResults(tsCode) {
  return request(() => mock.getAnalysisResults(tsCode), `/api/analysis/results/${tsCode}`)
}

/** POST /api/analysis/backtest — 策略回测 */
export function getBacktest(tsCode, type, params) {
  return request(
    () => mock.getBacktest(tsCode, type, params),
    '/api/analysis/backtest',
    { method: 'POST', body: JSON.stringify({ ts_code: tsCode, type, params }) },
  )
}

/** GET /api/analysis/ranking — 全市场选股排名（ranking_lgb） */
export function getRanking() {
  return request(() => mock.getRanking(), '/api/analysis/ranking')
}

/** GET /api/analysis/ranking/backtest — 排名组合回测 */
export function getRankingBacktest() {
  return request(() => mock.getRankingBacktest(), '/api/analysis/ranking/backtest')
}

/** GET /api/analysis/ranking/probe-selection — 探针法特征筛选过程 */
export function getProbeSelection() {
  return request(() => mock.getProbeSelection(), '/api/analysis/ranking/probe-selection')
}

/** GET /api/analysis/transformer/configs — Transformer 可选项 */
export function getTransformerConfigs() {
  return request(() => mock.TRANSFORMER_CONFIGS, '/api/analysis/transformer/configs')
}

/** POST /api/analysis/transformer/train — 训练历史（mock 直接生成） */
export function getTransformerTraining(config) {
  return request(
    () => mock.getTransformerTraining(config),
    '/api/analysis/transformer/train',
    { method: 'POST', body: JSON.stringify(config) },
  )
}

/** POST /api/analysis/transformer/predict — TopK 预测结果 */
export function getTransformerPredictions(config) {
  return request(
    () => mock.getTransformerPredictions(config),
    '/api/analysis/transformer/predict',
    { method: 'POST', body: JSON.stringify(config) },
  )
}
