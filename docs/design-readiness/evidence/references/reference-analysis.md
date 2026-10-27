# Reference execution drawing sets: what they contain and what they need

Scope: five customer reference PDFs, analysed as examples of what an execution or fabrication team receives. The files were analysed where they sit in the user's Downloads folder and were not copied into the repository. Personal data (names, phones, signatures) is left out. References use these short ids:

| Id | Panel | Pages | Sheet | Text extractable |
|---|---|---|---|---|
| POCKET-HALL | Distribution board with KNX lighting actuators | 17 | A4 landscape (841x595 pt) | Yes (vector, PDFlib 9.2 from an ECAD export) |
| SATAFI-PUMP | Pump panel with soft starter, plus lighting and power distribution | 10 | A4 landscape | Yes |
| SHANAA-POOL | Pool panel: filter, circulation and jacuzzi pumps, 12 V pool lights, KNX | 13 | A4 landscape | Yes |
| KHAMIS-MDB | Main distribution board with grid/generator ATS | 29 physical pages, 18 unique | A3 (1190x841) for schematics, A4 for layouts | Yes |
| ZALMOUT-FIRE | Fire-pump panel: jockey, duty and standby pumps | 11 | A4 landscape | Yes |

How they were inspected: all pages were text-extracted with pdfplumber (inside the API container), and page identity was checked with per-page text hashes. Visual rendering covered a subset of 19 pages (POCKET p4, p5, p14; SATAFI p4, p5, p8; SHANAA p6, p10, p11; KHAMIS p7, p11, p12, p13; ZALMOUT p4 to p9). I studied five of those images closely (POCKET p5, SATAFI p8, KHAMIS p11, ZALMOUT p6 and p8). The remaining pages were judged from their extracted text only. Where a finding depends on drawing geometry I did not view, it is marked "(text only)".

All five sets share one house template from the same panel builder (one ECAD title block, the same "Safety regulations" page, and a "Summarized parts list" generated from ECAD).

---

## Common template (all five references)

- **Cover page (p1):** a customer approval stamp block with codes A, B, C and D (A = approved without comments, B = approved with comments, C = revise and resubmit, D = rejected). Then these fields: Project name, Owner name, Consultant name, Contractor name, Panel name, Panel quantity, Title of page, Releasing date, Job number, Revision number, Quotation number, Sales/Manufacture number, Drawing number, Current page number, Total number of pages. "Design parameters: Design input / Design output / Specification code / Encl. Mech. Code" are labels with **no values** in every set.
- **Title block on every page:** revision rows (Sr/No., Drawing description such as Rev0.1, Issue date, Design by, Checked by), Project name, Panel name, Job number, Drawing number, Contractor name, Title of page, Sales-Date-Planning number, Drawing approved by, Current/total pages, and the builder logo. Signature fields are name text only; there is no signature or stamp image in the title block.
- **Sheet grid:** columns 0 to 9 and rows A to F. Cross-references use `page.column:row`, for example `8.5:D`, and device tags follow IEC 81346 style (`-K1`, `-Q`, `+Pump` for location, `=DB...` for function).
- **Design basis:** none of the sets cites a standard (IEC 61439, IEC 60364, NFPA 20 and so on), an edition, an earthing system, a prospective fault level, ambient temperature or an IP/IK rating. The p2 safety page is generic boilerplate. Voltage (400 V 3P+N, 230 V control) and frequency (50 Hz) are implied only by device ratings. The only IP rating found is a device's own (thermostat IP20). Enclosure material thickness is given on the external view (t = 1.25, 1.5 or 1.8, labelled "mm²", which is a unit error).
- **Calculations:** none in any set. There is no short-circuit, voltage-drop, cable-sizing, busbar, temperature-rise (IEC 61439-1 cl. 10.10) or discrimination study. The parts list carries ECAD weights only.
- **Parts list:** "Summarized parts list" pages give Quantity, Type number, Manufacturer, Order number, Designation, Supplier and weight. Most items are orderable (manufacturer plus order number). Exceptions are noted per reference.
- **Not present in any set:** a wiring/connection list (source to target), a cable list, a terminal plan page, a device-tag list, a sequence-of-operation narrative, a test/commissioning sheet, or a label/nameplate schedule. Terminal strips appear only as `-X` symbols on the schematics.

---

## POCKET-HALL (17 pages)

