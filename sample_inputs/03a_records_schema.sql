-- inspection_records - PART 1 of 2
-- This table was split during a migration in FY2024
-- Part 2 is in 03b_records_addl_cols.sql

CREATE TABLE inspection_records (
    record_id           BIGINT,
    site_id             VARCHAR(20),    -- should match inspection_sites.site_id
    inspector_id        INT,            -- should match inspectors.inspector_id
    inspection_date     DATE,
    inspection_type     VARCHAR(30),    -- Routine / Surprise / Complaint-Based / Re-inspection
    overall_status      VARCHAR(15),    -- PASS / FAIL / PARTIAL / DEFERRED
    score               DECIMAL(5,2),   -- 0 to 100, sometimes NULL if inspection incomplete
    duration_hrs        DECIMAL(4,1),   -- time taken, sometimes missing
    findings_count      INT,            -- number of violations found
    critical_findings   INT,            -- subset of findings_count that are critical
    report_submitted    VARCHAR(5),     -- 'Yes'/'No'/'yes'/'no' - inconsistent casing
    submitted_at        DATETIME,       -- NULL if report_submitted = No
    next_due_date       DATE,           -- calculated field, sometimes wrong
    created_by          VARCHAR(100)    -- inspector name or emp_code, not consistent
)
