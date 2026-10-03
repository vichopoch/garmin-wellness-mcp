# Tool side-effect audit

Review date: 2026-10-02 (America/Santiago). Upstream tool source plus installed `garminconnect==0.3.17` and its native client were inspected. Classification is based on call bodies and helper call chains, not tool names. All 153 upstream decorated tools are accounted for.

`READ`: fetch/in-memory transformation only. `WRITE`: Garmin mutations or tool-directed local file/config writes. `DESTRUCTIVE`: delete/remove/unschedule operations. `UNKNOWN`: absent or changed reviewed implementation; always denied. Internal OAuth refresh/token persistence and normalized DB cache writes are operational exceptions, not LLM-controlled Garmin mutation tools.

Registration requires a READ manifest entry, exact callable qualified identity, unchanged module SHA-256, and reviewed SDK source/version. An environment allowlist cannot override this policy. Newly added methods are UNKNOWN until reviewed. Fingerprints intentionally fail closed after edits; review and update the manifest in the same change.

## Significant findings

- `request_reload` performs POST and changes server-side state: WRITE.
- `download_activity_file`, `download_course_gpx`, and `set_fit_download_dir` write local files/config: WRITE. In-memory FIT parsing/download helpers remain READ.
- `get_scheduled_workouts`, `get_garmin_coach_workouts`, and its legacy alias issue POST GraphQL, but reviewed bodies construct fixed **query**, never mutation, with ISO-date validation. These are READ. The generic `query_garmin_graphql` method is excluded from the guarded high-level client.
- `connectapi` delegates to native `Client.connectapi`, which hardcodes GET. `download` delegates to native GET and returns bytes. Arbitrary-path helpers are excluded from high-level `READ_METHODS`.
- `set_heart_rate_zones` invokes explicit PUT through `client.request`; helper-mediated activity summary updates invoke PUT.
- `remove_gear_from_activity` uses PUT, but removes an association: DESTRUCTIVE by semantics.

## Every upstream MCP tool

