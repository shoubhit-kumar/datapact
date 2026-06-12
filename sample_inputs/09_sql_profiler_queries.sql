-- ============================================================
-- DataPact SQL Profiler Agent — Query Library
-- Database: inspectiondb (Azure SQL)
-- Purpose: Profile actual data to discover patterns, ranges,
--          null rates, relationships not in DDL
-- Run by: SQL Profiler Agent during DataPact analysis
-- ============================================================

-- ============================================================
-- SECTION 1: NULL RATE PROFILING (per column)
-- ============================================================

-- 1a. inspection_sites null rates
SELECT
    'inspection_sites'              AS table_name,
    COUNT(*)                        AS total_rows,
    SUM(CASE WHEN site_id IS NULL THEN 1 ELSE 0 END)          AS site_id_nulls,
    SUM(CASE WHEN zone_code IS NULL THEN 1 ELSE 0 END)        AS zone_code_nulls,
    SUM(CASE WHEN site_type IS NULL THEN 1 ELSE 0 END)        AS site_type_nulls,
    SUM(CASE WHEN area_sqft IS NULL THEN 1 ELSE 0 END)        AS area_sqft_nulls,
    SUM(CASE WHEN constructed_yr IS NULL THEN 1 ELSE 0 END)   AS constructed_yr_nulls,
    SUM(CASE WHEN owner_contact IS NULL THEN 1 ELSE 0 END)    AS owner_contact_nulls,
    SUM(CASE WHEN pincode IS NULL THEN 1 ELSE 0 END)          AS pincode_nulls,
    ROUND(SUM(CASE WHEN zone_code IS NULL THEN 1.0 ELSE 0 END) / COUNT(*) * 100, 2) AS zone_code_null_pct
FROM inspection_sites;

-- 1b. inspectors null rates
SELECT
    'inspectors'                    AS table_name,
    COUNT(*)                        AS total_rows,
    SUM(CASE WHEN license_expiry IS NULL THEN 1 ELSE 0 END)   AS license_expiry_nulls,
    SUM(CASE WHEN license_no IS NULL THEN 1 ELSE 0 END)       AS license_no_nulls,
    SUM(CASE WHEN region_codes IS NULL THEN 1 ELSE 0 END)     AS region_codes_nulls,
    SUM(CASE WHEN supervisor_id IS NULL THEN 1 ELSE 0 END)    AS supervisor_id_nulls,
    ROUND(SUM(CASE WHEN license_expiry IS NULL AND is_available = 1 THEN 1.0 ELSE 0 END)
          / COUNT(*) * 100, 2)                                AS active_missing_license_pct
FROM inspectors;

-- 1c. inspection_records null rates
SELECT
    'inspection_records'            AS table_name,
    COUNT(*)                        AS total_rows,
    SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END)            AS score_nulls,
    SUM(CASE WHEN duration_hrs IS NULL THEN 1 ELSE 0 END)     AS duration_nulls,
    SUM(CASE WHEN geo_lat IS NULL THEN 1 ELSE 0 END)          AS geo_lat_nulls,
    SUM(CASE WHEN client_sign_off IS NULL THEN 1 ELSE 0 END)  AS client_sign_off_nulls,
    SUM(CASE WHEN submitted_at IS NULL THEN 1 ELSE 0 END)     AS submitted_at_nulls
FROM inspection_records;

-- ============================================================
-- SECTION 2: VALUE DISTRIBUTION AND RANGE PROFILING
-- ============================================================

-- 2a. Distinct values for categorical columns
SELECT zone_code, COUNT(*) AS cnt
FROM inspection_sites
GROUP BY zone_code
ORDER BY cnt DESC;

SELECT overall_status, COUNT(*) AS cnt
FROM inspection_records
GROUP BY overall_status
ORDER BY cnt DESC;

SELECT cert_level, COUNT(*) AS cnt
FROM inspectors
GROUP BY cert_level
ORDER BY cnt DESC;

SELECT inspection_type, COUNT(*) AS cnt
FROM inspection_records
GROUP BY inspection_type
ORDER BY cnt DESC;

-- 2b. Numeric ranges
SELECT
    MIN(score)          AS score_min,
    MAX(score)          AS score_max,
    AVG(score)          AS score_avg,
    MIN(duration_hrs)   AS duration_min,
    MAX(duration_hrs)   AS duration_max,
    MIN(findings_count) AS findings_min,
    MAX(findings_count) AS findings_max,
    MIN(area_sqft)      AS area_min,
    MAX(area_sqft)      AS area_max,
    MIN(constructed_yr) AS yr_min,
    MAX(constructed_yr) AS yr_max
FROM inspection_records ir
LEFT JOIN inspection_sites s ON ir.site_id = s.site_id;

-- ============================================================
-- SECTION 3: PATTERN PROFILING
-- ============================================================

-- 3a. site_id format compliance
SELECT
    COUNT(*)                                                        AS total,
    SUM(CASE WHEN site_id LIKE 'SITE-[0-9][0-9][0-9][0-9][0-9]'
             THEN 1 ELSE 0 END)                                    AS pattern_match,
    SUM(CASE WHEN site_id NOT LIKE 'SITE-[0-9][0-9][0-9][0-9][0-9]'
             OR site_id IS NULL THEN 1 ELSE 0 END)                 AS pattern_violation
FROM inspection_sites;

-- 3b. emp_code format compliance
SELECT
    COUNT(*)                                                        AS total,
    SUM(CASE WHEN emp_code LIKE 'EMP-[0-9][0-9][0-9][0-9][0-9]'
             THEN 1 ELSE 0 END)                                    AS pattern_match,
    SUM(CASE WHEN emp_code NOT LIKE 'EMP-[0-9][0-9][0-9][0-9][0-9]'
             THEN 1 ELSE 0 END)                                    AS pattern_violation
