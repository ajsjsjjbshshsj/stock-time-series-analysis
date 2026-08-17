package com.stock.consumer.monitoring.web;

import com.stock.consumer.monitoring.ConsumerError;
import com.stock.consumer.monitoring.ConsumerErrorStore;
import com.stock.consumer.monitoring.ConsumerStatisticsService;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.OffsetDateTime;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class ConsumerMonitoringControllerTest {

    @Test
    void exposesZeroValueStartupState() throws Exception {
        MockMvc mvc = mvc(new ConsumerStatisticsService(), new ConsumerErrorStore(10));

        mvc.perform(get("/api/consumer/statistics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalConsumed").value(0))
                .andExpect(jsonPath("$.successCount").value(0))
                .andExpect(jsonPath("$.failureCount").value(0));
    }

    @Test
    void exposesStatisticsAsJson() throws Exception {
        ConsumerStatisticsService statistics = new ConsumerStatisticsService();
        statistics.recordDailySuccess();
        MockMvc mvc = mvc(statistics, new ConsumerErrorStore(10));

        mvc.perform(get("/api/consumer/statistics"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalConsumed").value(1))
                .andExpect(jsonPath("$.successCount").value(1))
                .andExpect(jsonPath("$.dailyEventCount").value(1))
                .andExpect(jsonPath("$.jsonParseFailureCount").value(0));
    }

    @Test
    void exposesRecentErrorsNewestFirst() throws Exception {
        ConsumerErrorStore errors = new ConsumerErrorStore(10);
        errors.add(new ConsumerError(OffsetDateTime.parse("2026-08-14T09:00:00+08:00"),
                "VALIDATION", "bad key", "stock.ods.daily.v1", "000001.SZ", 0, 7));
        errors.add(new ConsumerError(OffsetDateTime.parse("2026-08-14T09:01:00+08:00"),
                "DESERIALIZATION", "bad json", "stock.ods.daily.v1", "000002.SZ", 1, 8));
        MockMvc mvc = mvc(new ConsumerStatisticsService(), errors);

        mvc.perform(get("/api/consumer/errors"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].errorType").value("DESERIALIZATION"))
                .andExpect(jsonPath("$[0].message").value("bad json"))
                .andExpect(jsonPath("$[0].offset").value(8))
                .andExpect(jsonPath("$[1].errorType").value("VALIDATION"));
    }

    private static MockMvc mvc(ConsumerStatisticsService statistics, ConsumerErrorStore errors) {
        return MockMvcBuilders.standaloneSetup(new ConsumerMonitoringController(statistics, errors)).build();
    }
}