### Page index
| p | Content |
|---|---|
| 1 | Cover / approval page; revision table Rev0.1 to Rev0.3 |
| 2 | Safety regulations (boilerplate) |
| 3 | Table of contents |
| 4 | External views (front, side, top, bottom): 1650 W (700 + 650 + 300 cable compartment) x 1350 H, depth 300; t = 1.5; danger labels |
| 5 | Dead front, dimensioned: row heights 50/200/175/175/175/350/50 mm on the left section and 50/350/200/175/175/175/175 mm on the middle section; earth bar E 40x5 |
| 6 | Component view (mounting plate; ETEK breakers and ABB KNX actuators placed) |
| 7 | Main power: fed from SMDB 5x16 mm² Cu; QMain 3P 63 A MCB; FRST box fuse 10x38 6 A (lamps); QP1/QP2 3P 32 A to RCCB-P1/P2 40 A 30 mA; outgoing 1P 16 A to hand dryer, Wi-Fi, power and spare; cable 3x2.5 |
| 8 | Distribution: QP3/QF/QL; RCCB 30 mA and 300 mA; FCU, air curtain, exhaust fan, lighting; modular contactors K1 to K8 (2P/4P 25 A, 230 V coil) |
| 9 | Distribution: lighting MCBs 10 A, emergency, spare; contactors K9 to K13 |
| 10 to 12 | KNX1: ABB SAH/S24.10.7.1 24-fold switch/shutter actuator; 8 channels per page (A to H, three pages = 24); outputs to terminals XL17 to XL40; +KNX/-KNX bus terminal -XKNX with 2x1.5 mm² Cu XLPE cable (p12) |
| 13 to 15 | KNX2: second SAH/S24.10.7.1; channels drive lighting XL41 to XL51 (p13, p14) and contactor coils K1 to K13 (p14, p15) |
| 16 and 17 | Summarized parts list (21 items, total 19.07 kg) |

### Findings
- **Components:** orderable. ETEK EKM12/EKM6 MCBs, EKL6 RCCBs and EKMF modular contactors use the type as the order number. ABB 2CDG110249R0011. Phoenix Contact (PXC) UT/UTTB terminals (130x UTTB 4), fuse terminals and connection pins. ONKA terminals. Signal lamps carry only a generic internal code ("99900001808, China").
- **Power schematic:** complete per circuit (rating, poles, curve, RCD sensitivity, outgoing cable, load name). Breaking capacity is not printed for the ETEK devices.
- **Control:** none beyond KNX actuator outputs switching contactor coils. No hand/off/auto, interlocks or timers.
- **Terminals:** terminal numbers are shown on actuator channels (1 to 16) and outgoing terminals (XL n, L/N/E). There is no wire numbering. Connections are graphic only and there is no connection list.
- **Cross-references:** yes. Examples are `L/9.0:A` and `L.CON1 14.4:B`, plus actuator channel references `/11.1:C` and `/12.1:C`.
- **Enclosure:** dimensioned and meaningful (overall and per-row dead-front heights). Internal DIN rail and duct sizes are not dimensioned on p6 (text only).
- **KNX, what is shown:** two 24-fold actuators (DIN, MDRC), the channel-to-load mapping and the bus entry terminal. **Not shown:** KNX power supply or choke (none in the parts list), line/area topology, physical addresses, group addresses, ETS project, sensors or push-buttons, IP interface or router, bus cable type. Treat this as "KNX actuator hardware and wiring only".

---

## SATAFI-PUMP (10 pages)

### Page index
| p | Content |
|---|---|
| 1 to 3 | Cover (Revision 02), safety, table of contents |
| 4 | External views: 800 W x 1300 H x 500 D (text only), weatherproof with canopy, t = 1.5 |
| 5 | Dead front: door devices (fault/start/run/stop for the pump, NVR, fan, time switch); cable trunk 40x60 mm labelled; earth bar 40x5 |
| 6 | Component view (same layout as p5 with devices) |
| 7 | Main power and distribution: incoming 5x25 mm² N2XY from MDB; main MCCB ABB XT2S 160 Ekip LS/I with SOR shunt trip, fed via external trip and FKR2N phase failure/sequence relay; FH204 RCCBs 40 A 30 mA; lighting contactors K1 to K8 (AX09) under SUL181H time switch with 1-0-2 Auto/Man selector; ventilation fan PD120S plus NTL10B thermostat; outgoing 10 A/16 A circuits |
| 8 | Pump power and control: 25 HP 3-phase motor, cable 5x16 mm²; SH203 C50 MCB, then line contactor K-P AF38, then ABB PSE45-600-70 soft starter; control via Ctrl.P 6 A, EMG stop B200E, NVR contact, Start/Stop pushbuttons B102K20KY wired to soft-starter terminals 8 and 9; soft-starter RUN relay switches the K-P coil; EVENT relay drives the "S.S Fault" lamp; K-P 13-14 drives "Pump Run" |
| 9 and 10 | Summarized parts list (25 items, 12.77 kg) |

