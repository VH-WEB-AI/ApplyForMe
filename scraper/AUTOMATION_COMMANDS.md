# Jobs automation — main command

```bash
cd /home/vhits/applyForMe/scraper && .venv/bin/python -u daily_pipeline.py --date YYYY-MM-DD
```



expire jobs:


python3 expire_jobs.py --fetch --check --browser --apply



Replace `YYYY-MM-DD` with the posting date to scrape. It scrapes, fixes, checks, posts to both
panels and verifies. Result: `cat daily_runs/LAST_STATUS.txt`