| Module | Tool | Class | Call-chain evidence |
|---|---|---|---|
| activity_analysis | `get_activity_fit_messages` | READ | `garmin_client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL); getattr(candidate, 'name', None); getattr(candidate, attr, None); getattr(field, 'field_def', None); getattr(field, 'field_type', None); getattr(field, 'name', None); getattr(field, 'raw_value', None); getattr(field, 'type', None); getattr(field, 'units', None); getattr(field, 'value', None); getattr(field, source_attr, None); getattr(getattr(field, 'type', None), 'values', None); getattr(message, '_definition', None); getattr(message, 'definition', None); getattr(message, 'fields', []); getattr(message, 'name', None); getattr(metadata, 'name', None); getattr(selector_field, 'raw_value', None); getattr(selector_field, 'value', None); getattr(selector_type, 'values', {}); getattr(subtype_type, 'values', {}); getattr(value, 'isoformat', None)` |
| activity_analysis | `get_activity_fit_data` | READ | `garmin_client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL); garmin_client.get_body_composition(activity_date_str)` |
| activity_analysis | `get_power_duration_curve` | READ | `garmin_client.download_activity(act_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL); garmin_client.get_activities(0, num_activities)` |
| activity_analysis | `download_activity_file` | WRITE | `f.write(payload); garmin_client.download_activity(activity_id, dl_fmt=format_map[fmt]); open(file_path, 'wb'); open(path, 'r', encoding='utf-8'); os.makedirs(download_dir, exist_ok=True)` |
| activity_analysis | `set_fit_download_dir` | WRITE | `open(path, 'r', encoding='utf-8'); open(path, 'w', encoding='utf-8'); os.makedirs(abspath, exist_ok=True); os.makedirs(parent, exist_ok=True)` |
| activity_management | `get_activities_by_date` | READ | `garmin_client.connectapi(garmin_client.garmin_connect_activities, params=params)` |
| activity_management | `get_activities_fordate` | READ | `garmin_client.get_activities_fordate(date)` |
| activity_management | `get_activity` | READ | `garmin_client.get_activity(activity_id)` |
| activity_management | `set_activity_name` | WRITE | `garmin_client.set_activity_name(activity_id, activity_name)` |
| activity_management | `set_activity_type` | WRITE | `garmin_client.get_activity_types(); garmin_client.set_activity_type(activity_id, match['typeId'], match['typeKey'], match.get('parentTypeId'))` |
| activity_management | `set_activity_description` | WRITE | `garmin_client.client.put('connectapi', url, json=body, api=True)` |
| activity_management | `set_activity_event_type` | WRITE | `garmin_client.client.put('connectapi', url, json=body, api=True); garmin_client.connectapi('/activity-service/activity/eventTypes')` |
| activity_management | `set_perceived_effort` | WRITE | `garmin_client.client.put('connectapi', url, json=body, api=True)` |
| activity_management | `set_activity_feel` | WRITE | `garmin_client.client.put('connectapi', url, json=body, api=True)` |
| activity_management | `get_activity_splits` | READ | `garmin_client.get_activity_splits(activity_id)` |
| activity_management | `get_activity_typed_splits` | READ | `garmin_client.get_activity_typed_splits(activity_id)` |
| activity_management | `get_activity_split_summaries` | READ | `garmin_client.get_activity_split_summaries(activity_id)` |
| activity_management | `get_activity_weather` | READ | `garmin_client.get_activity_weather(activity_id); garmin_client.get_unit_system()` |
| activity_management | `get_activity_hr_in_timezones` | READ | `garmin_client.get_activity_hr_in_timezones(activity_id)` |
| activity_management | `get_activity_power_in_timezones` | READ | `garmin_client.get_activity_power_in_timezones(activity_id)` |
| activity_management | `get_activity_gear` | READ | `garmin_client.get_activity_gear(activity_id)` |
| activity_management | `get_activity_exercise_sets` | READ | `garmin_client.get_activity_exercise_sets(activity_id)` |
| activity_management | `count_activities` | READ | `garmin_client.count_activities()` |
| activity_management | `get_activities` | READ | `garmin_client.get_activities(start, limit)` |
| activity_management | `create_manual_activity` | WRITE | `garmin_client.create_manual_activity(start_datetime=start_datetime, time_zone=time_zone, type_key=type_key, distance_km=distance_km, duration_min=duration_minutes, activity_name=name)` |
| activity_management | `get_activity_types` | READ | `garmin_client.get_activity_types()` |
| calendar_events | `get_calendar_events` | READ | `garmin_client.connectapi(path); garmin_client.get_scheduled_workouts(year, month)` |
| challenges | `get_goals` | READ | `client.connectapi(url, params=params, headers=dict(_GOALS_HEADERS)); client.get_goals(goal_type); getattr(client, 'garmin_connect_goals_url', None)` |
| challenges | `get_personal_record` | READ | `garmin_client.get_personal_record()` |
| challenges | `get_earned_badges` | READ | `garmin_client.get_earned_badges()` |
| challenges | `get_adhoc_challenges` | READ | `garmin_client.get_adhoc_challenges(start, min(limit, 100))` |
| challenges | `get_available_badge_challenges` | READ | `garmin_client.get_available_badge_challenges(start, min(limit, 100))` |
| challenges | `get_badge_challenges` | READ | `garmin_client.get_badge_challenges(start, min(limit, 100))` |
| challenges | `get_non_completed_badge_challenges` | READ | `garmin_client.get_non_completed_badge_challenges(start, min(limit, 100))` |
| challenges | `get_race_predictions` | READ | `garmin_client.get_race_predictions()` |
| challenges | `get_inprogress_virtual_challenges` | READ | `garmin_client.get_inprogress_virtual_challenges(start, min(limit, 100))` |
| courses | `get_courses` | READ | `garmin_client.client.connectapi('/course-service/course')` |
| courses | `get_course_details` | READ | `garmin_client.client.connectapi(f'/course-service/course/{course_id}')` |
| courses | `download_course_gpx` | WRITE | `f.write('\n'.join(lines)); garmin_client.client.connectapi(f'/course-service/course/{course_id}'); open(target_path, 'w', encoding='utf-8'); os.makedirs(os.path.dirname(target_path), exist_ok=True)` |
| courses | `upload_course` | WRITE | `garmin_client.client.post('connectapi', '/course-service/course', json=payload, api=True); garmin_client.client.post('connectapi', '/course-service/course/import', files={'file': (os.path.basename(gpx_path), io.BytesIO(gpx_bytes), 'application/gpx+xml')}, api=True); open(gpx_path, 'rb')` |
| courses | `delete_course` | DESTRUCTIVE | `garmin_client.client.delete('connectapi', f'/course-service/course/{course_id}')` |
| data_management | `add_body_composition` | WRITE | `garmin_client.add_body_composition(date, weight=weight, percent_fat=percent_fat, percent_hydration=percent_hydration, visceral_fat_mass=visceral_fat_mass, bone_mass=bone_mass, muscle_mass=muscle_mass, basal_met=basal_met, active_met=active_met, physique_rating=physique_rating, metabolic_age=metabolic_age, visceral_fat_rating=visceral_fat_rating, bmi=bmi)` |
| data_management | `set_blood_pressure` | WRITE | `garmin_client.set_blood_pressure(systolic, diastolic, pulse, notes=notes)` |
| data_management | `add_hydration_data` | WRITE | `garmin_client.add_hydration_data(value_in_ml=value_in_ml, cdate=cdate, timestamp=timestamp)` |
| devices | `get_devices` | READ | `garmin_client.get_devices()` |
| devices | `get_device_last_used` | READ | `garmin_client.get_device_last_used()` |
| devices | `get_device_settings` | READ | `garmin_client.get_device_last_used(); garmin_client.get_device_settings(device_id)` |
| devices | `get_primary_training_device` | READ | `garmin_client.get_primary_training_device()` |
| devices | `get_device_solar_data` | READ | `garmin_client.get_device_solar_data(device_id, date)` |
| devices | `get_device_alarms` | READ | `garmin_client.get_device_alarms()` |
| gear_management | `get_gear` | READ | `garmin_client.connectapi(GEAR_V2_LIST_ENDPOINT); garmin_client.get_device_last_used(); garmin_client.get_gear(user_profile_id); garmin_client.get_gear_defaults(user_profile_id); garmin_client.get_gear_stats(uuid)` |
| gear_management | `add_gear_to_activity` | WRITE | `garmin_client.add_gear_to_activity(gear_uuid, activity_id)` |
| gear_management | `remove_gear_from_activity` | DESTRUCTIVE | `garmin_client.remove_gear_from_activity(gear_uuid, activity_id)` |
| health_wellness | `get_stats` | READ | `garmin_client.get_stats(date)` |
| health_wellness | `get_stats_range` | READ | `garmin_client.connectapi(url, params={'statsType': 'CALORIES'}); garmin_client.connectapi(url, params={'statsType': 'STEPS'})` |
| health_wellness | `get_energy_balance` | READ | `client.connectapi('/nutrition-service/food/logs/range', params={'startDate': start.isoformat(), 'endDate': end.isoformat()}); client.connectapi(url, params={'statsType': 'CALORIES'}); garmin_client.get_body_composition(start_date, end_date)` |
| health_wellness | `get_user_summary` | READ | `garmin_client.get_user_summary(date)` |
| health_wellness | `get_body_composition` | READ | `garmin_client.get_body_composition(start_date); garmin_client.get_body_composition(start_date, end_date)` |
| health_wellness | `get_stats_and_body` | READ | `garmin_client.get_stats_and_body(date)` |
| health_wellness | `get_steps_data` | READ | `garmin_client.get_steps_data(date)` |
| health_wellness | `get_daily_steps` | READ | `garmin_client.get_daily_steps(start_date, end_date)` |
| health_wellness | `get_training_readiness` | READ | `garmin_client.get_training_readiness(date)` |
| health_wellness | `get_body_battery` | READ | `garmin_client.get_body_battery(start_date, end_date)` |
| health_wellness | `get_body_battery_events` | READ | `garmin_client.get_body_battery_events(date)` |
| health_wellness | `get_blood_pressure` | READ | `garmin_client.get_blood_pressure(start_date, end_date)` |
| health_wellness | `get_floors` | READ | `garmin_client.get_floors(date)` |
| health_wellness | `get_rhr_day` | READ | `garmin_client.get_rhr_day(date)` |
| health_wellness | `get_heart_rates` | READ | `garmin_client.get_heart_rates(date)` |
| health_wellness | `get_heart_rates_summary` | READ | `garmin_client.get_heart_rates(date)` |
| health_wellness | `get_hydration_data` | READ | `garmin_client.get_hydration_data(date)` |
| health_wellness | `get_sleep_data` | READ | `garmin_client.get_sleep_data(date)` |
| health_wellness | `get_sleep_summary` | READ | `garmin_client.get_sleep_data(date)` |
| health_wellness | `get_sleep_summary_range` | READ | `garmin_client.get_sleep_data(date_str)` |
| health_wellness | `get_stress_data` | READ | `garmin_client.get_stress_data(date)` |
| health_wellness | `get_stress_summary` | READ | `garmin_client.get_stress_data(date)` |
| health_wellness | `get_respiration_data` | READ | `garmin_client.get_respiration_data(date)` |
| health_wellness | `get_respiration_summary` | READ | `garmin_client.get_respiration_data(date)` |
| health_wellness | `get_spo2_data` | READ | `garmin_client.get_spo2_data(date)` |
| health_wellness | `get_all_day_stress` | READ | `garmin_client.get_all_day_stress(date)` |
| health_wellness | `get_all_day_events` | READ | `garmin_client.get_all_day_events(date)` |
| health_wellness | `get_lifestyle_logging_data` | READ | `garmin_client.get_lifestyle_logging_data(date)` |
| health_wellness | `get_weekly_steps` | READ | `garmin_client.get_weekly_steps(end_date, weeks)` |
| health_wellness | `get_weekly_stress` | READ | `garmin_client.get_weekly_stress(end_date, weeks)` |
| health_wellness | `get_weekly_intensity_minutes` | READ | `garmin_client.get_weekly_intensity_minutes(start_date, end_date)` |
| health_wellness | `get_morning_training_readiness` | READ | `garmin_client.get_morning_training_readiness(date)` |
| health_wellness | `get_recovery_time_remaining` | READ | `get_activities(0, 20); getattr(client, 'get_activities', None); getattr(client, 'get_morning_training_readiness', None); getattr(client, 'get_training_readiness', None)` |
| nutrition | `get_nutrition_daily_food_log` | READ | `garmin_client.connectapi(url)` |
| nutrition | `get_nutrition_summary_between_dates` | READ | `garmin_client.connectapi('/nutrition-service/food/logs/range', params={'startDate': start_date, 'endDate': end_date})` |
| nutrition | `get_nutrition_daily_meals` | READ | `garmin_client.connectapi(url)` |
| nutrition | `get_nutrition_daily_settings` | READ | `garmin_client.connectapi(url)` |
| nutrition | `set_nutrition_daily_settings` | WRITE | `garmin_client.client.put('connectapi', url, json=current, api=True); garmin_client.connectapi(url)` |
| nutrition | `search_foods` | READ | `garmin_client.connectapi('/nutrition-service/food/search', params={'searchExpression': query, 'start': start, 'limit': limit})` |
| nutrition | `get_custom_foods` | READ | `garmin_client.connectapi('/nutrition-service/customFood', params={'searchExpression': search, 'start': start, 'limit': limit, 'includeContent': 'true'})` |
| nutrition | `get_custom_food_serving_units` | READ | `garmin_client.connectapi(url)` |
| nutrition | `create_custom_food` | WRITE | `garmin_client.client.put('connectapi', url, json=payload, api=True); getattr(e.error.response, 'text', '')` |
| nutrition | `update_custom_food` | WRITE | `garmin_client.client.put('connectapi', url, json=payload, api=True); garmin_client.connectapi('/nutrition-service/customFood', params={'searchExpression': food_name, 'start': 0, 'limit': 20, 'includeContent': 'true'}); getattr(e.error.response, 'text', '')` |
| nutrition | `delete_custom_food` | DESTRUCTIVE | `garmin_client.client.delete('connectapi', url, api=True); getattr(e.error.response, 'text', '')` |
| nutrition | `log_custom_food` | WRITE | `garmin_client.client.put('connectapi', url, json=payload, api=True); garmin_client.connectapi(meals_url); getattr(e.error.response, 'text', '')` |
| nutrition | `log_food` | WRITE | `garmin_client.client.put('connectapi', url, json=payload, api=True); garmin_client.connectapi(meals_url); getattr(e.error.response, 'text', '')` |
| nutrition | `delete_food_log` | DESTRUCTIVE | `garmin_client.client.delete('connectapi', url, json={'logIds': [log_id]}, api=True); getattr(e.error.response, 'text', '')` |
| nutrition | `upsert_and_log` | WRITE | `garmin_client.client.put('connectapi', '/nutrition-service/customFood', json=create_payload, api=True); garmin_client.client.put('connectapi', '/nutrition-service/food/logs', json=log_payload, api=True); garmin_client.connectapi('/nutrition-service/customFood', params={'searchExpression': food_name, 'start': 0, 'limit': 10, 'includeContent': 'true'}); garmin_client.connectapi(f'/nutrition-service/meals/{meal_date}'); getattr(e.error.response, 'text', '')` |
| training | `get_progress_summary_between_dates` | READ | `garmin_client.get_progress_summary_between_dates(start_date, end_date, metric)` |
| training | `get_hill_score` | READ | `garmin_client.get_hill_score(start_date, end_date)` |
| training | `get_endurance_score` | READ | `garmin_client.get_activity_types(); garmin_client.get_endurance_score(start_date, end_date)` |
| training | `get_training_effect` | READ | `connectapi(url, params=params); garmin_client.get_activity(activity_id); get_activities(0, limit); getattr(client, 'connectapi', None); getattr(client, 'garmin_connect_activities', '/activitylist-service/activities/search/activities'); getattr(client, 'get_activities', None)` |
| training | `get_hrv_data` | READ | `garmin_client.get_hrv_data(date)` |
| training | `get_fitnessage_data` | READ | `garmin_client.get_fitnessage_data(date)` |
| training | `get_training_status` | READ | `garmin_client.get_training_status(date)` |
| training | `get_cycling_ftp` | READ | `garmin_client.get_cycling_ftp()` |
| training | `get_lactate_threshold` | READ | `garmin_client.get_lactate_threshold(latest=False, start_date=start_date, end_date=end_date); garmin_client.get_lactate_threshold(latest=True)` |
| training | `request_reload` | WRITE | `garmin_client.request_reload(date)` |
| training | `get_training_load_trend` | READ | `garmin_client.get_training_status(date_str)` |
| training | `get_training_load_balance` | READ | `garmin_client.get_training_status(date)` |
| training | `get_hrv_trend` | READ | `garmin_client.get_hrv_data(date_str)` |
| training | `get_vo2max_trend` | READ | `connectapi(f'{metrics_url}/{start_date}/{end_date}'); garmin_client.get_user_profile(); getattr(client, 'connectapi', None); getattr(client, 'garmin_connect_metrics_url', None); getattr(garmin_client, method_name, None)` |
| training | `get_respiration_trend` | READ | `garmin_client.get_respiration_data(date_str)` |
| training | `get_running_tolerance` | READ | `garmin_client.get_running_tolerance(date, date, aggregation='daily')` |
| training | `get_running_tolerance_trend` | READ | `garmin_client.get_running_tolerance(start_date, end_date, aggregation=aggregation)` |
| training | `get_acclimation` | READ | `garmin_client.get_max_metrics(date)` |
| user_profile | `get_full_name` | READ | `garmin_client.get_full_name()` |
| user_profile | `get_unit_system` | READ | `garmin_client.get_unit_system()` |
| user_profile | `get_user_profile` | READ | `garmin_client.get_user_profile()` |
| user_profile | `get_userprofile_settings` | READ | `garmin_client.get_userprofile_settings()` |
| user_profile | `get_heart_rate_zones` | READ | `garmin_client.connectapi(_HEART_RATE_ZONES_URL)` |
| user_profile | `set_heart_rate_zones` | WRITE | `garmin_client.client.request('PUT', 'connectapi', _HEART_RATE_ZONES_URL, json=[current], api=True); garmin_client.connectapi(_HEART_RATE_ZONES_URL)` |
| weight_management | `get_weigh_ins` | READ | `garmin_client.get_weigh_ins(start_date, end_date)` |
| weight_management | `get_daily_weigh_ins` | READ | `garmin_client.get_daily_weigh_ins(date)` |
| weight_management | `delete_weigh_ins` | DESTRUCTIVE | `garmin_client.delete_weigh_ins(date, delete_all=delete_all)` |
| weight_management | `add_weigh_in` | WRITE | `garmin_client.add_weigh_in(weight=weight, unitKey=unit_key)` |
| weight_management | `add_weigh_in_with_timestamps` | WRITE | `garmin_client.add_weigh_in_with_timestamps(weight=weight, unitKey=unit_key, dateTimestamp=date_timestamp, gmtTimestamp=gmt_timestamp)` |
| womens_health | `get_pregnancy_summary` | READ | `garmin_client.get_pregnancy_summary()` |
| womens_health | `get_menstrual_data_for_date` | READ | `garmin_client.get_menstrual_data_for_date(date)` |
| womens_health | `get_menstrual_calendar_data` | READ | `garmin_client.get_menstrual_calendar_data(cursor.isoformat(), window_end.isoformat())` |
| workout_builders | `create_walk_run_workout` | WRITE | `garmin_client.upload_workout(workout_json)` |
| workout_builders | `create_run_workout` | WRITE | `garmin_client.upload_workout(workout_json)` |
| workout_builders | `create_run_interval_workout` | WRITE | `garmin_client.upload_workout(workout_json)` |
| workout_builders | `create_z2_walk_workout` | WRITE | `garmin_client.upload_workout(workout_json)` |
| workout_builders | `create_strength_workout` | WRITE | `garmin_client.upload_workout(workout_json)` |
| workout_builders | `schedule_week` | WRITE | `garmin_client.client.post('connectapi', url, json={'date': calendar_date})` |
| workouts | `get_workouts` | READ | `garmin_client.get_workouts()` |
| workouts | `get_workout_by_id` | READ | `garmin_client.connectapi(url); garmin_client.get_workout_by_id(int(workout_id_str))` |
| workouts | `download_workout` | READ | `garmin_client.download_workout(workout_id)` |
| workouts | `upload_workout` | WRITE | `garmin_client.upload_workout(workout_data)` |
| workouts | `upload_workouts` | WRITE | `garmin_client.upload_workout(workout_data)` |
| workouts | `delete_workout` | DESTRUCTIVE | `garmin_client.delete_workout(workout_id)` |
| workouts | `delete_workouts` | DESTRUCTIVE | `garmin_client.delete_workout(workout_id)` |
| workouts | `get_scheduled_workouts` | READ | `garmin_client.query_garmin_graphql(query)` |
| workouts | `get_garmin_coach_workouts` | READ | `garmin_client.query_garmin_graphql(query)` |
| workouts | `get_training_plan_workouts` | READ | `garmin_client.query_garmin_graphql(query)` |
| workouts | `schedule_workout` | WRITE | `garmin_client.client.post('connectapi', url, json={'date': calendar_date}); garmin_client.query_garmin_graphql(query)` |
| workouts | `schedule_workouts` | WRITE | `garmin_client.client.post('connectapi', url, json={'date': calendar_date}); garmin_client.query_garmin_graphql(query); garmin_client.upload_workout(workout_data)` |
| workouts | `unschedule_workout` | DESTRUCTIVE | `garmin_client.unschedule_workout(scheduled_workout_id)` |
| workouts | `unschedule_workouts` | DESTRUCTIVE | `garmin_client.unschedule_workout(scheduled_workout_id)` |