### Findings
- **Components:** all ABB devices carry order numbers. EMAS, KAEL, SALZER and Natural items are listed by type only. One lamp line gives the manufacturer as "China".
- **Wire numbering:** yes, on p8. Sequential wire numbers (1 to 17) with colour codes (RD, OG, VT, BU) and cross-section 1.5 mm². Device terminals are shown (1L1/2T1, A1/A2, 13/14, soft-starter terminals 1 to 16, X1/X2 on lamps). This is the only set with wire numbers.
- **Operating modes:** pump start/stop by pushbutton plus E-stop. There is no hand/off/auto and no level or pressure input for the pump. Lighting uses Auto (time switch) or Manual.
- **Faults and alarms:** soft-starter fault lamp; phase failure trips the main breaker via SOR.
- **Cross-references:** yes, for example `MBB-L1/8.0:A`, `/8.5:D` and contact images under each coil.
- **Questionable:** the motor branch is a C-curve MCB (50 A) ahead of a soft starter, and no Type 1/2 coordination is stated. The soft starter provides the overload protection, but the coordination is not documented. "K-P" is labelled AF38 on p8 while the parts list also lists 7x AX09 (lighting). This is consistent, but the K-P function (line contactor, not bypass) has to be inferred.

---

## SHANAA-POOL (13 pages)

### Page index
| p (physical) | Footer label | Content |
|---|---|---|
| 1 | 1of13 | Cover (Revision 03) |
| 2 | 2of13 | Table of contents (lists pages 1 to 15 with numbers 6 and 12 missing; no safety page) |
| 3 | 3of15 | External views: 650 W x 1000 H x 250 D (text only), weatherproof with canopy, t = 1.25 |
| 4 | 4of15 | Dead front: pilot lamps (Filter/Circulation/Jacuzzi run and fault, pool light), Auto/0/Manual selector, socket, FKR2N, overload reset |
| 5 | 5of15 | Component view |
| 6 | 7of13 | Main power: from MDB; QM SH203 C32; RCCB-M FH204 40 A 30 mA as **incomer RCD for the whole board**; FKR2N phase failure/sequence (NVR); fan plus thermostat; phase lamps R/Y/G |
| 7 | 8of13 | Pumps: Q-Circulation S203M C10 (with aux contact); contactors K-Filter, K-Circulation, K-Jacuzzi (AX09); motors 2.3 A, 1.3 A and 2.3 A, 3-phase |
| 8 | 9of13 | Pool lights: QP/L C20, K-P.Light, RCCB-P/L FH202 30 mA, then 250 VA transformer 220 V to 12 V, then 6 lamps of 12 V/35 W |
| 9 | 10of13 | Light, power and socket circuits 10 A/16 A |
| 10 | 11of13 | KNX-Lights: ABB SA/S4.16.2.1 4-fold 16 A switch actuator; channels drive the coils of K-Circulation, K-Jacuzzi and K-P.Light through MCB aux contacts (13-14) and O.L contacts (95-96); KNX bus +/- to an external terminal |
| 11 | 12of13 (text only) | Control: Filter run in Auto from the T24H time switch or Manual from the S-Filter selector (B101S30); run/fault lamps from contactor aux and O.L 97-98; MCB aux 21-22 status |
| 12 and 13 | | Summarized parts list (27 items, 4.88 kg) |