FROM inspectors;

-- 3c. pincode format check
SELECT
    COUNT(*)                                                        AS total,
    SUM(CASE WHEN pincode LIKE '[0-9][0-9][0-9][0-9][0-9][0-9]'
             THEN 1 ELSE 0 END)                                    AS clean_pincode,
    SUM(CASE WHEN pincode NOT LIKE '[0-9][0-9][0-9][0-9][0-9][0-9]'
             AND pincode IS NOT NULL THEN 1 ELSE 0 END)            AS dirty_pincode
FROM inspection_sites;

-- ============================================================
-- SECTION 4: DUPLICATE DETECTION
-- ============================================================

-- 4a. Duplicate site_ids
SELECT site_id, COUNT(*) AS duplicate_count
FROM inspection_sites
GROUP BY site_id
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC;

-- 4b. Duplicate emp_codes in inspectors
SELECT emp_code, COUNT(*) AS duplicate_count
FROM inspectors
WHERE emp_code IS NOT NULL
GROUP BY emp_code
HAVING COUNT(*) > 1;

-- ============================================================
-- SECTION 5: REFERENTIAL INTEGRITY DISCOVERY
-- ============================================================

-- 5a. Orphan inspection records (site_id not in sites)
SELECT COUNT(*) AS orphan_records
FROM inspection_records ir
WHERE NOT EXISTS (
    SELECT 1 FROM inspection_sites s WHERE s.site_id = ir.site_id
);

-- 5b. Orphan inspection records (inspector_id not in inspectors)
SELECT COUNT(*) AS orphan_inspector_refs
FROM inspection_records ir
WHERE NOT EXISTS (
    SELECT 1 FROM inspectors i WHERE i.inspector_id = ir.inspector_id
);

-- 5c. Supervisor self-reference or non-existent
SELECT COUNT(*) AS bad_supervisor_refs
FROM inspectors i
WHERE supervisor_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1 FROM inspectors s WHERE s.inspector_id = i.supervisor_id
);

-- ============================================================
-- SECTION 6: CROSS-TABLE BUSINESS RULE VALIDATION
-- ============================================================

-- 6a. Inspector cert_level vs site zone_code mismatch
SELECT
    ir.record_id,
    i.cert_level,
    s.zone_code,
    'L1 assigned to Zone ' + s.zone_code AS violation
FROM inspection_records ir
JOIN inspectors i ON ir.inspector_id = i.inspector_id
JOIN inspection_sites s ON ir.site_id = s.site_id
WHERE i.cert_level = 'L1' AND s.zone_code IN ('A', 'B')
UNION ALL
SELECT
    ir.record_id,
    i.cert_level,
    s.zone_code,
    'L2 assigned to Zone A'
FROM inspection_records ir
JOIN inspectors i ON ir.inspector_id = i.inspector_id
JOIN inspection_sites s ON ir.site_id = s.site_id
WHERE i.cert_level = 'L2' AND s.zone_code = 'A';

-- 6b. Score vs status consistency violations
SELECT
    record_id, score, overall_status,
    CASE
        WHEN score >= 85 AND overall_status != 'PASS'       THEN 'Score PASS but status not PASS'
        WHEN score < 60  AND overall_status != 'FAIL'       THEN 'Score FAIL but status not FAIL'
        WHEN score IS NULL AND overall_status != 'DEFERRED' THEN 'Score NULL but status not DEFERRED'
        WHEN overall_status = 'FAIL' AND (findings_count = 0 OR findings_count IS NULL)
                                                            THEN 'FAIL with zero findings'
    END AS violation_type
FROM inspection_records
WHERE
    (score >= 85 AND overall_status != 'PASS')
    OR (score < 60 AND overall_status != 'FAIL')
    OR (score IS NULL AND overall_status != 'DEFERRED')
    OR (overall_status = 'FAIL' AND (findings_count = 0 OR findings_count IS NULL));

-- 6c. Critical findings exceeds total findings
SELECT record_id, findings_count, critical_findings
FROM inspection_records
WHERE critical_findings > findings_count;

-- 6d. followup_deadline set but followup_required = 0 (scoped post bug-fix)
SELECT COUNT(*) AS logic_errors
FROM inspection_records
WHERE followup_required = 0
AND followup_deadline IS NOT NULL
AND created_at >= '2024-06-01';  -- scope to after DEV-4821 fix

-- 6e. Active inspector with expired license assigned to inspection
SELECT
    ir.record_id, ir.inspection_date,
    i.emp_code, i.license_expiry
FROM inspection_records ir
JOIN inspectors i ON ir.inspector_id = i.inspector_id
WHERE i.license_expiry < ir.inspection_date
AND i.is_available = 1;

-- ============================================================
-- SECTION 7: ROW COUNT AND GROWTH MONITORING
-- ============================================================

SELECT 'inspection_sites'   AS tbl, COUNT(*) AS row_count FROM inspection_sites
UNION ALL
SELECT 'inspectors',         COUNT(*) FROM inspectors
UNION ALL
SELECT 'inspection_records', COUNT(*) FROM inspection_records;

-- Weekly growth rate
SELECT
    DATEPART(YEAR, inspection_date)  AS yr,
    DATEPART(WEEK, inspection_date)  AS wk,
    COUNT(*)                         AS records_created
FROM inspection_records
WHERE inspection_date >= DATEADD(WEEK, -8, GETDATE())
GROUP BY DATEPART(YEAR, inspection_date), DATEPART(WEEK, inspection_date)
ORDER BY yr, wk;