## Reviewed SDK read methods

The following exact method bodies were inspected. Calls to `self.connectapi` terminate in native GET; nested named read methods below are traced likewise. `_require_display_name` checks loaded identity. Cached-profile getters have no network side effect. Generic endpoint methods are intentionally not callable through the restricted production client.

| Method | Underlying implementation |
|---|---|
| `count_activities` | `self.connectapi(url)` |
| `download_activity` | `self.download(url)` |
| `download_health_snapshot` | `self.download(url)` |
| `download_workout` | `self.download(url)` |
| `get_activities` | `self.connectapi(url, params=params)` |
| `get_activities_by_date` | `self.connectapi(url, params=params)` |
| `get_activities_fordate` | `self.connectapi(url)` |
| `get_activity` | `self.connectapi(url)` |
| `get_activity_details` | `self.connectapi(url, params=params)` |
| `get_activity_exercise_sets` | `self.connectapi(url)` |
| `get_activity_gear` | `self.connectapi(url, params=params)` |
| `get_activity_hr_in_timezones` | `self.connectapi(url)` |
| `get_activity_power_in_timezones` | `self.connectapi(url)` |
| `get_activity_split_summaries` | `self.connectapi(url)` |
| `get_activity_splits` | `self.connectapi(url)` |
| `get_activity_typed_splits` | `self.connectapi(url)` |
| `get_activity_types` | `self.connectapi(url)` |
| `get_activity_weather` | `self.connectapi(url)` |
| `get_adaptive_training_plan_by_id` | `self.connectapi(url)` |
| `get_adhoc_challenges` | `self.connectapi(url, params=params)` |
| `get_all_day_events` | `self.connectapi(url, params={'calendarDate': cdate})` |
| `get_all_day_stress` | `self.connectapi(url)` |
| `get_available_badge_challenges` | `self.connectapi(url, params=params)` |
| `get_available_badges` | `self.connectapi(url, params={'showExclusiveBadge': 'true'})` |
| `get_badge_challenges` | `self.connectapi(url, params=params)` |
| `get_blood_pressure` | `self.connectapi(url, params=params)` |
| `get_body_battery` | `self.connectapi(url, params=params)` |
| `get_body_battery_events` | `self.connectapi(url)` |
| `get_body_composition` | `self.connectapi(url, params=params)` |
| `get_calories_daily` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_cycling_ftp` | `self.connectapi(url)` |
| `get_daily_steps` | `self.connectapi(url)` |
| `get_daily_training_status` | `self.connectapi(url)` |
| `get_daily_weigh_ins` | `self.connectapi(url, params=params)` |
| `get_device_alarms` | `self.get_device_settings(device['deviceId']); self.get_devices()` |
| `get_device_last_used` | `self.connectapi(url)` |
| `get_device_settings` | `self.connectapi(url)` |
| `get_device_solar_data` | `self.connectapi(url, params=params)` |
| `get_devices` | `self.connectapi(url)` |
| `get_earned_badges` | `self.connectapi(url)` |
| `get_endurance_score` | `self.connectapi(url, params=params)` |
| `get_fitnessage_data` | `self.connectapi(url)` |
| `get_floors` | `self.connectapi(url)` |
| `get_full_name` | `Returns cached profile attribute; no network or file write.` |
| `get_functional_threshold_power_range` | `self.connectapi(url, params=params)` |
| `get_gear` | `self.connectapi(url, params={'userProfilePk': userProfileNumber})` |
| `get_gear_activities` | `self.connectapi(url, params={'start': 0, 'limit': limit})` |
| `get_gear_defaults` | `self.connectapi(url)` |
| `get_gear_stats` | `self.connectapi(url)` |
| `get_goals` | `self.connectapi(url, params=params, headers=headers)` |
| `get_golf_club_stats` | `self.connectapi(url, params=params)` |
| `get_golf_scorecard` | `self.connectapi(url, params=params)` |
| `get_golf_shot_data` | `self.connectapi(url, params=params)` |
| `get_golf_summary` | `self.connectapi(url, params=params)` |
| `get_golf_user_stats` | `self.connectapi(url)` |
| `get_heart_rate_zones` | `self.connectapi(self.garmin_connect_heart_rate_zones_url)` |
| `get_heart_rates` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_hill_score` | `self.connectapi(url, params=params)` |
| `get_hrv_data` | `self.connectapi(url)` |
| `get_hrv_data_range` | `self.connectapi(url)` |
| `get_hydration_data` | `self.connectapi(url)` |
| `get_in_progress_badges` | `self.get_available_badges(); self.get_earned_badges()` |
| `get_inprogress_virtual_challenges` | `self.connectapi(url, params=params)` |
| `get_intensity_minutes_data` | `self.connectapi(url)` |
| `get_lactate_threshold` | `self.connectapi(heart_rate_url, params=params); self.connectapi(power_url, params={'sport': 'Running'}); self.connectapi(speed_and_heart_rate_url); self.connectapi(speed_url, params=params); self.get_functional_threshold_power_range(start_date, end_date, sport='RUNNING', aggregation=aggregation)` |
| `get_last_activity` | `self.get_activities(0, 1)` |
| `get_lifestyle_logging_data` | `self.connectapi(url)` |
| `get_max_metrics` | `self.connectapi(url)` |
| `get_max_metrics_range` | `self.connectapi(url)` |
| `get_menstrual_calendar_data` | `self.connectapi(url)` |
| `get_menstrual_cycle_summary` | `self.connectapi(url)` |
| `get_menstrual_data_for_date` | `self.connectapi(url)` |
| `get_menstrual_last_confirmed` | `self.connectapi(url)` |
| `get_menstrual_reports` | `self.connectapi(url, params=params)` |
| `get_morning_training_readiness` | `self.get_training_readiness(cdate)` |
| `get_next_scheduled_workout` | `self.get_scheduled_workouts(next_month_year, next_month); self.get_scheduled_workouts(today.year, today.month)` |
| `get_non_completed_badge_challenges` | `self.connectapi(url, params=params)` |
| `get_nutrition_daily_food_log` | `self.connectapi(url)` |
| `get_nutrition_daily_meals` | `self.connectapi(url)` |
| `get_nutrition_daily_settings` | `self.connectapi(url)` |
| `get_personal_record` | `self._require_display_name(); self.connectapi(url)` |
| `get_power_zones` | `self.connectapi(url)` |
| `get_power_zones_for_sport` | `self.connectapi(url)` |
| `get_pregnancy_summary` | `self.connectapi(url)` |
| `get_primary_training_device` | `self.connectapi(url)` |
| `get_progress_summary_between_dates` | `self.connectapi(url, params=params)` |
| `get_race_predictions` | `self._require_display_name(); self.connectapi(url); self.connectapi(url, params=params)` |
| `get_respiration_data` | `self.connectapi(url)` |
| `get_rhr_daily` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_rhr_day` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_running_tolerance` | `self.connectapi(url, params=params)` |
| `get_scheduled_workout_by_id` | `self.connectapi(url)` |
| `get_scheduled_workouts` | `self.connectapi(url)` |
| `get_sleep_daily` | `self.connectapi(url)` |
| `get_sleep_data` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_spo2_data` | `self.connectapi(url)` |
| `get_stats` | `self.get_user_summary(cdate)` |
| `get_stats_and_body` | `self.get_body_composition(cdate); self.get_stats(cdate)` |
| `get_steps_data` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_stress_data` | `self.connectapi(url)` |
| `get_training_four_week_load_balance` | `self.connectapi(url)` |
| `get_training_load_activities` | `self.connectapi(url, params=params)` |
| `get_training_plan_by_id` | `self.connectapi(url)` |
| `get_training_plans` | `self.connectapi(url)` |
| `get_training_readiness` | `self.connectapi(url)` |
| `get_training_status` | `self.connectapi(url)` |
| `get_unit_system` | `Returns cached profile attribute; no network or file write.` |
| `get_user_profile` | `self.connectapi(url)` |
| `get_user_summary` | `self._require_display_name(); self.connectapi(url, params=params)` |
| `get_userprofile_settings` | `self.connectapi(url)` |
| `get_weekly_intensity_minutes` | `self.connectapi(url)` |
| `get_weekly_steps` | `self.connectapi(url)` |
| `get_weekly_stress` | `self.connectapi(url)` |
| `get_weigh_ins` | `self.connectapi(url, params=params)` |
| `get_workout_by_id` | `self.connectapi(url)` |
| `get_workouts` | `self.connectapi(url, params=params)` |

