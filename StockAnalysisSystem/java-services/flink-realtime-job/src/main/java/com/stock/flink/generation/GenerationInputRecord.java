package com.stock.flink.generation;

import com.stock.common.model.GenerationInputEnvelope;
import com.stock.flink.model.RawKafkaRecord;

public record GenerationInputRecord(RawKafkaRecord raw, GenerationInputEnvelope envelope) {
}
