import logging
import json
import pyodbc
import os
import urllib.request
import azure.functions as func
import azure.servicebus as servicebus
import hashlib
import re
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

SERVER          = os.environ["SQL_SERVER"]
DATABASE        = os.environ["SQL_DATABASE"]
USERNAME        = os.environ["SQL_USERNAME"]
PASSWORD        = os.environ["SQL_PASSWORD"]
DRIVER          = os.environ["SQL_DRIVER"]
OPENAI_ENDPOINT = os.environ["OPENAI_ENDPOINT"]
OPENAI_KEY      = os.environ["OPENAI_KEY"]
OPENAI_DEPLOY   = os.environ.get("OPENAI_DEPLOYMENT", "gpt-4-1-mini")
SEARCH_ENDPOINT   = os.environ["SEARCH_ENDPOINT"]
SEARCH_KEY        = os.environ["SEARCH_KEY"]
SEARCH_INDEX      = os.environ["SEARCH_INDEX"]
STORAGE_CONNECTION = os.environ["STORAGE_CONNECTION"]
DOCINTEL_ENDPOINT = os.environ["DOCINTEL_ENDPOINT"]
DOCINTEL_KEY      = os.environ["DOCINTEL_KEY"]

ALLOWED_DATABASES = ["inspectiondb"]

# ── Connection ────────────────────────────────────────────────

def get_connection(database=None):
    db = database if database else DATABASE
    conn_str = (
        f"DRIVER={DRIVER};"
        f"SERVER={SERVER};"
        f"DATABASE={db};"
        f"UID={USERNAME};"
        f"PWD={PASSWORD};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)

def get_columns(cursor, table_name):
    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, table_name)
    return cursor.fetchall()

# ── Azure OpenAI ──────────────────────────────────────────────

def call_openai(prompt, max_tokens=1200):
    url = (
        f"{OPENAI_ENDPOINT}/openai/deployments/{OPENAI_DEPLOY}"
        f"/chat/completions?api-version=2024-02-01"
    )
    payload = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "api-key": OPENAI_KEY}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()

# ── Schema helpers ────────────────────────────────────────────

def schema_profile(cursor, table_name):
    cursor.execute("""
        SELECT c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE,
               c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION,
               c.NUMERIC_SCALE, c.COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS c
        WHERE c.TABLE_NAME = ?
        ORDER BY c.ORDINAL_POSITION
    """, table_name)
    return [
        {
            "column_name": r[0], "data_type": r[1],
            "is_nullable": r[2], "max_length": r[3],
            "numeric_precision": r[4], "numeric_scale": r[5],
            "column_default": r[6]
        }
        for r in cursor.fetchall()
    ]

def get_constraints(cursor, table_name):
    cursor.execute("""
        SELECT c.COLUMN_NAME, tc.CONSTRAINT_TYPE
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE c
            ON tc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
        WHERE tc.TABLE_NAME = ?
        AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE')
    """, table_name)
    return {row[0]: row[1] for row in cursor.fetchall()}

def get_column_comments(cursor, table_name):
    try:
        cursor.execute("""
            SELECT c.name, CAST(ep.value AS NVARCHAR(1000))
            FROM sys.tables t
            JOIN sys.columns c ON t.object_id = c.object_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = c.object_id
                AND ep.minor_id = c.column_ordinal
                AND ep.name = 'MS_Description'
            WHERE t.name = ? AND ep.value IS NOT NULL
        """, table_name)
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception:
        return {}

def get_table_comment(cursor, table_name):
    try:
        cursor.execute("""
            SELECT CAST(ep.value AS NVARCHAR(1000))
            FROM sys.tables t
            JOIN sys.extended_properties ep ON ep.major_id = t.object_id
            WHERE t.name = ? AND ep.minor_id = 0 AND ep.name = 'MS_Description'
        """, table_name)
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        return None

def get_cardinality(cursor, table_name, columns):
    results = {}
    for col, dtype in columns:
        cursor.execute(f"""
            SELECT COUNT(*) AS total, COUNT(DISTINCT [{col}]) AS distinct_count
            FROM [{table_name}] WHERE [{col}] IS NOT NULL
        """)
        row = cursor.fetchone()
        total, distinct = row[0], row[1]
        results[col] = {
            "total": total,
            "distinct_count": distinct,
            "cardinality_ratio": round(distinct / total, 4) if total > 0 else 0
        }
    return results

# ── Data profilers ────────────────────────────────────────────

def null_profile(cursor, table_name, columns):
    results = []
    for col, dtype in columns:
        cursor.execute(f"""
            SELECT COUNT(*) AS total_rows,
                   SUM(CASE WHEN [{col}] IS NULL THEN 1 ELSE 0 END) AS null_count,
                   ROUND(SUM(CASE WHEN [{col}] IS NULL THEN 1.0 ELSE 0 END)/COUNT(*)*100, 2) AS null_pct
            FROM [{table_name}]
        """)
        row = cursor.fetchone()
        results.append({
            "column_name": col,
            "total_rows": row[0],
            "null_count": row[1],
            "null_pct": float(row[2]) if row[2] else 0.0
        })
    results.sort(key=lambda x: x["null_pct"], reverse=True)
    return results

def distinct_values(cursor, table_name, columns):
    results = []
    categorical = ("varchar", "nvarchar", "char", "nchar", "text")
    for col, dtype in columns:
        if dtype.lower() in categorical:
            cursor.execute(f"""
                SELECT TOP 20 CAST([{col}] AS NVARCHAR(200)), COUNT(*) AS frequency
                FROM [{table_name}]
                WHERE [{col}] IS NOT NULL
                GROUP BY [{col}]
                ORDER BY COUNT(*) DESC
            """)
            for row in cursor.fetchall():
                results.append({
                    "column_name": col,
                    "distinct_value": row[0],
                    "frequency": row[1]
                })
    return results

def range_profile(cursor, table_name, columns):
    results = []
    numeric = ("int", "bigint", "smallint", "tinyint", "decimal",
               "numeric", "float", "real", "money")
    for col, dtype in columns:
        if dtype.lower() in numeric:
            cursor.execute(f"""
                SELECT MIN([{col}]), MAX([{col}]),
                       AVG(CAST([{col}] AS FLOAT)),
                       SUM(CASE WHEN [{col}] IS NULL THEN 1 ELSE 0 END)
                FROM [{table_name}]
            """)
            row = cursor.fetchone()
            results.append({
                "column_name": col, "data_type": dtype,
                "min_val": str(row[0]) if row[0] is not None else None,
                "max_val": str(row[1]) if row[1] is not None else None,
                "avg_val": round(float(row[2]), 2) if row[2] is not None else None,
                "null_count": row[3]
            })
    return results

