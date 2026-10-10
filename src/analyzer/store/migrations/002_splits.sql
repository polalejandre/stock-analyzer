-- Split events behind the split-adjusted `prices.close`; see analyzer.universe.market_cap.
CREATE TABLE splits (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,                 -- first trading day on the post-split basis
    ratio  REAL NOT NULL,                 -- 10.0 = 10-for-1, 0.1 = 1-for-10 reverse split
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;
