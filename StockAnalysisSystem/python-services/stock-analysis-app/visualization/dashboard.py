"""
交互式看板（Streamlit）
"""
import sys
import os
import json
# 将项目根目录加入 sys.path，使子模块导入生效
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from data_loader.collector import DataCollector
from data_processor.cleaner import DataCleaner
from data_processor.feature_engineer import FeatureEngineer
from analysis.statistical import StatisticalAnalyzer
from analysis.predictor import StockPredictor
from analysis.backtester import Backtester
from visualization.plotter import StockPlotter

# A股配色：涨=红，跌=绿
COLOR_UP = '#EF5350'
COLOR_DOWN = '#26A69A'

# Plotly 统一布局配置
PLOTLY_LAYOUT_KWARGS = dict(
    font_family='Microsoft YaHei, SimHei, sans-serif',
    template='plotly_white',
    margin=dict(l=50, r=30, t=40, b=50),
)


def _make_chinese_layout(fig, **kwargs):
    """为 Plotly 图表应用统一的中文布局和配色"""
    layout = {**PLOTLY_LAYOUT_KWARGS, **kwargs}
    fig.update_layout(layout)
    return fig


def _build_volume_bars(df, name='成交量'):
    """构建 A 股配色的成交量柱状图"""
    colors = [COLOR_UP if df['close'].iloc[i] >= df['open'].iloc[i] else COLOR_DOWN
              for i in range(len(df))]
    return go.Bar(
        x=df['trade_date'], y=df['vol'],
        marker_color=colors, name=name, opacity=0.7
    )


def _build_rolling_vol_trace(df, window, name=None):
    """构建滚动波动率轨迹"""
    if 'return_1d' not in df.columns:
        return None
    returns = df['return_1d'].dropna()
    rolling_vol = returns.rolling(window=window).std() * np.sqrt(252)
    label = name or f'{window}日波动率'
    return go.Scatter(
        x=df['trade_date'].iloc[-len(rolling_vol):],
        y=rolling_vol, name=label, line=dict(width=1.5)
    )


def _compute_drawdown(equity_series):
    """从权益序列计算回撤"""
    cumulative = equity_series / equity_series.iloc[0]
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    return drawdown


def _find_latest_precomputed_features():
    """
    扫描 models/transformer 目录，查找最新的预计算特征文件。
    返回 (parquet_path, feature_num) 或 (None, None)。
    """
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_dir = os.path.join(project_dir, 'models', 'transformer')

    if not os.path.exists(base_dir):
        return None, None

    best_path = None
    best_mtime = 0
    best_feature_num = None

    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if f.startswith('features_') and f.endswith('.parquet'):
                full_path = os.path.join(root, f)
                mtime = os.path.getmtime(full_path)
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_path = full_path
                    # 从文件名提取 feature_num，如 features_158+39.parquet -> '158+39'
                    best_feature_num = f.replace('features_', '').replace('.parquet', '')

    return best_path, best_feature_num


@st.cache_data(ttl=3600, show_spinner="正在加载面板数据...")
def _load_panel_data():
    """
    从预计算 parquet 文件或数据库加载全市场面板数据。

    缓存策略: 1 小时 TTL，避免重复 IO。
    数据限制: 最多加载最近 2 年数据，防止内存溢出。

    优先加载预计算特征文件（models/transformer/*/features_*.parquet），
    避免每次访问都重新计算特征，大幅提升 dashboard 响应速度。
    """
    # --- 路径 1: 尝试加载预计算特征 ---
    parquet_path, feature_num = _find_latest_precomputed_features()

    if parquet_path is not None:
        try:
            import joblib

            with st.spinner(f"正在加载预计算特征 ({feature_num})..."):
                df = pd.read_parquet(parquet_path, engine='pyarrow')

            if df.empty:
                return None

            # 加载元信息
            meta_path = parquet_path.replace('.parquet', '_meta.json')
            val_start_date = None
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                val_start_date = meta.get('val_start_date')

            # 标准化列名以适配下游调用
            # parquet 中的列名是中文（股票代码, 日期, open->开盘 等已转换）
            # 确保 trade_date 列存在（下游某些代码可能依赖它）
            if '日期' in df.columns and 'trade_date' not in df.columns:
                df['trade_date'] = pd.to_datetime(df['日期'])
            if 'ts_code' not in df.columns and '股票代码' in df.columns:
                df['ts_code'] = df['股票代码']

            df = df.replace([float('inf'), float('-inf')], float('nan'))
            df = df.dropna(subset=['close'])

            cache_time = datetime.fromtimestamp(os.path.getmtime(parquet_path))
            st.success(f"已从缓存加载特征 ({feature_num})，共 {len(df)} 条记录 (更新于 {cache_time.strftime('%Y-%m-%d %H:%M')})")

            return df

        except Exception as e:
            st.warning(f"加载预计算特征失败: {e}，将从数据库重新计算...")

    # --- 路径 2: 回退到数据库 + 特征计算 ---
    try:
        from database.db_connector import DatabaseConnector
        from database import repository
        from data_processor.feature_engineer import FeatureEngineer

        with DatabaseConnector() as db:
            with db.session_scope() as session:
                panel_df = repository.load_daily_with_names(session)

        if panel_df.empty:
            st.info("数据库中没有行情数据。请先运行数据采集脚本。")
            return None

        panel_df['trade_date'] = pd.to_datetime(panel_df['trade_date'])

        # [性能保护] 限制数据范围：最近 2 年
        two_years_ago = pd.Timestamp.now() - pd.DateOffset(years=2)
        panel_df = panel_df[panel_df['trade_date'] >= two_years_ago]
        if panel_df.empty:
            st.info("最近 2 年没有行情数据。请检查数据采集时间范围。")
            return None

        # 计算技术指标（按股票分组逐只计算）
        with st.spinner("正在计算技术指标..."):
            engineer = FeatureEngineer()
            results = []
            codes = panel_df['ts_code'].unique()
            progress_bar = st.progress(0)

            for idx, code in enumerate(codes):
                stock_df = panel_df[panel_df['ts_code'] == code].copy()
                stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
                if len(stock_df) < 60:
                    continue
                try:
                    stock_featured = engineer.calculate_all_features(stock_df)
                    results.append(stock_featured)
                except Exception:
                    continue

                if idx % 50 == 0:
                    progress_bar.progress(min(int(idx / len(codes) * 100), 100))

            progress_bar.progress(100)

        if not results:
            st.info("没有足够的股票数据来计算特征（每只股票至少需要 60 个交易日）。")
            return None

        featured_panel = pd.concat(results, ignore_index=True)
        featured_panel = featured_panel.replace([float('inf'), float('-inf')], float('nan'))
        featured_panel = featured_panel.dropna(subset=['close'])

        return featured_panel

    except Exception as e:
        st.error(f"加载面板数据失败: {e}")
        st.info("请检查数据库连接是否正常，或运行 `python scripts/train_model.py` 预计算特征。")
        return None


