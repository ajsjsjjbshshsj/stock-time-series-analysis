package com.stock.consumer.daily;

/** Raised separately so unsupported protocol versions can be monitored and isolated. */
public final class UnsupportedSchemaVersionException extends RuntimeException {

    private final int actualVersion;
    private final int supportedVersion;

    public UnsupportedSchemaVersionException(int actualVersion, int supportedVersion) {
        super("Unsupported schemaVersion " + actualVersion + "; supported version is " + supportedVersion);
        this.actualVersion = actualVersion;
        this.supportedVersion = supportedVersion;
    }

    public int actualVersion() {
        return actualVersion;
    }

    public int supportedVersion() {
        return supportedVersion;
    }
}
