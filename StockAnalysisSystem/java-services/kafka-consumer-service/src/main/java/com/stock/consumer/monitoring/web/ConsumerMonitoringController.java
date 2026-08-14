package com.stock.consumer.monitoring.web;

import com.stock.consumer.monitoring.ConsumerError;
import com.stock.consumer.monitoring.ConsumerErrorStore;
import com.stock.consumer.monitoring.ConsumerStatisticsService;
import com.stock.consumer.monitoring.ConsumerStatisticsSnapshot;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/** Read-only monitoring endpoints for the current consumer process. */
@RestController
@RequestMapping("/api/consumer")
public final class ConsumerMonitoringController {

    private final ConsumerStatisticsService statistics;
    private final ConsumerErrorStore errors;

    public ConsumerMonitoringController(ConsumerStatisticsService statistics, ConsumerErrorStore errors) {
        this.statistics = statistics;
        this.errors = errors;
    }

    @GetMapping("/statistics")
    public ConsumerStatisticsSnapshot statistics() {
        return statistics.snapshot();
    }

    @GetMapping("/errors")
    public List<ConsumerError> errors() {
        return errors.recent();
    }
}