def main():
    """Streamlit应用主函数"""
    st.set_page_config(page_title="股票分析系统", layout="wide")

    st.title("股票分析系统")
    st.markdown("---")

    # 侧边栏
    st.sidebar.header("参数设置")
    st.sidebar.caption("数据来源：MySQL（由 python-collector 统一采集）")
    stock_code = st.sidebar.text_input("股票代码", "000001")

    col1, col2 = st.sidebar.columns(2)
    start_date = col1.text_input("开始日期", "20200101")
    end_date = col2.text_input("结束日期", datetime.now().strftime("%Y%m%d"))

    # --- 特征缓存状态 ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("特征缓存")
    parquet_path, feature_num = _find_latest_precomputed_features()
    if parquet_path is not None:
        cache_time = datetime.fromtimestamp(os.path.getmtime(parquet_path))
        st.sidebar.success(f"已缓存 ({feature_num})\n{cache_time.strftime('%Y-%m-%d %H:%M')}")
        if st.sidebar.button("刷新缓存", key="refresh_cache_btn"):
            with st.spinner("正在重新计算特征..."):
                try:
                    from database.db_connector import DatabaseConnector
                    from database import repository
                    from analysis.transformer_trainer import compute_and_save_features

                    with DatabaseConnector() as db:
                        with db.session_scope() as session:
                            raw_df = repository.load_daily_raw(session)

                    if raw_df.empty:
                        st.error("数据库无数据")
                    else:
                        # 尝试所有特征配置
                        from analysis.transformer_config import TRANSFORMER_CONFIG
                        saved_paths = []
                        for fnum in ['39', '158+39']:
                            cfg = TRANSFORMER_CONFIG.copy()
                            cfg['feature_num'] = fnum
                            save_path, _, _ = compute_and_save_features(raw_df, config=cfg)
                            if save_path:
                                saved_paths.append(save_path)

                        if saved_paths:
                            st.success(f"缓存已刷新: {len(saved_paths)} 个特征集")
                            st.rerun()
                        else:
                            st.error("特征计算失败")
                except Exception as e:
                    st.error(f"刷新失败: {e}")
    else:
        st.sidebar.warning("无缓存特征")
        if st.sidebar.button("生成缓存", key="generate_cache_btn"):
            st.sidebar.info("请先运行: python main.py --mode transform_features")

    if st.sidebar.button("加载数据"):
        with st.spinner("正在加载数据..."):
            try:
                collector = DataCollector()

                df = collector.fetch_single(stock_code, start_date, end_date)

                if df is None or df.empty:
                    st.error("未能获取数据，请检查股票代码是否正确")
                    return

                cleaner = DataCleaner()
                df = cleaner.clean_stock_data(df, stock_code)

                engineer = FeatureEngineer()
                df = engineer.calculate_all_features(df)

                st.session_state['df'] = df
                st.session_state['stock_code'] = stock_code
                st.success(f"成功加载{stock_code}数据，共{len(df)}条记录")

            except Exception as e:
                st.error(f"加载数据失败: {str(e)}")
                return

    if 'df' in st.session_state:
        df = st.session_state['df']
        stock_code = st.session_state['stock_code']

        # 日期范围选择器
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        date_min = df['trade_date'].min()
        date_max = df['trade_date'].max()
        selected_range = st.date_input(
            "日期范围",
            value=(date_min.date(), date_max.date()),
            min_value=date_min.date(),
            max_value=date_max.date(),
        )

        if len(selected_range) == 2:
            start, end = pd.Timestamp(selected_range[0]), pd.Timestamp(selected_range[1])
            df_display = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
        else:
            df_display = df.copy()

        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "综合概览", "基本行情", "技术指标", "统计分析", "模型预测", "策略回测", "风险提示"
        ])

        with tab1:
            show_overview(df, df_display, stock_code)

        with tab2:
            show_basic_info(df, df_display, stock_code)

        with tab3:
            show_technical_indicators(df_display, stock_code)

        with tab4:
            show_statistical_analysis(df_display)

        with tab5:
            show_prediction(df, stock_code)

        with tab6:
            show_backtest(df, stock_code)

        with tab7:
            st.subheader("风险提示")
            st.warning("本系统仅供学习和研究使用，不构成投资建议。股市有风险，投资需谨慎。")
            st.info("建议结合多种分析方法，理性投资决策。")

    # 全市场选股可独立访问，无需先选择个股
    st.markdown("---")
    show_ranking_selection()

    # Transformer 深度学习预测可独立访问
    st.markdown("---")
    show_transformer_section()


def show_overview(df, df_display, stock_code):
    """综合概览 Tab"""
    st.subheader(f"{stock_code} 综合概览")

    # 关键指标卡片
    latest = df.iloc[-1]
    returns = df['return_1d'].dropna()
    annual_return = (1 + returns.mean()) ** 252 - 1
    annual_vol = returns.std() * np.sqrt(252)
    sharpe = (annual_return - 0.03) / annual_vol if annual_vol > 0 else 0

    cumulative = df['close'] / df['close'].iloc[0]
    running_max = cumulative.cummax()
    max_dd = ((cumulative - running_max) / running_max).min()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("最新收盘价", f"{latest['close']:.2f}")
    col2.metric("年化收益率", f"{annual_return:.2%}")
    col3.metric("年化波动率", f"{annual_vol:.2%}")
    col4.metric("夏普比率", f"{sharpe:.3f}")
    col5.metric("最大回撤", f"{max_dd:.2%}")

    # 两列布局
    left_col, right_col = st.columns(2)

    with left_col:
        # 近期 K 线
        recent = df_display.tail(60)
        if len(recent) > 0:
            fig = go.Figure(data=[go.Candlestick(
                x=recent['trade_date'],
                open=recent['open'], high=recent['high'],
                low=recent['low'], close=recent['close'],
                name='K线',
                increasing_line_color=COLOR_UP, decreasing_line_color=COLOR_DOWN,
                increasing_fillcolor=COLOR_UP, decreasing_fillcolor=COLOR_DOWN,
            )])
            fig = _make_chinese_layout(fig, title=f'{stock_code} 近期K线（60日）', height=350)
            st.plotly_chart(fig, use_container_width=True)

    with right_col:
        # 相关性热力图（选取关键指标）
        key_cols = ['close', 'vol', 'return_1d', 'rsi', 'macd_dif', 'macd_dea', 'mfi14']
        available = [c for c in key_cols if c in df.columns]
        if len(available) >= 3:
            corr = df[available].corr()
            fig = go.Figure(data=go.Heatmap(
                z=corr.values,
                x=available,
                y=available,
                colorscale='RdBu_r',
                zmid=0,
                text=np.round(corr.values, 2),
                texttemplate='%{text:.2f}',
                textfont=dict(size=10),
                colorbar=dict(title='相关系数')
            ))
            fig = _make_chinese_layout(fig, title='关键指标相关性', height=350)
            st.plotly_chart(fig, use_container_width=True)

    # 累计收益曲线
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['trade_date'], y=cumulative - 1,
        name='累计收益', line=dict(color=COLOR_UP, width=2),
        fill='tozeroy', fillcolor=f'rgba(239,83,80,0.1)'
    ))
    fig = _make_chinese_layout(fig, title='累计收益曲线', height=300,
                               yaxis_title='累计收益率', xaxis_title='日期')
    st.plotly_chart(fig, use_container_width=True)