## SDK mutation endpoints (denied)

| Method | Reviewed implementation |
|---|---|
| `add_body_composition` | `self.client.post('connectapi', url, files=files, api=True)` |
| `add_gear_to_activity` | `self.client.put('connectapi', url); self.client.put('connectapi', url).json()` |
| `add_hydration_data` | `self.client.put('connectapi', url, json=payload); self.client.put('connectapi', url, json=payload).json()` |
| `add_weigh_in` | `self.client.post('connectapi', url, json=payload)` |
| `add_weigh_in_with_timestamps` | `self.client.post('connectapi', url, json=payload)` |
| `create_manual_activity` | `self.create_manual_activity_from_json(payload)` |
| `create_manual_activity_from_json` | `self.client.post('connectapi', url, json=payload, api=True)` |
| `delete_weigh_ins` | `self.delete_weigh_in(w['samplePk'], cdate); self.get_daily_weigh_ins(cdate)` |
| `delete_weigh_in` | `self.client.request('DELETE', 'connectapi', url, api=True)` |
| `delete_workout` | `self.client.delete('connectapi', url, api=True)` |
| `remove_gear_from_activity` | `self.client.put('connectapi', url); self.client.put('connectapi', url).json()` |
| `request_reload` | `self.client.post('connectapi', url, api=True)` |
| `set_activity_name` | `self.client.put('connectapi', url, json=payload, api=True)` |
| `set_activity_type` | `self.client.put('connectapi', url, json=payload, api=True)` |
| `set_blood_pressure` | `self.client.post('connectapi', url, json=payload); self.client.post('connectapi', url, json=payload).json()` |
| `unschedule_workout` | `self.client.delete('connectapi', url, api=True)` |
| `upload_workout` | `self.client.post('connectapi', url, json=payload, api=True)` |

