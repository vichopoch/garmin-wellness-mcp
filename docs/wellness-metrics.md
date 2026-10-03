# Wellness tools and data contract

The `wellness` profile registers exactly 22 tools. The implementation is in
`src/garmin_mcp/wellness.py`; contract tests are in `tests/test_wellness_tools.py`.
All tools declare `readOnlyHint=true`, `destructiveHint=false`, and
`openWorldHint=false`, return structured JSON, and advertise the `garmin:read`
OAuth scope. `get_profile` also declares `openai/profile=true`.

**Availability is account- and date-dependent.** Real Garmin calls were verified
on Railway on 2026-10-03; private execution results record which dates and metrics
were available. Implemented support is not proof that every watch/account
supplies a measurement. Mock values are test fixtures, never account data.

## Registered tools

| Tool | Purpose and bounds |
| --- | --- |
| `get_profile` | Stable configured opaque profile ID and fixed display name; no Garmin email |
| `get_capabilities` | Metrics observed today/yesterday; false means not observed, not unsupported |
| `get_wellness_today` | Full compact daily summary and one latest activity summary |
| `get_daily_health` | One ISO date: health, sleep, HRV, Body Battery, stress, training, naps |
| `get_health_range` | Full summaries for 1–31 inclusive days |
| `get_sleep` | Sleep duration, phases, score, need and recorded timing |
| `get_sleep_analysis` | Duration, score and need statistics for up to 365 days |
| `get_naps` | Total nap seconds from the daily sleep response |
| `get_recovery_context` | Today versus preceding 2–90 days; median baseline, change, slope |
| `get_hrv` | Nightly/weekly HRV, baseline and Garmin status |
| `get_body_battery` | Latest/high/low level and charged/drained quantities |
| `get_stress` | Daily mean/max and measured stress durations |
| `get_training_overview` | Readiness, recovery seconds, status, load and VO2 max |
| `get_training_readiness` | Readiness score and recovery seconds |
| `get_training_status` | Garmin status, acute/chronic load and ratio |
| `get_vo2max` | Running and cycling estimates with available precision |
| `get_activities` | Curated activity list; limit 1–50, offset 0–10000 |
| `get_activity` | Curated summary by positive numeric activity ID |
| `get_metric_trend` | Daily points, rolling means (window 1–90) and linear slope |
| `compare_periods` | Up to 10 metrics, each period at most 365 days |
| `get_metric_timeseries` | Daily points, at most 365 days and `max_points` 1–2000 |
| `find_correlations` | Pearson correlation with lag −30…30 calendar days |

An omitted date on individual sleep/recovery/training tools defaults to the
current date in the resolved account timezone. Tools with explicit range
arguments require ISO `YYYY-MM-DD` strings.

## Normalized metric mappings

The paths below are implemented extraction paths, not claims that Garmin always
returns them. Consult tool availability and missing-data fields for each date.
`dailySleepDTO` is abbreviated to `sleepDTO` below.

