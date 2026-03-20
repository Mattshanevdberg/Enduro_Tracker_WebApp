-- ALWAYS BACK UP THE DATABASE BEFORE RUNNING THIS SCRIPT, AS IT WILL PERMANENTLY DELETE DATA.
-- back up BASH: pg_dump -h localhost -U enduro_tracker -d enduro_tracker > enduro_tracker_$(date +%F-%H%M%S).sql

-- This SQL script deletes old track history snapshots, keeping only the most recent entry for each race rider
-- To run this use the command: psql -h localhost -U enduro_tracker -d enduro_tracker -f tests/prune_old_track_hist_snaps.sql
DELETE FROM track_hist
WHERE id NOT IN (
  SELECT MAX(id) FROM track_hist GROUP BY race_rider_id
);
VACUUM;