### Findings
- **Components:** mostly orderable (ABB). Parts-list item 7 (2CDG110160R0011, the KNX actuator) has a quantity but **no description**. The pool-light transformer is "Locally" sourced with no standard or safety-isolating designation.
- **Interlocks:** contactor coils are interlocked through the overload NC (95-96) and MCB aux contacts. Auto/Manual is used for the filter only.
- **Terminals and cross-references:** device terminals and aux contacts are shown, with cross-references `/8.3:B` and `/13.3:F`. There is no wire numbering.
- **KNX:** one 4-fold actuator and a bus terminal. No addressing, power supply or topology.
- **Questionable:**
  - The overload relays are 3x TA25DU-**6.5M**. If the ABB naming means a 4.5 to 6.5 A setting range (verify against the datasheet), that cannot protect motors rated 1.3 to 2.3 A.
  - The labels under the jacuzzi pump read "Spare".
  - The pool-light transformer is not declared as a safety isolating transformer. IEC 60364-7-702 requires SELV with the source outside zones 0 to 2 for underwater luminaires (general knowledge, not shown in the PDF).
  - A single 30 mA RCD is the incomer for all circuits, which carries a nuisance-tripping and discrimination risk.

---

## KHAMIS-MDB (29 physical pages; 18 unique)

Page-hash check: **p4 to p6 are exact duplicates of p1 to p3**, and **p19 to p29 are exact duplicates of p8 to p18**.

### Page index (unique pages)
| p | Footer label | Content |
|---|---|---|
| 1 to 3 | 1of15 to 3of15 | Cover (Revision 01; revision table shows Rev0.1 and Rev0.2), safety, table of contents (16 entries) |
| 7 | 4of16 | External views: 2400 W (sections of 600 mm) x 2000 H x 700 D (text only); weatherproof without canopy; door shows the DKG-173, the meter, Grid/Generator lamps and the selectors |
| 8 | 5of16 | Dead front: 2x Emax 2 ACBs, MCCB feeders labelled by load; dimensioned panel covers 50 to 500 mm |
| 9 | 6of16 | Component view: busbars 4P 80x10 mm² Cu "1600A", N 80x10, E 50x10, CTs, terminals |
| 10 | 7of15 | Main power: +Company (from kWh meter) and +Generator incomers, cable "4(3x XX+XX+XX mm²)"; QC and QG are ABB E1.2N 1600 Ekip Dip LSIG 4P withdrawable ACBs, Icu/Ics 66 kA; **"Mechanical Interlock"** between QC and QG; voltage-sensing MCBs 6 A per source; EEM-MA770 meter; CTs 1600/5 A |
| 11 | 8of15 | ATS control: Entes MKC-03 phase failure relay per source (C.NVR, G.NVR); Datakom DKG-173 ATS controller (R/S/T mains sensing, G generator sensing, MCB/GCB close outputs); relays R1 (GCB) and R2 (MCB) with cross-interlocking NC contacts; Com Live / Gen Live lamps |
| 12 | 9of15 | ATS control: EMG stop; ATS ON/OFF selector (B100S20); manual "Gen C.B Closing" / "Com C.B Closing" pushbuttons; Gen Stop-Start; relays R3, R4, R5 (gen start, GRS); ERX-10 multifunction timer T1 |
| 13 | 10of15 | Breaker control: QC/QG motor operators, YC closing coil, YO opening coil, aux (open/closed) and S51 (tripped) contacts; CB-closed and CB-tripped lamps; buzzer with mute |
| 14 and 15 | 11of15, 12of15 | Outgoing feeders: 7x XT3N/XT1C (250 A, 3x200 A, 2x160 A, 125 A) and 5x XT1C (3x63 A, 2x32 A), with load names and cable sizes (5x95 down to 5x10 mm²) |
| 16 to 18 | 13of15 to 15of15 | Summarized parts list (36 items, 58.31 kg) |

### ATS, what is shown
- Two supplies: utility via kWh meter, and generator.
- **Mechanical interlock:** an ABB cable interlock (parts-list item 27), drawn as "Mechanical Interlock" on p10.
- **Electrical interlock:** the R1 and R2 NC contacts are cross-wired (p11).
- Transfer logic: delegated to the DKG-173 controller (its internal timers are not documented), plus the ERX-10 timer and the gen-start relay.
- Manual close per breaker; ATS ON/OFF; emergency stop; status, trip and alarm indication.

**Not shown:** settings for transfer, retransfer and cool-down times; generator start/stop signal wiring to the genset terminals; neutral transfer philosophy (the 4P ACBs imply switched neutral, but this is not stated); earthing at the generator; load-shed or priority loads; DKG-173 parameter sheet.