def smart_duplicate_profile(cursor, table_name, columns,
                             cardinality, constraints, column_comments):
    results = []
    uniqueness_keywords = [
        "unique", "primary", "identifier", "pk", " id", "id ",
        "not duplicate", "must be unique", "key"
    ]
    for col, dtype in columns:
        card  = cardinality.get(col, {})
        total = card.get("total", 0)
        ratio = card.get("cardinality_ratio", 0)
        if total < 5:
            continue
        has_constraint          = col in constraints
        high_cardinality        = ratio >= 0.95 and total > 10
        comment                 = column_comments.get(col, "").lower()
        comment_suggests_unique = any(kw in comment for kw in uniqueness_keywords)

        if not (has_constraint or high_cardinality or comment_suggests_unique):
            continue

        cursor.execute(f"""
            SELECT TOP 5 CAST([{col}] AS NVARCHAR(200)), COUNT(*) AS cnt
            FROM [{table_name}]
            WHERE [{col}] IS NOT NULL
            GROUP BY [{col}]
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        """)
        for row in cursor.fetchall():
            reason = []
            if has_constraint:
                reason.append(f"DB {constraints[col]} constraint")
            if high_cardinality:
                reason.append(f"cardinality ratio {ratio}")
            if comment_suggests_unique:
                reason.append("column comment suggests uniqueness")
            results.append({
                "column_name": col,
                "duplicate_value": row[0],
                "occurrences": row[1],
                "cardinality_ratio": ratio,
                "detection_reason": ", ".join(reason)
            })
    return results

# ── AI reasoning ──────────────────────────────────────────────

def ai_identify_unique_columns(table_name, schema, cardinality,
                               column_comments, table_comment,
                               constraints, distinct_vals,
                               rag_context=""):
    # Get top 3 sample values per column for AI context
    sample_vals = {}
    for item in distinct_vals:
        col = item["column_name"]
        if col not in sample_vals:
            sample_vals[col] = []
        if len(sample_vals[col]) < 3:
            sample_vals[col].append(item["distinct_value"])

    schema_summary = []
    for col in schema:
        col_name   = col["column_name"]
        card       = cardinality.get(col_name, {})
        comment    = column_comments.get(col_name, "")
        constraint = constraints.get(col_name, "none")
        samples    = sample_vals.get(col_name, [])
        schema_summary.append(
            f"- {col_name} ({col['data_type']}, nullable={col['is_nullable']}, "
            f"cardinality_ratio={card.get('cardinality_ratio', 'unknown')}, "
            f"distinct={card.get('distinct_count', '?')}/{card.get('total', '?')}, "
            f"max_length={col.get('max_length', '-')}, "
            f"constraint={constraint}, "
            f"sample_values={samples}, "
            f"comment='{comment}')"
        )

    table_ctx = f"Table comment: {table_comment}" if table_comment else "No table comment available."
    rag_section = f"\nRELEVANT DOCUMENTATION:\n{rag_context}\n" if rag_context else ""
    prompt = f"""You are a data quality expert analysing a database table called '{table_name}'.

{table_ctx}
{rag_section}
Column details:
{chr(10).join(schema_summary)}

Your task has TWO parts:

PART 1 — Identify columns that are logically intended to be UNIQUE identifiers or keys.
Rules for uniqueness:
- Columns with DB constraints (PRIMARY KEY, UNIQUE) are definitively unique
- Columns with cardinality_ratio = 1.0 AND meaningful identifier names are likely unique
- Column comments mentioning 'unique', 'identifier', 'pk', 'primary key' are strong signals
- High cardinality alone is weak evidence — name or comment must confirm intent
- NEVER flag as unique: name, address, city, description, notes, comment, email,
  phone, contact, department, status, type, category, owner fields
  even if cardinality appears high in a small sample

PART 2 — Identify columns where allowed-values (enum) rules are NOT appropriate.
Do NOT create enum rules for:
- Free-text fields: address, name, description, notes, comments, contact info
- Geographic data: city, country, state, region, district, area, zone (if free text)
- Person names: owner_name, full_name, inspector_name, created_by, modified_by
- Numeric measurements: area, size, weight, distance, score (unless fixed scale)
- Date and timestamp columns
- Any column where values are expected to grow or vary across deployments
- Columns with cardinality > 30% of total rows (too many values to be a true enum)

DO create enum rules for:
- Status columns: PASS/FAIL, ACTIVE/INACTIVE, true/false
- Classification codes: zone codes, type codes, category codes
- Fixed categorical lists explicitly defined in a data contract or business rule

Respond ONLY with valid JSON in this exact format, no explanation, no markdown:
{{
  "unique_columns": [
    {{
      "column_name": "example_id",
      "should_be_unique": true,
      "reasoning": "Primary key — DB constraint + name pattern + cardinality 1.0",
      "confidence": 0.98
    }}
  ],
  "exclude_enum_columns": [
    {{
      "column_name": "city",
      "reason": "Geographic free-text field — values vary across deployments"
    }}
  ]
}}"""

    try:
        response = call_openai(prompt, max_tokens=1200)
        response = response.replace("```json", "").replace("```", "").strip()
        parsed   = json.loads(response)

        unique_cols = {
            item["column_name"]: {
                "should_be_unique": item["should_be_unique"],
                "reasoning":        item["reasoning"],
                "confidence":       item["confidence"]
            }
            for item in parsed.get("unique_columns", [])
            if item.get("should_be_unique", False)
        }

        exclude_enum = {
            item["column_name"]: item["reason"]
            for item in parsed.get("exclude_enum_columns", [])
        }

        return unique_cols, exclude_enum

    except Exception as e:
        logging.warning(f"AI reasoning failed: {str(e)}")
        return {}, {}

# ── Rule generation ───────────────────────────────────────────

