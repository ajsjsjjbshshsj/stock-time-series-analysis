package com.stock.consumer.daily;

import com.stock.common.model.StockDailyEvent;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class StockDailyEventValidatorTest {

    private final StockDailyEventValidator validator = new StockDailyEventValidator(1);

    @Test
    void acceptsValidEventAndNullableChangeFields() {
        StockDailyEvent base = validEvent();
        StockDailyEvent event = new StockDailyEvent(base.eventId(), base.traceId(), base.tsCode(), base.tradeDate(),
                base.open(), base.high(), base.low(), base.close(), base.preClose(), null, null, base.volume(),
                base.amount(), base.source(), base.eventTime(), base.ingestTime(), base.schemaVersion());

        assertDoesNotThrow(() -> validator.validate("000001.SZ", event));
    }

    @Test
    void reportsEveryMissingRequiredField() {
        StockDailyEvent invalid = new StockDailyEvent(
                " ", "", null, null, null, null, null, null, null,
                null, null, null, null, " ", null, null, null);

        StockDailyEventValidationException exception = assertThrows(
                StockDailyEventValidationException.class,
                () -> validator.validate(null, invalid));

        for (String field : new String[]{"eventId", "traceId", "tsCode", "tradeDate", "open", "high",
                "low", "close", "preClose", "volume", "amount", "source", "eventTime", "ingestTime",
                "schemaVersion", "kafkaKey"}) {
            assertTrue(exception.violations().stream().anyMatch(message -> message.contains(field)), field);
        }
    }

    @Test
    void rejectsUnsupportedVersionSeparately() {
        StockDailyEvent event = copy(validEvent(), null, null, null, null, null, null, null, null,
                null, null, null, null, null, null, null, null, 2, null);

        UnsupportedSchemaVersionException exception = assertThrows(
                UnsupportedSchemaVersionException.class,
                () -> validator.validate("600000.SH", event));

        assertTrue(exception.getMessage().contains("2"));
        assertTrue(exception.getMessage().contains("1"));
    }

    @Test
    void rejectsNegativeCloseVolumeAndAmount() {
        StockDailyEvent event = copy(validEvent(), null, null, null, null, null, null, null,
                new BigDecimal("-0.01"), null, null, null, new BigDecimal("-1"),
                new BigDecimal("-2"), null, null, null, null, null);

        StockDailyEventValidationException exception = assertThrows(
                StockDailyEventValidationException.class,
                () -> validator.validate("000001.SZ", event));

        assertTrue(exception.violations().stream().anyMatch(message -> message.contains("close")));
        assertTrue(exception.violations().stream().anyMatch(message -> message.contains("volume")));
        assertTrue(exception.violations().stream().anyMatch(message -> message.contains("amount")));
    }

    private static StockDailyEvent validEvent() {
        return new StockDailyEvent("TUSHARE:000001.SZ:20260703", "daily-tushare-20260703-10001",
                "000001.SZ", LocalDate.of(2026, 7, 3), new BigDecimal("10.25"), new BigDecimal("10.68"),
                new BigDecimal("10.12"), new BigDecimal("10.55"), new BigDecimal("10.20"),
                new BigDecimal("0.35"), new BigDecimal("3.4314"), new BigDecimal("1250345.00"),
                new BigDecimal("13054890.25"), "TUSHARE", OffsetDateTime.parse("2026-07-03T15:00:00+08:00"),
                OffsetDateTime.parse("2026-07-03T16:20:30+08:00"), 1);
    }

    private static StockDailyEvent copy(StockDailyEvent base, String eventId, String traceId, String tsCode,
                                        LocalDate tradeDate, BigDecimal open, BigDecimal high, BigDecimal low,
                                        BigDecimal close, BigDecimal preClose, BigDecimal change, BigDecimal pctChg,
                                        BigDecimal volume, BigDecimal amount, String source, OffsetDateTime eventTime,
                                        OffsetDateTime ingestTime, Integer schemaVersion, Boolean unused) {
        return new StockDailyEvent(eventId != null ? eventId : base.eventId(), traceId != null ? traceId : base.traceId(),
                tsCode != null ? tsCode : base.tsCode(), tradeDate != null ? tradeDate : base.tradeDate(),
                open != null ? open : base.open(), high != null ? high : base.high(), low != null ? low : base.low(),
                close != null ? close : base.close(), preClose != null ? preClose : base.preClose(),
                change != null ? change : base.change(), pctChg != null ? pctChg : base.pctChg(),
                volume != null ? volume : base.volume(), amount != null ? amount : base.amount(),
                source != null ? source : base.source(), eventTime != null ? eventTime : base.eventTime(),
                ingestTime != null ? ingestTime : base.ingestTime(), schemaVersion != null ? schemaVersion : base.schemaVersion());
    }
}
