package com.stock.consumer.daily;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Supplies the Jackson 2 mapper used by the V0.3 shared message model. */
@Configuration(proxyBeanMethods = false)
public class StockDailyEventJsonConfiguration {

    @Bean
    ObjectMapper stockEventObjectMapper() {
        return new ObjectMapper();
    }
}
