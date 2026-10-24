-- Company identity for past index members (phase 2, addendum 3.1-3.2).
-- The S&P 500 Wikipedia table has carried each member's CIK since 2014-06; NULL before that and
-- for every S&P 400 revision.
ALTER TABLE index_snapshots ADD COLUMN cik INTEGER;

-- A past member's ticker whose prices live under another symbol (a rename: ANTM -> ELV).
CREATE TABLE ticker_aliases (
    ticker       TEXT PRIMARY KEY,
    price_symbol TEXT NOT NULL,
    cik          INTEGER,
    source       TEXT NOT NULL            -- 'cik' | 'manual'
);

-- Why a past member is left out of research: no prices under any symbol (acquired, other,
-- unknown), or its ticker now belongs to another company (recycled).
CREATE TABLE member_fates (
    ticker TEXT PRIMARY KEY,
    fate   TEXT NOT NULL,                 -- 'acquired' | 'other' | 'unknown' | 'recycled'
    reason TEXT
);