def generate_rules(profile, run_id, table_name, ai_unique_cols, exclude_enum_cols, rag_context=""):
    rules      = []
    violations = []
    counter    = [1]

    schema   = {c["column_name"]: c for c in profile.get("schema", [])}
    null_map = {c["column_name"]: c for c in profile.get("null_profile", [])}
    rng_map  = {c["column_name"]: c for c in profile.get("range_profile", [])}
    dups     = profile.get("duplicates", [])
    distinct = profile.get("distinct_values", [])

    distinct_by_col = {}
    for item in distinct:
        col = item["column_name"]
        if col not in distinct_by_col:
            distinct_by_col[col] = []
        distinct_by_col[col].append(item["distinct_value"])

    def rc():
        code = f"DQ-R{run_id}-{counter[0]:03d}"
        counter[0] += 1
        return code

    def rule(col, desc, conf, status, evidence,
             conflict="false", detail="none"):
        return {
            "run_id": run_id, "rule_code": rc(),
            "table_name": table_name, "column_name": col,
            "rule_description": desc, "confidence": conf,
            "status": status,
            "source_tools": "DataPactAnalysisWorker+OpenAI",
            "evidence": evidence,
            "conflict_detected": conflict,
            "conflict_detail": detail
        }

    def violation(col, vtype, sev, rows, detail, sample, sql):
        return {
            "run_id": run_id, "rule_code": f"DQ-R{run_id}-{counter[0]:03d}",
            "table_name": table_name, "column_name": col,
            "violation_type": vtype, "severity": sev,
            "affected_rows": rows, "violation_detail": detail,
            "sample_values": sample, "remediation_sql": sql,
            "status": "OPEN"
        }

    for col_name, col_info in schema.items():
        dtype       = col_info["data_type"]
        is_nullable = col_info["is_nullable"]
        max_length  = col_info["max_length"]
        null_info   = null_map.get(col_name, {})
        null_pct    = null_info.get("null_pct", 0)
        null_count  = null_info.get("null_count", 0)
        total_rows  = null_info.get("total_rows", 0)

        # NOT NULL — schema enforced
        if is_nullable == "NO":
            rules.append(rule(
                col_name,
                f"{col_name} must not be NULL — enforced by schema",
                0.95, "VALIDATED",
                f"Schema IS_NULLABLE=NO. {null_count} nulls in {total_rows} rows."
            ))

        # NOT NULL — schema gap (data never null but schema allows it)
        elif is_nullable == "YES" and null_pct == 0.0 and total_rows > 0:
            rules.append(rule(
                col_name,
                f"{col_name} must not be NULL — 0% null rate despite schema allowing NULL",
                0.85, "VALIDATED",
                f"0 nulls in {total_rows} rows. Schema IS_NULLABLE=YES is a DDL gap.",
                "true",
                f"Schema IS_NULLABLE=YES but 0 nulls — DDL should enforce NOT NULL"
            ))

        # Moderate null rate — needs review
        elif 0 < null_pct < 50:
            rules.append(rule(
                col_name,
                f"{col_name} has {null_pct}% null rate — confirm if NULL is acceptable",
                0.65, "NEEDS_VERIFICATION",
                f"{null_count} nulls in {total_rows} rows ({null_pct}%)"
            ))
            violations.append(violation(
                col_name, "NULL_VALUE",
                "HIGH" if null_pct > 20 else "MEDIUM",
                null_count,
                f"{null_pct}% null rate in {col_name}",
                "NULL",
                f"SELECT * FROM [{table_name}] WHERE [{col_name}] IS NULL"
            ))

        # Very high null rate
        elif null_pct >= 50:
            rules.append(rule(
                col_name,
                f"{col_name} has {null_pct}% null rate — column may be deprecated or optional",
                0.55, "NEEDS_VERIFICATION",
                f"{null_count} nulls in {total_rows} rows ({null_pct}%)"
            ))

        # Max length
        if max_length and dtype.lower() in ("varchar", "nvarchar", "char", "nchar"):
            rules.append(rule(
                col_name,
                f"{col_name} must not exceed {max_length} characters",
                0.95, "VALIDATED",
                f"Schema CHARACTER_MAXIMUM_LENGTH={max_length}"
            ))

        # Allowed values — skip AI-excluded free-text columns
        if col_name in distinct_by_col and col_name not in exclude_enum_cols:
            values = distinct_by_col[col_name]
            if 2 <= len(values) <= 10:
                rules.append(rule(
                    col_name,
                    f"{col_name} must be one of: {', '.join(str(v) for v in values)}",
                    0.80, "REVIEW_RECOMMENDED",
                    f"{len(values)} distinct values in {total_rows} rows — low cardinality suggests enum"
                ))

        # Range rules
        if col_name in rng_map:
            ri      = rng_map[col_name]
            min_val = ri.get("min_val")
            max_val = ri.get("max_val")
            if min_val is not None and max_val is not None:
                rules.append(rule(
                    col_name,
                    f"{col_name} must be between {min_val} and {max_val}",
                    0.75, "REVIEW_RECOMMENDED",
                    f"Range profiler: min={min_val}, max={max_val}, avg={ri.get('avg_val')}"
                ))
                try:
                    if float(min_val) < 0:
                        violations.append(violation(
                            col_name, "OUT_OF_RANGE", "HIGH", 1,
                            f"Negative value detected: min={min_val}",
                            str(min_val),
                            f"SELECT * FROM [{table_name}] WHERE [{col_name}] < 0"
                        ))
                except (ValueError, TypeError):
                    pass

        # AI uniqueness rule
        if col_name in ai_unique_cols:
            ai_info = ai_unique_cols[col_name]
            rules.append(rule(
                col_name,
                f"{col_name} must be unique — identified by AI reasoning",
                ai_info["confidence"], "VALIDATED",
                f"AI reasoning: {ai_info['reasoning']}"
            ))

    # Duplicate violations — only for AI-confirmed unique columns
    seen = set()
    for dup in dups:
        col_name = dup["column_name"]
        if col_name not in seen and col_name in ai_unique_cols:
            seen.add(col_name)
            r_code = rc()
            rules.append({
                "run_id": run_id, "rule_code": r_code,
                "table_name": table_name, "column_name": col_name,
                "rule_description": f"{col_name} has duplicate values — violates uniqueness rule",
                "confidence": 0.95, "status": "VALIDATED",
                "source_tools": "DataPactAnalysisWorker+OpenAI",
                "evidence": (
                    f"Duplicate '{dup['duplicate_value']}' found {dup['occurrences']} times. "
                    f"AI: {ai_unique_cols[col_name]['reasoning']}"
                ),
                "conflict_detected": "false",
                "conflict_detail": "none"
            })
            violations.append({
                "run_id": run_id, "rule_code": r_code,
                "table_name": table_name, "column_name": col_name,
                "violation_type": "DUPLICATE", "severity": "CRITICAL",
                "affected_rows": dup["occurrences"],
                "violation_detail": f"Duplicate: {dup['duplicate_value']} (AI confirmed uniqueness)",
                "sample_values": str(dup["duplicate_value"]),
                "remediation_sql": (
                    f"SELECT [{col_name}], COUNT(*) FROM [{table_name}] "
                    f"GROUP BY [{col_name}] HAVING COUNT(*) > 1"
                ),
                "status": "OPEN"
            })

    return rules, violations

def generate_doc_rules(run_id, table_name, rag_context, counter):
    """Generate additional rules purely from document evidence via AI."""
    if not rag_context:
        return []

    prompt = f"""You are a data quality rule generator.

Below is documentation about the table '{table_name}'. Extract specific, concrete data quality rules from this documentation.

DOCUMENTATION:
{rag_context}

For each rule you find, extract:
- The column name it applies to
- The exact rule (pattern, allowed values, range, not null, unique, etc.)
- The confidence (0.85 if explicitly stated, 0.75 if implied)
- The source document name

Only extract rules that are EXPLICITLY stated in the documentation.
Do not infer or guess rules. Only extract what is directly written.

Respond ONLY with valid JSON, no markdown:
{{
  "rules": [
    {{
      "column_name": "site_id",
      "rule_description": "site_id must match pattern SITE-[0-9]{{5}}",
      "confidence": 0.95,
      "evidence": "data_contract_v2.yaml: pattern field specifies SITE-[0-9]{{5}}"
    }}
  ]
}}"""

    try:
        response = call_openai(prompt, max_tokens=1500)
        response = response.replace("```json", "").replace("```", "").strip()
        parsed   = json.loads(response)

        rules = []
        for item in parsed.get("rules", []):
            code = f"DQ-R{run_id}-{counter[0]:03d}"
            counter[0] += 1
            rules.append({
                "run_id":            run_id,
                "rule_code":         code,
                "table_name":        table_name,
                "column_name":       item["column_name"],
                "rule_description":  item["rule_description"],
                "confidence":        item["confidence"],
                "status":            "VALIDATED" if item["confidence"] >= 0.85 else "REVIEW_RECOMMENDED",
                "source_tools":      "DataPactAnalysisWorker+OpenAI+RAG",
                "evidence":          item["evidence"],
                "conflict_detected": "false",
                "conflict_detail":   "none"
            })
        return rules

    except Exception as e:
        logging.warning(f"Doc rule generation failed: {str(e)}")
        return []

