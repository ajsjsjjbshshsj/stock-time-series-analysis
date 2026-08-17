USE stock_analysis;

RENAME TABLE
  stock_daily TO stock_daily_float_backup,
  stock_daily_decimal TO stock_daily;
