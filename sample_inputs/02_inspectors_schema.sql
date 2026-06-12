-- INSPECTORS TABLE
-- Created by: Rajesh K, backend team
-- DO NOT MODIFY without DBA approval
-- Last reviewed: March 2024

create table inspectors
(
    inspector_id    int,            -- auto increment intended but not set up
    emp_code        nvarchar(15),   -- IBM-style code e.g. EMP-00123
    full_name       nvarchar(255)   not null,
    email           nvarchar(150),
    phone           nvarchar(20),
    department      nvarchar(100),  -- e.g. "Fire Safety", "Structural", "Health & Hygiene"
    license_no      nvarchar(30),   -- format: LIC-YYYY-NNNNNN
    license_expiry  date,
    cert_level      nvarchar(10),   -- L1 / L2 / L3 / SENIOR
    is_available    bit,            -- 1=available, 0=on leave or suspended
    joined_date     date,
    supervisor_id   int,            -- references inspector_id of supervisor, NOT enforced
    region_codes    nvarchar(200)   -- comma separated zone codes e.g. "A,B,C" or "A" or null
    -- No constraints. No PK. supervisor_id has no FK.
)

-- Known issues (from issue_tracker export):
-- INC-2024-0088: Several inspectors have NULL license_expiry - imported from old HR system
-- INC-2024-0091: emp_code format inconsistent - some use EMP-XXXXX some use just numbers
-- INC-2024-0102: region_codes sometimes has spaces "A, B" vs "A,B"
