"""
静态图表绘制（Matplotlib/Seaborn/mplfinance）
"""
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import pandas as pd
import numpy as np
from scipy import stats
from config.logging_config import get_logger
logger = get_logger(__name__)

# A股配色：涨=红，跌=绿
COLOR_UP = '#EF5350'
COLOR_DOWN = '#26A69A'

# 中文显示设置
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'FangSong']
matplotlib.rcParams['axes.unicode_minus'] = False
matplotlib.rcParams['font.family'] = 'sans-serif'


class StockPlotter:
    """股票图表绘制器"""

    def __init__(self, style='seaborn-v0_8-whitegrid'):
        """
        初始化绘图器

        Args:
            style: matplotlib样式
        """
        plt.style.use(style)
        # seaborn style 会重置 rcParams，需要重新设置中文
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'FangSong']
        plt.rcParams['axes.unicode_minus'] = False
        logger.info("图表绘制器初始化完成")

    def plot_candlestick(self, df, title='股票K线图', save_path=None):
        """
        绘制真正的OHLC蜡烛图（使用mplfinance）

        Args:
            df: 包含OHLC数据的DataFrame（需有trade_date, open, high, low, close, vol列）
            title: 图表标题
            save_path: 保存路径
        """
        required_cols = ['open', 'high', 'low', 'close']
        if not all(c in df.columns for c in required_cols):
            logger.warning("缺少OHLC列，使用折线图fallback")
            self._plot_candlestick_fallback(df, title, save_path)
            return

        try:
            import mplfinance as mpf
        except ImportError:
            logger.warning("未安装mplfinance，使用折线图fallback")
            self._plot_candlestick_fallback(df, title, save_path)
            return

        # 准备mplfinance格式数据
        plot_df = df.copy()
        if 'trade_date' in plot_df.columns:
            plot_df = plot_df.set_index('trade_date')
        if not isinstance(plot_df.index, pd.DatetimeIndex):
            plot_df.index = pd.to_datetime(plot_df.index)

        mpf_columns = {
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close'
        }
        rename_map = {v: k for k, v in mpf_columns.items() if v in plot_df.columns}
        plot_df = plot_df.rename(columns=rename_map)

        # 添加均线
        mav = ()
        if 'ma5' in df.columns:
            mav = mav + (5,)
        if 'ma20' in df.columns:
            mav = mav + (20,)

        # 添加成交量
        has_volume = 'Volume' in plot_df.columns or 'vol' in plot_df.columns
        if 'vol' in plot_df.columns and 'Volume' not in plot_df.columns:
            plot_df = plot_df.rename(columns={'vol': 'Volume'})
            has_volume = True

        # 自定义颜色样式
        mc = mpf.make_marketcolors(
            up=COLOR_UP, down=COLOR_DOWN,
            edge='inherit',
            wick='inherit',
            volume='in'
        )
        s = mpf.make_mpf_style(marketcolors=mc, figcolor='white', gridcolor='#eeeeee', gridstyle='-')

        kwargs = dict(
            type='candle',
            title=title,
            ylabel='价格',
            style=s,
            show_nontrading=False
        )
        if mav:
            kwargs['mav'] = mav
        if has_volume:
            kwargs['volume'] = True
            kwargs['ylabel_lower'] = '成交量'

        if save_path:
            kwargs['savefig'] = save_path
            kwargs['tight_layout'] = True

        fig, axes = mpf.plot(plot_df[['Open', 'High', 'Low', 'Close', 'Volume'] if has_volume else
                                      ['Open', 'High', 'Low', 'Close']],
                             returnfig=True, **kwargs)

        if not save_path:
            plt.show()
        else:
            logger.info(f"图表保存到: {save_path}")

    def _plot_candlestick_fallback(self, df, title, save_path):
        """当OHLC列不全或mplfinance不可用时的fallback"""
        fig, ax = plt.subplots(figsize=(14, 7))
        ax.plot(df['trade_date'], df['close'], label='收盘价', linewidth=1.5)
        if 'ma5' in df.columns:
            ax.plot(df['trade_date'], df['ma5'], label='MA5', linewidth=1)
        if 'ma20' in df.columns:
            ax.plot(df['trade_date'], df['ma20'], label='MA20', linewidth=1)
        ax.set_xlabel('日期')
        ax.set_ylabel('价格')
        ax.set_title(title)
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.xticks(rotation=45)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_bollinger_bands(self, df, title='布林带指标', save_path=None):
        """
        绘制布林带指标

        Args:
            df: 包含close, bb_upper, bb_middle, bb_lower, bb_width列的DataFrame
            title: 图表标题
            save_path: 保存路径
        """
        required = ['close', 'bb_upper', 'bb_lower', 'bb_middle']
        if not all(c in df.columns for c in required):
            logger.warning("缺少布林带列，跳过绘图")
            return

        fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True,
                                 gridspec_kw={'height_ratios': [3, 1]})

        # 上图：价格 + 布林带
        axes[0].fill_between(df['trade_date'], df['bb_upper'], df['bb_lower'],
                             alpha=0.15, color='orange', label='布林带区间')
        axes[0].plot(df['trade_date'], df['close'], label='收盘价', linewidth=1.5, color='black')
        axes[0].plot(df['trade_date'], df['bb_upper'], label='上轨', linewidth=1, color='orange', linestyle='-')
        axes[0].plot(df['trade_date'], df['bb_middle'], label='中轨', linewidth=1, color='blue', linestyle='--')
        axes[0].plot(df['trade_date'], df['bb_lower'], label='下轨', linewidth=1, color='orange', linestyle='-')
        axes[0].set_ylabel('价格')
        axes[0].set_title(title)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 下图：布林带宽度
        if 'bb_width' in df.columns:
            axes[1].plot(df['trade_date'], df['bb_width'], label='BB宽度', linewidth=1.5, color='purple')
            axes[1].fill_between(df['trade_date'], df['bb_width'], alpha=0.3, color='purple')
            axes[1].axhline(df['bb_width'].mean(), color='red', linestyle='--', alpha=0.5,
                           label=f'均值: {df["bb_width"].mean():.4f}')
            axes[1].set_ylabel('BB宽度')
            axes[1].set_xlabel('日期')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)

        plt.xticks(rotation=45)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_kdj(self, df, title='KDJ指标', save_path=None):
        """
        绘制KDJ指标

        Args:
            df: 包含kdj_k, kdj_d, kdj_j列的DataFrame
            title: 图表标题
            save_path: 保存路径
        """
        if not all(c in df.columns for c in ['kdj_k', 'kdj_d', 'kdj_j']):
            logger.warning("缺少KDJ列，跳过绘图")
            return

        fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True,
                                 gridspec_kw={'height_ratios': [2, 1]})

        # 上图：收盘价
        axes[0].plot(df['trade_date'], df['close'], label='收盘价', linewidth=1.5, color='black')
        axes[0].set_ylabel('价格')
        axes[0].set_title(title)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 下图：KDJ
        axes[1].plot(df['trade_date'], df['kdj_k'], label='K', linewidth=1.2, color='blue')
        axes[1].plot(df['trade_date'], df['kdj_d'], label='D', linewidth=1.2, color='orange')
        axes[1].plot(df['trade_date'], df['kdj_j'], label='J', linewidth=1.2, color='purple')
        axes[1].axhline(y=80, color=COLOR_DOWN, linestyle='--', alpha=0.6, label='超买线(80)')
        axes[1].axhline(y=20, color=COLOR_UP, linestyle='--', alpha=0.6, label='超卖线(20)')
        axes[1].fill_between(df['trade_date'], 80, 100, alpha=0.1, color=COLOR_DOWN)
        axes[1].fill_between(df['trade_date'], 0, 20, alpha=0.1, color=COLOR_UP)
        axes[1].set_ylabel('KDJ')
        axes[1].set_xlabel('日期')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.xticks(rotation=45)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_drawdown(self, prices, title='回撤曲线', save_path=None):
        """
        绘制回撤曲线

        Args:
            prices: 价格序列（pd.Series或DataFrame中的列）
            title: 图表标题
            save_path: 保存路径
        """
        if isinstance(prices, pd.DataFrame):
            prices = prices['close'] if 'close' in prices.columns else prices.iloc[:, 0]

        prices = prices.dropna()
        cumulative = prices / prices.iloc[0]
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max

        fig, ax = plt.subplots(figsize=(14, 5))
        ax.fill_between(drawdown.index, drawdown.values, 0,
                        color=COLOR_UP, alpha=0.4)
        ax.plot(drawdown.index, drawdown.values, color=COLOR_UP, linewidth=1)
        ax.axhline(y=0, color='black', linestyle='-', alpha=0.3)

        # 标注最大回撤
        min_idx = drawdown.idxmin()
        min_val = drawdown.min()
        ax.annotate(f'最大回撤: {min_val:.2%}',
                    xy=(min_idx, min_val),
                    xytext=(min_idx, min_val * 0.5),
                    arrowprops=dict(arrowstyle='->', color='red'),
                    fontsize=11, color='red', fontweight='bold')

        ax.set_ylabel('回撤')
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_rolling_volatility(self, df, windows=(20, 60, 120), title='滚动波动率', save_path=None):
        """
        绘制多窗口滚动波动率

        Args:
            df: 包含return_1d或close列的DataFrame（或已有volatility_20d/60d/120d列）
            windows: 滚动窗口列表
            title: 图表标题
            save_path: 保存路径
        """
        fig, ax = plt.subplots(figsize=(14, 5))

        # 优先使用已有的 volatility 列
        volatility_cols = {20: 'volatility_20d', 60: 'volatility_60d', 120: 'volatility_120d'}
        use_existing = all(volatility_cols.get(w) in df.columns for w in windows if w in volatility_cols)

        if use_existing:
            for w in windows:
                col = volatility_cols.get(w)
                if col and col in df.columns:
                    ax.plot(df['trade_date'], df[col], label=f'{w}日年化波动率', linewidth=1.2)
        else:
            if 'return_1d' not in df.columns:
                logger.warning("缺少return_1d列，无法计算滚动波动率")
                return
            returns = df['return_1d'].dropna()
            for w in windows:
                rolling_vol = returns.rolling(window=w).std() * np.sqrt(252)
                ax.plot(df['trade_date'].iloc[-len(rolling_vol):], rolling_vol,
                       label=f'{w}日年化波动率', linewidth=1.2)

        # 整体波动率参考线
        if 'return_1d' in df.columns:
            overall_vol = df['return_1d'].std() * np.sqrt(252)
            ax.axhline(y=overall_vol, color='red', linestyle='--', alpha=0.5,
                      label=f'整体年化波动率: {overall_vol:.2%}')

        ax.set_ylabel('波动率')
        ax.set_title(title)
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.xticks(rotation=45)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_monthly_returns_heatmap(self, df, title='月度收益率热力图', save_path=None):
        """
        绘制月度收益率热力图

        Args:
            df: 包含trade_date和return_1d（或close）列的DataFrame
            title: 图表标题
            save_path: 保存路径
        """
        if 'return_1d' not in df.columns:
            if 'close' in df.columns:
                df = df.copy()
                df['return_1d'] = df['close'].pct_change()
            else:
                logger.warning("缺少return_1d和close列，无法绘制热力图")
                return

        # 构建年×月矩阵
        returns_df = df[['trade_date', 'return_1d']].dropna().copy()
        returns_df['trade_date'] = pd.to_datetime(returns_df['trade_date'])
        returns_df['year'] = returns_df['trade_date'].dt.year
        returns_df['month'] = returns_df['trade_date'].dt.month
        returns_df['monthly_return'] = returns_df['return_1d']

        # 按月聚合收益率（简单求和近似月收益）
        monthly = returns_df.groupby(['year', 'month'])['return_1d'].sum().unstack(fill_value=0)
        monthly = monthly.rename(columns={i: f'{i}月' for i in range(1, 13)})

        plt.figure(figsize=(max(10, len(monthly) * 0.8), max(5, len(monthly.columns) * 0.5)))

        # 自定义红涨绿跌 colormap
        cmap = sns.diverging_palette(10, 120, as_cmap=True)

        sns.heatmap(monthly * 100, annot=True, fmt='.1f', cmap=cmap, center=0,
                   linewidths=0.5, cbar_kws={'label': '月收益率 (%)'},
                   vmin=-15, vmax=15)
        plt.title(title, fontsize=14)
        plt.xlabel('')
        plt.ylabel('')
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_distribution(self, df, column='return_1d', title='收益率分布', save_path=None):
        """
        绘制分布图（直方图 + KDE + 正态拟合）

        Args:
            df: 数据DataFrame
            column: 分析的列
            title: 图表标题
            save_path: 保存路径
        """
        data = df[column].dropna()
        if data.empty:
            logger.warning(f"列 {column} 无有效数据")
            return

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # 左图：直方图 + KDE + 正态拟合
        sns.histplot(data=data, x=data.values, bins=50, kde=True, ax=axes[0],
                    color='steelblue', edgecolor='black', alpha=0.7)

        # 正态拟合曲线
        mu, std = data.mean(), data.std()
        x_norm = np.linspace(data.min(), data.max(), 200)
        y_norm = stats.norm.pdf(x_norm, mu, std) * len(data) * (data.max() - data.min()) / 50
        axes[0].plot(x_norm, y_norm, 'r--', linewidth=2, label=f'正态拟合 (μ={mu:.4f}, σ={std:.4f})')

        axes[0].axvline(data.mean(), color='red', linestyle='--', alpha=0.7, label=f'均值: {data.mean():.4f}')
        axes[0].axvline(data.median(), color='green', linestyle='--', alpha=0.7, label=f'中位数: {data.median():.4f}')
        axes[0].set_xlabel(column)
        axes[0].set_ylabel('频数')
        axes[0].set_title(f'{column} 分布')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 标注偏度和峰度
        skew = data.skew()
        kurt = data.kurt()
        axes[0].text(0.02, 0.95, f'偏度: {skew:.4f}\n峰度: {kurt:.4f}',
                    transform=axes[0].transAxes, fontsize=10,
                    verticalalignment='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        # 右图：箱线图 + 小提琴图
        axes[1].violinplot(data.values, showmeans=True, showmedians=True)
        axes[1].set_ylabel(column)
        axes[1].set_title(f'{column} 分布详情')
        axes[1].grid(True, alpha=0.3, axis='y')

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_correlation_heatmap(self, df, columns=None, title='相关性热力图', save_path=None):
        """
        绘制相关性热力图

        Args:
            df: 数据DataFrame
            columns: 需要分析的列
            title: 图表标题
            save_path: 保存路径
        """
        if columns is None:
            columns = df.select_dtypes(include=[np.number]).columns

        corr_matrix = df[columns].corr()

        plt.figure(figsize=(12, 10))
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool))
        cmap = sns.diverging_palette(10, 120, as_cmap=True)
        sns.heatmap(corr_matrix, mask=mask, annot=False, fmt='.2f', cmap=cmap,
                   square=True, linewidths=0.5, cbar_kws={"shrink": 0.5})
        plt.title(title, fontsize=14)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")

        plt.show()

    def plot_technical_indicators(self, df, title='技术指标图', save_path=None):
        """
        绘制技术指标图

        Args:
            df: 包含技术指标的DataFrame
            title: 图表标题
            save_path: 保存路径
        """
        fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True)

        # 1. 价格和均线
        axes[0].plot(df['trade_date'], df['close'], label='收盘价', linewidth=1.5)
        if 'ma5' in df.columns:
            axes[0].plot(df['trade_date'], df['ma5'], label='MA5', linewidth=1)
        if 'ma20' in df.columns:
            axes[0].plot(df['trade_date'], df['ma20'], label='MA20', linewidth=1)
        axes[0].set_ylabel('价格')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 2. MACD
        if 'macd_dif' in df.columns:
            axes[1].plot(df['trade_date'], df['macd_dif'], label='DIF', linewidth=1)
            axes[1].plot(df['trade_date'], df['macd_dea'], label='DEA', linewidth=1)
            axes[1].bar(df['trade_date'], df['macd_hist'], label='MACD', alpha=0.3)
            axes[1].set_ylabel('MACD')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)

        # 3. RSI
        if 'rsi' in df.columns:
            axes[2].plot(df['trade_date'], df['rsi'], label='RSI', color='purple', linewidth=1)
            axes[2].axhline(y=70, color=COLOR_DOWN, linestyle='--', alpha=0.5, label='超买线')
            axes[2].axhline(y=30, color=COLOR_UP, linestyle='--', alpha=0.5, label='超卖线')
            axes[2].set_ylabel('RSI')
            axes[2].legend()
            axes[2].grid(True, alpha=0.3)

        # 4. 成交量
        if 'vol' in df.columns:
            colors = [COLOR_UP if df['close'].iloc[i] >= df['open'].iloc[i] else COLOR_DOWN
                     for i in range(len(df))]
            axes[3].bar(df['trade_date'], df['vol'], color=colors, alpha=0.6)
            axes[3].set_ylabel('成交量')
            axes[3].set_xlabel('日期')
            axes[3].grid(True, alpha=0.3)

        plt.suptitle(title, fontsize=16, y=0.995)
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")

        plt.show()

    def plot_prediction_results(self, y_true, y_pred, title='预测结果对比', save_path=None):
        """
        绘制预测结果对比图

        Args:
            y_true: 真实值
            y_pred: 预测值
            title: 图表标题
            save_path: 保存路径
        """
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # 散点图
        axes[0].scatter(y_true, y_pred, alpha=0.5, s=10, color='steelblue')
        min_val = min(y_true.min(), y_pred.min())
        max_val = max(y_true.max(), y_pred.max())
        axes[0].plot([min_val, max_val], [min_val, max_val], 'r--', linewidth=2)
        axes[0].set_xlabel('真实值')
        axes[0].set_ylabel('预测值')
        axes[0].set_title('预测vs真实')
        axes[0].grid(True, alpha=0.3)

        # 折线图（前100个样本）
        n_samples = min(100, len(y_true))
        axes[1].plot(range(n_samples), y_true[:n_samples], label='真实值', linewidth=1.5)
        axes[1].plot(range(n_samples), y_pred[:n_samples], label='预测值', linewidth=1.5)
        axes[1].set_xlabel('样本')
        axes[1].set_ylabel('值')
        axes[1].set_title('预测趋势对比（前100个样本）')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")

        plt.show()

    def plot_feature_importance(self, df_importance, top_n=15, title='特征重要性', save_path=None):
        """
        绘制特征重要性图

        Args:
            df_importance: 特征重要性DataFrame
            top_n: 显示前N个特征
            title: 图表标题
            save_path: 保存路径
        """
        top_features = df_importance.head(top_n)

        plt.figure(figsize=(10, 8))
        plt.barh(range(len(top_features)), top_features['importance'].values, color='steelblue')
        plt.yticks(range(len(top_features)), top_features['feature'].values)
        plt.xlabel('重要性')
        plt.title(title)
        plt.gca().invert_yaxis()
        plt.grid(True, alpha=0.3, axis='x')
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")

        plt.show()

    # ============================================================
    # Transformer 专属可视化
    # ============================================================

    def plot_transformer_training_history(self, history, title='Transformer 训练历史', save_path=None):
        """
        绘制 Transformer 训练 Loss 和 Score 曲线。

        Args:
            history: 包含 train_loss, eval_loss, final_score 等历史记录的列表或字典
            title: 图表标题
            save_path: 保存路径
        """
        if isinstance(history, dict):
            history_list = [history]
        else:
            history_list = history

        epochs = range(1, len(history_list) + 1)

        fig, axes = plt.subplots(1, 3, figsize=(18, 5))

        # 训练/验证 Loss
        if 'train_loss' in history_list[0]:
            train_loss = [h.get('train_loss', 0) for h in history_list]
            eval_loss = [h.get('eval_loss', 0) for h in history_list]
            axes[0].plot(epochs, train_loss, label='Train Loss', color=COLOR_UP, linewidth=1.5)
            axes[0].plot(epochs, eval_loss, label='Eval Loss', color=COLOR_DOWN, linewidth=1.5)
            axes[0].set_xlabel('Epoch')
            axes[0].set_ylabel('Loss')
            axes[0].set_title('训练/验证 Loss')
            axes[0].legend()
            axes[0].grid(True, alpha=0.3)

        # Final Score
        if 'final_score' in history_list[0]:
            train_scores = [h.get('train_final_score', h.get('final_score', 0)) for h in history_list]
            eval_scores = [h.get('eval_final_score', h.get('final_score', 0)) for h in history_list]
            axes[1].plot(epochs, train_scores, label='Train Final Score', color='purple', linewidth=1.5)
            axes[1].plot(epochs, eval_scores, label='Eval Final Score', color='green', linewidth=2)
            best_idx = np.argmax(eval_scores)
            axes[1].scatter(best_idx + 1, eval_scores[best_idx], color='red', s=100, zorder=5,
                          label=f'Best Eval: {eval_scores[best_idx]:.4f}')
            axes[1].axhline(y=0, color='gray', linestyle='--', alpha=0.5)
            axes[1].set_xlabel('Epoch')
            axes[1].set_ylabel('Score')
            axes[1].set_title('Final Score (越高越好)')
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)

        # 预测Top5收益
        if 'pred_return_sum' in history_list[0]:
            pred_ret = [h.get('pred_return_sum', 0) for h in history_list]
            max_ret = [h.get('max_return_sum', 0) for h in history_list]
            random_ret = [h.get('random_return_sum', 0) for h in history_list]
            axes[2].plot(epochs, pred_ret, label='模型预测Top5', color='blue', linewidth=1.5)
            axes[2].plot(epochs, max_ret, label='理论最优Top5', color='gray', linestyle='--', linewidth=1)
            axes[2].plot(epochs, random_ret, label='随机选股', color='orange', linestyle='--', linewidth=1)
            axes[2].set_xlabel('Epoch')
            axes[2].set_ylabel('收益率')
            axes[2].set_title('Top 5 收益率对比')
            axes[2].legend()
            axes[2].grid(True, alpha=0.3)

        plt.suptitle(title, fontsize=14, y=1.02)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_top_n_ranking(self, result_df, title='Transformer 股票排名 Top N', save_path=None):
        """
        绘制 Top N 股票预测排名柱状图。

        Args:
            result_df: 预测结果DataFrame，需包含 '股票代码', '预测分数', '调整后分数'
            title: 图表标题
            save_path: 保存路径
        """
        if '股票代码' not in result_df.columns or '预测分数' not in result_df.columns:
            logger.warning("结果DataFrame缺少必要列")
            return

        df = result_df.copy()
        df = df.sort_values('排名' if '排名' in df.columns else '预测分数', ascending=True)

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        # 左图：预测分数柱状图
        stocks = df['股票代码'].astype(str).tolist()
        scores = df['预测分数'].values
        colors = [COLOR_UP if s > 0 else COLOR_DOWN for s in scores]
        bars = axes[0].bar(range(len(stocks)), scores, color=colors, edgecolor='gray', linewidth=0.5)

        # 在柱子上标注数值
        for i, (bar, score) in enumerate(zip(bars, scores)):
            axes[0].text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f'{score:.6f}', ha='center', va='bottom' if score > 0 else 'top',
                        fontsize=9)

        axes[0].set_xticks(range(len(stocks)))
        axes[0].set_xticklabels(stocks, rotation=30)
        axes[0].set_ylabel('预测分数')
        axes[0].set_title('各股票预测分数')
        axes[0].axhline(y=0, color='gray', linestyle='-', alpha=0.3)
        axes[0].grid(True, alpha=0.3, axis='y')

        # 右图：调整后分数（含不确定性）柱状图
        if '调整后分数' in df.columns:
            adj_scores = df['调整后分数'].values
            colors2 = [COLOR_UP if s > 0 else COLOR_DOWN for s in adj_scores]
            bars2 = axes[1].bar(range(len(stocks)), adj_scores, color=colors2, edgecolor='gray', linewidth=0.5)

            for i, (bar, score) in enumerate(zip(bars2, adj_scores)):
                axes[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                            f'{score:.6f}', ha='center', va='bottom' if score > 0 else 'top',
                            fontsize=9)

            axes[1].set_xticks(range(len(stocks)))
            axes[1].set_xticklabels(stocks, rotation=30)
            axes[1].set_ylabel('调整后分数')
            axes[1].set_title('调整后分数（含不确定性惩罚）')
            axes[1].axhline(y=0, color='gray', linestyle='-', alpha=0.3)
            axes[1].grid(True, alpha=0.3, axis='y')

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_uncertainty_distribution(self, result_df, title='模型不确定性分布', save_path=None):
        """
        绘制多头模型的不确定性分布图。

        Args:
            result_df: 预测结果DataFrame，需包含 '不确定性', '调整后分数', '股票代码'
            title: 图表标题
            save_path: 保存路径
        """
        if '不确定性' not in result_df.columns:
            logger.warning("结果中不包含不确定性数据")
            return

        fig, axes = plt.subplots(1, 3, figsize=(16, 5))

        # 左图：不确定性直方图
        uncertainty = result_df['不确定性'].values
        axes[0].hist(uncertainty, bins=20, color='steelblue', edgecolor='white', alpha=0.8)
        axes[0].axvline(uncertainty.mean(), color='red', linestyle='--', linewidth=2,
                       label=f'均值: {uncertainty.mean():.4f}')
        axes[0].axvline(np.median(uncertainty), color='orange', linestyle='--', linewidth=2,
                       label=f'中位数: {np.median(uncertainty):.4f}')
        axes[0].set_xlabel('不确定性（多模型预测方差）')
        axes[0].set_ylabel('股票数量')
        axes[0].set_title('不确定性分布')
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # 中图：不确定性 vs 预测分数 散点图
        if '预测分数' in result_df.columns:
            scores = result_df['预测分数'].values
            scatter = axes[1].scatter(scores, uncertainty, alpha=0.6, s=50, c=uncertainty,
                                     cmap='YlOrRd', edgecolors='gray', linewidth=0.5)
            plt.colorbar(scatter, ax=axes[1], label='不确定性')
            axes[1].set_xlabel('预测分数')
            axes[1].set_ylabel('不确定性')
            axes[1].set_title('不确定性 vs 预测分数')
            axes[1].grid(True, alpha=0.3)

        # 右图：各股票不确定性柱状图
        stocks = result_df['股票代码'].astype(str).tolist()
        axes[2].barh(range(len(stocks)), uncertainty, color='coral', edgecolor='gray', linewidth=0.5)
        axes[2].set_yticks(range(len(stocks)))
        axes[2].set_yticklabels(stocks)
        axes[2].set_xlabel('不确定性')
        axes[2].set_title('各股票不确定性')
        axes[2].invert_yaxis()
        axes[2].grid(True, alpha=0.3, axis='x')

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_weight_allocation(self, result_df, title='动态权重分配', save_path=None):
        """
        绘制 Top 5 股票动态权重分配饼图。

        Args:
            result_df: 预测结果DataFrame（需包含 '股票代码' 和 '权重' 列）
            title: 图表标题
            save_path: 保存路径
        """
        if 'weight' not in result_df.columns and '权重' not in result_df.columns:
            logger.warning("结果中不包含权重数据，无法绘制")
            return

        df = result_df.head(5).copy()
        weight_col = '权重' if '权重' in df.columns else 'weight'
        stocks = df['股票代码'].astype(str).tolist()
        weights = df[weight_col].values

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        # 左图：饼图
        colors = plt.cm.Set3(np.linspace(0, 1, len(stocks)))
        wedges, texts, autotexts = axes[0].pie(weights, labels=stocks, autopct='%1.1f%%',
                                               colors=colors, startangle=90,
                                               textprops={'fontsize': 11})
        for autotext in autotexts:
            autotext.set_fontsize(12)
            autotext.set_fontweight('bold')
        axes[0].set_title('权重分配饼图')

        # 右图：柱状图
        axes[1].bar(range(len(stocks)), weights, color=colors[:len(stocks)],
                   edgecolor='gray', linewidth=0.5)
        for i, (stock, weight) in enumerate(zip(stocks, weights)):
            axes[1].text(i, weight, f'{weight:.2f}', ha='center', va='bottom', fontsize=12, fontweight='bold')
        axes[1].set_xticks(range(len(stocks)))
        axes[1].set_xticklabels(stocks, rotation=30)
        axes[1].set_ylabel('权重')
        axes[1].set_title('权重柱状图')
        axes[1].axhline(y=1.0/len(stocks), color='red', linestyle='--', alpha=0.5,
                       label=f'均分权重 ({1.0/len(stocks):.2f})')
        axes[1].legend()
        axes[1].grid(True, alpha=0.3, axis='y')

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_cross_sectional_features(self, panel_df, date=None, title='截面特征分布', save_path=None):
        """
        绘制某一交易日截面特征分布（箱线图）。

        Args:
            panel_df: 面板数据，需包含 'trade_date', '涨跌幅', '成交量', '换手率' 等
            date: 指定日期，None 则使用最新日期
            title: 图表标题
            save_path: 保存路径
        """
        df = panel_df.copy()
        if 'trade_date' not in df.columns:
            logger.warning("面板数据缺少 trade_date 列")
            return

        df['trade_date'] = pd.to_datetime(df['trade_date'])
        if date is None:
            date = df['trade_date'].max()
        else:
            date = pd.to_datetime(date)

        day_data = df[df['trade_date'] == date]
        if day_data.empty:
            logger.warning(f"无 {date.date()} 的数据")
            return

        cs_features = {
            '涨跌幅': '个股涨跌幅',
            '换手率': '换手率',
            '成交额': '成交额',
        }

        plot_cols = []
        plot_labels = []
        for col, label in cs_features.items():
            if col in day_data.columns:
                plot_cols.append(col)
                plot_labels.append(label)

        if not plot_cols:
            logger.warning("无可用截面特征列")
            return

        fig, axes = plt.subplots(1, len(plot_cols), figsize=(6 * len(plot_cols), 5))
        if len(plot_cols) == 1:
            axes = [axes]

        for ax, col, label in zip(axes, plot_cols, plot_labels):
            data = day_data[col].dropna()
            ax.violinplot(data.values, showmeans=True, showmedians=True)
            ax.set_xticks([1])
            ax.set_xticklabels([label])
            ax.grid(True, alpha=0.3, axis='y')
            ax.text(1, data.mean(), f'均值={data.mean():.3f}', ha='center', va='bottom',
                   fontsize=9, color='red')

        plt.suptitle(f'{title} - {date.strftime("%Y-%m-%d")}', fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()

    def plot_multi_head_comparison(self, result_df, title='多头模型预测对比', save_path=None):
        """
        对比多头模型各头的预测结果（ranking / regression / classification / direction）。

        Args:
            result_df: 包含各头预测结果的DataFrame
            title: 图表标题
            save_path: 保存路径
        """
        head_cols = [c for c in result_df.columns if c.startswith('head_')]
        if not head_cols:
            logger.warning("结果中不包含多头预测列")
            return

        n_heads = len(head_cols)
        fig, axes = plt.subplots(1, n_heads, figsize=(5 * n_heads, 5))
        if n_heads == 1:
            axes = [axes]

        for ax, col in zip(axes, head_cols):
            data = result_df[col].values
            stocks = result_df['股票代码'].astype(str).tolist() if '股票代码' in result_df.columns else range(len(data))
            colors = [COLOR_UP if v > 0 else COLOR_DOWN for v in data]
            ax.bar(range(len(data)), data, color=colors, edgecolor='gray', linewidth=0.5)
            ax.set_xticks(range(len(data)))
            if isinstance(stocks[0], str):
                ax.set_xticklabels(stocks, rotation=30)
            ax.set_title(col)
            ax.grid(True, alpha=0.3, axis='y')

        plt.suptitle(title, fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            logger.info(f"图表保存到: {save_path}")
        plt.show()