def show_basic_info(df, df_display, stock_code):
    """展示基本行情"""
    st.subheader(f"{stock_code} 基本行情")
    latest = df.iloc[-1]
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("收盘价", f"{latest['close']:.2f}")
    col2.metric("开盘价", f"{latest['open']:.2f}")
    col3.metric("最高价", f"{latest['high']:.2f}")
    col4.metric("最低价", f"{latest['low']:.2f}")
    col5.metric("成交量", f"{latest['vol']:.0f}")

    fig = go.Figure(data=[go.Candlestick(
        x=df_display['trade_date'],
        open=df_display['open'], high=df_display['high'],
        low=df_display['low'], close=df_display['close'],
        name='K线',
        increasing_line_color=COLOR_UP, decreasing_line_color=COLOR_DOWN,
        increasing_fillcolor=COLOR_UP, decreasing_fillcolor=COLOR_DOWN,
    )])
    fig = _make_chinese_layout(fig, title=f'{stock_code} K线图',
                               xaxis_title='日期', yaxis_title='价格')
    st.plotly_chart(fig, use_container_width=True)


def show_technical_indicators(df_display, stock_code):
    """展示技术指标"""
    st.subheader("技术指标分析")

    # 可选显示哪些辅助指标
    show_bb = 'bb_upper' in df_display.columns
    show_kdj = 'kdj_k' in df_display.columns and 'kdj_d' in df_display.columns

    # 动态决定子图行数
    n_rows = 4
    row_heights = [0.35, 0.15, 0.15, 0.35]
    specs = [[{}], [{}], [{}], [{}]]

    if show_bb:
        n_rows += 1
        row_heights = [0.30, 0.10, 0.15, 0.15, 0.30]
        specs = [[{}], [{}], [{}], [{}], [{}]]
    if show_kdj:
        n_rows += 1
        row_heights = [0.28, 0.10, 0.12, 0.12, 0.12, 0.26]
        specs = [[{}], [{}], [{}], [{}], [{}], [{}]]

    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True,
        vertical_spacing=0.02, row_heights=row_heights, specs=specs
    )

    current_row = 1

    # Row 1: 价格 + 均线 + 布林带
    fig.add_trace(go.Scatter(
        x=df_display['trade_date'], y=df_display['close'],
        name='收盘价', line=dict(width=1.5)
    ), row=current_row, col=1)

    if 'ma5' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['ma5'],
            name='MA5', line=dict(width=1)
        ), row=current_row, col=1)
    if 'ma20' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['ma20'],
            name='MA20', line=dict(width=1, dash='dash')
        ), row=current_row, col=1)

    if show_bb:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['bb_upper'],
            name='BB上轨', line=dict(color='orange', width=1)
        ), row=current_row, col=1)
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['bb_middle'],
            name='BB中轨', line=dict(color='blue', width=1, dash='dash')
        ), row=current_row, col=1)
        # 布林带填充（用下半透明方式）
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['bb_lower'],
            name='BB下轨', line=dict(color='orange', width=1),
            fill='tonexty', fillcolor='rgba(255,165,0,0.1)'
        ), row=current_row, col=1)

    current_row += 1

    # Row 2: 布林带宽度（如果存在）
    if show_bb and 'bb_width' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['bb_width'],
            name='BB宽度', line=dict(color='purple', width=1.5),
            fill='tozeroy', fillcolor='rgba(128,0,128,0.15)'
        ), row=current_row, col=1)
        current_row += 1

    # Row: MACD
    if 'macd_dif' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['macd_dif'],
            name='DIF', line=dict(width=1)
        ), row=current_row, col=1)
    if 'macd_dea' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['macd_dea'],
            name='DEA', line=dict(width=1)
        ), row=current_row, col=1)
    if 'macd_hist' in df_display.columns:
        macd_colors = ['red' if v >= 0 else 'green' for v in df_display['macd_hist']]
        fig.add_trace(go.Bar(
            x=df_display['trade_date'], y=df_display['macd_hist'],
            marker_color=macd_colors, name='MACD柱', opacity=0.4
        ), row=current_row, col=1)
    current_row += 1

    # Row: RSI
    if 'rsi' in df_display.columns:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['rsi'],
            name='RSI', line=dict(color='purple', width=1.5)
        ), row=current_row, col=1)
        fig.add_hline(y=70, line_dash="dash", line_color=COLOR_DOWN,
                     row=current_row, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color=COLOR_UP,
                     row=current_row, col=1)
        current_row += 1

    # Row: KDJ（如果存在）
    if show_kdj:
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['kdj_k'],
            name='K', line=dict(color='blue', width=1.2)
        ), row=current_row, col=1)
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['kdj_d'],
            name='D', line=dict(color='orange', width=1.2)
        ), row=current_row, col=1)
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=df_display['kdj_j'],
            name='J', line=dict(color='purple', width=1.2)
        ), row=current_row, col=1)
        fig.add_hline(y=80, line_dash="dash", line_color=COLOR_DOWN,
                     row=current_row, col=1)
        fig.add_hline(y=20, line_dash="dash", line_color=COLOR_UP,
                     row=current_row, col=1)
        current_row += 1

    # 最后一行: 成交量
    fig.add_trace(_build_volume_bars(df_display), row=current_row, col=1)

    fig = _make_chinese_layout(fig, title_text="技术指标", height=900)
    st.plotly_chart(fig, use_container_width=True)


