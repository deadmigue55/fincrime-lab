# Scaling benchmark

Query time and memory from 1 million to 180 million rows on one machine. Protocol in
`src/fincrime/lake/benchmark.py`: each size is a prefix of the same time-ordered file, each
query runs three times (once only if its first run exceeds two minutes), the median is
reported, and memory is the peak resident set sampled every 50 ms.

Machine: 20 threads, 34 GB RAM, Windows-11-10.0.26200-SP0, DuckDB 2.0.0, Python 3.12.15.

| Query | 1M | 5M | 10M | 25M | 50M | 100M | 180M | Growth 1M to max |
|---|---|---|---|---|---|---|---|---|
| Scan and aggregate | 0.05s | 0.05s | 0.07s | 0.11s | 0.21s | 0.53s | 1.16s | time x25 for data x180 |
| Group by account (top 10) | 0.07s | 0.12s | 0.16s | 0.61s | 1.34s | 3.72s | 11.04s | time x148 for data x180 |
| Distinct accounts | 0.09s | 0.23s | 0.52s | 1.99s | 5.15s | 24.01s | 109.49s | time x1200 for data x180 |
| Build the transfer-edge table | 0.08s | 0.14s | 0.33s | 0.87s | 2.39s | 11.17s | 40.24s | time x511 for data x180 |
| Join to the accounts table | 0.11s | 0.12s | 0.15s | 0.26s | 0.44s | 1.14s | 3.03s | time x27 for data x180 |
| Rolling 24-hour velocity | 0.09s | 0.39s | 0.93s | 3.14s | 8.61s | 38.71s | 128.81s | time x1364 for data x180 |
| Read one day | 0.03s | 0.04s | 0.06s | 0.11s | 0.24s | 0.57s | 0.81s | time x24 for data x180 |

Peak process memory over the whole run: 16.9 GB.

## File layout

| Layout | Median time | Size on disk |
|---|---|---|
| sorted file | 37 ms | 1,153 MB |
| shuffled file | 1,566 ms | 1,540 MB |
| day partitions | 16 ms | 992 MB |