### Findings
- **Components:** orderable for ABB and PXC.
- **Cross-references:** dense, for example `/8.6:D` and `9.0:A/G/C-L`, with contact tables under each relay.
- **Terminals:** device terminals and aux contacts are shown, with no wire numbering.
- **Questionable or inconsistent:**
  - The trip unit is **LSIG** in the schematic and **LI** in the parts list (item 23).
  - The parts list gives **XT3N 250 MA 200** (magnetic-only, motor type) ×3, while the schematic draws TMD 200 A feeders.
  - Parts-list item 4 has a Siemens-style type "3VA1032-2ED32-0AA0" against an ABB order number and manufacturer.
  - A **single 80x10 mm² Cu bar per phase for 1600 A** is not supported by any calculation and looks undersized for typical enclosed ratings.
  - Incomer cables are left as "XX".
  - The job number is the template value "IEC_bas001" (spelled "ICE_bas001" on p7).

---

## ZALMOUT-FIRE (11 pages)

### Page index
| p | Footer label | Content |
|---|---|---|
| 1 to 3 | 1of11 to 3of11 | Cover (Revision 01), safety, table of contents |
| 4 | 5of10 | External views: 650 W x 1200 H x 300 D (text only), weatherproof with canopy, t = 1.8; door: Emergency, NVR Health, Low Level, Jockey Run/Fault, A-0-M selectors and Duty/Standby Run |
| 5 | 6of10 | Component view (600 mm plate width shown) |
| 6 | 6of11 | Main power: source from MDB 3x50+35+35 XLPE; QMain XT2N 160 TMA 125 A; **jockey** 1.5 kW 3.8 A via MO132-4.0 magnetic-only starter + AF09 + **TF42 thermal overload**; **duty and standby** 18.5 kW 32.5 A each via **XT2N 160 MA 52 magnetic-only MCCB** + AF38 contactor, **direct on line, no overload relay**; CM-PBE phase failure monitoring relay (NVR); Q.Ctrl 6 A control MCB |
| 7 | 7of11 | Switch controls: EMG; dry-run protection (float switch X.FSL.L, R1 relay, ERV-30 delay TA); "Medium-low" float (X.FSML) with TX delay; valve output (X.V "VALVE RUN") |
| 8 | 8of11 | Per-pump A-0-M selectors (CM101S30); pressure switch per pump (X.PS1, X.PS2, X.PS3); ERV-30 run-delay and stop-delay timers T1 to T5; relays R2 and R3; jockey coil chain includes the NC of K.Duty and K.Standby (jockey stops when a main pump runs) |
| 9 | 9of11 | Indicators: Jockey run/fault, Duty run, Standby run, Low level plus buzzer |
| 10 and 11 | | Summarized parts list (24 items, 6.14 kg) |

### What makes it fire-pump-like (shown in the PDF)
- Jockey pump plus duty and standby main pumps.
- Pressure-switch start for each pump.
- A main-pump branch with **no thermal overload**, only a magnetic-only MCCB.
- Phase failure monitoring.
- Start and stop delay timers.
- Jockey lockout while a main pump runs.

### What a listed or certified fire-pump controller normally requires and this reference does not show
This section is general knowledge of NFPA 20 / UL 218 / FM 1321, not verified against a specific edition and not taken from the PDF:
- A listed/approved controller assembly. This PDF is a generic panel with no listing mark or rating label.
- An isolating switch plus a circuit breaker with locked-rotor overcurrent protection, sized to the motor's locked-rotor current.
- **No stop on low suction or dry run** for the main fire pump. This reference has dry-run protection that, from the p7 cross-references, appears to act on the pump control chain (text only; confirm visually). That would conflict with usual fire-pump practice.
- **An emergency stop is generally not permitted** to stop an automatically started fire pump. This reference has an EMG in the control supply.
- Automatic stop only after a minimum run time, or manual stop only. T3 and T5 "stop time delay" could be a minimum-run timer, but this is not stated.
- Alarms: phase reversal, power/phase loss, pump running, controller trouble, with dry contacts to the fire alarm panel. Only lamps and a buzzer are shown, and there are no remote dry-contact outputs.
- Mechanical emergency-run means, a pressure recorder, a weekly test timer, and an alternate power source or transfer.
- The control circuit is fed through a 6 A MCB, which could disable automatic start if it trips.

