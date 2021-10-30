#!/bin/bash

# Path to sqlite logs (usually you don't need to edit this)
logs=$HOME/.thelounge/logs

# How many days to keep of logs?
max_logs_days=7

# Path to the uploads folder (usually you don't need to edit this)
uploads=$HOME/.thelounge/uploads

# How many days to keep the files
max_upload_days=7

# Cleaning up the sqlite logs
for filename in "$logs"/*.sqlite3; do
  sqlite3 "$filename" "delete from messages where time < strftime('%s', datetime('now', '-$max_logs_days day'))*1000; VACUUM;"
done

# Cleaning up the uploaded files
find "$uploads" -type f -ctime +"$max_upload_days" -delete
