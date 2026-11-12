package com.stock.market.storage;

import com.stock.market.api.QueryParameters;
import java.time.LocalDate;
import java.util.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Repository;

/** Opt-in read adapter. Inherited V1 ingestion is unchanged. */
@Repository
@Primary
@ConditionalOnProperty(name="market.generation-reads-enabled",havingValue="true")
public class ActiveIndicatorRepository extends IndicatorRepository {
    private static final LocalDate FLOOR=LocalDate.of(2023,1,1);
    private static final List<String> PUBLIC_FIELDS=List.of("trade_date","close","volume","pct_chg","ma5","ma10","ma20",
        "vol_ma5","vol_ma10","volume_ratio","window_size","is_warmup","calculation_time","event_id","source_event_id","trace_id","source");
    private final ActiveProjectionPointer pointer;
    private final GenerationIndicatorRepository generations;

    public ActiveIndicatorRepository(ClickHouseClient client,ActiveProjectionPointer pointer,GenerationIndicatorRepository generations) {
        super(client);
        this.pointer=pointer;
        this.generations=generations;
    }

    @Override public List<Map<String,Object>> query(QueryParameters p) {
        QueryParameters.series(p.code(),p.limit(),p.start(),p.end());
        var selection=pointer.current();
        if(selection.isEmpty()) {
            var legacy=super.query(p);
            if(pointer.current().isPresent())throw new DependencyException();
            return legacy;
        }
        var selected=selection.get();
        if(!pointer.keyCovered(selected,p.code()))throw new DependencyException();
        LocalDate start=p.start()==null || p.start().isBefore(FLOOR)?FLOOR:p.start();
        LocalDate end=p.end()==null || p.end().isAfter(selected.cutoff())?selected.cutoff():p.end();
        if(start.isAfter(end)) {
            if(!pointer.stillCurrent(selected))throw new DependencyException();
            return List.of();
        }
        var result=generations.query(selected.binding(),p.code(),start,end,p.limit());
        if(result.status()!=GenerationIndicatorRepository.KeyStatus.SEED_COMPLETE || !pointer.keyCovered(selected,p.code()) || !pointer.stillCurrent(selected))
            throw new DependencyException();
        var rows=new ArrayList<Map<String,Object>>();
        for(var source:result.indicators()) {
            var row=new LinkedHashMap<String,Object>();
            for(String field:PUBLIC_FIELDS)if(source.containsKey(field))row.put(field,source.get(field));
            if(row.get("calculation_time") instanceof String value && !value.contains("T"))
                row.put("calculation_time",value.replace(' ','T')+"Z");
            rows.add(Collections.unmodifiableMap(row));
        }
        return List.copyOf(rows);
    }
}