**Conclusion:** the reference is a "fire-pump-duty" motor control panel and not evidence of a compliant fire-pump controller. PanelPilot must not claim NFPA 20 compliance from it.

### Other findings
- **Components:** orderable for ABB and PXC. The parts list lists FS 4CO relay sockets with a Weidmüller order number but "ABB" as manufacturer.
- **Wire numbering:** none. Terminals (X.PS1, X.FSL.L) and contact cross-references are shown.

---

## CROSS-REFERENCE CAPABILITY CHECKLIST

Y = present, P = partial, N = absent. Page numbers are physical pages.

| Capability | POCKET-HALL | SATAFI-PUMP | SHANAA-POOL | KHAMIS-MDB | ZALMOUT-FIRE |
|---|---|---|---|---|---|
| Cover with approval-code block | Y p1 | Y p1 | Y p1 | Y p1 | Y p1 |
| Revision table in title block | Y (3 revs) all | Y (2) | Y (3) | Y (2) | Y (1) |
| Table of contents | Y p3 | Y p3 | P p2 (wrong numbers) | Y p3 (16 vs 15/18) | Y p3 |
| Design basis / standards / earthing / fault level | N | N | N | N | N |
| External views, dimensioned | Y p4 | Y p4 | Y p3 | Y p7 | Y p4 |
| Dead front, dimensioned | Y p5 | P p5 | P p4 | Y p8 | P p4 |
| Component/mounting-plate layout | Y p6 | Y p6 | Y p5 | Y p9 | Y p5 |
| DIN rail / duct sizes | N | P p5 (trunk 40x60) | N | N | N |
| Busbar sizes | N | P (earth 40x5) | N | Y p9 | N |
| Power single-line/schematic with ratings | Y p7-9 | Y p7-8 | Y p6-9 | Y p10, p14-15 | Y p6 |
| Outgoing cable size per circuit | Y | Y | P | Y (incomers "XX") | P (motor cables missing) |
| Breaking capacity per device | P (MCCB/ACB only) | Y | Y | Y | Y |
| Control schematic | P (KNX outputs) | Y p8 | Y p10-11 | Y p11-13 | Y p7-9 |
| Operating modes (A-0-M / Auto-Man) | N | P (lighting only) | P (filter only) | P (ATS on/off, manual close) | Y p8 |
| Interlocks | N | P | Y p10-11 | Y mech p10 + elec p11 | Y p8 |
| Timers | N | P (time switch) | P (time switch) | Y p12 | Y p7-8 |
| Fault/alarm indication | N | Y p8 | Y p11 | Y p13 | Y p9 |
| Device terminals / aux contacts shown | Y | Y | Y | Y | Y |
| Wire numbering | N | Y p8 | N | N | N |
| Wiring/connection list (source to target) | N | N | N | N | N |
| Terminal plan / strip list | N | N | N | N | N |
| Cable list | N | N | N | N | N |
| Cross-references page.col:row | Y | Y | Y | Y | Y |
| Contact image under coils | P | Y | Y | Y | Y |
| BOM with manufacturer + order number | Y p16-17 | Y p9-10 | P p12-13 | Y p16-18 | Y p10-11 |
| Quantities | Y | Y | Y | Y (some inconsistent) | Y |
| Calculations | N | N | N | N | N |
| Sequence of operation text | N | N | N | N | N |
| KNX | Actuators + channel map + bus terminal | - | 1 actuator + bus terminal | - | - |
| ATS | - | - | - | 2 sources, mech + elec interlock, controller, motor operators | - |
| Fire-pump specifics | - | - | - | - | Jockey/duty/standby, PS start, no OL on main pumps |

---

## Reference defects and ambiguities (do not copy into requirements)

