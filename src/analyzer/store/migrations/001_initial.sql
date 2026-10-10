CREATE TABLE companies (
    cik             INTEGER PRIMARY KEY,
    name            TEXT,
    sic             TEXT,
    sic_description TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE tickers (
    ticker            TEXT PRIMARY KEY,
    cik               INTEGER,
    name              TEXT,
    exchange          TEXT,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    sector_etf        TEXT,
    industry_etf      TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE index_snapshots (
    index_name        TEXT NOT NULL,      -- 'sp500' | 'sp400'
    snapshot_date     TEXT NOT NULL,      -- the date the revision represents
    revision_id       INTEGER NOT NULL,
    ticker            TEXT NOT NULL,
    security          TEXT,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    date_added        TEXT,
    PRIMARY KEY (index_name, snapshot_date, ticker)
);

CREATE TABLE index_membership (
    index_name TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT,                      -- exclusive; NULL means still a member
    PRIMARY KEY (index_name, ticker, start_date)
);

CREATE TABLE young_listings (
    ticker           TEXT PRIMARY KEY,
    cik              INTEGER,
    first_trade_date TEXT NOT NULL,
    screened_on      TEXT NOT NULL
);

CREATE TABLE prices (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL NOT NULL,
    adj_close REAL,
    volume    REAL,
    source    TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;

CREATE TABLE facts (
    cik          INTEGER NOT NULL,
    concept      TEXT NOT NULL,
    tag          TEXT NOT NULL,           -- 'us-gaap:Revenues', 'dei:EntityCommonStockSharesOutstanding'
    unit         TEXT NOT NULL,
    period_start TEXT NOT NULL DEFAULT '',-- '' for instant facts
    period_end   TEXT NOT NULL,
    value        REAL NOT NULL,
    filed        TEXT NOT NULL,
    form         TEXT,
    accession    TEXT NOT NULL,
    source       TEXT NOT NULL,           -- 'companyfacts' | 'instance'
    PRIMARY KEY (cik, concept, tag, period_start, period_end, accession)
) WITHOUT ROWID;

CREATE TABLE filings (
    accession        TEXT PRIMARY KEY,
    cik              INTEGER NOT NULL,
    form             TEXT NOT NULL,
    filing_date      TEXT NOT NULL,
    report_date      TEXT,
    items            TEXT,
    primary_document TEXT
);
CREATE INDEX filings_by_cik ON filings (cik, form, filing_date);

CREATE TABLE insider_transactions (
    accession         TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    issuer_cik        INTEGER NOT NULL,
    filing_date       TEXT NOT NULL,
    owner_names       TEXT,
    relationship      TEXT,               -- e.g. 'Director,Officer'
    officer_title     TEXT,
    trans_date        TEXT,
    trans_code        TEXT,               -- 'P' = open-market purchase
    shares            REAL,
    price             REAL,
    acquired_disposed TEXT,
    source            TEXT NOT NULL,      -- 'bulk' | 'xml'
    PRIMARY KEY (accession, seq)
);
CREATE INDEX insider_by_issuer ON insider_transactions (issuer_cik, trans_code, trans_date);

CREATE TABLE macro (
    series_id TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (series_id, date)
) WITHOUT ROWID;

CREATE TABLE provider_status (
    provider         TEXT PRIMARY KEY,
    last_success     TEXT NOT NULL,
    latest_data_date TEXT,
    detail           TEXT
);

CREATE TABLE fetch_failures (
    run_id TEXT NOT NULL,
    step   TEXT NOT NULL,
    item   TEXT NOT NULL,
    error  TEXT NOT NULL,
    at     TEXT NOT NULL
);

CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