# ── Save helpers ──────────────────────────────────────────────

def save_rules(cursor, rules):
    for r in rules:
        # Check if rule_code already exists for this run
        cursor.execute("""
            SELECT COUNT(*) FROM datapact_rules
            WHERE run_id = ? AND rule_code = ?
        """, r["run_id"], r["rule_code"])
        if cursor.fetchone()[0] > 0:
            logging.warning(f"Skipping duplicate rule_code: {r['rule_code']}")
            continue
        cursor.execute("""
            INSERT INTO datapact_rules (
                run_id, rule_code, table_name, column_name,
                rule_description, confidence, status,
                source_tools, evidence, conflict_detected, conflict_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            r["run_id"], r["rule_code"], r["table_name"], r["column_name"],
            r["rule_description"], r["confidence"], r["status"],
            r["source_tools"], r["evidence"],
            r["conflict_detected"], r["conflict_detail"]
        )

def save_violations(cursor, violations):
    for v in violations:
        cursor.execute("""
            INSERT INTO datapact_violations (
                run_id, rule_code, table_name, column_name,
                violation_type, severity, affected_rows,
                violation_detail, sample_values, remediation_sql, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            v["run_id"], v["rule_code"], v["table_name"], v["column_name"],
            v["violation_type"], v["severity"], v["affected_rows"],
            v["violation_detail"], v["sample_values"],
            v["remediation_sql"], v["status"]
        )

# ── Config generators ─────────────────────────────────────────

def rules_to_ge_suite(rules, table_name, run_id):
    """Convert datapact_rules to Great Expectations expectation suite JSON."""
    expectations = []

    for r in rules:
        col  = r["column_name"]
        desc = r["rule_description"].lower()
        conf = r["confidence"]
        rc   = r["rule_code"]

        meta = {
            "rule_code":  rc,
            "confidence": conf,
            "status":     r["status"],
            "evidence":   r["evidence"]
        }

        # NOT NULL
        if "must not be null" in desc or "not be null" in desc:
            expectations.append({
                "expectation_type": "expect_column_values_to_not_be_null",
                "kwargs": {"column": col},
                "meta": meta
            })

        # UNIQUE
        elif "must be unique" in desc or "unique" in desc and "duplicate" not in desc:
            expectations.append({
                "expectation_type": "expect_column_values_to_be_unique",
                "kwargs": {"column": col},
                "meta": meta
            })

        # PATTERN
        elif "must match pattern" in desc or "pattern" in desc:
            import re
            pattern = re.search(r'([A-Z]+-\[.*?\]\{.*?\})', r["rule_description"])
            if not pattern:
                pattern = re.search(r'\[.*?\]\{.*?\}', r["rule_description"])
            if pattern:
                expectations.append({
                    "expectation_type": "expect_column_values_to_match_regex",
                    "kwargs": {"column": col, "regex": f"^{pattern.group()}$"},
                    "meta": meta
                })

        # ALLOWED VALUES
        elif "must be one of" in desc:
            import re
            values = re.findall(r"'([^']+)'|([A-Z][a-z]+)", r["rule_description"])
            flat   = [v[0] or v[1] for v in values if v[0] or v[1]]
            if flat:
                expectations.append({
                    "expectation_type": "expect_column_values_to_be_in_set",
                    "kwargs": {"column": col, "value_set": flat},
                    "meta": meta
                })

        # RANGE
        elif "must be between" in desc:
            import re
            nums = re.findall(r'-?\d+\.?\d*', r["rule_description"])
            if len(nums) >= 2:
                expectations.append({
                    "expectation_type": "expect_column_values_to_be_between",
                    "kwargs": {
                        "column":    col,
                        "min_value": float(nums[0]),
                        "max_value": float(nums[1])
                    },
                    "meta": meta
                })

        # MAX LENGTH
        elif "must not exceed" in desc and "character" in desc:
            import re
            nums = re.findall(r'\d+', r["rule_description"])
            if nums:
                expectations.append({
                    "expectation_type": "expect_column_value_lengths_to_be_between",
                    "kwargs": {"column": col, "min_value": None, "max_value": int(nums[0])},
                    "meta": meta
                })

    return {
        "expectation_suite_name": f"{table_name}.datapact_suite",
        "data_asset_type":        "Dataset",
        "meta": {
            "generated_by":  "DataPact",
            "run_id":        run_id,
            "table":         table_name,
            "generated_at":  __import__('datetime').datetime.utcnow().isoformat()
        },
        "expectations": expectations
    }

def rules_to_dbt_schema(rules, table_name, run_id):
    """Convert datapact_rules to dbt schema.yml patch."""
    from collections import defaultdict
    import re

    cols = defaultdict(list)
    for r in rules:
        cols[r["column_name"]].append(r)

    columns_yaml = []
    for col_name, col_rules in cols.items():
        tests = []
        for r in col_rules:
            desc = r["rule_description"].lower()
            sev  = "error" if r["confidence"] >= 0.85 else "warn"
            base = {"severity": sev, "meta": {"rule_code": r["rule_code"], "confidence": r["confidence"]}}

            if "must not be null" in desc:
                tests.append({"not_null": {"config": base}})
            elif "must be unique" in desc and "duplicate" not in desc:
                tests.append({"unique": {"config": base}})
            elif "must match pattern" in desc:
                pattern = re.search(r'([A-Z]+-\[.*?\]\{.*?\}|\[.*?\]\{.*?\})', r["rule_description"])
                if pattern:
                    tests.append({"dbt_expectations.expect_column_values_to_match_regex": {"regex": f"^{pattern.group()}$", "config": base}})
            elif "must be one of" in desc:
                values = re.findall(r"'([^']+)'|([A-Z][a-z]+)", r["rule_description"])
                flat   = [v[0] or v[1] for v in values if v[0] or v[1]]
                if flat:
                    tests.append({"accepted_values": {"values": flat, "config": base}})
            elif "must be between" in desc:
                nums = re.findall(r'-?\d+\.?\d*', r["rule_description"])
                if len(nums) >= 2:
                    tests.append({"dbt_expectations.expect_column_values_to_be_between": {"min_value": float(nums[0]), "max_value": float(nums[1]), "config": base}})
            elif "must not exceed" in desc and "character" in desc:
                nums = re.findall(r'\d+', r["rule_description"])
                if nums:
                    tests.append({"dbt_expectations.expect_column_value_lengths_to_be_between": {"max_value": int(nums[0]), "config": base}})

        if tests:
            columns_yaml.append({"name": col_name, "tests": tests})

    import yaml
    schema = {
        "version": 2,
        "models": [{
            "name": table_name,
            "meta": {"datapact_run_id": run_id},
            "columns": columns_yaml
        }]
    }
    return yaml.dump(schema, default_flow_style=False, allow_unicode=True)

def rules_to_soda_checks(rules, table_name, run_id):
    """Convert datapact_rules to Soda checks YAML."""
    import re
    checks = [
        f"# DataPact generated Soda checks",
        f"# Table: {table_name}",
        f"# Run ID: {run_id}",
        f"# Generated: {__import__('datetime').datetime.utcnow().isoformat()}",
        f"",
        f"checks for {table_name}:"
    ]

    for r in rules:
        col  = r["column_name"]
        desc = r["rule_description"].lower()
        rc   = r["rule_code"]

        checks.append(f"  # {rc} | confidence: {r['confidence']}")

        if "must not be null" in desc:
            checks.append(f"  - missing_count({col}) = 0:")
            checks.append(f"      name: {rc} {col} must not be null")

        elif "must be unique" in desc and "duplicate" not in desc:
            checks.append(f"  - duplicate_count({col}) = 0:")
            checks.append(f"      name: {rc} {col} must be unique")

        elif "must be one of" in desc:
            values = re.findall(r"'([^']+)'|([A-Z][a-z]+)", r["rule_description"])
            flat   = [v[0] or v[1] for v in values if v[0] or v[1]]
            if flat:
                val_list = ", ".join(f'"{v}"' for v in flat)
                checks.append(f"  - invalid_count({col}) = 0:")
                checks.append(f"      valid values: [{val_list}]")
                checks.append(f"      name: {rc} {col} allowed values")

        elif "must be between" in desc:
            nums = re.findall(r'-?\d+\.?\d*', r["rule_description"])
            if len(nums) >= 2:
                checks.append(f"  - min({col}) >= {nums[0]}:")
                checks.append(f"      name: {rc} {col} min value")
                checks.append(f"  - max({col}) <= {nums[1]}:")
                checks.append(f"      name: {rc} {col} max value")

        elif "must not exceed" in desc and "character" in desc:
            nums = re.findall(r'\d+', r["rule_description"])
            if nums:
                checks.append(f"  - max_length({col}) <= {nums[0]}:")
                checks.append(f"      name: {rc} {col} max length")

        checks.append("")

    return "\n".join(checks)

def save_configs_to_blob(ge_json, dbt_yaml, soda_yaml, table_name, run_id):
    """Save all config files to datapact-configs Blob container."""
    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION)
    urls = {}

    configs = [
        (f"{table_name}/run_{run_id}/{table_name}_ge_suite.json",    json.dumps(ge_json, indent=2).encode(),  "application/json"),
        (f"{table_name}/run_{run_id}/{table_name}_dbt_schema.yml",   dbt_yaml.encode(),                       "text/yaml"),
        (f"{table_name}/run_{run_id}/{table_name}_soda_checks.yaml", soda_yaml.encode(),                      "text/yaml"),
    ]

    for blob_path, content, content_type in configs:
        blob_client = blob_service.get_blob_client(
            container="datapact-configs",
            blob=blob_path
        )
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type)
        )
        urls[blob_path.split("/")[-1]] = blob_client.url
        logging.info(f"Config saved: {blob_path}")

    return urls