## Dependency evidence

- [Garmin 0.3.17 release](https://github.com/cyberjunky/python-garminconnect/releases/tag/0.3.17)
- [Garmin client source](https://github.com/cyberjunky/python-garminconnect/blob/0.3.17/garminconnect/client.py)
- [Garmin API source](https://github.com/cyberjunky/python-garminconnect/blob/0.3.17/garminconnect/__init__.py)

Full implementation evidence and source fingerprints live in `src/garmin_mcp/read_only_manifest.json`. This audit establishes read semantics; it does not claim metrics are present on a particular account or that real Garmin authentication has been tested.

## Wellness production toolset

Additional 22 handlers are READ. Each delegates to `WellnessService` with a fixed source-method allowlist: `get_stats`, `get_sleep_data`, `get_hrv_data`, `get_body_battery`, `get_stress_data`, `get_training_readiness`, `get_training_status`, `get_max_metrics`, `get_spo2_data`, `get_respiration_data`, `get_user_profile`, `get_activities`, `get_activity`. These SDK methods are included in the reviewed GET table above. No tool accepts a method name or arbitrary URL. The bounded-call decorator installs/resets a context-local call budget and emits only static tool name, generated request ID, duration, status and exception type through the safe structured logger; its exact wrapper qualified name is separately authorized and source-fingerprinted. Unreviewed wrappers are rejected, even if `functools.wraps` copies a reviewed name.

`daily()` may persist curated scalars using the internally configured store. This is an operational normalized cache, not an exposed arbitrary write interface or a Garmin mutation. No tool can invoke sync, migration, authentication, deletion, token dump, arbitrary SQL or file writes. Production `TokenClient` adds its own narrower read-method boundary.

| Tool | Class | Implementation evidence |
|---|---|---|
| `get_profile` | READ | `Returns configured opaque profile ID and literal display name; no network or writes.` |
| `get_capabilities` | READ | `service.daily(today); service.daily(yesterday); service.today()` |
| `get_wellness_today` | READ | `service.call('get_activities', 0, 1); service.daily(day); service.today()` |
| `get_daily_health` | READ | `service.daily(date)` |
| `get_health_range` | READ | `service.daily(day)` |
| `get_sleep` | READ | `section(date, 'sleep') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_sleep_analysis` | READ | `service.series(metric, start_date, end_date)` |
| `get_naps` | READ | `section(date, 'naps') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_recovery_context` | READ | `service.series(metric, start, today); service.today()` |
| `get_hrv` | READ | `section(date, 'hrv') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_body_battery` | READ | `section(date, 'body_battery') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_stress` | READ | `section(date, 'stress') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_training_overview` | READ | `section(date, 'training') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_training_readiness` | READ | `section(date, 'training') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_training_status` | READ | `section(date, 'training') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_vo2max` | READ | `section(date, 'training') -> service.daily(day, groups=(group,)) -> fixed reviewed SDK methods` |
| `get_activities` | READ | `service.call('get_activities', offset, limit)` |
| `get_activity` | READ | `service.call('get_activity', activity_id)` |
| `get_metric_timeseries` | READ | `service.series(metric, start_date, end_date)` |
| `get_metric_trend` | READ | `service.series(metric, start_date, end_date)` |
| `compare_periods` | READ | `service.series(metric, period_a_start, period_a_end); service.series(metric, period_b_start, period_b_end)` |
| `find_correlations` | READ | `service.series(metric_x, start_date, end_date); service.series(metric_y, start_date, end_date)` |
