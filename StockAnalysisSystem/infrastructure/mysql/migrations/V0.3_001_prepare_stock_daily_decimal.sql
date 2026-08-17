USE stock_analysis;

CREATE TABLE stock_daily_decimal (
  id INT NOT NULL AUTO_INCREMENT,
  ts_code VARCHAR(10) COLLATE utf8mb4_general_ci NOT NULL COMMENT 'Tushare code',
  trade_date DATE NOT NULL COMMENT 'Trading date',
  open DECIMAL(20,6) NULL COMMENT 'Open price',
  high DECIMAL(20,6) NULL COMMENT 'High price',
  low DECIMAL(20,6) NULL COMMENT 'Low price',
  close DECIMAL(20,6) NULL COMMENT 'Close price',
  pre_close DECIMAL(20,6) NULL COMMENT 'Previous close price',
  `change` DECIMAL(20,6) NULL COMMENT 'Price change',
  pct_chg DECIMAL(20,6) NULL COMMENT 'Percentage change',
  vol DECIMAL(24,4) NULL COMMENT 'Volume in lots',
  amount DECIMAL(24,4) NULL COMMENT 'Amount in thousands of CNY',
  created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Creation time',
  PRIMARY KEY (id),
  UNIQUE KEY idx_code_date (ts_code, trade_date),
  KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

INSERT INTO stock_daily_decimal (
  id,
  ts_code,
  trade_date,
  open,
  high,
  low,
  close,
  pre_close,
  `change`,
  pct_chg,
  vol,
  amount,
  created_at
)
SELECT
  id,
  ts_code,
  trade_date,
  CAST(open AS DECIMAL(20,6)),
  CAST(high AS DECIMAL(20,6)),
  CAST(low AS DECIMAL(20,6)),
  CAST(close AS DECIMAL(20,6)),
  CAST(pre_close AS DECIMAL(20,6)),
  CAST(`change` AS DECIMAL(20,6)),
  CAST(pct_chg AS DECIMAL(20,6)),
  CAST(vol AS DECIMAL(24,4)),
  CAST(amount AS DECIMAL(24,4)),
  created_at
FROM stock_daily
ORDER BY id;