# ── RAG helpers────────────────────────────────────────────

def query_rag(table_name, database_name, query, top=5):
    """Query AI Search for document context about a table/column."""
    try:
        query_vector = get_embedding(query)

        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=SEARCH_INDEX,
            credential=AzureKeyCredential(SEARCH_KEY)
        )

        from azure.search.documents.models import VectorizedQuery
        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top,
            fields="content_vector"
        )

        filters = []
        if table_name:
            filters.append(f"table_name eq '{table_name}'")
        if database_name:
            filters.append(f"database_name eq '{database_name}'")
        filter_str = " and ".join(filters) if filters else None

        results = search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=filter_str,
            select=["doc_name", "doc_type", "content"],
            top=top
        )

        chunks = []
        for r in results:
            chunks.append(
                f"[Source: {r['doc_name']} ({r['doc_type']})]\n{r['content']}"
            )
        return "\n\n---\n\n".join(chunks)

    except Exception as e:
        logging.warning(f"RAG query failed: {str(e)}")
        return ""
    
# ── Document helpers ──────────────────────────────────────────

def get_embedding(text):
    """Generate text embedding using Azure OpenAI text-embedding-ada-002."""
    url = (
        f"{OPENAI_ENDPOINT}/openai/deployments/text-embedding-ada-002"
        f"/embeddings?api-version=2024-02-01"
    )
    payload = json.dumps({"input": text[:8000]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "api-key": OPENAI_KEY}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result["data"][0]["embedding"]

def chunk_text(text, chunk_size=1000, overlap=200):
    """Split text into overlapping chunks for indexing."""
    chunks = []
    start  = 0
    text   = text.strip()
    while start < len(text):
        end   = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
        if start >= len(text):
            break
    return chunks

def parse_document_with_intelligence(blob_url, doc_key):
    """Use Document Intelligence to extract text from PDF/DOCX/image."""
    client = DocumentAnalysisClient(
        endpoint=DOCINTEL_ENDPOINT,
        credential=AzureKeyCredential(DOCINTEL_KEY)
    )
    poller = client.begin_analyze_document_from_url(
        "prebuilt-read", blob_url
    )
    result = poller.result()
    full_text = ""
    for page in result.pages:
        for line in page.lines:
            full_text += line.content + "\n"
    return full_text.strip()

def parse_text_file(content_bytes, filename):
    """Parse plain text files — SQL, CSV, TXT, YAML."""
    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return content_bytes.decode("latin-1")

def index_chunks(chunks, doc_id, doc_name, doc_type,
                 table_name, database_name):
    """Generate embeddings and index all chunks in AI Search."""
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY)
    )
    documents = []
    for i, chunk in enumerate(chunks):
        try:
            embedding = get_embedding(chunk)
        except Exception as e:
            logging.warning(f"Embedding failed for chunk {i}: {str(e)}")
            embedding = [0.0] * 1536

        chunk_id = hashlib.md5(
            f"{doc_id}-{i}-{chunk[:50]}".encode()
        ).hexdigest()

        documents.append({
            "id":             chunk_id,
            "doc_id":         doc_id,
            "doc_name":       doc_name,
            "doc_type":       doc_type,
            "table_name":     table_name,
            "database_name":  database_name,
            "chunk_index":    i,
            "content":        chunk,
            "content_vector": embedding
        })

    # Batch upload to AI Search
    batch_size = 10
    total_indexed = 0
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        result = search_client.upload_documents(documents=batch)
        total_indexed += len([r for r in result if r.succeeded])
        logging.info(f"Indexed batch {i//batch_size + 1}: {len(batch)} chunks")

    return total_indexed

# ── Service Bus Worker ────────────────────────────────────────

