package com.stock.market;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

import com.stock.market.api.*;
import com.stock.market.storage.*;

import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

class MarketControllerTest {
    @Test
    void emitsNumericPricesIsoDatesAndNullableOptionals() throws Exception {
        var mysql = mock(MySqlMarketRepository.class);
        var indicators = mock(IndicatorRepository.class);
        when(mysql.exists("000001.SZ")).thenReturn(true);
        var row = new java.util.LinkedHashMap<String, Object>();
        row.put("trade_date", "2026-09-01");
        row.put("close", new java.math.BigDecimal("10.000000"));
        row.put("pct_chg", null);
        when(mysql.bars(any())).thenReturn(List.of(row));
        when(indicators.query(any())).thenReturn(List.of(row));
        var mvc =
                MockMvcBuilders.standaloneSetup(new MarketController(mysql, indicators))
                        .setControllerAdvice(new ApiExceptionHandler())
                        .build();
        mvc.perform(get("/api/analysis/kline/000001.SZ"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.bars[0].close").isNumber())
                .andExpect(jsonPath("$.bars[0].trade_date").value("2026-09-01"))
                .andExpect(jsonPath("$.bars[0].pct_chg").value(org.hamcrest.Matchers.nullValue()));
        mvc.perform(get("/api/analysis/indicators/000001.SZ"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.indicators[0].close").isNumber());
        when(indicators.query(any())).thenThrow(new DependencyException());
        mvc.perform(get("/api/analysis/indicators/000001.SZ"))
                .andExpect(status().isServiceUnavailable());
    }

    @Test
    void validatesParametersDistinguishesMissingEmptyAndUnavailable() throws Exception {
        var mysql = mock(MySqlMarketRepository.class);
        var indicators = mock(IndicatorRepository.class);
        var mvc =
                MockMvcBuilders.standaloneSetup(new MarketController(mysql, indicators))
                        .setControllerAdvice(new ApiExceptionHandler())
                        .build();
        mvc.perform(get("/api/analysis/kline/bad")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/analysis/stocks?limit=6001")).andExpect(status().isBadRequest());
        mvc.perform(get("/api/analysis/indicators/000001.SZ")).andExpect(status().isNotFound());
        when(mysql.exists("000001.SZ")).thenReturn(true);
        when(indicators.query(any())).thenReturn(List.of());
        mvc.perform(get("/api/analysis/indicators/000001.SZ"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.indicators").isEmpty())
                .andExpect(jsonPath("$.ts_code").value("000001.SZ"));
        mvc.perform(get("/api/analysis/kline/000001.SZ?start=2026-02-01&end=2026-01-01"))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/api/analysis/kline/000001.SZ?start=garbage"))
                .andExpect(status().isBadRequest());
        when(mysql.stocks(6000)).thenThrow(new DependencyException());
        mvc.perform(get("/api/analysis/stocks"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("Market dependency unavailable"));
    }

    @Test
    void dailyBasicDistinguishesInvalidUnknownMissingRowAndUnavailable() throws Exception {
        var mysql = mock(MySqlMarketRepository.class);
        var indicators = mock(IndicatorRepository.class);
        var mvc =
                MockMvcBuilders.standaloneSetup(new MarketController(mysql, indicators))
                        .setControllerAdvice(new ApiExceptionHandler())
                        .build();
        // 代码格式非法 → 400
        mvc.perform(get("/api/analysis/daily-basic/bad")).andExpect(status().isBadRequest());
        // 股票不存在 → 404
        mvc.perform(get("/api/analysis/daily-basic/000001.SZ")).andExpect(status().isNotFound());
        when(mysql.exists("000001.SZ")).thenReturn(true);
        // 股票存在但无估值行 → 404（Mockito 对 Map 默认回空对象，需显式回 null）
        when(mysql.latestDailyBasic("000001.SZ")).thenReturn(null);
        mvc.perform(get("/api/analysis/daily-basic/000001.SZ")).andExpect(status().isNotFound());
        var row = new java.util.LinkedHashMap<String, Object>();
        row.put("trade_date", "2026-10-13");
        row.put("pe_ttm", new java.math.BigDecimal("8.500000"));
        row.put("total_mv", null);
        when(mysql.latestDailyBasic("000001.SZ")).thenReturn(row);
        mvc.perform(get("/api/analysis/daily-basic/000001.SZ"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.trade_date").value("2026-10-13"))
                .andExpect(jsonPath("$.pe_ttm").value(8.5))
                .andExpect(jsonPath("$.total_mv").value(org.hamcrest.Matchers.nullValue()));
        when(mysql.latestDailyBasic("000001.SZ")).thenThrow(new DependencyException());
        mvc.perform(get("/api/analysis/daily-basic/000001.SZ"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("Market dependency unavailable"));
    }

    @Test
    void marketOverviewReturnsLatestDayAggregateAndMapsDependencyFailure() throws Exception {
        var mysql = mock(MySqlMarketRepository.class);
        var indicators = mock(IndicatorRepository.class);
        var mvc =
                MockMvcBuilders.standaloneSetup(new MarketController(mysql, indicators))
                        .setControllerAdvice(new ApiExceptionHandler())
                        .build();
        var overview = new java.util.LinkedHashMap<String, Object>();
        overview.put("trade_date", "2026-10-13");
        overview.put("up_count", new java.math.BigDecimal("2100"));
        overview.put("industries", List.of());
        when(mysql.marketOverview()).thenReturn(overview);
        mvc.perform(get("/api/analysis/market/overview"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.trade_date").value("2026-10-13"))
                .andExpect(jsonPath("$.up_count").value(2100))
                .andExpect(jsonPath("$.industries").isEmpty());
        when(mysql.marketOverview()).thenThrow(new DependencyException());
        mvc.perform(get("/api/analysis/market/overview"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("Market dependency unavailable"));
    }
}
