package com.stock.consumer.monitoring.web;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.kafka.listener.auto-startup=false",
        "spring.docker.compose.enabled=false"
})
@AutoConfigureMockMvc
class MonitorPageTest {

    @Autowired
    private MockMvc mvc;

    @Test
    void servesMonitoringPage() throws Exception {
        mvc.perform(get("/monitor/"))
                .andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("消费链路监控")))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("手动刷新")))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("Kafka UI")))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("成功率")));
    }

    @Test
    void servesPageAssets() throws Exception {
        mvc.perform(get("/monitor/monitor.css"))
                .andExpect(status().isOk());
        mvc.perform(get("/monitor/monitor.js"))
                .andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.containsString("/actuator/health")));
    }
}
