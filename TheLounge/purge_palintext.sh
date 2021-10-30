#!/bin/bash

# Path to sqlite logs (usually you don't need to edit this)
logs=$HOME/.thelounge/logs

# How many days to keep of logs?
max_logs_lines=1000

# Path to the uploads folder (usually you don't need to edit this)
uploads=$HOME/.thelounge/uploads

# How many days to keep the files
max_upload_days=7

# Cleaning up the plaintext logs
find "$logs" -name "*.log" -print0 | while read -rd $'\0' file
do
    tail -n "$max_logs_lines" "$file" > "$logs"/file.tmp
    mv "$logs"/file.tmp "$file"
done

# Cleaning up the uploaded files
find "$uploads" -type f -ctime +"$max_upload_days" -delete
