(* Control of PLC-driven circuits: TEST INPUT - readiness trace pump panel. *)
(* Generated from the design; every output drops when EStop_OK is FALSE. *)
PROGRAM TEST_INPUT_readiness_trace_pump_panel_Control
VAR_INPUT
    EStop_OK : BOOL; (* Emergency stop chain healthy (normally closed: TRUE while healthy) *)
    Auto_Mode : BOOL; (* Hand/auto selector in auto *)
    Schedule_On : BOOL; (* Time clock or BMS enable *)
    Start_PP1_Q5 : BOOL; (* =PP1-Q5: start (NO) *)
    Stop_PP1_Q5 : BOOL; (* =PP1-Q5: stop (NC) *)
    OL_PP1_Q5 : BOOL; (* =PP1-F2: overload healthy (NC) *)
    Start_PP1_Q7 : BOOL; (* =PP1-Q7: start (NO) *)
    Stop_PP1_Q7 : BOOL; (* =PP1-Q7: stop (NC) *)
    OL_PP1_Q7 : BOOL; (* =PP1-F3: overload healthy (NC) *)
    Start_PP1_T1 : BOOL; (* =PP1-T1: start (NO) *)
    Stop_PP1_T1 : BOOL; (* =PP1-T1: stop (NC) *)
    Ready_PP1_T1 : BOOL; (* =PP1-T1: drive ready, no fault *)
END_VAR
VAR_OUTPUT
    K_PP1_Q5 : BOOL; (* =PP1-Q5: contactor coil *)
    KY_PP1_Q7 : BOOL; (* =PP1-Q9: star contactor coil *)
    KD_PP1_Q7 : BOOL; (* =PP1-Q8: delta contactor coil *)
    KM_PP1_Q7 : BOOL; (* =PP1-Q7: line contactor coil *)
    Run_PP1_T1 : BOOL; (* =PP1-T1: drive run *)
END_VAR
VAR
    Run_PP1_Q7 : BOOL; (* motor commanded to run *)
    TStar_PP1_Q7 : TON; (* star period *)
    TGap_PP1_Q7 : TON; (* star-to-delta gap *)
END_VAR

(* =PP1-Q5: Duty pump (test) *)
K_PP1_Q5 := EStop_OK AND Stop_PP1_Q5 AND OL_PP1_Q5 AND ((NOT Auto_Mode AND (Start_PP1_Q5 OR K_PP1_Q5)) OR (Auto_Mode AND Schedule_On));

(* =PP1-Q7: Standby pump (test) *)
Run_PP1_Q7 := EStop_OK AND Stop_PP1_Q7 AND OL_PP1_Q7 AND ((NOT Auto_Mode AND (Start_PP1_Q7 OR Run_PP1_Q7)) OR (Auto_Mode AND Schedule_On));
TStar_PP1_Q7(IN := Run_PP1_Q7, PT := T#6S);
TGap_PP1_Q7(IN := TStar_PP1_Q7.Q, PT := T#100MS);
KM_PP1_Q7 := Run_PP1_Q7;
KY_PP1_Q7 := Run_PP1_Q7 AND NOT TStar_PP1_Q7.Q AND NOT KD_PP1_Q7;
KD_PP1_Q7 := Run_PP1_Q7 AND TGap_PP1_Q7.Q AND NOT KY_PP1_Q7;

(* =PP1-T1: Booster (test) *)
Run_PP1_T1 := EStop_OK AND Stop_PP1_T1 AND Ready_PP1_T1 AND ((NOT Auto_Mode AND (Start_PP1_T1 OR Run_PP1_T1)) OR (Auto_Mode AND Schedule_On));

END_PROGRAM
