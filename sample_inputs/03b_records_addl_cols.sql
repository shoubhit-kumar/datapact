-- inspection_records - PART 2 of 2
-- Additional columns added post-migration (Sprint 19-21)
-- These columns DO NOT EXIST in records before 2024-01-01

ALTER TABLE inspection_records ADD
    geo_lat             DECIMAL(9,6),   -- GPS latitude, only for mobile app inspections
    geo_long            DECIMAL(9,6),   -- GPS longitude, only for mobile app inspections
    weather_condition   VARCHAR(50),    -- added for outdoor inspections only
    client_sign_off     BIT,            -- 1 if client signed digital form, else 0 or NULL
    followup_required   BIT,            -- 1 if re-inspection needed
    followup_deadline   DATE,           -- NULL if followup_required = 0
    inspector_notes     NVARCHAR(MAX),  -- free text, unstructured
    last_modified_by    VARCHAR(100),
    last_modified_at    DATETIME;

-- WARNING from DBA (email thread 2024-03-15):
-- "client_sign_off is NULL for all records before the digital form rollout (Jan 2024)
--  Do NOT treat NULL as 0 for this column - it means data was not captured"
-- "followup_deadline has some records where it is set even when followup_required = 0
--  This is a bug from the auto-populate script, Jira ticket DEV-4821"