| Metric / normalized field | SDK source method | Garmin field or transformation | Primary tools |
| --- | --- | --- | --- |
| `health.steps` | `get_stats` | `totalSteps` | daily health, wellness today |
| `health.distance_meters` | `get_stats` | `totalDistanceMeters` | daily health |
| `health.active_calories` | `get_stats` | `activeKilocalories` | daily health |
| `health.resting_hr` | `get_stats` | `restingHeartRate`, bpm | daily health, recovery context |
| `health.min_hr`, `health.max_hr` | `get_stats` | `minHeartRate`, `maxHeartRate`, bpm | daily health |
| `health.intensity_minutes` | `get_stats` | `intensityMinutes`; otherwise moderate + 2 × vigorous, only when both inputs exist | daily health |
| `health.spo2_avg` | `get_spo2_data` | `averageSpO2` or `averageSpo2`, percent | daily health, wellness today |
| `health.respiration_avg` | `get_respiration_data` | `avgWakingRespirationValue` or `avgRespirationRate`, breaths/min | daily health, wellness today |
| `sleep.sleep_seconds` | `get_sleep_data` | `sleepDTO.sleepTimeSeconds` | sleep, sleep analysis |
| `sleep.sleep_hours` | `get_sleep_data` | sleep seconds / 3600; display rounded to 3 decimals, original seconds preserved | sleep |
| `sleep.sleep_score` | `get_sleep_data` | `sleepDTO.sleepScores.overall.value` | sleep, recovery context |
| `sleep.deep_seconds`, `light_seconds`, `rem_seconds`, `awake_seconds` | `get_sleep_data` | `deepSleepSeconds`, `lightSleepSeconds`, `remSleepSeconds`, `awakeSleepSeconds` within sleepDTO | sleep |
| `sleep.sleep_need_seconds` | `get_sleep_data` | `sleepDTO.sleepNeedSeconds`; otherwise `sleepDTO.sleepNeed.actual` minutes × 60 | sleep, sleep analysis |
| `sleep.bedtime`, `sleep.wake_time` | `get_sleep_data` | GMT start/end epoch milliseconds converted to ISO timestamps with UTC offset | sleep |
| `sleep.hrv_avg` | `get_sleep_data` | `avgOvernightHrv`, ms | sleep |
| `sleep.skin_temperature_change` | `get_sleep_data` | `sleepDTO.avgSkinTempDeviationC`, signed °C deviation | sleep, capabilities |
| `naps.total_seconds` | `get_sleep_data` | `sleepDTO.napTimeSeconds`; zero remains measured zero | naps |
| `hrv.nightly_avg`, `weekly_avg` | `get_hrv_data` | `hrvSummary.lastNightAvg`, `weeklyAvg`, ms | HRV, recovery context |
| `hrv.baseline_low`, `baseline_high` | `get_hrv_data` | `hrvSummary.baseline.balancedLow`, `balancedUpper`, ms | HRV |
| `hrv.status` | `get_hrv_data` | `hrvSummary.status` | HRV |
| `body_battery.latest` | `get_body_battery` | `bodyBatteryMostRecentValue`; otherwise last valid level in provided sample order | Body Battery |
| `body_battery.charged`, `drained` | `get_body_battery` | `charged`/`chargedValue`, `drained`/`drainedValue` | Body Battery |
| `body_battery.high`, `low` | `get_stats`, `get_body_battery` | `bodyBatteryHighestValue`/`bodyBatteryLowestValue`; fallback to valid sample maximum/minimum | Body Battery |
| `stress.average` | `get_stress_data`, `get_stats` | `avgStressLevel`; fallback `averageStressLevel` | stress, recovery context |
| `stress.maximum` | `get_stress_data` | `maxStressLevel` | stress |
| `stress.low_seconds`, `medium_seconds`, `high_seconds` | `get_stats` | `lowStressDuration`, `mediumStressDuration`, `highStressDuration` | stress |
| `training.readiness` | `get_training_readiness` | Latest snapshot `score` or `readinessScore` | training readiness, recovery context |
| `training.recovery_seconds` | `get_training_readiness` | Latest `recoveryTime` minutes × 60; zero when change phrase is `REACHED_ZERO` | training readiness |
| `training.training_status` | `get_training_status` | `mostRecentTrainingStatus.latestTrainingStatusData` latest dated device record's `trainingStatus` | training status |
| `training.acute_load`, `chronic_load`, `load_ratio` | `get_training_status` | Record's `acuteTrainingLoadDTO.dailyTrainingLoadAcute`, `dailyTrainingLoadChronic`, `dailyAcuteChronicWorkloadRatio` | training status |
| `training.vo2max`, `cycling_vo2max` | `get_max_metrics`, `get_training_status` | `generic`/`cycling` blocks, fallback `mostRecentVO2Max`; prefer `vo2MaxPreciseValue` over `vo2MaxValue` | VO2 max |
| Activity ID/type/start/duration/distance/calories/HR/effect/load | `get_activities`, `get_activity` | Allowlisted summary fields; details read from `summaryDTO` when present | activities, activity |

Activity fields are `activity_id`, `activity_type`, `start_time`,
`duration_seconds`, `distance_meters`, `calories`, `avg_hr`, `max_hr`,
`training_effect`, and `training_load`. GPS, names, descriptions, raw splits,
user IDs, email, and device IDs are not included.

### Verified SDK semantics