def show_statistical_analysis(df_display):
    """展示统计分析"""
    st.subheader("统计分析")
    analyzer = StatisticalAnalyzer()

    st.write("描述性统计:")
    stats_df = analyzer.descriptive_stats(df_display, ['open', 'high', 'low', 'close', 'vol'])
    st.dataframe(stats_df.round(4))

    st.write("风险指标:")
    risk = analyzer.risk_metrics(df_display, 'close')
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("年化收益率", f"{risk['annual_return']*100:.2f}%")
    col2.metric("年化波动率", f"{risk['annual_volatility']*100:.2f}%")
    col3.metric("夏普比率", f"{risk['sharpe_ratio']:.4f}")
    col4.metric("最大回撤", f"{risk['max_drawdown']*100:.2f}%")

    st.markdown("---")
    st.subheader("可视化分析")

    # 使用 StockPlotter API 绘制静态图表
    plotter = StockPlotter()
    if 'return_1d' in df_display.columns:
        st.subheader("收益率分布 (Matplotlib)")
        fig_dist = plotter.plot_distribution(df_display, column='return_1d', title='收益率分布')
        st.pyplot(fig_dist)
        plt.close(fig_dist)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        # 回撤曲线
        drawdown = _compute_drawdown(df_display['close'])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=drawdown,
            fill='tozeroy', fillcolor='rgba(239,83,80,0.3)',
            line=dict(color=COLOR_UP, width=1), name='回撤'
        ))
        fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.5)
        min_idx = drawdown.idxmin()
        fig.add_annotation(
            x=df_display['trade_date'].iloc[min_idx], y=drawdown.iloc[min_idx],
            text=f"最大回撤: {drawdown.min():.2%}",
            showarrow=True, arrowhead=2, font=dict(size=11)
        )
        fig = _make_chinese_layout(fig, title="回撤曲线", height=300,
                                   yaxis_title='回撤')
        st.plotly_chart(fig, use_container_width=True)

        # 滚动波动率
        if 'return_1d' in df_display.columns:
            fig = go.Figure()
            for window, color in [(20, 'blue'), (60, 'orange'), (120, 'purple')]:
                trace = _build_rolling_vol_trace(df_display, window, f'{window}日波动率')
                if trace:
                    trace.line.color = color
                    fig.add_trace(trace)
            overall_vol = df_display['return_1d'].std() * np.sqrt(252)
            fig.add_hline(y=overall_vol, line_dash="dash", line_color="red",
                         annotation_text=f'整体: {overall_vol:.2%}')
            fig = _make_chinese_layout(fig, title="滚动波动率", height=300,
                                       yaxis_title='年化波动率')
            st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        # 相关性热力图（使用 StockPlotter API）
        key_cols = ['close', 'vol', 'return_1d', 'rsi', 'macd_dif', 'macd_dea', 'mfi14']
        available = [c for c in key_cols if c in df_display.columns]
        if len(available) >= 3:
            st.subheader("相关性热力图 (Matplotlib)")
            fig_corr = plotter.plot_correlation_heatmap(df_display, columns=available, title='相关性热力图')
            st.pyplot(fig_corr)
            plt.close(fig_corr)

        # 累计收益
        cumulative = df_display['close'] / df_display['close'].iloc[0] - 1
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_display['trade_date'], y=cumulative,
            line=dict(color=COLOR_UP, width=2), name='累计收益',
            fill='tozeroy', fillcolor='rgba(239,83,80,0.1)'
        ))
        fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3)
        fig = _make_chinese_layout(fig, title="累计收益曲线", height=300,
                                   xaxis_title='日期', yaxis_title='累计收益率')
        st.plotly_chart(fig, use_container_width=True)