1. **KHAMIS-MDB:** pages 4 to 6 and 19 to 29 are exact duplicates. The footer says 15 of 15 and later 16, the table of contents lists 16 entries, and the file has 29 pages.
2. **KHAMIS-MDB:** the ACB trip unit is LSIG in the schematic and LI in the parts list.
3. **KHAMIS-MDB:** the feeders are drawn as TMD but the parts list has XT3N MA 200 (magnetic-only).
4. **KHAMIS-MDB:** a Siemens type code sits against an ABB order number (parts-list item 4).
5. **KHAMIS-MDB:** incomer cables are "XX", and the job number "IEC_bas001"/"ICE_bas001" is a template value.
6. **KHAMIS-MDB:** a 1600 A rating on a single 80x10 Cu bar per phase has no calculation.
7. **SHANAA-POOL:** the table of contents numbering (1 to 15 with gaps) does not match the 13 physical pages, and footers mix "of15" and "of13".
8. **SHANAA-POOL:** the function tags "=DB-VRF/1" and "=DB-POOL-FLOOR/8" are leftovers from other projects.
9. **SHANAA-POOL:** parts-list item 7 has no description.
10. **SHANAA-POOL:** the jacuzzi pump is labelled "Spare".
11. **SHANAA-POOL:** the overload ranges (TA25DU-6.5M) look too high for 1.3 to 2.3 A motors.
12. **SHANAA-POOL:** the 12 V transformer is not identified as safety isolating, and a 30 mA RCD is the incomer.
13. **POCKET-HALL:** the function tags "=DBF-M/15" on p1 and "=DBB-HALL/1" on p17 do not match the panel name DBG-HALL.
14. **POCKET-HALL:** the cover shows Revision 01 while the title block lists three revisions.
15. **POCKET-HALL:** an ETEK RCCB is listed with manufacturer ABB.
16. **POCKET-HALL:** there is no KNX power supply in the parts list, and the bus cable is drawn as 2x1.5 mm² XLPE instead of a KNX-certified bus cable.
17. **ZALMOUT-FIRE:** footers on p4 and p5 read "5of10" and "6of10" in an 11-page set.
18. **ZALMOUT-FIRE:** an E-stop and dry-run protection appear in fire-pump control (a compliance concern).
19. **ZALMOUT-FIRE:** there are no remote alarm contacts and no listed-controller statement.
20. **ZALMOUT-FIRE:** a Weidmüller part is listed under the manufacturer ABB.
21. **SATAFI-PUMP:** a C-curve MCB protects the soft-starter branch, and no coordination type is stated. Two parts-list pages have no dates in the table of contents.
22. **All five:**
    - None states a standard, earthing system, fault level, ambient or IP rating.
    - The "Design input/output" fields are empty.
    - Thickness is given in "mm²".
    - "Tabel" and "Mian" are misspelled.
    - Signature fields hold names only.

---

## What an execution team needs (derived requirements)

Tags show the supporting references (H = POCKET-HALL, S = SATAFI-PUMP, P = SHANAA-POOL, M = KHAMIS-MDB, F = ZALMOUT-FIRE). "Gap" means the references do not show the item but a fabrication team needs it. These requirements are inferred from the gaps, not from what the references show.

### All families (common)
- R1. A cover and approval page with the A to D approval codes, project, owner, consultant and contractor, panel name, quantity, job, quotation and sales numbers, revision, and total pages. [H S P M F]
- R2. A per-page title block with a revision history (rev, date, designer, checker, approver) and correct "page n of N" counts that are consistent across the table of contents, footers and cover. [all; defects 1, 7, 14, 17]
- R3. A table of contents generated from the actual pages, with no duplicates. [all; defect 1]
- R4. External views (front, side, top, bottom) with overall W x H x D, material thickness in mm, and weatherproof/canopy option. [H S P M F]
- R5. A dimensioned dead front and a mounting-plate/component layout using the real device footprints. [H S P M F]
- R6. A power schematic with, per device: tag, type, rating, poles, curve, Icu/Ics/Icn and RCD sensitivity, plus outgoing cable size and load name per circuit. [H S P M F]
- R7. Device terminal numbers and auxiliary contacts on the schematic, with page.column:row cross-references and contact images under the coils. [S P M F]
- R8. An orderable parts list (manufacturer, order number, quantity, description) whose manufacturer agrees with the order number, and with no blank descriptions. [all; defects 4, 9, 15, 20]
- R9. A parts list consistent with the schematic: same trip unit and same type. [defects 2, 3]
- R10. Gap: a stated design basis (standard and edition, for example IEC 61439-1/-2; system voltage and frequency; earthing system; prospective fault level; ambient; IP/IK rating). [none show it]
- R11. Gap: wire numbering, a terminal plan and a source-to-target connection list. Only S shows wire numbers.
- R12. Gap: a cable list.
- R13. Gap: calculations (short-circuit withstand, busbar sizing or temperature rise, cable sizing and voltage drop). [defect 6]
- R14. Gap: no template residue (function tags, job numbers or labels from other projects). [defects 5, 8, 13]

