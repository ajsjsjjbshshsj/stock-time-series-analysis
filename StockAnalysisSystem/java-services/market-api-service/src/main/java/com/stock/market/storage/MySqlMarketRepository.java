package com.stock.market.storage;

import com.stock.market.api.QueryParameters;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.*;

@Repository
public class MySqlMarketRepository {
    private final JdbcTemplate jdbc;

    public MySqlMarketRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        jdbc.setQueryTimeout(5);
    }

    public List<Map<String, Object>> stocks(int limit) {
        try {
            return jdbc
                    .queryForList(
                            "SELECT ts_code,symbol,name,area,industry,list_date FROM stock_basic"
                                    + " ORDER BY ts_code ASC LIMIT ?",
                            limit)
                    .stream()
                    .map(MySqlMarketRepository::dates)
                    .toList();
        } catch (org.springframework.dao.DataAccessException ex) {
            throw new DependencyException();
        }
    }

    public boolean exists(String code) {
        try {
            return Boolean.TRUE.equals(
                    jdbc.queryForObject(
                            "SELECT EXISTS(SELECT 1 FROM stock_basic WHERE ts_code = ?)",
                            Boolean.class,
                            code));
        } catch (org.springframework.dao.DataAccessException ex) {
            throw new DependencyException();
        }
    }

    public List<Map<String, Object>> bars(QueryParameters p) {
        List<Object> args = new ArrayList<>();
        args.add(p.code());
        String filters = "";
        if (p.start() != null) {
            filters += " AND trade_date >= ?";
            args.add(p.start());
        }
        if (p.end() != null) {
            filters += " AND trade_date <= ?";
            args.add(p.end());
        }
        args.add(p.limit());
        String sql =
                "SELECT * FROM (SELECT"
                    + " trade_date,open,high,low,close,pre_close,`change`,pct_chg,vol,amount FROM"
                    + " stock_daily WHERE ts_code = ?"
                        + filters
                        + " ORDER BY trade_date DESC LIMIT ?) latest ORDER BY trade_date ASC";
        try {
            return jdbc.queryForList(sql, args.toArray()).stream()
                    .map(MySqlMarketRepository::dates)
                    .toList();
        } catch (org.springframework.dao.DataAccessException ex) {
            throw new DependencyException();
        }
    }

    /** 最新估值行（stock_daily_basic，按 trade_date 降序取 1）；该股无估值数据时返回 null。 */
    public Map<String, Object> latestDailyBasic(String code) {
        try {
            List<Map<String, Object>> rows =
                    jdbc.queryForList(
                            "SELECT ts_code,trade_date,turnover_rate,pe,pe_ttm,pb,ps,total_mv,source"
                                + " FROM stock_daily_basic WHERE ts_code = ?"
                                + " ORDER BY trade_date DESC LIMIT 1",
                            code);
            return rows.isEmpty() ? null : dates(rows.getFirst());
        } catch (org.springframework.dao.DataAccessException ex) {
            throw new DependencyException();
        }
    }

    /**
     * 市场概览：以 stock_daily 的最新交易日为口径，聚合涨跌家数、平均涨幅、
     * 行业平均涨跌幅（降序）与涨跌幅排行（前后各 10）。单日过滤走 idx_trade_date。
     */
    public Map<String, Object> marketOverview() {
        try {
            java.time.LocalDate latest =
                    jdbc.queryForObject("SELECT MAX(trade_date) FROM stock_daily", java.time.LocalDate.class);
            if (latest == null) return Map.of();

            Map<String, Object> overview = new LinkedHashMap<>(dates(jdbc.queryForMap(
                    "SELECT COUNT(*) AS total,"
                        + " SUM(pct_chg > 0) AS up_count, SUM(pct_chg < 0) AS down_count,"
                        + " SUM(pct_chg = 0) AS flat_count, ROUND(AVG(pct_chg), 4) AS avg_pct_chg,"
                        + " ROUND(SUM(amount), 2) AS total_amount"
                        + " FROM stock_daily WHERE trade_date = ?",
                    latest)));
            overview.put("trade_date", latest.toString());

            overview.put("industries",
                    jdbc.queryForList(
                            "SELECT b.industry, COUNT(*) AS count, ROUND(AVG(d.pct_chg), 4) AS avg_pct_chg"
                                + " FROM stock_daily d JOIN stock_basic b ON d.ts_code = b.ts_code"
                                + " WHERE d.trade_date = ? AND b.industry IS NOT NULL AND b.industry <> ''"
                                + " GROUP BY b.industry ORDER BY avg_pct_chg DESC",
                            latest)
                            .stream()
                            .map(MySqlMarketRepository::dates)
                            .toList());

            String ranking =
                    "SELECT d.ts_code, b.name, d.close, d.pct_chg, d.amount"
                        + " FROM stock_daily d LEFT JOIN stock_basic b ON d.ts_code = b.ts_code"
                        + " WHERE d.trade_date = ? AND d.pct_chg IS NOT NULL"
                        + " ORDER BY d.pct_chg %s, d.ts_code ASC LIMIT 10";
            overview.put("top_stocks",
                    jdbc.queryForList(String.format(ranking, "DESC"), latest).stream()
                            .map(MySqlMarketRepository::dates)
                            .toList());
            // ASC 排序天然跌幅最深在前，与前端跌幅榜展示顺序一致
            overview.put("bottom_stocks",
                    jdbc.queryForList(String.format(ranking, "ASC"), latest).stream()
                            .map(MySqlMarketRepository::dates)
                            .toList());
            return overview;
        } catch (org.springframework.dao.DataAccessException ex) {
            throw new DependencyException();
        }
    }

    private static Map<String, Object> dates(Map<String, Object> row) {
        Map<String, Object> copy = new LinkedHashMap<>(row);
        copy.replaceAll(
                (k, v) ->
                        v instanceof java.sql.Date d
                                ? d.toLocalDate().toString()
                                : v instanceof java.time.LocalDate d ? d.toString() : v);
        return copy;
    }
}
