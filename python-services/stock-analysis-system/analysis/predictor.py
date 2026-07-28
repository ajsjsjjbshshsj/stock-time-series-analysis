"""
预测模型模块：LSTM和XGBoost（二分类：涨=1，跌=0）
支持：单轮划分（train/val/test）与时序滚动交叉验证
"""
import os
import pickle
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix, roc_auc_score
from sklearn.preprocessing import MinMaxScaler
from config.settings import MODEL_CONFIG
from config.logging_config import get_logger
logger = get_logger(__name__)

# 模型保存目录
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


class StockPredictor:
    """股票预测器"""

    def __init__(self, model_type='xgboost'):
        """
        初始化预测器

        Args:
            model_type: 模型类型 ('xgboost' 或 'lstm')
        """
        self.model_type = model_type
        self.model = None
        self.scaler = None  # [防泄漏] scaler 仅在训练时拟合，预测时用已拟合的 scaler
        self.feature_names = None  # 记录训练时使用的特征列表
        self.train_metadata = None  # 训练元数据
        logger.info(f"预测器初始化完成，使用{model_type}模型")


    def prepare_features(self, df, feature_columns=None, target_column='future_direction_1d'):
        """
        准备特征数据

        Args:
            df: 数据DataFrame
            feature_columns: 特征列名列表
            target_column: 目标列名（默认二分类标签：涨=1，跌=0）

        Returns:
            tuple: (X, y, feature_names)
        """
        if feature_columns is None:
            feature_columns = [
                # 情绪指标
                'macd_dif', 'macd_dea', 'macd_hist',
                'vol_ma5', 'vol_ma10', 'volume_ratio',
                'kdj_k', 'kdj_d', 'kdj_j',
                'rsi',
                'turnover_rate_5', 'turnover_rate_60', 'turnover_rate_120',
                'br', 'ar',

                # 风险指标（v2.0 更名：原 Variance* → volatility_*）
                'volatility_20d', 'volatility_60d', 'volatility_120d',
                'skewness_20d', 'skewness_60d', 'skewness_120d',
                'kurtosis_20d', 'kurtosis_60d', 'kurtosis_120d',

                # 技术指标
                'bb_upper', 'bb_middle', 'bb_lower', 'bb_width',
                'ema5', 'ema10', 'ema20', 'ema26', 'ema60', 'ema120',
                'ma5', 'ma10', 'ma20', 'ma60',
                'mfi14',

                # 动量指标
                'arron_up_25', 'arron_down_25',
                'bear_power', 'bull_power',
                'cci10', 'cci15', 'cci20', 'cci88',
                'cr20',
                'mass',

                # 技术信号
                'golden_cross', 'death_cross',
                'macd_golden_cross',
                'rsi_oversold', 'rsi_overbought',

                # 基本指标
                'open', 'high', 'low', 'close', 'vol',
                'turnover_rate', 'pe', 'pe_ttm', 'pb', 'ps', 'total_mv'
            ]

        # 排除未来标签，防止前视偏差
        # 注意: log_return 不是未来数据（= log(close/close.shift(1))），可以保留
        future_columns = [
            'future_return_1d', 'future_return_5d',
            'future_direction_1d', 'future_direction_5d',
        ]
        feature_columns = [c for c in feature_columns if c not in future_columns]

        df_for_model = df.copy()
        # 过滤出实际存在的列
        available_features = [c for c in feature_columns if c in df_for_model.columns]
        missing_features = [c for c in feature_columns if c not in df_for_model.columns]
        if missing_features:
            logger.warning(f"以下特征列不存在，已跳过: {missing_features}")

        if target_column not in df_for_model.columns:
            logger.warning(f"目标列 {target_column} 不存在")
            return None, None, None

        df_clean = df_for_model[available_features + [target_column]].dropna()

        if df_clean.empty:
            logger.warning("清洗后数据为空")
            return None, None, None

        X = df_clean[available_features].values
        y = df_clean[target_column].values

        # 检查类别不平衡
        class_counts = pd.Series(y).value_counts()
        total = len(y)
        for cls, count in class_counts.items():
            pct = count / total * 100
            logger.info(f"  类别 {cls}: {count} 样本 ({pct:.1f}%)")
        imbalance_ratio = class_counts.max() / class_counts.min() if len(class_counts) > 1 and class_counts.min() > 0 else 0
        if imbalance_ratio > 3:
            logger.warning(f"检测到类别不平衡（比例 {imbalance_ratio:.1f}:1），建议使用 scale_pos_weight 或过采样")

        logger.info(f"特征准备完成: X.shape={X.shape}, y.shape={y.shape}")
        return X, y, available_features

    # =========================================================================
    # 数据集划分辅助方法
    # =========================================================================
    def _split_time_series(self, X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2):
        """
        按时间顺序划分为训练集/验证集/测试集

        Args:
            X: 特征数据
            y: 目标数据
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例

        Returns:
            tuple: (X_train, X_val, X_test, y_train, y_val, y_test)
        """
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "比例之和必须为1"

        n = len(X)
        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        X_train, y_train = X[:train_end], y[:train_end]
        X_val, y_val = X[train_end:val_end], y[train_end:val_end]
        X_test, y_test = X[val_end:], y[val_end:]

        logger.info(f"数据集划分: train={len(X_train)}, val={len(X_val)}, test={len(X_test)}")
        return X_train, X_val, X_test, y_train, y_val, y_test

    def _time_series_cv_splits(self, X, y, n_splits=5, test_ratio=0.2):
        """
        生成时序滚动交叉验证的索引划分

        每次划分取前面的数据作为训练+验证集，最新的一部分作为测试集，
        逐步向前滚动。

        Args:
            X: 特征数据
            y: 目标数据
            n_splits: 折数
            test_ratio: 测试集比例

        Yields:
            tuple: (train_indices, test_indices)
        """
        n = len(X)
        test_size = int(n * test_ratio)

        # 计算每次滚动的步长
        step = (n - test_size) // n_splits

        for i in range(n_splits):
            test_start = (n - test_size) - (n_splits - 1 - i) * step
            if test_start < 0:
                continue

            train_end = test_start
            train_indices = np.arange(0, train_end)
            test_indices = np.arange(train_end, train_end + test_size)

            if len(train_indices) == 0:
                continue

            yield train_indices, test_indices

    def _train_val_split_from_train(self, X_train, y_train, val_ratio=0.25):
        """
        从训练集中再划分出验证集（val_ratio 是相对于训练集的比例）

        Args:
            X_train: 训练集特征
            y_train: 训练集目标
            val_ratio: 验证集占训练集的比例

        Returns:
            tuple: (X_train_final, X_val, y_train_final, y_val)
        """
        n = len(X_train)
        val_end = int(n * (1 - val_ratio))

        X_train_final = X_train[:val_end]
        X_val = X_train[val_end:]
        y_train_final = y_train[:val_end]
        y_val = y_train[val_end:]

        return X_train_final, X_val, y_train_final, y_val

    def _create_sequences(self, X, y, sequence_length):
        """
        构建时间序列样本（用于LSTM）

        Args:
            X: 特征数据
            y: 目标数据
            sequence_length: 序列长度

        Returns:
            tuple: (X_seq, y_seq)
        """
        X_seq, y_seq = [], []
        for i in range(len(X) - sequence_length):
            X_seq.append(X[i:i + sequence_length])
            y_seq.append(y[i + sequence_length])
        return np.array(X_seq), np.array(y_seq)

    # =========================================================================
    # 单轮训练方法（train/val/test 三分法）
    # =========================================================================
    @classmethod
    def load_model(cls, model_path: str = None) -> 'StockPredictor':
        """
        从磁盘加载模型。

        Args:
            model_path: .pkl 文件路径。None 则自动加载最新模型。

        Returns:
            StockPredictor: 已加载模型的预测器实例
        """
        from analysis.model_registry import load_model as reg_load

        model_bundle, metadata = reg_load(model_path, model_type=None)
        if model_bundle is None:
            return None

        predictor = cls(model_type=model_bundle.get('model_type', 'xgboost'))
        predictor.model = model_bundle['model']
        predictor.scaler = model_bundle.get('scaler')
        predictor.feature_names = metadata.get('feature_names', []) if metadata else []
        predictor.train_metadata = metadata

        return predictor

    # =========================================================================
    # 训练方法
    # =========================================================================
    def train_xgboost(self, X, y, feature_names=None,
                      train_ratio=0.6, val_ratio=0.2, test_ratio=0.2):
        """
        训练XGBoost分类模型（train/val/test 三分法）

        Args:
            X: 特征数据
            y: 目标数据（0或1）
            feature_names: 特征名称列表
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例

        Returns:
            dict: 训练结果
        """
        try:
            from xgboost import XGBClassifier
            from analysis.model_registry import set_global_seed
            set_global_seed(MODEL_CONFIG.get('random_seed', 42))

            X_train, X_val, X_test, y_train, y_val, y_test = self._split_time_series(
                X, y, train_ratio, val_ratio, test_ratio
            )

            params = MODEL_CONFIG['xgboost_params'].copy()
            params.setdefault('random_state', MODEL_CONFIG.get('random_seed', 42))
            model = XGBClassifier(**params, eval_metric='logloss')

            # 使用验证集做early stopping
            model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False
            )

            # 在测试集上评估
            y_pred = model.predict(X_test)
            metrics = self._evaluate_model(y_test, y_pred)

            self.model = model
            self.feature_names = feature_names
            logger.info(f"XGBoost分类模型训练完成: {metrics}")

            return {
                'model': model,
                'metrics': metrics,
                'predictions': y_pred,
                'y_test': y_test,
                'feature_names': feature_names,
                'split_info': {
                    'train_size': len(X_train),
                    'val_size': len(X_val),
                    'test_size': len(X_test),
                },
            }

        except ImportError:
            logger.error("未安装xgboost，请运行: pip install xgboost")
            return None

    # =========================================================================
    # 预测 & 评估
    # =========================================================================
    def predict(self, X):
        """
        预测

        Args:
            X: 特征数据

        Returns:
            array: 预测结果（0或1）
        """
        if self.model is None:
            logger.error("模型未训练")
            return None

        if self.model_type == 'xgboost':
            return self.model.predict(X)
        elif self.model_type == 'lstm':
            if self.scaler is None:
                logger.error("LSTM 模型的 scaler 未拟合，请先训练")
                return None
            X_scaled = self.scaler.transform(X)
            y_prob = self.model.predict(X_scaled).flatten()
            return (y_prob >= 0.5).astype(int)

        return None

    def _evaluate_model(self, y_true, y_pred):
        """
        评估模型（二分类任务）

        Args:
            y_true: 真实值（0或1）
            y_pred: 预测值（0或1）

        Returns:
            dict: 评估指标
        """
        acc = accuracy_score(y_true, y_pred)
        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        cm = confusion_matrix(y_true, y_pred)

        # 尝试计算AUC
        try:
            auc = roc_auc_score(y_true, y_pred)
        except Exception:
            auc = None

        metrics = {
            'Accuracy': acc,
            'Precision': precision,
            'Recall': recall,
            'F1': f1,
            'Confusion_Matrix': cm
        }
        if auc is not None:
            metrics['AUC'] = auc

        return metrics

    def feature_importance(self, feature_names):
        """
        获取特征重要性（仅XGBoost）

        Args:
            feature_names: 特征名称列表

        Returns:
            DataFrame: 特征重要性
        """
        if self.model_type != 'xgboost' or self.model is None:
            logger.warning("仅XGBoost支持特征重要性")
            return None

        importance = self.model.feature_importances_
        df_importance = pd.DataFrame({
            'feature': feature_names,
            'importance': importance
        }).sort_values('importance', ascending=False)

        return df_importance

    # =========================================================================
    # 模型持久化
    # =========================================================================
    def save_model(self, model_name=None, feature_names=None,
                   train_start_date=None, train_end_date=None, metrics=None):
        """
        保存模型 + 元数据到磁盘。

        生成:
            {model_name}.pkl       模型权重 + scaler
            {model_name}.meta.json 训练元数据（特征版本、评价指标、时间范围等）

        Args:
            model_name: 文件名前缀，None 则自动生成
            feature_names: 使用的特征列表
            train_start_date: 训练数据起始日期
            train_end_date: 训练数据结束日期
            metrics: 评价指标字典
        """
        if self.model is None:
            logger.error("模型未训练，无法保存")
            return None

        from analysis.model_registry import build_metadata, save_model as reg_save

        if model_name is None:
            import datetime
            model_name = f"{self.model_type}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

        metadata = build_metadata(
            model_name=model_name,
            model_type=self.model_type,
            feature_names=feature_names or [],
            train_start_date=train_start_date,
            train_end_date=train_end_date,
            metrics=metrics or {},
            params=self._get_model_params(),
            seed=42,
        )

        # 打包模型对象 + scaler
        model_bundle = {
            'model': self.model,
            'scaler': self.scaler,
            'model_type': self.model_type,
        }

        return reg_save(model_bundle, metadata, model_name)

    def _get_model_params(self) -> dict:
        """提取当前模型的超参数。"""
        params = {}
        if self.model is not None:
            if hasattr(self.model, 'get_params'):
                params = self.model.get_params()
        return params

    # =========================================================================
    # 回归预测（预测具体收益率）
    # =========================================================================
    def train_xgboost_regression(self, X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2):
        """
        训练XGBoost回归模型，预测具体收益率

        Args:
            X: 特征数据
            y: 目标收益率（连续值）
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例

        Returns:
            dict: 训练结果
        """
        try:
            from xgboost import XGBRegressor

            X_train, X_val, X_test, y_train, y_val, y_test = self._split_time_series(
                X, y, train_ratio, val_ratio, test_ratio
            )

            params = MODEL_CONFIG['xgboost_params']
            model = XGBRegressor(
                max_depth=params.get('max_depth', 6),
                learning_rate=params.get('learning_rate', 0.1),
                n_estimators=params.get('n_estimators', 100),
                objective='reg:squarederror',
                eval_metric='rmse'
            )

            model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False
            )

            y_pred = model.predict(X_test)
            metrics = self._evaluate_regression(y_test, y_pred)

            self.model = model
            self.model_type = 'xgboost_regression'
            logger.info(f"XGBoost回归模型训练完成: {metrics}")

            return {
                'model': model,
                'metrics': metrics,
                'predictions': y_pred,
                'y_test': y_test
            }

        except ImportError:
            logger.error("未安装xgboost，请运行: pip install xgboost")
            return None

    def train_xgboost_multiclass(self, X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2,
                                 thresholds=(-0.03, -0.01, 0.01, 0.03)):
        """
        训练XGBoost多分类模型（5类：大幅跌/小跌/平/小涨/大涨）

        Args:
            X: 特征数据
            y: 目标收益率（连续值，内部自动离散化为5类）
            train_ratio: 训练集比例
            val_ratio: 验证集比例
            test_ratio: 测试集比例
            thresholds: 4个阈值，将收益率划分为5类

        Returns:
            dict: 训练结果
        """
        try:
            from xgboost import XGBClassifier

            # 将连续收益率离散化为5类
            labels = ['大幅跌', '小跌', '平', '小涨', '大涨']
            y_multiclass = np.digitize(y, thresholds)  # 0-4 共5类
            y_multiclass = np.clip(y_multiclass, 0, 4)  # 确保范围

            X_train, X_val, X_test, y_train, y_val, y_test = self._split_time_series(
                X, y_multiclass, train_ratio, val_ratio, test_ratio
            )

            # 计算类别权重
            from collections import Counter
            counts = Counter(y_train)
            n_samples = len(y_train)
            n_classes = len(labels)
            class_weight = {i: n_samples / (n_classes * counts.get(i, 1)) for i in range(n_classes)}
            sample_weights = [class_weight[int(label)] for label in y_train]

            params = MODEL_CONFIG['xgboost_params']
            model = XGBClassifier(
                max_depth=params.get('max_depth', 6),
                learning_rate=params.get('learning_rate', 0.1),
                n_estimators=params.get('n_estimators', 100),
                objective='multi:softprob',
                num_class=n_classes,
                eval_metric='mlogloss'
            )

            model.fit(
                X_train, y_train,
                sample_weight=sample_weights,
                eval_set=[(X_val, y_val)],
                verbose=False
            )

            y_pred = model.predict(X_test)
            metrics = self._evaluate_multiclass(y_test, y_pred, labels)

            self.model = model
            self.model_type = 'xgboost_multiclass'
            logger.info(f"XGBoost多分类模型训练完成: {metrics}")

            return {
                'model': model,
                'metrics': metrics,
                'predictions': y_pred,
                'y_test': y_test,
                'labels': labels,
                'thresholds': thresholds
            }

        except ImportError:
            logger.error("未安装xgboost，请运行: pip install xgboost")
            return None

    # =========================================================================
    # 回归 & 多分类 评估
    # =========================================================================
    def _evaluate_regression(self, y_true, y_pred):
        """
        评估回归模型

        Returns:
            dict: RMSE, MAE, R2, Direction Accuracy
        """
        from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

        rmse = np.sqrt(mean_squared_error(y_true, y_pred))
        mae = mean_absolute_error(y_true, y_pred)
        r2 = r2_score(y_true, y_pred)

        # 方向预测准确率
        correct_direction = (np.sign(y_true) == np.sign(y_pred)).mean()

        metrics = {
            'RMSE': rmse,
            'MAE': mae,
            'R2': r2,
            'Direction_Accuracy': correct_direction
        }
        logger.info(f"回归评估: RMSE={rmse:.6f}, MAE={mae:.6f}, R2={r2:.4f}, "
                     f"方向准确率={correct_direction:.2%}")
        return metrics

    def _evaluate_multiclass(self, y_true, y_pred, labels):
        """
        评估多分类模型

        Returns:
            dict: Accuracy, Weighted F1, Confusion Matrix
        """
        acc = accuracy_score(y_true, y_pred)
        f1 = f1_score(y_true, y_pred, average='weighted', zero_division=0)
        cm = confusion_matrix(y_true, y_pred)

        metrics = {
            'Accuracy': acc,
            'F1_Weighted': f1,
            'Confusion_Matrix': cm,
            'Labels': labels
        }
        logger.info(f"多分类评估: Accuracy={acc:.4f}, Weighted F1={f1:.4f}")
        return metrics

    def predict_regression(self, X):
        """
        回归预测：返回具体收益率预测值

        Args:
            X: 特征数据

        Returns:
            array: 预测收益率
        """
        if self.model is None:
            logger.error("模型未训练")
            return None

        if self.model_type == 'xgboost_regression':
            return self.model.predict(X)
        elif self.model_type == 'xgboost_multiclass':
            # 多分类返回概率，用阈值加权
            y_prob = self.model.predict_proba(X)
            # 用期望值作为回归预测
            return y_prob @ np.array([-0.05, -0.02, 0, 0.02, 0.05])

        logger.error(f"模型类型 {self.model_type} 不支持回归预测")
        return None

    def predict_multiclass(self, X):
        """
        多分类预测：返回类别标签

        Args:
            X: 特征数据

        Returns:
            array: 预测类别
        """
        if self.model is None or self.model_type != 'xgboost_multiclass':
            logger.error("模型未训练或不是多分类模型")
            return None

        return self.model.predict(X)

    def predict_multiclass_proba(self, X):
        """
        多分类概率预测

        Args:
            X: 特征数据

        Returns:
            array: 各类别概率
        """
        if self.model is None or self.model_type != 'xgboost_multiclass':
            logger.error("模型未训练或不是多分类模型")
            return None

        return self.model.predict_proba(X)

    def predict_proba(self, X):
        """
        二分类涨类概率预测（用于全市场排名排序）。

        Args:
            X: 特征数据

        Returns:
            array: 预测为"涨"的概率
        """
        if self.model is None:
            logger.error("模型未训练")
            return None

        if self.model_type == 'xgboost':
            return self.model.predict_proba(X)[:, 1]
        elif self.model_type == 'lstm':
            if self.scaler is None:
                logger.error("LSTM 模型的 scaler 未拟合，请先训练")
                return None
            X_scaled = self.scaler.transform(X)
            return self.model.predict(X_scaled).flatten()
        elif self.model_type == 'xgboost_regression':
            return self.model.predict(X)
        elif self.model_type == 'xgboost_multiclass':
            # 多分类返回期望收益率
            y_prob = self.model.predict_proba(X)
            return y_prob @ np.array([-0.05, -0.02, 0, 0.02, 0.05])

        logger.error(f"模型类型 {self.model_type} 不支持概率预测")
        return None