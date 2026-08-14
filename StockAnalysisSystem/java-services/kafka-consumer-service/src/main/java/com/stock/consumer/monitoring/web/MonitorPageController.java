package com.stock.consumer.monitoring.web;

import org.springframework.core.io.ClassPathResource;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ResponseBody;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** Serves the monitoring entry point with an explicit UTF-8 HTML content type. */
@Controller
public final class MonitorPageController {

    private final ClassPathResource page = new ClassPathResource("static/monitor/index.html");

    @GetMapping(value = {"/monitor", "/monitor/"}, produces = MediaType.TEXT_HTML_VALUE)
    @ResponseBody
    public String monitor() throws IOException {
        return page.getContentAsString(StandardCharsets.UTF_8);
    }
}
