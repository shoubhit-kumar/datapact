-- Municipal Inspection Management System
-- Table: inspection_sites
-- Owner: City Planning Dept
-- Last modified: sometime in 2024 (exact date unknown)
-- NOTE: zone_code was added in sprint 14, may not be backfilled for older records

CREATE TABLE inspection_sites (
    site_id         VARCHAR(20),        -- should be SITE-XXXXX format but not enforced
    site_name       VARCHAR(200) NOT NULL,
    address_line1   VARCHAR(300),
    address_line2   VARCHAR(300),       -- optional, often blank
    city            VARCHAR(100),
    pincode         VARCHAR(10),        -- sometimes 6 digit, sometimes with spaces
    zone_code       CHAR(1),            -- A, B or C. NULL for pre-2022 records
    site_type       VARCHAR(50),        -- Building / Industrial / Food / Healthcare
    constructed_yr  INT,               -- year of construction, not always known
    area_sqft       DECIMAL(10,2),
    owner_name      VARCHAR(200),
    owner_contact   VARCHAR(50),        -- phone or email, mixed formats
    is_active       VARCHAR(5),         -- 'true'/'false' as string, legacy decision
    created_at      DATETIME,
    updated_at      DATETIME
    -- NO PRIMARY KEY DEFINED - site_id intended as PK but not enforced
    -- NO INDEXES
);

-- Note from dev team (Slack export 2023-11-02):
-- "site_id should always be unique but we had a bulk load issue in Q3 2023
--  that created ~200 duplicates, most were cleaned but not all confirmed"
