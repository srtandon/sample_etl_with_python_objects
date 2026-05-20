-- Sample DuckDB SQL queries over ingested Parquet files.
--
-- Prerequisites:
--   Run the pipeline at least once to populate the output/ directory:
--
--     python scripts/run_ingestion.py --feed fixtures/feed_v1.json --trial-id TrialB --output-dir output
--     python scripts/run_ingestion.py --feed fixtures/feed_v2.json --trial-id TrialC --output-dir output
--
-- Then run any query interactively:
--
--     duckdb -c ".read queries/sample_queries.sql"
--
-- Or run all queries at once via the helper script:
--
--     python scripts/run_queries.py


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. All cohorts across every ingested trial, ranked by total dose exposure
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    trial_id,
    cohort_id,
    patient_count,
    dose,
    total_dose_exposure
FROM read_parquet('output/cohorts/*.parquet')
ORDER BY total_dose_exposure DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Total enrolled patient headcount and cohort count per trial
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    trial_id,
    SUM(patient_count)  AS total_patients,
    COUNT(cohort_id)    AS cohort_count,
    AVG(dose)           AS avg_dose,
    SUM(total_dose_exposure) AS trial_dose_burden
FROM read_parquet('output/cohorts/*.parquet')
GROUP BY trial_id
ORDER BY total_patients DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Schedule details joined to cohort summary
--    (demonstrates cross-file JOIN — a query that would need DynamoDB exports
--     or custom code in a procedural pipeline)
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    s.trial_id,
    s.name,
    s.cycle_length,
    s.duration,
    s.total_days,
    c.cohort_count,
    c.total_patients,
    ROUND(c.total_patients * 1.0 / c.cohort_count, 1) AS avg_patients_per_cohort
FROM read_parquet('output/schedules/*.parquet') s
JOIN (
    SELECT
        trial_id,
        COUNT(*)          AS cohort_count,
        SUM(patient_count) AS total_patients
    FROM read_parquet('output/cohorts/*.parquet')
    GROUP BY trial_id
) c ON s.trial_id = c.trial_id;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Window function: rank cohorts within each trial by dose exposure
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    trial_id,
    cohort_id,
    total_dose_exposure,
    RANK() OVER (
        PARTITION BY trial_id
        ORDER BY total_dose_exposure DESC
    ) AS rank_within_trial
FROM read_parquet('output/cohorts/*.parquet')
ORDER BY trial_id, rank_within_trial;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Ingestion audit: latest ingested_at timestamp per trial
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    trial_id,
    MAX(ingested_at) AS last_ingested
FROM read_parquet('output/cohorts/*.parquet')
GROUP BY trial_id
ORDER BY last_ingested DESC;
