package com.stock.market.api;

import com.stock.market.storage.*;
import java.time.LocalDate;
import java.util.*;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

/** Read-only evidence status; raw/feature/model readiness are separate facts. */
@RestController
@RequestMapping("/api/analysis/indicators")
public class IndicatorReadinessController {
    private final ObjectProvider<ActiveProjectionPointer> pointer;
    private final GenerationIndicatorRepository generations;
    private final MySqlMarketRepository mysql;

    public IndicatorReadinessController(ObjectProvider<ActiveProjectionPointer> pointer,
            GenerationIndicatorRepository generations,MySqlMarketRepository mysql) {
        this.pointer=pointer;
        this.generations=generations;
        this.mysql=mysql;
    }

    @GetMapping("/{tsCode}/readiness")
    public Map<String,Object> readiness(@PathVariable String tsCode) {
        QueryParameters.code(tsCode);
        if(!mysql.exists(tsCode))throw new ResponseStatusException(HttpStatus.NOT_FOUND,"Stock not found");
        var response=new LinkedHashMap<String,Object>();
        response.put("ts_code",tsCode);
        response.put("mode","LEGACY");response.put("state","UNVERIFIED");
        response.put("indicator_ready",false);response.put("guarantees_latest",false);
        response.put("verified_through",null);response.put("latest_observation",null);
        var source=pointer.getIfAvailable();
        if(source==null)return response;
        var selected=source.current();
        if(selected.isEmpty())return response;
        var current=selected.get();
        var result=source.keyCovered(current,tsCode)
            ?generations.query(current.binding(),tsCode,LocalDate.of(2023,1,1),current.cutoff(),1):null;
        boolean covered=source.keyCovered(current,tsCode);
        if(!source.stillCurrent(current))throw new DependencyException();
        boolean ready=covered && result!=null && result.status()==GenerationIndicatorRepository.KeyStatus.SEED_COMPLETE && !result.indicators().isEmpty();
        response.put("mode","GENERATION");
        response.put("generation",current.binding().generation());
        response.put("epoch",current.epoch());response.put("verified_at",current.verifiedAt());
        response.put("state",ready?"VERIFIED_SNAPSHOT":(!covered || result==null)?"SOURCE_PENDING":result.status().name());
        response.put("indicator_ready",ready);
        response.put("verified_through",current.cutoff().toString());
        if(ready) {
            var latest=result.indicators().getLast();
            response.put("latest_observation",latest.get("trade_date"));
            response.put("is_warmup",latest.get("is_warmup"));
        }
        // Snapshot proof has an upper bound, not a guarantee about future
        // collection, source gap eligibility, model features or predictions.
        return response;
    }
}
