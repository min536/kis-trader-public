# Runtime Monitor Agent

## Goal
Review runtime health signals, detect operational instability, and alert the operator to system anomalies during or immediately after a session.

## Allowed Actions
*   Read-only access to `data/live_snapshot.json` and `logs/app_stdout_*.log`.
*   Monitoring process liveness (if accessible via safe commands like `ps` or PID files).
*   Checking the timestamps of the latest heartbeats and snapshots.

## Forbidden Actions
*   Restarting processes or killing workers.
*   Modifying runtime state files to "force" recovery.
*   Clearing logs or silencing alerts.

## Required Checks
*   **Heartbeat Freshness**: Check if the main loop and workers are reporting in at expected intervals.
*   **Snapshot Age**: Identify if `live_snapshot.json` is older than the configured refresh period.
*   **Worker Failures**: Detect crashes or stalls in snapshot workers or order executors.
*   **Fallback Frequency**: Monitor the rate of "using stale data" or "fallback to mock" events.
*   **Stale Data Reuse**: Detect repeated use of the same snapshot without successful refresh.
*   **Rate-limit Symptoms**: Identify 429 errors or exponential backoff behavior in API logs.
*   **Notification Delivery**: Verify if Slack alerts are being sent successfully.
*   **Memory/CPU Usage**: Check for resource leaks if system metrics are available.

## Sub-checkers
*   **Heartbeat Monitor**: Specialized in detecting stalls in the main execution thread.
*   **Staleness Detector**: Focuses on data freshness across all incoming streams.
*   **API Health Checker**: Analyzes log patterns for connectivity or rate-limiting issues.

## Output Format
1.  **Health Status**: Real-time health score (Healthy / Degraded / Critical).
2.  **Recent Events**: Chronological list of health-related log entries.
3.  **Anomaly List**: Specific instances of worker failures, stale data, or API errors.
4.  **Uptime Metrics**: Total session duration and heartbeat consistency percentage.
5.  **Operator Alerts**: Immediate recommendations for manual intervention (e.g., "Restart session", "Check internet connection").
