/*SELECT
  runs.number    run,
  event_count_table.int_value   event_count,
  C2.float_value events_rate,
  C3.int_value   temperature
FROM runs
  LEFT JOIN conditions event_count_table
    ON event_count_table.run_number = runs.number AND event_count_table.condition_type_id = 1
  LEFT JOIN conditions C2
    ON C2.run_number = runs.number AND C2.condition_type_id = 2
  LEFT JOIN conditions C3
    ON C3.run_number = runs.number AND C3.condition_type_id = 3;
*/


  SELECT
    runs.number                          run,
    event_rate_table.float_value         event_rate,
    event_count_table.int_value          event_count,
    run_type_table.text_value            run_type,
    run_config_table.text_value          run_config,
    polarization_angle_table.float_value polarization_angle
  FROM runs
    LEFT JOIN conditions event_rate_table
      ON event_rate_table.run_number = runs.number AND event_rate_table.condition_type_id = 1
    LEFT JOIN conditions event_count_table
      ON event_count_table.run_number = runs.number AND event_count_table.condition_type_id = 2
    LEFT JOIN conditions run_type_table
      ON run_type_table.run_number = runs.number AND run_type_table.condition_type_id = 3
    LEFT JOIN conditions run_config_table
      ON run_config_table.run_number = runs.number AND run_config_table.condition_type_id = 4
    LEFT JOIN conditions polarization_angle_table
      ON polarization_angle_table.run_number = runs.number AND polarization_angle_table.condition_type_id = 56
  WHERE runs.number > 10000 AND event_count_table.int_value > 1000000
;
