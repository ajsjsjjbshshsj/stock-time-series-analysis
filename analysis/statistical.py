"""
统计分析模块
"""
import pandas as pd
import numpy as np
from scipy import stats
from config.logging_config import get_logger
logger = get_logger(__name__)


class StatisticalAnalyzer:
    """统计分析器"""
    
    def __init__(self):
        logger.info("统计分析器初始化完成")
    
    def descriptive_stats(self, df, columns=None):
        """
        描述性统计
        
        Args:
            df: 数据DataFrame
            columns: 需要统计的列，None则统计所有数值列
            
        Returns:
            DataFrame: 统计结果
        """
        if columns is None:
            columns = df.select_dtypes(include=[np.number]).columns
        
        stats_dict = {
            'count': df[columns].count(),
            'mean': df[columns].mean(),
            'std': df[columns].std(),
            'min': df[columns].min(),
            '25%': df[columns].quantile(0.25),
            '50%': df[columns].median(),
            '75%': df[columns].quantile(0.75),
            'max': df[columns].max(),
            'skewness': df[columns].skew(),
            'kurtosis': df[columns].kurtosis()
        }
        
        result = pd.DataFrame(stats_dict)
        logger.info("描述性统计完成")
        return result

    def trend_analysis(self, df, column='close'):
        """
        趋势分析
        
        Args:
            df: 数据DataFrame
            column: 分析的列名
            
        Returns:
            dict: 趋势分析结果
        """
        series = df[column].dropna()
        
        # 计算趋势
        x = np.arange(len(series))
        slope, intercept, r_value, p_value, std_err = stats.linregress(x, series.values)
        
        result = {
            'slope': slope,
            'intercept': intercept,
            'r_squared': r_value ** 2,
            'p_value': p_value,
            'trend': 'upward' if slope > 0 else 'downward',
            'significant': p_value < 0.05
        }
        
        logger.info(f"趋势分析: {result['trend']}, R²={result['r_squared']:.4f}")
        return result

    def risk_metrics(self, df, column='close', risk_free_rate=0.03):
        """
        风险指标计算
        
        Args:
            df: 数据DataFrame
            column: 价格列
            risk_free_rate: 无风险利率
            
        Returns:
            dict: 风险指标
        """
        returns = df[column].pct_change().dropna()
        
        # 年化收益率
        annual_return = (1 + returns.mean()) ** 252 - 1
        
        # 年化波动率
        annual_vol = returns.std() * np.sqrt(252)
        
        # 夏普比率
        sharpe_ratio = (annual_return - risk_free_rate) / annual_vol if annual_vol != 0 else 0
        
        # 最大回撤
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()
        
        result = {
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown
        }
        
        logger.info(f"风险指标: 夏普比率={sharpe_ratio:.4f}, 最大回撤={max_drawdown:.4f}")
        return result