The pinned `garminconnect==0.3.17` implementation and its `typed.py` document
Body Battery responses as lists containing timestamp/level pairs, and Training
Readiness responses as snapshots. The most recent snapshot is selected by
`timestampLocal`, falling back to `timestamp` and then `calendarDate`. Recovery
minutes and the `REACHED_ZERO` exception are documented by the SDK.
See [the upstream response models](https://github.com/cyberjunky/python-garminconnect/blob/0.3.17/garminconnect/typed.py).

Sleep need's `actual` field is measured in minutes, as shown by the dependency's
[Daily Sleep Data with Sleep Need documentation](https://garth.readthedocs.io/en/latest/api/data/).
The GMT sleep fields are used because the SDK documents incorrect local sleep
timestamps for some accounts. Actual bedtime/wake time are recorded sleep timing,
not recommended optimal bedtime/wake time.

## Analytics contract

Accepted metric names are:

```text
steps
resting_hr
sleep_duration
sleep_score
sleep_need
hrv
body_battery
stress
training_readiness
training_load
vo2max
spo2
respiration
```

`sleep_duration` and `sleep_need` use seconds; `hrv` uses ms; `resting_hr` uses bpm.
`body_battery` means the latest measured daily level. `training_load` means the
acute load reported by Garmin, not the sum of activity loads.

Statistics exclude missing/non-finite values and retained stale fields from failed
refreshes while reporting `sample_count` and
`missing_days`. No gap is filled with zero. Recovery baseline is the median of
the preceding requested days, excluding today; change is today minus that median.
Linear slope uses actual day indices so gaps do not compress time. Rolling means
use available observations in a trailing calendar-day window and include their
sample counts. Period comparisons use B minus A and percentages relative to A's
mean; percentage change is null when A's mean is zero or data are missing.

A positive correlation lag pairs X(day) with Y(day + lag), only within the
requested date range. Results distinguish missing pairs from boundary exclusions.
At least three complete pairs and nonzero variance are required. Correlation is
exploratory, does not imply causation, and is not medical advice. No multiple-test
correction, detrending, confidence interval, or clinical inference is provided.

## Missing data, caching and limits

Each summary section has `available`. Missing sections include a neutral reason;
source request failures appear separately in `source_errors` using sanitized error
codes. Timeseries keep missing days with `value: null` and a reason. Measurements
are never fabricated. Capability observations span two dates and do not establish
permanent device support.

When a refresh fails, the store may retain the previous measured fields. Section
tools and training projections preserve `source_updated_at` and `stale_groups`,
including each failed source, its retained fields, and last successful fetch time.
Analytics excludes these retained fields with `value: null` and
`reason: cached_value_stale`; failed refreshes never silently turn old readings
into current observations.

The service checks the normalized persistent store before Garmin. Its process
cache has a configurable `GARMIN_CACHE_TTL_SECONDS` (default 900 seconds) and a
4096-entry cap. Raw responses are transient in process memory only; the persistent
store receives curated daily summaries. SDK calls run outside the event loop with
concurrency limited to two; timed-out workers retain permits until they finish.
Network retry/backoff belongs to the production client wrapper, not duplicated in
the Wellness service.

Each tool invocation permits at most **60 uncached SDK calls**. Larger cold queries
return an explicit instruction to run historical sync or reduce the range. A
full daily summary uses ten source methods; today adds profile/timezone resolution
once and one activity-list call. Cached historical analytics need no Garmin calls.
The background sync invokes the daily service directly and is not subject to a
single interactive tool's budget.

Every tool call emits one structured `mcp_tool_call` event with tool name, status,
duration, generated request ID, and exception class on failure. Arguments, results
and exception messages are never included. Partial upstream failures use status
`unavailable`.

## Explicit limitations

- Real account/device availability and Garmin payload variants remain unverified
  until the interactive authentication/bootstrap step succeeds.
- Intraday heart rate, Body Battery, stress, respiration, SpO₂ and HRV arrays are
  not exposed; timeseries are daily summaries.
- Nap duration is supported; individual nap intervals are not extracted.
- Optimal bedtime/wake-time recommendations are not extracted.
- Fitness Age, Endurance Score, Hill Score, Lactate Threshold and load-balance
  detail are not currently exposed by this profile. The SDK has read methods for
  several of these, but adding them safely requires representative account
  payloads, curated mappings, tests, and a new read-only audit fingerprint.
- Mixed-device training status chooses a latest dated record, not a verified
  preferred device when multiple records share the same date.
- Skin-temperature and sleep-need fields are returned only when their implemented
  response paths exist; no extra endpoint is queried to infer them.
- Historical sync requests 90 days of daily summaries. Activity persistence is a
  snapshot of the latest 100 activities, not a complete 90-day activity history.
- Daily activity load/late-activity time are not analytics metrics; correlations
  involving late activity are not currently supported.
- No GPS tracks are persisted or returned. There is no Garmin mutation tool.