### Distribution / lighting boards (POCKET-HALL, SHANAA-POOL)
- Grouped RCD (30 mA and 300 mA) and MCB distribution with per-circuit load names and cables. [H P]
- Lighting via modular contactors or KNX actuator channels, with a channel-to-load map. [H P]
- KNX hardware only, as evidenced: actuator type and order number, channel mapping, and bus terminal. Additional KNX requirements (gap, and not to be inferred as supported): KNX PSU and choke, bus cable type, line topology, physical and group address list, and ETS handover. [H P]

### Pump panels (SATAFI-PUMP, SHANAA-POOL)
- Motor branch: protective device, contactor, overload relay or soft starter, with the overload range matched to motor FLC (defect 11) and the coordination type stated (defect 21). [S P]
- Control: E-stop, start/stop or A-0-M (Auto/Off/Manual), and time switch or KNX auto start. [S P]
- Interlocks through O.L/MCB aux contacts. [P]
- Run/fault lamps. [S P]
- Phase failure/sequence relay, with a trip-on-fault action stated. [S P]
- Wire-numbered control schematic. [S]
- Pool-specific gap: SELV safety isolating transformer for underwater lights, and RCD discrimination. [P; defect 12]
- Enclosure ventilation (fan, filter, thermostat). [S P]

### Main distribution board with ATS (KHAMIS-MDB)
- Two incomers (utility and generator), 4P ACBs with motor operator, YC/YO and aux/trip contacts. [M]
- Mechanical interlock (named part) plus electrical cross-interlock. [M]
- ATS controller with sensing per source, phase-failure relays, gen start/stop relay, and transfer timer. [M]
- ATS on/off, manual close, E-stop, status/trip lamps and buzzer. [M]
- Metering with CTs and a meter. [M]
- Busbar main/N/E sizes. [M]
- Outgoing MCCB schedule. [M]
- Gaps:
  - Transfer, retransfer and cool-down timer values, and a controller parameter sheet.
  - Neutral switching and earthing philosophy.
  - Incomer cable sizes. [defect 5]
  - A busbar rating calculation. [defect 6]
  - A sequence-of-operation narrative.

### Fire-pump panels (ZALMOUT-FIRE)
- As evidenced:
  - Jockey, duty and standby pumps.
  - Pressure-switch auto start per pump.
  - A-0-M per pump.
  - Run and stop delay timers.
  - Jockey lockout when a main pump runs.
  - No thermal overload on the main pumps (magnetic-only MCCB).
  - Thermal overload on the jockey pump.
  - Phase-failure monitoring.
  - Run/fault/low-level indication. [F]
- Gaps that a certified controller normally needs (general knowledge, verify against the governing edition of NFPA 20 / UL 218 / local code before treating as requirements):
  - A listed controller.
  - Locked-rotor-rated protection and an isolating means.
  - No E-stop or dry-run stop on the automatic fire pump.
  - A minimum-run or manual-only stop.
  - Phase-reversal and power-loss alarms with remote dry contacts to the fire alarm panel.
  - An emergency-run means.
  - Alternate power or transfer.
  - Pressure recording and a weekly test.
- PanelPilot should present such panels as "fire-pump duty motor control (non-listed)" unless these items are designed and certified. [F; defects 18, 19]

---

## What could not be determined
- **Visual check:** only 19 of the 80 physical pages were rendered, and five were studied closely. These items were judged from extracted text only: depth values and other geometry on the external views, DIN rail and duct sizes on the component views, and SHANAA p11 control details. The ZALMOUT dry-run action on the main pumps also comes from text, and its exact effect (stop or alarm only) should be confirmed visually on ZALMOUT p7 and p8.
- **Standards:** no standard text was consulted. All NFPA 20 / IEC 60364-7-702 statements are general knowledge and not edition-verified.
- **Device datasheets:** overload ranges and busbar ampacity were not checked against manufacturer data.
- **ATS settings:** the DKG-173 internal settings and timer values are not in the PDF.
- **Signatures and stamps:** whether signatures or stamps exist on the approved copies is unknown. The PDFs carry name text only.