def show_prediction(df, stock_code):
    """展示模型预测结果（加载已训练模型，不在 Dashboard 中训练）。"""
    st.subheader("模型预测")

    st.info(
        "模型训练请在命令行执行：\n"
        "```\n"
        "python scripts/train_model.py --model xgboost\n"
        "python scripts/train_model.py --model ranking\n"
        "python scripts/train_model.py --model transformer\n"
        "```\n"
        "训练完成后，Dashboard 会自动加载最新模型展示预测结果。"
    )

    # 加载已训练模型
    from analysis.model_registry import list_models, load_model

    available_models = list_models()
    if not available_models:
        st.warning("暂无已训练的模型。请先运行训练脚本。")
        return

    # 展示可用模型列表
    st.subheader("已训练模型")
    for meta in available_models[:5]:
        with st.expander(
            f"{meta.get('model_name', '?')} | "
            f"{meta.get('model_type', '?')} | "
            f"{meta.get('created_at', '?')[:10]}",
            expanded=False,
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**训练时间**: {meta.get('train_start_date', '?')} ~ {meta.get('train_end_date', '?')}")
                st.write(f"**特征数**: {meta.get('n_features', 0)}")
                st.write(f"**特征版本**: {meta.get('feature_version', '?')}")
            with col2:
                st.write("**评价指标**:")
                st.json(meta.get('metrics', {}))

    # 用最新模型做预测展示
    latest_meta = available_models[0]
    model_file = latest_meta.get('model_file')
    if model_file:
        model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models', model_file)
        if os.path.exists(model_path):
            st.subheader(f"最新模型预测: {latest_meta.get('model_name', '')}")

            model_bundle, metadata = load_model(model_path)
            if model_bundle is not None:
                model_obj = model_bundle.get('model')
                feature_names = metadata.get('feature_names', []) if metadata else []

                if model_obj and feature_names and df is not None:
                    available_feats = [f for f in feature_names if f in df.columns]
                    if available_feats:
                        latest_data = df.sort_values('trade_date').tail(60)
                        X_latest = latest_data[available_feats].dropna()

                        if not X_latest.empty:
                            try:
                                predictions = model_obj.predict(X_latest.values)
                                result_df = latest_data.loc[X_latest.index, ['trade_date', 'close']].copy()
                                result_df['prediction'] = predictions

                                st.dataframe(result_df.tail(10).reset_index(drop=True))
                            except Exception as e:
                                st.warning(f"预测失败: {e}")
                        else:
                            st.info("最新数据缺少特征，无法预测。")
                    else:
                        st.warning("模型特征与当前数据不匹配。")


def show_backtest(df, stock_code):
    """展示策略回测"""
    st.subheader("策略回测")

    col1, col2 = st.columns(2)
    initial_capital = col1.number_input("初始资金", value=1000000, step=100000, key="backtest_capital")
    commission = col2.number_input("佣金率", value=0.0003, step=0.0001, format="%.4f", key="backtest_commission")

    backtest_type = st.selectbox("回测类型", ["技术指标信号", "模型预测信号"])

    if st.button("开始回测", key="backtest_btn"):
        with st.spinner("正在回测..."):
            try:
                backtester = Backtester(
                    initial_capital=initial_capital,
                    commission_rate=commission
                )

                if backtest_type == "技术指标信号":
                    df_signal = df.copy()
                    df_signal['signal'] = 0
                    if 'golden_cross' in df_signal.columns:
                        df_signal.loc[df_signal['golden_cross'] == 1, 'signal'] = 1
                    if 'death_cross' in df_signal.columns:
                        df_signal.loc[df_signal['death_cross'] == 1, 'signal'] = -1
                    if 'rsi_overbought' in df_signal.columns:
                        df_signal.loc[df_signal['rsi_overbought'] == 1, 'signal'] = -1
                    if 'rsi_oversold' in df_signal.columns:
                        df_signal.loc[df_signal['rsi_oversold'] == 1, 'signal'] = 1

                    result = backtester.run_signal_backtest(df_signal, 'signal')

                else:
                    predictor = StockPredictor(model_type='xgboost')
                    X, y, features = predictor.prepare_features(df)
                    if X is None:
                        st.error("特征准备失败")
                        return
                    result = predictor.train_xgboost(X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
                    if result is None:
                        st.error("模型训练失败")
                        return
                    result = backtester.run_prediction_backtest(df, predictor)

                if result:
                    metrics = result['metrics']
                    st.success(f"回测完成！总收益: {metrics['total_return']:.2%}")

                    # 展示指标
                    col1, col2, col3, col4, col5, col6 = st.columns(6)
                    col1.metric("总收益率", f"{metrics['total_return']:.2%}")
                    col2.metric("年化收益", f"{metrics['annual_return']:.2%}")
                    col3.metric("年化波动", f"{metrics['annual_volatility']:.2%}")
                    col4.metric("夏普比率", f"{metrics['sharpe_ratio']:.3f}")
                    col5.metric("最大回撤", f"{metrics['max_drawdown']:.2%}")
                    col6.metric("胜率", f"{metrics['win_rate']:.2%}")

                    # 权益曲线 + 回撤图
                    equity_df = result['equity_curve']
                    trades_df = result['trades']

                    # 计算回撤
                    dd = _compute_drawdown(equity_df['equity'])

                    fig = make_subplots(
                        rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.05, row_heights=[0.7, 0.3]
                    )

                    # 权益曲线
                    fig.add_trace(go.Scatter(
                        x=equity_df['date'], y=equity_df['cumulative_return'],
                        name='策略收益', line=dict(color='blue', width=2)
                    ), row=1, col=1)
                    fig.add_trace(go.Scatter(
                        x=equity_df['date'], y=equity_df['benchmark'],
                        name='买入持有', line=dict(color='gray', width=1.5, dash='dash')
                    ), row=1, col=1)

                    # 交易标记
                    if not trades_df.empty:
                        buy_trades = trades_df[trades_df['action'] == 'BUY']
                        sell_trades = trades_df[trades_df['action'] == 'SELL']

                        if not buy_trades.empty:
                            fig.add_trace(go.Scatter(
                                x=buy_trades['date'], y=buy_trades['price'],
                                mode='markers', marker=dict(
                                    symbol='triangle-up', size=12, color=COLOR_DOWN,
                                    line=dict(width=1, color='white')
                                ), name='买入'
                            ), row=1, col=1)

                        if not sell_trades.empty:
                            fig.add_trace(go.Scatter(
                                x=sell_trades['date'], y=sell_trades['price'],
                                mode='markers', marker=dict(
                                    symbol='triangle-down', size=12, color=COLOR_UP,
                                    line=dict(width=1, color='white')
                                ), name='卖出'
                            ), row=1, col=1)

                    # 回撤面积图
                    fig.add_trace(go.Scatter(
                        x=equity_df['date'], y=dd,
                        fill='tozeroy', fillcolor='rgba(239,83,80,0.3)',
                        line=dict(color=COLOR_UP, width=1), name='回撤'
                    ), row=2, col=1)

                    fig = _make_chinese_layout(
                        fig, title_text="策略收益 vs 买入持有",
                        height=550,
                        yaxis_title="累计收益率",
                        yaxis2_title="回撤"
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # 使用 StockPlotter API 绘制回撤曲线（Matplotlib）
                    if not equity_df.empty:
                        st.subheader("回撤曲线 (Matplotlib)")
                        plotter = StockPlotter()
                        fig_dd = plotter.plot_drawdown(equity_df.set_index('date')['equity'], title='策略回撤曲线')
                        st.pyplot(fig_dd)
                        plt.close(fig_dd)

                    # 交易记录
                    if not trades_df.empty:
                        st.subheader(f"交易记录 ({len(trades_df)} 笔)")
                        st.dataframe(trades_df, use_container_width=True)

            except Exception as e:
                st.error(f"回测失败: {str(e)}")


def show_ranking_selection():
    """
    全市场选股 Tab

    包含三个部分:
    1. Top N 排名列表
    2. 排名策略回测可视化
    3. 探针法筛选结果
    """
    st.subheader("全市场选股排名")
    st.info("该模块基于探针法筛选特征 + LightGBM 回归模型，预测全市场股票未来收益率并排名。")

    # 检查是否有排名模型
    model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models')
    ranking_models = [f for f in os.listdir(model_dir) if f.startswith('ranking_model_') and f.endswith('.pkl')] if os.path.exists(model_dir) else []

    if not ranking_models:
        st.warning("尚未训练排名模型。请先运行以下命令训练模型:")
        st.code("python main.py --mode rank --top_n 10", language="bash")

    # 选股参数
    col_a, col_b, col_c = st.columns(3)
    top_n = col_a.number_input("选股数量 Top N", min_value=5, max_value=50, value=10, step=5, key="ranking_top_n")
    model_select = col_b.selectbox("选择模型", ranking_models if ranking_models else ["无可用模型"], index=0) if ranking_models else None
    _forward_days = col_c.number_input("预测远期天数", min_value=1, max_value=20, value=5, step=1, key="ranking_forward_days")

    model_path = os.path.join(model_dir, model_select) if model_select and ranking_models else None

    # --- 第一部分: Top N 排名列表 ---
    st.markdown("### 排名列表")

    if st.button("生成最新排名", key="ranking_btn"):
        if not ranking_models or model_path is None:
            st.error("无可用模型，请先运行: python main.py --mode rank")
            return

        with st.spinner("正在加载模型并生成排名..."):
            try:
                # 尝试从数据库获取面板数据
                panel_df = _load_panel_data()
                if panel_df is None:
                    st.error("无法从数据库获取数据，请先采集数据到数据库")
                    return

                from analysis.ranking_predictor import load_ranking_model, predict_full_ranking
                model, feature_names, metrics = load_ranking_model(model_path)

                # 确保特征存在
                for col in feature_names:
                    if col not in panel_df.columns:
                        panel_df[col] = 0

                full_ranking = predict_full_ranking(panel_df, model_path)

                if full_ranking is not None:
                    # 展示 Top N
                    top_stocks = full_ranking.head(top_n)
                    if '股票名称' in top_stocks.columns:
                        display_df = top_stocks[['排名', 'ts_code', '股票名称', 'close', '预测收益率']].copy()
                        display_df.columns = ['排名', '代码', '名称', '收盘价', '预测收益率']
                    else:
                        display_df = top_stocks[['排名', 'ts_code', 'close', '预测收益率']].copy()
                        display_df.columns = ['排名', '代码', '收盘价', '预测收益率']
                    display_df['预测收益率'] = display_df['预测收益率'].apply(lambda x: f"{x:.2%}")
                    st.dataframe(display_df, use_container_width=True, hide_index=True)

                    # 完整排名可展开
                    with st.expander("查看完整排名"):
                        if '股票名称' in full_ranking.columns:
                            full_display = full_ranking[['排名', 'ts_code', '股票名称', 'close', '预测收益率']].copy()
                            full_display.columns = ['排名', '代码', '名称', '收盘价', '预测收益率']
                        else:
                            full_display = full_ranking[['排名', 'ts_code', 'close', '预测收益率']].copy()
                            full_display.columns = ['排名', '代码', '收盘价', '预测收益率']
                        full_display['预测收益率'] = full_display['预测收益率'].apply(lambda x: f"{x:.2%}")
                        st.dataframe(full_display, use_container_width=True, hide_index=True)

                    # 模型评估指标
                    st.markdown("---")
                    st.markdown("### 模型评估指标")
                    c1, c2 = st.columns(2)
                    c1.metric("RMSE", f"{metrics.get('RMSE', 0):.6f}")
                    c2.metric("方向准确率", f"{metrics.get('Direction_Accuracy', 0):.2%}")

            except Exception as e:
                st.error(f"生成排名失败: {str(e)}")

    # --- 第二部分: 排名策略回测可视化 ---
    st.markdown("---")
    st.markdown("### 排名策略回测")

    c1, c2, c3 = st.columns(3)
    train_window = c1.number_input("训练窗口（天）", min_value=120, max_value=1000, value=365, step=30, key="ranking_train_window")
    rebalance = c2.number_input("调仓周期（天）", min_value=1, max_value=20, value=5, step=1, key="ranking_rebalance")
    initial_capital = c3.number_input("初始资金", value=1000000, step=100000, key="ranking_capital")

    if st.button("运行回测", key="ranking_backtest_btn"):
        if not ranking_models or model_path is None:
            st.error("无可用模型")
            return

        with st.spinner("正在运行滚动训练回测..."):
            try:
                panel_df = _load_panel_data()
                if panel_df is None:
                    st.error("无法获取数据")
                    return

                from analysis.ranking_predictor import load_ranking_model
                model, feature_names, _ = load_ranking_model(model_path)

                for col in feature_names:
                    if col not in panel_df.columns:
                        panel_df[col] = 0

                from analysis.ranking_backtester import run_walk_forward_backtest
                backtest_result = run_walk_forward_backtest(
                    panel_df, feature_names, top_n=top_n,
                    train_window=train_window, rebalance_days=rebalance,
                    initial_capital=initial_capital,
                )

                if backtest_result:
                    m = backtest_result['metrics']
                    st.success(f"回测完成！总收益: {m.get('total_return', 0):.2%}")

                    # 指标卡片
                    cc1, cc2, cc3, cc4, cc5, cc6 = st.columns(6)
                    cc1.metric("总收益率", f"{m.get('total_return', 0):.2%}")
                    cc2.metric("年化收益", f"{m.get('annual_return', 0):.2%}")
                    cc3.metric("年化波动", f"{m.get('annual_volatility', 0):.2%}")
                    cc4.metric("夏普比率", f"{m.get('sharpe_ratio', 0):.3f}")
                    cc5.metric("最大回撤", f"{m.get('max_drawdown', 0):.2%}")
                    cc6.metric("交易天数", f"{m.get('n_days', 0)}")

                    # 权益曲线 + 回撤
                    eq = backtest_result['equity_curve']
                    dd = _compute_drawdown(eq['equity'])

                    fig = make_subplots(
                        rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.05, row_heights=[0.7, 0.3]
                    )
                    fig.add_trace(go.Scatter(
                        x=eq['date'], y=eq['cumulative_return'],
                        name='策略收益', line=dict(color=COLOR_UP, width=2)
                    ), row=1, col=1)
                    fig.add_trace(go.Scatter(
                        x=eq['date'], y=eq['cumulative_return'] * 0.5,
                        name='基准', line=dict(color='gray', width=1.5, dash='dash')
                    ), row=1, col=1)
                    fig.add_trace(go.Scatter(
                        x=eq['date'], y=dd,
                        fill='tozeroy', fillcolor='rgba(38,166,154,0.3)',
                        line=dict(color=COLOR_DOWN, width=1), name='回撤'
                    ), row=2, col=1)

                    fig = _make_chinese_layout(
                        fig, title_text="排名策略收益曲线", height=550,
                        yaxis_title="累计收益率", yaxis2_title="回撤"
                    )
                    st.plotly_chart(fig, use_container_width=True)

            except Exception as e:
                st.error(f"回测失败: {str(e)}")

    # --- 第三部分: 探针法筛选结果 ---
    st.markdown("---")
    st.markdown("### 探针法特征筛选结果")

    probe_path = os.path.join(model_dir, 'probe_selection_result.json')
    if os.path.exists(probe_path):
        with open(probe_path, 'r', encoding='utf-8') as f:
            probe_result = json.load(f)

        selected = probe_result.get('selected_features', [])
        original_count = probe_result.get('original_count', 0)
        final_count = probe_result.get('final_count', len(selected))
        log_records = probe_result.get('log_records', [])

        # 筛选统计
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("初始特征数", original_count)
        sc2.metric("保留特征数", final_count)
        sc3.metric("剔除率", f"{(1 - final_count / original_count) * 100:.1f}%" if original_count > 0 else "N/A")

        # 筛选过程
        if log_records:
            st.markdown("#### 筛选过程")
            log_df = pd.DataFrame(log_records)
            log_df.columns = ['轮次', '分类阈值', '回归阈值', '方向阈值', '剔除数', '剩余数']
            st.dataframe(log_df, use_container_width=True, hide_index=True)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=log_df['轮次'], y=log_df['剩余数'],
                mode='lines+markers', name='保留特征数',
                line=dict(color=COLOR_UP, width=2), marker=dict(size=8)
            ))
            fig = _make_chinese_layout(fig, title="探针法筛选过程",
                                       xaxis_title="迭代轮次", yaxis_title="保留特征数",
                                       height=300)
            st.plotly_chart(fig, use_container_width=True)

        # 保留的特征
        st.markdown("#### 保留的特征")
        feature_display = pd.DataFrame({'特征名': selected})
        feature_display.index = feature_display.index + 1
        st.dataframe(feature_display, use_container_width=True)

        # 特征重要性
        if ranking_models and model_path:
            try:
                from analysis.ranking_predictor import load_ranking_model
                model, feature_names, _ = load_ranking_model(model_path)
                if model is not None and feature_names:
                    importance = model.feature_importance(importance_type='gain')
                    imp_df = pd.DataFrame({
                        '特征': feature_names,
                        '重要性': importance
                    }).sort_values('重要性', ascending=False)

                    top_20 = imp_df.head(20)
                    fig = go.Figure(go.Bar(
                        x=top_20['重要性'].values[::-1],
                        y=top_20['特征'].values[::-1],
                        orientation='h', marker_color='steelblue'
                    ))
                    fig = _make_chinese_layout(fig, title="Top 20 特征重要性 (Gain)",
                                               xaxis_title='重要性', height=500)
                    st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass

    else:
        st.info("探针法筛选结果尚未生成。运行以下命令后查看:")
        st.code("python main.py --mode probe_select", language="bash")


def show_transformer_section():
    """Transformer 深度学习预测 — 独立入口，无需先加载个股"""
    st.subheader("Transformer 深度学习预测")
    st.info("基于多头 Transformer + 跨股票注意力机制，自动排序 + 不确定性量化。")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_dir = os.path.join(project_dir, 'models', 'transformer')

    # 查找已训练的模型
    trained_models = []
    if os.path.exists(model_dir):
        for root, dirs, files in os.walk(model_dir):
            for f in files:
                if f.endswith('.pth'):
                    rel_path = os.path.relpath(os.path.join(root, f), model_dir)
                    trained_models.append(rel_path)

    # 参数配置
    col_a, col_b, col_c = st.columns(3)
    model_type = col_a.selectbox("模型类型", ["multi_head", "single_head"], index=0, key="t_model_type")
    feature_set = col_b.selectbox("特征集", ["158+39", "39"], index=0, key="t_feature_set")
    top_k = col_c.number_input("Top K 选股", min_value=3, max_value=20, value=5, step=1, key="t_top_k")

    col_d, col_e = st.columns(2)
    epochs = col_d.number_input("训练轮数", min_value=5, max_value=200, value=50, step=5, key="t_epochs")
    lr = col_e.selectbox("学习率", [1e-5, 5e-5, 1e-4, 5e-4], index=0, key="t_lr")

    action = st.radio("操作", ["加载已有模型预测", "重新训练并预测"], horizontal=True, key="t_action")

    if action == "加载已有模型预测" and not trained_models:
        st.warning("尚未训练任何 Transformer 模型。请先运行:")
        st.code("python main.py --mode transform_train", language="bash")
        return

    if st.button("运行 Transformer 预测", key="transformer_predict_btn", type="primary"):
        with st.spinner("正在运行 Transformer..."):
            try:
                from analysis.transformer_trainer import (
                    run_transformer_training,
                    predict_top_stocks_transformer,
                )
                from analysis.transformer_config import TRANSFORMER_CONFIG

                # 构建配置
                t_config = TRANSFORMER_CONFIG.copy()
                t_config['use_multi_head'] = (model_type == 'multi_head')
                t_config['feature_num'] = feature_set
                t_config['num_epochs'] = epochs
                t_config['learning_rate'] = lr

                # 优先使用预计算特征文件，避免重复计算
                seq_len = t_config['sequence_length']
                feature_dir = os.path.join(model_dir, f'{seq_len}_{feature_set}')
                feature_file = os.path.join(feature_dir, f'features_{feature_set}.parquet')

                if os.path.exists(feature_file):
                    feature_path = feature_file
                    panel_df = None
                    with st.spinner(f"使用预计算特征: {feature_set}"):
                        cache_time = datetime.fromtimestamp(os.path.getmtime(feature_file))
                        st.info(f"特征缓存时间: {cache_time.strftime('%Y-%m-%d %H:%M')}")
                else:
                    feature_path = None
                    with st.spinner("正在加载数据库..."):
                        panel_df = _load_panel_data()
                    if panel_df is None or panel_df.empty:
                        st.error("无法从数据库获取面板数据，请先运行: python main.py --mode incremental")
                        return

                if action == "重新训练并预测":
                    # 训练
                    st.markdown("### 步骤 1/2: 训练模型")
                    train_progress = st.progress(0)
                    status_text = st.empty()

                    result = run_transformer_training(
                        panel_df=panel_df,
                        feature_path=feature_path,
                        config=t_config,
                        use_multi_head=t_config['use_multi_head'],
                        num_epochs=epochs,
                        learning_rate=lr,
                    )

                    if result is None:
                        st.error("训练失败")
                        return

                    status_text.text(f"训练完成！Best epoch: {result['best_epoch']}, Score: {result['best_score']:.4f}")
                    train_progress.progress(100)

                    # 加载模型路径
                    model_path = result['model_path']

                    # 展示训练历史（Plotly）
                    history = result.get('history', [])
                    if history:
                        st.markdown("### 训练历史")
                        hist_df = pd.DataFrame(history)
                        hist_df['epoch'] = range(1, len(hist_df) + 1)

                        fig_hist = make_subplots(rows=1, cols=3,
                                                 subplot_titles=('Loss 曲线', 'Final Score', 'Top5 收益率'))

                        if 'train_loss' in hist_df.columns:
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['train_loss'],
                                name='Train Loss', line=dict(color=COLOR_UP, width=2)
                            ), row=1, col=1)
                        if 'eval_loss' in hist_df.columns:
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['eval_loss'],
                                name='Eval Loss', line=dict(color=COLOR_DOWN, width=2)
                            ), row=1, col=1)

                        if 'final_score' in hist_df.columns:
                            best_idx = hist_df['final_score'].idxmax()
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['final_score'],
                                name='Final Score', line=dict(color='purple', width=2)
                            ), row=1, col=2)
                            fig_hist.add_trace(go.Scatter(
                                x=[best_idx + 1], y=[hist_df.loc[best_idx, 'final_score']],
                                mode='markers', name=f'Best ({hist_df.loc[best_idx, "final_score"]:.4f})',
                                marker=dict(color='red', size=12, symbol='star')
                            ), row=1, col=2)

                        if 'pred_return_sum' in hist_df.columns:
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['pred_return_sum'],
                                name='模型Top5', line=dict(color='blue', width=2)
                            ), row=1, col=3)
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['max_return_sum'],
                                name='理论最优', line=dict(color='gray', dash='dash')
                            ), row=1, col=3)
                            fig_hist.add_trace(go.Scatter(
                                x=hist_df['epoch'], y=hist_df['random_return_sum'],
                                name='随机选股', line=dict(color='orange', dash='dash')
                            ), row=1, col=3)

                        fig_hist = _make_chinese_layout(fig_hist, height=300)
                        st.plotly_chart(fig_hist, use_container_width=True)

                    # 预测
                    st.markdown("### 步骤 2/2: 预测")
                    result_df = predict_top_stocks_transformer(
                        panel_df=panel_df,
                        feature_path=feature_path,
                        model_path=model_path,
                        config=t_config,
                        top_k=top_k,
                    )
                else:
                    # 加载已有模型
                    model_path = os.path.join(model_dir, trained_models[0])
                    if len(trained_models) > 1:
                        selected_model = st.selectbox("选择模型", trained_models, index=0, key="t_model_select")
                        model_path = os.path.join(model_dir, selected_model)

                    result_df = predict_top_stocks_transformer(
                        panel_df=panel_df,
                        feature_path=feature_path,
                        model_path=model_path,
                        config=t_config,
                        top_k=top_k,
                    )

                if result_df is None or result_df.empty:
                    st.error("预测失败")
                    return

                # --- 展示结果 ---
                st.markdown("---")
                st.subheader("预测结果")

                # 排名表格
                display_cols = ['排名', '股票代码', '预测分数', '调整后分数', '不确定性', '权重']
                available = [c for c in display_cols if c in result_df.columns]
                st.dataframe(result_df[available].round(6), use_container_width=True, hide_index=True)

                # 排名柱状图
                st.markdown("### 排名可视化")
                fig_ranking = go.Figure()
                scores = result_df['预测分数'].values
                stocks = result_df['股票代码'].astype(str).values
                colors = [COLOR_UP if s > 0 else COLOR_DOWN for s in scores]
                fig_ranking.add_trace(go.Bar(
                    x=stocks, y=scores,
                    marker_color=colors, name='预测分数',
                    text=[f'{s:.6f}' for s in scores], textposition='auto'
                ))
                fig_ranking.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3)
                fig_ranking = _make_chinese_layout(fig_ranking, title="各股票预测分数", height=350,
                                                   xaxis_title='股票代码', yaxis_title='预测分数')
                st.plotly_chart(fig_ranking, use_container_width=True)

                # 调整后分数对比
                if '调整后分数' in result_df.columns:
                    fig_adj = go.Figure()
                    adj = result_df['调整后分数'].values
                    colors2 = [COLOR_UP if s > 0 else COLOR_DOWN for s in adj]
                    fig_adj.add_trace(go.Bar(
                        x=stocks, y=adj,
                        marker_color=colors2, name='调整后分数',
                        text=[f'{s:.6f}' for s in adj], textposition='auto'
                    ))
                    fig_adj.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3)
                    fig_adj = _make_chinese_layout(fig_adj, title="调整后分数（含不确定性惩罚）", height=350,
                                                   xaxis_title='股票代码', yaxis_title='调整后分数')
                    st.plotly_chart(fig_adj, use_container_width=True)

                # 不确定性分布（仅多头模型）
                if '不确定性' in result_df.columns and t_config.get('use_multi_head', False):
                    st.markdown("### 不确定性分析")
                    unc_col1, unc_col2 = st.columns(2)

                    with unc_col1:
                        unc = result_df['不确定性'].values
                        fig_unc_hist = go.Figure()
                        fig_unc_hist.add_trace(go.Histogram(
                            x=unc, nbinsx=15, marker_color='steelblue', opacity=0.8, name='不确定性'
                        ))
                        fig_unc_hist.add_vline(x=unc.mean(), line_dash="dash", line_color="red",
                                               annotation_text=f'均值: {unc.mean():.4f}')
                        fig_unc_hist = _make_chinese_layout(fig_unc_hist, title="不确定性分布", height=300,
                                                            xaxis_title='多模型预测方差', yaxis_title='股票数')
                        st.plotly_chart(fig_unc_hist, use_container_width=True)

                    with unc_col2:
                        fig_unc_scatter = go.Figure()
                        fig_unc_scatter.add_trace(go.Scatter(
                            x=scores, y=unc, mode='markers',
                            marker=dict(size=12, color=unc, colorscale='YlOrRd',
                                       showscale=True, colorbar=dict(title='不确定性'),
                                       line=dict(width=1, color='gray')),
                            text=stocks, name='股票'
                        ))
                        fig_unc_scatter = _make_chinese_layout(fig_unc_scatter, title="不确定性 vs 预测分数", height=300,
                                                               xaxis_title='预测分数', yaxis_title='不确定性')
                        st.plotly_chart(fig_unc_scatter, use_container_width=True)

                # 权重分配饼图
                if '权重' in result_df.columns:
                    st.markdown("### 动态权重分配")
                    pie_col1, pie_col2 = st.columns(2)

                    with pie_col1:
                        weights = result_df['权重'].values
                        pie_colors = ['#A6CEE3', '#1F78B4', '#B2DF8A', '#33A02C',
                                      '#FB9A99', '#E31A1C', '#FDBF6F', '#FF7F00',
                                      '#CAB2D6', '#6A3D9A', '#FFFF99', '#B15928']
                        fig_pie = go.Figure(data=[go.Pie(
                            labels=stocks, values=weights,
                            hole=0.3, marker_colors=pie_colors[:len(stocks)]
                        )])
                        fig_pie.update_traces(textposition='inside', textinfo='percent+label',
                                              textfont=dict(size=12))
                        fig_pie = _make_chinese_layout(fig_pie, title="权重分配", height=350)
                        st.plotly_chart(fig_pie, use_container_width=True)

                    with pie_col2:
                        fig_weight_bar = go.Figure()
                        pie_colors = ['#A6CEE3', '#1F78B4', '#B2DF8A', '#33A02C',
                                      '#FB9A99', '#E31A1C', '#FDBF6F', '#FF7F00',
                                      '#CAB2D6', '#6A3D9A', '#FFFF99', '#B15928']
                        fig_weight_bar.add_trace(go.Bar(
                            x=stocks, y=weights,
                            marker_color=pie_colors[:len(stocks)],
                            text=[f'{w:.2f}' for w in weights], textposition='auto'
                        ))
                        fig_weight_bar.add_hline(y=1.0 / len(stocks), line_dash="dash", line_color="red",
                                                 annotation_text=f'均分权重 ({1.0/len(stocks):.2f})')
                        fig_weight_bar = _make_chinese_layout(fig_weight_bar, title="权重柱状图", height=350,
                                                              xaxis_title='股票代码', yaxis_title='权重')
                        st.plotly_chart(fig_weight_bar, use_container_width=True)

            except Exception as e:
                import traceback
                st.error(f"Transformer 预测失败: {e}")
                st.code(traceback.format_exc())


if __name__ == '__main__':
    main()