@app.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="datapact-analysis-queue",
    connection="SERVICE_BUS_CONNECTION"
)
def DataPactAnalysisWorker(msg: func.ServiceBusMessage) -> None:
    logging.info("DataPact Analysis Worker triggered")
    try:
        payload       = json.loads(msg.get_body().decode("utf-8"))
        table_name    = payload.get("table_name", "").strip()
        database_name = payload.get("database_name", DATABASE).strip()
        triggered_by  = payload.get("triggered_by", "System").strip()
        run_id        = payload.get("run_id", None)

        if not table_name or database_name not in ALLOWED_DATABASES:
            logging.error(f"Invalid: {database_name}.{table_name}")
            return

        # Profile
        conn   = get_connection(database_name)
        cursor = conn.cursor()
        cols   = get_columns(cursor, table_name)

        s_profile    = schema_profile(cursor, table_name)
        n_profile    = null_profile(cursor, table_name, cols)
        d_values     = distinct_values(cursor, table_name, cols)
        r_profile    = range_profile(cursor, table_name, cols)
        cardinality  = get_cardinality(cursor, table_name, cols)
        constraints  = get_constraints(cursor, table_name)
        col_comments = get_column_comments(cursor, table_name)
        tbl_comment  = get_table_comment(cursor, table_name)
        duplicates   = smart_duplicate_profile(
            cursor, table_name, cols,
            cardinality, constraints, col_comments
        )

        # Query RAG for document context
        logging.info("Querying RAG for document context...")
        rag_context = query_rag(
            table_name, database_name,
            f"data quality rules constraints allowed values for {table_name} columns"
        )
        logging.info(f"RAG returned {len(rag_context)} chars of context")

        total_rows = n_profile[0]["total_rows"] if n_profile else 0
        cursor.close()
        conn.close()

        # AI reasoning
        logging.info("Calling OpenAI for reasoning...")
        ai_unique_cols, exclude_enum_cols = ai_identify_unique_columns(
            table_name, s_profile, cardinality,
            col_comments, tbl_comment, constraints,
            d_values, rag_context
        )
        logging.info(f"AI unique cols: {list(ai_unique_cols.keys())}")
        logging.info(f"AI excluded enums: {list(exclude_enum_cols.keys())}")

        profile = {
            "schema":          s_profile,
            "null_profile":    n_profile,
            "distinct_values": d_values,
            "range_profile":   r_profile,
            "duplicates":      duplicates,
        }

        # Persist run
        meta_conn   = get_connection(DATABASE)
        meta_cursor = meta_conn.cursor()

        if run_id:
            meta_cursor.execute("""
                UPDATE datapact_runs
                SET status = 'PROFILING', total_rows = ?,
                    tools_used = 'DataPactAnalysisWorker+OpenAI'
                WHERE run_id = ?
            """, total_rows, run_id)
        else:
            meta_cursor.execute("""
                INSERT INTO datapact_runs
                    (table_name, triggered_by, total_rows, tools_used, status)
                OUTPUT INSERTED.run_id
                VALUES (?, ?, ?, ?, ?)
            """, table_name, triggered_by, total_rows,
                "DataPactAnalysisWorker+OpenAI", "PROFILING")
            run_id = int(meta_cursor.fetchone()[0])

        meta_conn.commit()

        rules, violations = generate_rules(
            profile, run_id, table_name,
            ai_unique_cols, exclude_enum_cols
        )

        # Save data profiling rules first
        try:
            save_rules(meta_cursor, rules)
            meta_conn.commit()
            logging.info(f"Profiling rules saved: {len(rules)}")
        except Exception as e:
            logging.error(f"Failed to save profiling rules: {str(e)}")
            meta_conn.rollback()

        # Save violations
        try:
            save_violations(meta_cursor, violations)
            meta_conn.commit()
            logging.info(f"Violations saved: {len(violations)}")
        except Exception as e:
            logging.error(f"Failed to save violations: {str(e)}")
            meta_conn.rollback()

        # Generate and save document-driven rules independently
        try:
            doc_counter = [500]
            doc_rules = generate_doc_rules(run_id, table_name, rag_context, doc_counter)
            if doc_rules:
                save_rules(meta_cursor, doc_rules)
                meta_conn.commit()
                logging.info(f"Document-driven rules saved: {len(doc_rules)}")
            else:
                logging.info("No document-driven rules generated")
        except Exception as e:
            logging.error(f"Failed to save doc rules: {str(e)}")
            meta_conn.rollback()

        # Auto-generate config files
        try:
            cursor_cfg = meta_conn.cursor()
            cursor_cfg.execute("""
                SELECT rule_code, table_name, column_name, rule_description,
                       confidence, status, source_tools, evidence
                FROM datapact_rules
                WHERE run_id = ?
                AND status IN ('VALIDATED', 'REVIEW_RECOMMENDED')
                ORDER BY rule_code
            """, run_id)
            cfg_rules = [
                {
                    "rule_code":        row[0],
                    "table_name":       row[1],
                    "column_name":      row[2],
                    "rule_description": row[3],
                    "confidence":       float(row[4]),
                    "status":           row[5],
                    "source_tools":     row[6],
                    "evidence":         row[7]
                }
                for row in cursor_cfg.fetchall()
            ]
            cursor_cfg.close()

            if cfg_rules:
                ge_json   = rules_to_ge_suite(cfg_rules, table_name, run_id)
                dbt_yaml  = rules_to_dbt_schema(cfg_rules, table_name, run_id)
                soda_yaml = rules_to_soda_checks(cfg_rules, table_name, run_id)
                urls      = save_configs_to_blob(ge_json, dbt_yaml, soda_yaml, table_name, run_id)
                logging.info(f"Configs auto-generated: {list(urls.keys())}")

        except Exception as e:
            logging.warning(f"Config generation failed (non-critical): {str(e)}")

        # Mark completed
        meta_cursor.execute(
            "UPDATE datapact_runs SET status = 'COMPLETED' WHERE run_id = ?",
            run_id
        )
        meta_conn.commit()
        meta_cursor.close()
        meta_conn.close()

        logging.info(
            f"Done. run_id={run_id} rules={len(rules)} "
            f"violations={len(violations)} "
            f"ai_unique={list(ai_unique_cols.keys())} "
            f"excluded_enums={list(exclude_enum_cols.keys())}"
        )

    except Exception as e:
        logging.error(f"Worker error: {str(e)}")
        raise

# ── QueueAnalysis ─────────────────────────────────────────────

