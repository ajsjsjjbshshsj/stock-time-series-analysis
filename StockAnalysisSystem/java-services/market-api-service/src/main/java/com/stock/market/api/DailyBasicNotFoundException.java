package com.stock.market.api;

/** A known stock has no valuation row; distinct from an absent HTTP route. */
public final class DailyBasicNotFoundException extends RuntimeException {}
