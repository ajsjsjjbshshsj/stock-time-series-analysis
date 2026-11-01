package com.stock.market.storage;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.stock.common.model.IndicatorGenerationBinding;
import com.stock.market.ingest.IndicatorListener;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** Read-only CURRENT-proof lookup. An invalid active pointer never falls back. */
@Repository
@ConditionalOnProperty(name="market.generation-reads-enabled",havingValue="true")
public class ActiveProjectionPointer {
    public record Selection(IndicatorGenerationBinding binding,long epoch,String proofId,
                            LocalDate cutoff,long verifiedAt) {}
    private final JdbcTemplate jdbc;
    private final GenerationRegistry registry;
    private final ObjectMapper json=IndicatorListener.mapper().enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);

    public ActiveProjectionPointer(JdbcTemplate jdbc,GenerationRegistry registry) {
        this.jdbc=jdbc;
        this.registry=registry;
    }

    public Optional<Selection> current() {
        try {
            var rows=jdbc.queryForList("SELECT a.generation,a.epoch,a.proof_id,p.generation AS proof_generation,p.kind,p.cutoff,"
                +"p.manifest_sha256 AS proof_manifest,p.source_rows,p.source_stocks,p.evidence_json,p.evidence_sha256,p.created_at,"
                +"g.manifest_sha256 AS generation_manifest,g.state FROM indicator_projection_active a "
                +"LEFT JOIN indicator_projection_proof p ON p.id=a.proof_id AND p.generation=a.generation "
                +"LEFT JOIN indicator_generation g ON g.id=a.generation WHERE a.slot='daily'");
            if(rows.size()!=1)throw invalid();
            var row=rows.getFirst();long epoch=number(row.get("epoch"));
            if(row.get("generation")==null) {
                if(epoch!=0 || row.get("proof_id")!=null)throw invalid();
                return Optional.empty();
            }
            String generation=hex(row.get("generation"),32),proofId=hex(row.get("proof_id"),32);
            String manifest=hex(row.get("proof_manifest"),64);
            if(epoch==0 || !generation.equals(row.get("proof_generation"))
                    || !"CURRENT".equals(row.get("kind")) || !"ACTIVE".equals(row.get("state"))
                    || !manifest.equals(row.get("generation_manifest")))throw invalid();
            LocalDate cutoff=LocalDate.parse((String)row.get("cutoff"));
            if(cutoff.isBefore(LocalDate.of(2023,1,1)))throw invalid();
            long count=number(row.get("source_rows")),stocks=number(row.get("source_stocks"));
            if(count==0 || stocks==0 || stocks>10000)throw invalid();
            String evidence=(String)row.get("evidence_json");
            if(evidence==null || evidence.length()>16*1024*1024 || evidence.chars().anyMatch(c->c>127))throw invalid();
            byte[] expected=HexFormat.of().parseHex(hex(row.get("evidence_sha256"),64));
            if(!MessageDigest.isEqual(expected,MessageDigest.getInstance("SHA-256").digest(evidence.getBytes(StandardCharsets.US_ASCII))))throw invalid();
            JsonNode node=json.readTree(evidence);
            if(node==null || !node.isObject() || !generation.equals(node.path("generation").asText())
                    || !"CURRENT".equals(node.path("kind").asText()) || !cutoff.toString().equals(node.path("cutoff").asText())
                    || !manifest.equals(node.path("manifest_sha256").asText())
                    || !node.path("source_rows").isIntegralNumber() || !node.path("source_rows").canConvertToLong() || node.path("source_rows").longValue()!=count
                    || !node.path("source_stocks").isIntegralNumber() || !node.path("source_stocks").canConvertToLong() || node.path("source_stocks").longValue()!=stocks)throw invalid();
            hex(node.path("fence_token").asText(),32);
            return Optional.of(new Selection(registry.require(generation),epoch,proofId,cutoff,number(row.get("created_at"))));
        } catch(Exception error) {
            throw invalid();
        }
    }

    public boolean stillCurrent(Selection selected) {
        return current().filter(selected::equals).isPresent();
    }

    private static long number(Object value) {
        if(!(value instanceof Number))throw invalid();
        long result=new BigDecimal(value.toString()).longValueExact();
        if(result<0)throw invalid();
        return result;
    }

    private static String hex(Object value,int size) {
        if(!(value instanceof String text) || !text.matches("[0-9a-f]{"+size+"}"))throw invalid();
        return text;
    }

    private static DependencyException invalid() {return new DependencyException();}
}
