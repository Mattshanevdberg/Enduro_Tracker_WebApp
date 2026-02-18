-- ALWAYS BACK UP THE DATABASE BEFORE RUNNING THIS SCRIPT, AS IT WILL PERMANENTLY DELETE DATA.
-- back up BASH: cp enduro_tracker.db enduro_tracker.db.bak.$(date +%F-%H%M%S)
-- This SQL script deletes all raw text entries from track_hist
UPDATE track_hist SET raw_txt = NULL;
VACUUM;