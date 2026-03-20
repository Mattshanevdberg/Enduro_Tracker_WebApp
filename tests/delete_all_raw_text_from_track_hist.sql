-- ALWAYS BACK UP THE DATABASE BEFORE RUNNING THIS SCRIPT, AS IT WILL PERMANENTLY DELETE DATA.
-- back up BASH: pg_dump -h localhost -U enduro_tracker -d enduro_tracker > enduro_tracker_$(date +%F-%H%M%S).sql
-- This SQL script deletes all raw text entries from track_hist
UPDATE track_hist SET raw_txt = NULL;
VACUUM;