@app.route(route="QueueAnalysis", methods=["POST"])
def QueueAnalysis(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body          = req.get_json()
        table_name    = body.get("table_name", "").strip()
        database_name = body.get("database_name", DATABASE).strip()
        triggered_by  = body.get("triggered_by", "System").strip()

        if not table_name or database_name not in ALLOWED_DATABASES:
            return func.HttpResponse(
                json.dumps({"error": "Invalid table or database"}),
                status_code=400, mimetype="application/json"
            )

        conn   = get_connection(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO datapact_runs
                (table_name, triggered_by, total_rows, tools_used, status)
            OUTPUT INSERTED.run_id
            VALUES (?, ?, 0, 'Queued', 'PENDING')
        """, table_name, triggered_by)
        run_id = int(cursor.fetchone()[0])
        conn.commit()
        cursor.close()
        conn.close()

        sb_conn = os.environ["SERVICE_BUS_CONNECTION"]
        with servicebus.ServiceBusClient.from_connection_string(sb_conn) as client:
            with client.get_queue_sender("datapact-analysis-queue") as sender:
                sender.send_messages(servicebus.ServiceBusMessage(json.dumps({
                    "table_name": table_name, "database_name": database_name,
                    "triggered_by": triggered_by, "run_id": run_id
                })))

        return func.HttpResponse(
            json.dumps({
                "status": "queued", "run_id": run_id,
                "message": f"Analysis queued for {table_name} in {database_name}"
            }),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"QueueAnalysis error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── ListDatabases ─────────────────────────────────────────────

@app.route(route="ListDatabases", methods=["GET"])
def ListDatabases(req: func.HttpRequest) -> func.HttpResponse:
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sys.databases
            WHERE name NOT IN ('master','tempdb','model','msdb')
            ORDER BY name
        """)
        databases = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return func.HttpResponse(
            json.dumps({"databases": databases}),
            status_code=200, mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── ListTables ────────────────────────────────────────────────

@app.route(route="ListTables", methods=["POST"])
def ListTables(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body          = req.get_json()
        database_name = body.get("database_name", DATABASE).strip()

        if database_name not in ALLOWED_DATABASES:
            return func.HttpResponse(
                json.dumps({"error": f"Database '{database_name}' not allowed"}),
                status_code=403, mimetype="application/json"
            )

        conn   = get_connection(database_name)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME
        """)
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return func.HttpResponse(
            json.dumps({"tables": tables}),
            status_code=200, mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── DataPactProfiler (direct — used by Copilot agent) ────────

@app.route(route="DataPactProfiler", methods=["POST"])
def DataPactProfiler(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json"
        )

    table_name    = body.get("table_name", "").strip()
    database_name = body.get("database_name", DATABASE).strip()
    profile_type  = body.get("profile_type", "all").strip().lower()

    if not table_name:
        return func.HttpResponse(
            json.dumps({"error": "table_name is required"}),
            status_code=400, mimetype="application/json"
        )
    if database_name not in ALLOWED_DATABASES:
        return func.HttpResponse(
            json.dumps({"error": f"Database '{database_name}' not allowed"}),
            status_code=403, mimetype="application/json"
        )

    try:
        conn   = get_connection(database_name)
        cursor = conn.cursor()
        cols   = get_columns(cursor, table_name)

        result = {"table_name": table_name, "profile_type": profile_type}

        if profile_type in ("all", "schema"):
            result["schema"] = schema_profile(cursor, table_name)
        if profile_type in ("all", "null"):
            result["null_profile"] = null_profile(cursor, table_name, cols)
        if profile_type in ("all", "distinct"):
            result["distinct_values"] = distinct_values(cursor, table_name, cols)
        if profile_type in ("all", "range"):
            result["range_profile"] = range_profile(cursor, table_name, cols)
        if profile_type in ("all", "duplicate"):
            cardinality  = get_cardinality(cursor, table_name, cols)
            constraints  = get_constraints(cursor, table_name)
            col_comments = get_column_comments(cursor, table_name)
            result["duplicates"] = smart_duplicate_profile(
                cursor, table_name, cols,
                cardinality, constraints, col_comments
            )
        if profile_type in ("all", "cardinality"):
            result["cardinality"] = get_cardinality(cursor, table_name, cols)
        if profile_type in ("all", "comments"):
            result["column_comments"] = get_column_comments(cursor, table_name)
            result["table_comment"]   = get_table_comment(cursor, table_name)
        if profile_type in ("all", "constraints"):
            result["constraints"] = get_constraints(cursor, table_name)

        cursor.close()
        conn.close()

        return func.HttpResponse(
            json.dumps(result, default=str),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"Profiler error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )
    
# ── Upload Document ───────────────────────────────────────────

@app.route(route="UploadDocument", methods=["POST"])
def UploadDocument(req: func.HttpRequest) -> func.HttpResponse:
    """
    Accepts multipart form data or JSON with base64 content.
    Saves to Blob Storage and creates a datapact_documents record.
    """
    logging.info("UploadDocument triggered")
    try:
        # Get metadata from query params or headers
        table_name    = req.params.get("table_name", "").strip()
        database_name = req.params.get("database_name", DATABASE).strip()
        uploaded_by   = req.params.get("uploaded_by", "System").strip()
        doc_type      = req.params.get("doc_type", "general").strip()

        if database_name not in ALLOWED_DATABASES:
            return func.HttpResponse(
                json.dumps({"error": f"Database '{database_name}' not allowed"}),
                status_code=403, mimetype="application/json"
            )

        # Get file content and name
        body = req.get_body()
        if not body:
            return func.HttpResponse(
                json.dumps({"error": "No file content provided"}),
                status_code=400, mimetype="application/json"
            )

        # Try JSON with base64
        try:
            json_body  = json.loads(body)
            import base64
            file_content = base64.b64decode(json_body["content"])
            file_name    = json_body.get("filename", "document.txt")
            doc_type     = json_body.get("doc_type", doc_type)
            table_name   = json_body.get("table_name", table_name)
            database_name = json_body.get("database_name", database_name)
            uploaded_by  = json_body.get("uploaded_by", uploaded_by)
        except Exception:
            # Raw binary upload
            file_content = body
            file_name    = req.params.get("filename", "document.bin")

        # Upload to Blob Storage
        blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION)
        blob_client  = blob_service.get_blob_client(
            container="datapact-documents",
            blob=f"{database_name}/{table_name}/{file_name}"
        )

        # Detect content type
        ext = file_name.lower().split(".")[-1]
        content_type_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain",
            "sql": "text/plain",
            "csv": "text/csv",
            "yaml": "text/yaml",
            "yml": "text/yaml",
            "json": "application/json"
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        blob_client.upload_blob(
            file_content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type)
        )
        blob_url = blob_client.url

        # Save to datapact_documents
        conn   = get_connection(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO datapact_documents
                (table_name, database_name, doc_name, doc_type,
                 blob_url, status, uploaded_by)
            OUTPUT INSERTED.doc_id
            VALUES (?, ?, ?, ?, ?, 'UPLOADED', ?)
        """, table_name, database_name, file_name,
             doc_type, blob_url, uploaded_by)
        doc_id = int(cursor.fetchone()[0])
        conn.commit()
        cursor.close()
        conn.close()

        logging.info(f"Document uploaded: {file_name} → doc_id={doc_id}")

        return func.HttpResponse(
            json.dumps({
                "status":    "uploaded",
                "doc_id":    doc_id,
                "doc_name":  file_name,
                "blob_url":  blob_url,
                "message":   f"Document uploaded. Call ProcessDocument with doc_id={doc_id} to index it."
            }),
            status_code=201, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"UploadDocument error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── Process Document ──────────────────────────────────────────

@app.route(route="ProcessDocument", methods=["POST"])
def ProcessDocument(req: func.HttpRequest) -> func.HttpResponse:
    """
    Reads document from Blob Storage, extracts text via Document Intelligence
    or direct parsing, chunks it, generates embeddings, indexes in AI Search.
    """
    logging.info("ProcessDocument triggered")
    try:
        body   = req.get_json()
        doc_id = body.get("doc_id")

        if not doc_id:
            return func.HttpResponse(
                json.dumps({"error": "doc_id is required"}),
                status_code=400, mimetype="application/json"
            )

        # Fetch document record
        conn   = get_connection(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT doc_id, doc_name, doc_type, blob_url,
                   table_name, database_name
            FROM datapact_documents
            WHERE doc_id = ?
        """, doc_id)
        row = cursor.fetchone()
        if not row:
            return func.HttpResponse(
                json.dumps({"error": f"doc_id {doc_id} not found"}),
                status_code=404, mimetype="application/json"
            )

        doc_id_val    = row[0]
        doc_name      = row[1]
        doc_type      = row[2]
        blob_url      = row[3]
        table_name    = row[4]
        database_name = row[5]

        # Update status to PROCESSING
        cursor.execute("""
            UPDATE datapact_documents SET status = 'PROCESSING'
            WHERE doc_id = ?
        """, doc_id_val)
        conn.commit()

        # Extract text
        ext = doc_name.lower().split(".")[-1]
        logging.info(f"Processing {doc_name} ({ext})")

        if ext in ("pdf", "docx", "doc", "png", "jpg", "jpeg", "tiff"):
            # Use Document Intelligence for binary formats
            text = parse_document_with_intelligence(blob_url, DOCINTEL_KEY)
        else:
            # Direct text parsing for SQL, CSV, TXT, YAML, JSON
            blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION)
            blob_client  = blob_service.get_blob_client(
                container="datapact-documents",
                blob=f"{database_name}/{table_name}/{doc_name}"
            )
            content_bytes = blob_client.download_blob().readall()
            text = parse_text_file(content_bytes, doc_name)

        if not text:
            cursor.execute("""
                UPDATE datapact_documents
                SET status = 'FAILED'
                WHERE doc_id = ?
            """, doc_id_val)
            conn.commit()
            return func.HttpResponse(
                json.dumps({"error": "No text extracted from document"}),
                status_code=422, mimetype="application/json"
            )

        # Chunk and index
        chunks        = chunk_text(text, chunk_size=1000, overlap=200)
        total_indexed = index_chunks(
            chunks, doc_id_val, doc_name, doc_type,
            table_name, database_name
        )

        # Update status to INDEXED
        cursor.execute("""
            UPDATE datapact_documents
            SET status = 'INDEXED',
                chunks_indexed = ?,
                processed_at = GETDATE()
            WHERE doc_id = ?
        """, total_indexed, doc_id_val)
        conn.commit()
        cursor.close()
        conn.close()

        logging.info(f"Indexed {total_indexed} chunks for doc_id={doc_id_val}")

        return func.HttpResponse(
            json.dumps({
                "status":        "indexed",
                "doc_id":        doc_id_val,
                "doc_name":      doc_name,
                "chunks_indexed": total_indexed,
                "message":       f"Successfully indexed {total_indexed} chunks from {doc_name}"
            }),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"ProcessDocument error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── Search Documents (RAG query) ──────────────────────────────

@app.route(route="SearchDocuments", methods=["POST"])
def SearchDocuments(req: func.HttpRequest) -> func.HttpResponse:
    """
    Semantic + vector search over indexed documents.
    Used by the worker to enrich rule generation with document context.
    """
    logging.info("SearchDocuments triggered")
    try:
        body          = req.get_json()
        query         = body.get("query", "").strip()
        table_name    = body.get("table_name", "").strip()
        database_name = body.get("database_name", "").strip()
        top           = body.get("top", 5)

        if not query:
            return func.HttpResponse(
                json.dumps({"error": "query is required"}),
                status_code=400, mimetype="application/json"
            )

        # Generate query embedding
        query_vector = get_embedding(query)

        # Vector search
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=SEARCH_INDEX,
            credential=AzureKeyCredential(SEARCH_KEY)
        )

        from azure.search.documents.models import VectorizedQuery
        vector_query = VectorizedQuery(
            vector=query_vector,
            k_nearest_neighbors=top,
            fields="content_vector"
        )

        # Build filter
        filters = []
        if table_name:
            filters.append(f"table_name eq '{table_name}'")
        if database_name:
            filters.append(f"database_name eq '{database_name}'")
        filter_str = " and ".join(filters) if filters else None

        results = search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            filter=filter_str,
            select=["id", "doc_name", "doc_type", "table_name",
                    "chunk_index", "content"],
            top=top
        )

        chunks = []
        for r in results:
            chunks.append({
                "doc_name":    r["doc_name"],
                "doc_type":    r["doc_type"],
                "table_name":  r.get("table_name", ""),
                "chunk_index": r["chunk_index"],
                "content":     r["content"],
                "score":       r["@search.score"]
            })

        return func.HttpResponse(
            json.dumps({
                "query":   query,
                "results": chunks,
                "count":   len(chunks)
            }, default=str),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"SearchDocuments error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

@app.route(route="TestDocRules", methods=["POST"])
def TestDocRules(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body          = req.get_json()
        table_name    = body.get("table_name", "inspection_sites")
        database_name = body.get("database_name", DATABASE)

        rag_context = query_rag(
            table_name, database_name,
            f"data quality rules constraints allowed values for {table_name} columns"
        )

        counter   = [100]
        doc_rules = generate_doc_rules("TEST", table_name, rag_context, counter)

        return func.HttpResponse(
            json.dumps({
                "rag_chars":    len(rag_context),
                "rag_preview":  rag_context[:500],
                "doc_rules":    doc_rules,
                "rules_count":  len(doc_rules)
            }, default=str),
            status_code=200, mimetype="application/json"
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )

# ── Generate Configs ────────────────────────────────────────────

@app.route(route="GenerateConfigs", methods=["POST"])
def GenerateConfigs(req: func.HttpRequest) -> func.HttpResponse:
    """Generate GE, dbt, and Soda config files from a completed run."""
    logging.info("GenerateConfigs triggered")
    try:
        body   = req.get_json()
        run_id = body.get("run_id")

        if not run_id:
            return func.HttpResponse(
                json.dumps({"error": "run_id is required"}),
                status_code=400, mimetype="application/json"
            )

        # Fetch run details
        conn   = get_connection(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name, status FROM datapact_runs WHERE run_id = ?
        """, run_id)
        run = cursor.fetchone()

        if not run:
            return func.HttpResponse(
                json.dumps({"error": f"run_id {run_id} not found"}),
                status_code=404, mimetype="application/json"
            )

        table_name = run[0]
        status     = run[1]

        if status != "COMPLETED":
            return func.HttpResponse(
                json.dumps({"error": f"Run {run_id} is not COMPLETED (status: {status})"}),
                status_code=400, mimetype="application/json"
            )

        # Fetch all validated rules
        cursor.execute("""
            SELECT rule_code, table_name, column_name, rule_description,
                   confidence, status, source_tools, evidence
            FROM datapact_rules
            WHERE run_id = ?
            AND status IN ('VALIDATED', 'REVIEW_RECOMMENDED')
            ORDER BY rule_code
        """, run_id)

        rules = [
            {
                "rule_code":        row[0],
                "table_name":       row[1],
                "column_name":      row[2],
                "rule_description": row[3],
                "confidence":       float(row[4]),
                "status":           row[5],
                "source_tools":     row[6],
                "evidence":         row[7]
            }
            for row in cursor.fetchall()
        ]

        cursor.close()
        conn.close()

        if not rules:
            return func.HttpResponse(
                json.dumps({"error": f"No rules found for run_id {run_id}"}),
                status_code=404, mimetype="application/json"
            )

        # Generate configs
        ge_json   = rules_to_ge_suite(rules, table_name, run_id)
        dbt_yaml  = rules_to_dbt_schema(rules, table_name, run_id)
        soda_yaml = rules_to_soda_checks(rules, table_name, run_id)

        # Save to Blob Storage
        urls = save_configs_to_blob(ge_json, dbt_yaml, soda_yaml, table_name, run_id)

        return func.HttpResponse(
            json.dumps({
                "status":      "generated",
                "run_id":      run_id,
                "table_name":  table_name,
                "rules_used":  len(rules),
                "configs":     urls,
                "message":     f"Generated 3 config files for {table_name} run {run_id}"
            }),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logging.error(f"GenerateConfigs error: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500, mimetype="application/json"
        )