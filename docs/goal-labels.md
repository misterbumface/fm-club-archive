# Goal labels: the game's own words against the save's shot events

Started 2 Nov 2026. Goal: learn what the highlight events in `match_stats_cfg.dat` say about each shot (header, volley,
free kick, cross, distance), so the papers can tell how a goal was scored as a fact the fact checker can check. Nothing is
built on these codes until each one has at least five labelled matches and no misses.

`tools/shot_events.py <save.fm>` prints every shot event with its player, so a new label can be matched to its bytes.

## What is known

Each detailed match record (`match_stats_cfg.dat`, the reports `read_match_reports` reads) holds, besides the player
records, a list of highlight events. An event: `02 <side 0/1> <slot> <code> <tick u16> 00 00 <minute> 00`, then detail bytes
from +10. The tick is the match clock of the shot records (their bytes 11-12): a shot event and its shot record carry the
same tick (+-4), which links an event to its shot exactly (836 goal events in the save of 9 Feb 2064).

| Finding | Evidence | Status |
|---|---|---|
| slot = the player's index in his side's player records (team-sheet order) | 836 of 836 goal events linked by tick | proven |
| code 38 = yellow card | Strasbourg 0-1 OL, 8 Feb 2064: OL cards 19', 22', 23' (Álvaro, Devoy, Franco) | firm |
| code 1 = a shot; detail: value u32 at +10, type byte at +14, then bytes +15.. | see labels | firm |
| type `03` = header | Paulo Roberto 71', Pardal 59', Hernando 2', Etxaniz 56', Falah 63', Themistokleous 36', Black 35', Lazar 77', Pelayes 18', Ciută 79', Pardal 8' (11 labels; the game said only "strike", the user's replay showed a header: the data told it first); 200 of 701 inside-the-box goals, 0 of 82 outside-the-box goals | 5 labels |
| bit `0x80` in the type (`80`, `81`, `85`, `a1`, `a5`) = something of shots in the box | 147 inside-the-box goals, 0 outside | likely; meaning unknown |
| bit `0x04` = volley | Broadfoot 65' `05`, Ghiocel 48' `85`, Rodrigo 23' `05` "thunderous volley", Joel 72' `85` "volley from within the six-yard box", Formicola 56' `85` "volley from within the six-yard box", Óscar Pérez 74' `85` "volley from close in" | firm (6 of 6) |
| bit `0x80` | finish `81`: "a close range finish" 3 of 3 (Ondo, Ferrer 35', Ataba 88'); volley `85` "close range volley" (Ghiocel); drives `a1` (Ángel, Sheminant, Valera: "close range" not said); but "close range" without it: Francois 72' `01` "deftly executed finish from close range", Rondina 8' `01` "powerful close range effort", the headers `03`; only in the box | weaker: Messi 13' `a1` "rifled drive" and Formicola 17' `a1` "deflected low drive" carry it, Pardal 8' `03` "from just inside the six-yard box" does not (3 Nov). Not close range. Never outside the box, never on a header (0 of 191 `03` goals); with 24 of 327 strikes, 82 of 178 drives, 26 of 85 volleys. The user watched (3 Nov): Messi 13' `a1` a first-time shot from a cut-back; Formicola 17' `a1` a first-time cut-back tap-in. New lead: a first-time shot with the foot from a low ball in the box. To test: Myers 69' `21`, Ferrer 22' `01` (no flag), Ferrer 35' `81` |
| bit `0x20` = drive (low, hard) | `a1`: Ángel 43' "low drive" (h 0.46), Sheminant 74' "rifled drive which kept low" (1.00), Valera 15' "skidding drive" (0.00); `21`: Uranga 17' "skidding drive" (0.19), Pirson 4' "rifled drive which kept low"; `a1`: Reginaldo 20' and Yildiz 63' "skidding drive"; Luton game (3 Nov): Messi 13' `a1`, Formicola 17' `a1`, Hilgers 43' `21` (24 m: so not only in the box), Myers 69' `21`; López 74' `21`, Robertson 63' `21` (13 labels). So the type is bits: `01` foot, `02` head, `04` volley, `20` drive, `80` unknown (never outside the box) | likely (4) |
| type `01` = finish | Jones 8' "deftly executed finish", Odogwu 54' "accurate finish", Formicola 33' "accurate finish", Javier 81' "tidy finish", Brugué 18' "placed shot", Ferrer 22' "placed shot", Messi 25' "curling effort", Francois 72' "deftly executed finish"; also Diarra 53' (free kick: marked elsewhere) | 5 labels |
| byte +20 `44` = deflected | Messi 25' `02 42 00 04 40 44` and Formicola 17' `40 42 00 01 00 44` (both deflections said by the game, both also `42` at +16); 7 goals in the save of 3 Nov have `44`, one of them Ondo 33' "close range finish" (no deflection said) | broken: Robertson 63' "deflected low drive" has `40` (and `4a` at +16). 2 of 3 |
| byte +15 = a distance band: `40` close range | every goal with the `0x80` type bit has `40` (141 of 141, save of 3 Nov). Labels with `40`: all the game's "close range" / six-yard goals but one (Paulo Roberto, Hernando, Etxaniz, Falah, Themistokleous, Ghiocel, Ondo, Ferrer 35', Rondina, Pelayes, Ataba, Michalik, Joel, Pardal 59' and 8', Formicola 17' and 56', Bautista, Pérez; Broadfoot "close range volley" from the penalty area); the user watched Messi 13' (`40`, closer in) and Myers 69' (`10`, near the penalty spot), both first-time shots from cut-backs. `10`: Myers (about 11 m), Brugué (15-16 m), Baran (about 13 m), Rodrigo; `90`: Júnior (23 m), Hilgers (24 m), López (edge of the area; the user watched it: from outside the area); `88` Diarra (free kick); penalties `20`/`30`. The one miss: Francois "from close range" `50` (`40` and `10` both: between bands?). `00` Javier (12 m) and Uranga: unknown | BUILT 3 Nov 2026 as two bits (`fm_save.shot_range`): over the whole save, 0x40 is set on 510 goals of scorers whose goals were all inside the box and on none of those all outside; 0x80 on 28 all-outside and no all-inside; `10`/`00` on both sides of the box line (162 inside, 48 outside), so they tell nothing. Goals only: match_shots.range, the goal lines, fact_check goal_kind range |
| shot origins from the game's analysis screen (user's screenshots, 3 Nov; measured at 18.3 px a metre from the box lines) | Chelsea 6-0 Luton: Pardal 8' header 5.8 m (`40`); Messi 13' 7.3 m (`40`); Formicola 17' and 56' about 6.7 and 7.4 m, the same spot (`40`); Myers 69' 12.3 m (`10`); Hilgers 43' 24.1 m (`90`). Middlesbrough 0-2 Forest: Baldy 49' penalty 11.3 m (`30`); Robertson 63' about 16 m, 11 m to the side (`10`) | the band holds: `40` = up to about 7.5 m, `10` = about 11-16 m in the box, `90` = outside the box. The save's report bytes near the goal events hold no origin: the analysis points are kept somewhere not found yet |
| type bit `0x80` = a first-time finish from close range? | only with byte +15 `40`, only with the foot; Messi 13' and Formicola 17' first-time from cut-backs; Myers 69' also first-time from a cut-back, but from the penalty spot (`10`), so no flag | a lead (2) |
| byte +15 `88` = free kick | Diarra 53' (the others: `10` or `40`) | guess (1) |
| the value at +10 | rises with distance (headers lowest), but 48 of 82 outside-the-box goals are under 16 500 (inside: median 8 700, outside: median 15 200); Javier's "finish from 12 metres" is 8 769, Etxaniz's "close range header" 12 834 | NOT the distance (the game's text knows the distance: it is stored elsewhere or computed) |
| code 47 | at minutes with no goal (Chelsea v Shamrock 13', 24', 33', 42'); also at a penalty's own tick | unknown, not "goal" |
| code 3 = a penalty kick | 4 of 4 penalties (save of 22:56), and Baldy, Marcelinho twice, Aureli (16 Feb 2064): 8 of 8; the side and height of each fit the game's "bottom corner" / "top corner"; Marcelinho's "powerful penalty" has the drive bit `21` | firm |
| byte +15 `20`/`30` = a penalty | the four penalties of 16 Feb 2064 (`30 20 20 20`), no other goal has it | a lead (4); not needed: code 3 tells it |
| the event list | only some moments: Marks 90+2' and Myers 90+3' (Chelsea v Shamrock) have no shot event | a selection |

Ruled out: the shot record's flag bytes (they repeat the goal-mouth zone and a high/low band; header, volley and free kick
share them); the goal list (scorer, side, kind 0-5, minute; also in the match news item in `news.dat`); the player's
11-byte event items (empty for these scorers); the `.apm` files (match-engine data of one team each). The words of the
game's news are written at display time; the facts come from these stores.

Whole-save counts (save of 9 Feb 2064, 1 198 shot events): type `01` 351 (median value 12.3k), `03` 213 (5.8k), `00` 310,
`21` 114 (12.6k), `a1` 95 (8.3k), `80` 62 (always 1.5k), `05` 50 (7.7k), `02` 34, `85` 33 (5.3k), `81` 22. The players'
"headers" figure (aerial duels) does not tell headed shots apart: 96% of `03` events and 92% of `01` events have one.
The test that works is the scorer's goals from outside the box (his match record counts them): types of the 82 goals of
scorers whose goals were all from outside: `01` 54, `21` 19, `05` 9; of the 701 all-inside: `01` 224, `03` 200, `a1` 90,
`21` 78, `05` 40, `85` 30, `81` 19, `a5` 8.

The user's reading (2 Nov 2026), which the labels so far bear out: the type byte is the kind of shot the game's text
names (finish, drive, header, volley); how the ball went in (low, top corner) is the shot record's height and across.

## Labels

Source: the game's match report text or the user watching the goal. Bytes from the save of 9 Feb 2064
(the save written at 20:11 that day). "Event" is the shot event's value, type and the bytes after the type.

| Game (date) | Goal | The game says | Event: value, type, then | Shot record: height, across |
|---|---|---|---|---|
| Bolton 3-3 Newcastle, FA Cup (9 Feb 2064) | Kyle Jones 8' | "a deftly executed finish" | 13 884, `01`, `10 08 00 04 40 01 00 01 10` | 1.48, 2.95 |
| | Ángel 43' | "a low drive" | 6 749, `a1`, `40 00 00 01 80 00 00 01 01` | 0.46, 2.65 |
| | Amos Odogwu 54' | "an accurate finish" | 8 435, `01`, `10 42 10 02 00 00 00 a1 00` | 0.18, -3.18 |
| | Ally Broadfoot 65' | "a powerful close range volley into the top corner"; user: from the penalty area | 10 748, `05`, `40 48 00 02 c0 00 00 88 00` | 1.67, -1.42 |
| | Paulo Roberto 71' | "a good header from just inside the six-yard box"; user: from a cross | 6 608, `03`, `40 40 00 01 c0 00 00 22 01` | 1.58, -3.38 |
| | Tegan Sheminant 74' | "a rifled drive which kept low" | 11 592, `a1`, `40 40 00 02 80 00 00 08 01` | 1.00, 2.99 |
| Strasbourg 0-1 OL, Ligue 1 (8 Feb 2064) | Aoula Diarra 53' | "a free kick into the top corner from the edge of the area" | 22 661, `01`, `88 00 00 00 c4 40 00 00` | 1.74, -3.45 |
| Nottingham Forest 0-3 Chelsea, Premier Division (3 Feb 2064) | Nacho Valera 15' | "a skidding drive" | 2 836, `a1`, `40 40 00 01 80 40 00 00 00` | 0.00, -1.79 |
| | Marcelo Formicola 33' | "an accurate finish from inside the penalty area"; user: aimed high, keeper got a touch | 12 714, `01`, `50 48 00 08 c0 40 00 02 10` | 1.46, -0.81 |
| | Marcelo Formicola 45' | "a powerful close range effort" | no shot event | |
| Girona 1-0 Barcelona, La Liga (8 Feb 2064) | Jesús Hernando 2' | "a close range header" | 8 427, `03`, `40 00 00 01 c0 40 00 41 01` | 2.04, -2.50 |
| Oviedo 1-3 Espanyol, La Liga (8 Feb 2064) | José Ramón Uranga 17' | "a skidding drive" | 4 947, `21`, `00 08 00 02 80 40 00 24 10` | 0.19, -3.06 |
| | Delfí Català 45' | "a volley from within the six-yard box" | no shot event | |
| | Mikel Etxaniz 56' | "a close range header" | 12 834, `03`, `40 40 00 01 c0 40 00 02 01` | 1.59, 2.90 |
| | Javier 81' | "a tidy finish from 12 metres" | 8 769, `01`, `00 80 02 02 e0 40 00 01 10` | 1.71, -1.81 |
| Chelsea 6-1 Liverpool, Premier Division (22 Dec 2063) | Júnior 5' | Goal of the Month, December: "an accurate finish from 23 metres" | 22 220, `01`, `90 08 00 00 88 40 00 04 10` | 0.07, 2.40 |
| FC Bayern 5-0 Eintracht Frankfurt, Bundesliga (9 Feb 2064) | Teodoro Brugué 18' (his other goal, 40', is a header `03`, so this is the one) | "a placed shot which hit the inside of the post before crossing the line" | 14 548, `01`, `10 08 00 02 00 45 00 01 10` | 0.51, 1.34. The user watched it: a blistering shot through the centre, from just inside the box (about 15-16 m), keeper a metre or two off his line. So "hit the inside of the post" was the game's own colour: the shot record beats the game's prose. His 40' header: user saw it go in "top centre"; record 1.29, -1.34 |
| FC Nantes 2-0 RC Lens, Ligue 1 (9 Feb 2064) | Abdallah Falah 63' | "a good header from just inside the six-yard box" | 2 335, `03`, `40 40 00 01 80 40 00 02 01` | 0.67, -2.29 |
| Rotherham 1-2 Middlesbrough, FA Cup (9 Feb 2064) | Kostas Themistokleous 36' | "a close range header" | 10 397, `03`, `40 40 00 01 00 41 00 01 01` | 0.29, -2.30 |
| | Flavius Ghiocel 48' | after an error by Goñi, "a close range volley into the top corner" | 13 964, `85`, `40 40 00 02 40 41 00 01 09` | 1.35, 2.45 |
| | Rodrigão 82' | "strike"; user watched it: his shot was deflected back to him and he headed it into the corner | no shot event | goal 0.40, 2.16; his blocked shot (1.46, 1.60) in the same second, 81:08 |
| Manchester City 0-2 Everton, FA Cup (9 Feb 2064) | Rodrigo 23' | "a thunderous volley" | 12 372, `05`, `10 08 00 00 04 40 00 02 01` | 1.14, 0.95 |
| | Manuel Ondo 33' | after an error by Calamaro, "a close range finish into the bottom right corner" | 3 783, `81`, `40 00 00 01 00 44 00 08 09` | 0.55, -0.15 (central: the game's "corner" is loose, as Brugué's "post") |
| Chelsea 4-1 Aston Villa, Premier Division (13 Feb 2064; save of 2 Nov 2026, 22:48) | Pablo Ferrer 22' | after an error by Phil Richards, "a placed shot into the bottom corner" | 10 563, `01`, `10 08 00 02 00 21 08 04 18` | 0.27, -2.22 |
| | Lionel Messi 25' | "a curling effort which beat the keeper with the aid of a deflection" (the first deflection) | 9 222, `01`, `02 42 00 04 40 44 00 88 00` | 1.31, -3.38; no blocked shot before it: a deflected goal is one goal shot |
| | Pablo Ferrer 35' | "a close range finish into the bottom left corner" | 13 424, `81`, `40 40 00 02 80 40 00 08 10` | 0.37, 2.21 |
| | Jesse Francois 72' | "a deftly executed finish from close range" | 12 312, `01`, `50 48 00 01 80 40 00 04 10` | 0.53, 2.36 (his event's clock was 2.9 minutes off its minute: the reader's check was loosened to 8) |
| | Roberto Baggio 91' | "a placed shot into the bottom corner" | no shot event | 0.56, -3.05 |
| Manchester City 3-1 Rotherham, Premier Division (save of 2 Nov 2026, 22:56) | Daniel Rondina 8' | after an error by Cazenave, "a powerful close range effort" | 8 035, `01`, `40 08 00 02 c0 20 08 08 18` | 1.87, -2.44 |
| | Nahuel Pelayes 18' | "a close range header" | 6 767, `03`, `40 40 00 01 80 20 08 08 01` | 0.81, -2.89 |
| | Ataba 88' | "a close range finish into the bottom right corner" | 9 648, `81`, `40 40 00 02 00 41 00 08 10` | 1.05, -2.40 (not low: "bottom" is colour) |
| | Kemal Aydın 90+3' | "a close range volley which hit the inside of the post before crossing the line" | no shot event | 0.74, 3.33 (near the post at 3.66: this "inside of the post" fits, Brugué's did not) |
| Napoli 3-1 CFR Cluj, Champions League play-off, first leg (13 Feb 2064; save of 22:56) | Basile Pirson 4' | "a rifled drive which kept low" | 13 957, `21`, `50 48 00 02 00 20 08 04 10` | 0.67, -1.06 |
| | Dariusz Michalik 11' | "a powerful close range effort" | 4 813, `01`, `40 08 00 20 40 41 00 20 00` | 1.75, -0.70 |
| | Reginaldo 20' | after an error by Michalik, "a skidding drive" | 10 819, `a1`, `40 40 00 02 80 20 08 04 18` | 1.21, -1.49 |
| | Selçuk Yildiz 63' | after an error by Jorge Ramos, "a skidding drive" | 13 418, `a1`, `40 40 00 02 c0 40 00 08 09` | 1.31, 2.35 |
| Vitesse 1-2 Real Madrid, Champions League play-off, first leg (13 Feb 2064) | Rıdvan Baran 11' | "an accurate finish which went in off the post"; the user's replay: shot from inside the box, about 12-13 m out and a few metres to the side (about 13 m from goal), the keeper diving near his line | 18 218, `01`, `10 08 00 04 00 45 00 01 10` | 0.02, -3.43 (at the post: fits) |
| | Joel 72' | after an error by Daniel, "a volley from within the six-yard box" | 1 827, `85`, `40 00 00 02 00 20 08 44 18` | 0.76, -0.38 |
| | Alexandru Ciută 79' | "strike" (the game's word for any goal it does not describe); the user watched it: a header | 10 978, `03` (a header) | 0.69, -3.17 |
| Chelsea 3-1 Shamrock Rovers, Champions League (30 Jan 2064) | Francisco Pardal 59' | user: header from close in, edge of the six-yard box near the post | 4 397, `03`, `40 40 00 01 00 21 08 41 01` | 1.00, -2.68 |
| | Jake Marks 90+2' | user: shot from outside the box, left of the D (about 22-25 m) | no shot event | 1.96, 3.56 |
| | Ken Myers 90+3' | user: shot from outside the box, right of the D (about 22-25 m) | no shot event | 0.15, -2.76 |
| Chelsea 6-0 Luton, Premier Division (16 Feb 2064; save of 3 Nov 2026, 09:16) | Francisco Pardal 8' | "a good header from just inside the six-yard box" | 2 987, `03`, `40 40 00 02 80 40 00 22 01` | 0.98, -1.19 |
| | Lionel Messi 13' | "a rifled drive which kept low" | 10 628, `a1`, `40 40 00 02 80 40 00 08 01` | 0.66, -2.77 |
| | Marcelo Formicola 17' | "a deflected low drive" (the second deflection) | 5 807, `a1`, `40 42 00 01 00 44 00 08 10` | 0.01, -0.95 |
| | Bruce Hilgers 43' | "a low drive from 24 metres out" | 22 769, `21`, `90 08 00 00 08 41 00 01 10` | 0.18, 3.50 |
| | Marcelo Formicola 56' | "a volley from within the six-yard box" | 4 026, `85`, `40 40 00 01 80 20 08 40 00` | 0.19, 2.41 |
| | Ken Myers 69' | "a well struck drive which stayed low and went in via the post" | 10 677, `21`, `10 48 00 02 00 45 00 04 10` | 0.48, -2.48 (1.2 m from the post at 3.66: "via the post" does not fit well) |
| Middlesbrough 0-2 Nottingham Forest, Premier Division (16 Feb 2064; save of 3 Nov 2026, 09:20) | Leighton Baldy 49' | "a penalty placed into the bottom right corner" | code 3: 3 480, `01`, `30 00 00 02 00 41 00 00 00` | 0.46, 3.31 (fits) |
| | Murray Robertson 63' | "a deflected low drive" (the third deflection) | 9 775, `21`, `10 4a 00 04 80 40 00 04 10` | 1.87, 3.56 (not low: high at the post; the record may keep the ball after the deflection) |
| Arsenal 2-2 Newcastle, Premier Division (16 Feb 2064) | Kilian Bautista 45' | "a powerful close range effort" | 15 200, `01`, `40 48 00 02 c0 40 00 02 01` | 2.11, -1.28 |
| | Marcelinho 48' | "a penalty into the bottom corner" | code 3: 3 543, `01`, `20 00 00 02 00 41 00 00 00` | 0.53, 3.28 (fits) |
| | Marcelinho 55' | "a powerful penalty into the bottom corner" | code 3: 3 822, `21`, `20 08 00 02 80 40 00 00 00` | 0.50, -3.25 (fits; "powerful" = the drive bit `20`) |
| | Mattéo Blanc 91' | after Rafael's error, "a deflected low drive"; the user watched it: a low cross and a tap-in (the game's words were wrong) | no shot event | |
| Everton 1-1 Plymouth, Premier Division (16 Feb 2064) | Aureli 69' | "a penalty into the top corner" | code 3: 3 449, `01`, `20 00 00 02 40 41 00 00 00` | 1.50, 3.21 (in the corner; 1.5 m is the upper part, not under the bar) |
| | Óscar Pérez 74' | "a volley from close in which hit the post on its way" | 2 611, `85`, `40 00 00 02 00 45 00 00 00` | 0.51, -2.58 (1.1 m from the post) |
| Watford 1-0 Rotherham, Premier Division (16 Feb 2064) | Carlos López 74' | "a low drive from the edge of the area which went in off the post" | 13 544, `21`, `90 08 00 00 04 45 00 04 10` | 0.74, -2.15 (1.5 m from the post) |
| Manchester United 1-0 Aston Villa; Liverpool 1-0 Sheffield United (16 Feb 2064) | José Miguel 94', Conall Beck 91' | "a low drive" both | no shot event | |

Labelled in the game's text but not in the save's detailed reports (no event to compare): Cosenza 2-3 AC Milan (9 Feb 2064:
Alan Black 35' close range header, Tariq Tanser 50' powerful close range effort, Ion Lazar header from inside the six-yard
box, Borgogni 86' freak goal, Neri 21' own goal); Freiburg 1-1 Gladbach (9 Feb 2064: Unai Martínez 56' volley from within
the six-yard box, Salgado 90' own goal).

Distance (the game's words against the value): Júnior "23 metres" 22 220; Javier "12 metres" 8 769; Diarra "the edge of the
area" 22 661; Pardal close in 4 397; Etxaniz "close range header" 12 834. "Close range" goals with large values: Etxaniz header 12 834, Themistokleous header 10 397, Ghiocel volley 13 964: the
game's "close range" is loose, or the value is not the shot's distance (the length of the pass or cross before it?).
Rıdvan Baran 11' (user: about 13 m from goal, keeper near his line) 18 218: fits neither the distance to goal nor to the
keeper, so the value is no shooting distance in metres.
Penalties settle it: a penalty is no code-1 shot event but code 3 (4 of 4, two to three ticks after the shot record, with
a code 47 at the shot's own tick), and its value runs 986 to 3 954 for kicks all taken from 11 m (Crewe v Chelsea 30' 3 515,
Bnei Yehuda v Hapoel Be'er-Sheva 43' 3 954, Craiova v UTA Arad 10' 986, FCSB v Concordia 17' 1 889; save of 22:56). The
value is no distance; its range fits a time in milliseconds (a guess).
Brugué 18' (user: shot from about 15-16 m, keeper 1-2 m out) 14 548 fits both. An idea to test: the distance to the goalkeeper
(a keeper off his line makes it short of the distance to goal; Etxaniz does not fit unless "close range" is loose).
Premier Division Goal of the Month, Aug 2063 - Jan 2064 (screenshot, 2 Nov 2026): only Júnior's goal is in the save's detailed
reports; the rest (Rotherham, Sheffield United, Luton, Reading, Newcastle and Arsenal games, or older than late November) are not.

The user's replays of three Premier Division Goal of the Month goals (2 Nov 2026) show each shot taken from about the
game's distance: Rodrigão (Rotherham v Middlesbrough, 19 Jan) "19 metres", at the edge of the box; Jorge Ramírez (Sheffield
United v Wolves, 1 Jan) "23 metres"; Alberto (Luton v Plymouth, 5 Jan) "a 28 metre free kick". So the game's distances are
true shooting distances; none of the three matches is in the save's detailed reports, so there is no value to compare.

Goal of the Month, Sky Bet Championship, January 2064 (the game gives the distance): Devon Jones (Blackburn at MK Dons, 26
Jan) "a spectacular close range overhead kick"; Matthew Dickson (Leicester v Brighton, 26 Jan) "a fierce effort from 20
metres out" after a team move; Regan Cooper (Charlton v Cardiff, 26 Jan) dribbled from his own half, "a terrific chip from
20 metres". None of the three matches is among the save's detailed reports (neither the save of 9 Feb 20:11 nor the backup
of 15:14): the game details only some matches (about 518 at a time: the user's clubs, their competitions and some others),
so award goals help only from those.

## Waiting for a save

Labels from the game's text whose match is in no save yet; match them with the tool when it comes. (None now.)

## Facts from the shot records alone

A rebound: a goal in the same second as (or a few seconds after) the same player's blocked or saved shot. Rodrigão 81:08
(Rotherham v Middlesbrough, 9 Feb 2064): blocked, then the goal, both at 81:08, as the user saw. This needs no event code.
A deflected goal is one goal shot, not a blocked shot and then a goal: Messi 25' (Chelsea v Villa, 13 Feb 2064) "with the aid
of a deflection" has no other shot near it. Which byte marks the deflection needs more labels (his bytes after the type:
`02 42 00 04 40 44 00 88 00`). Not a clue: a shot entry's part count. An entry groups one player's shots that share
the zone flags, minutes apart (Sheminant 23:01 blocked and 73:54 goal are one entry), so its parts are not stages of a shot.

## Wanted

Goal of the Month or Week entries (they give the distance) from the Premier Division or the Champions League, whose
matches the game details. Goals the game describes with: a deflected shot, a cross, a corner, a through ball, a penalty, a long shot "from 25 yards", a free kick, a
rebound, a one-on-one, a chip, the left or right foot. Each one fixes or breaks a code.

## The shot points of the analysis screen (search started 3 Nov 2026)

The game's match analysis draws each shot from the point it was taken (Middlesbrough 0-2 Forest: Baldy's penalty 11.3 m,
Robertson about 11.7 m out and 11.2 m to the side). Not found yet in the report's 98 KB (Middlesbrough v Forest): no f32,
u16 or u8 pair near these points in metres, decimetres, centimetres, yards or half-metres, measured from the goal, a corner
or the centre spot, that is not chance. The report holds the player records, the highlight events, a table of injury names
and, after the next header, each player's name and attribute block. Your own matches keep far more (Chelsea 6-0 Luton:
2.6 MB, likely the whole match), so the points of your games may be there. Not looked at yet: `.apm` files (one per team
and match, not for every match), the Chelsea report.

Found on the way: after the highlight events, a second shot list (2 flag bytes, a count, 15-byte parts like the players'
shot lists) with other goal-line floats. Robertson 63' is there twice, at ticks 15 359 and 15 362, both -3.07 across, 0.03
high: low, as the game said ("a deflected low drive"). The shooter's own record has 3.56, 1.87: where his shot was going
before the deflection. So for a deflected goal the shooter's record is not where the ball went in. Baldy's penalty is
there too (3.31, 0.46, as his record). Whose list this is (the goalkeeper's?) is not known yet.

Luton's two shots (Chelsea 6-0 Luton, analysis screen, 3 Nov 2026): Roberto Pajares 22' (21:27) blocked, from 9.0 m out and
10.8 m to one side, blocked at about 5 m; Renato Bernhard 70' (69:14) off target over the bar, from 8.6 m out and 13.4 m to
the other side. Neither has a highlight event: the report keeps events almost only for goals, but the analysis draws every
shot. Their shot records' flag bytes are `10 08` and `10 20`: the first byte is the goal-mouth band (`20` within about 1.2 m
of the centre, `10` about 2-2.8 m, `40` beyond about 3.3 m: 10 of 10 shots), the second the outcome (`04` goal, `08` blocked,
`20` off target). So the shot's point is not in its record. The float before a player's shot list is his xG in the match
without penalties (Chelsea 4.11 of 4.11; Forest 0.38 + a penalty's 0.79 = the game's 1.17), already read as match_players.xg.
In your own games the passes (with points) are kept too, in the 2.4 MB packed block (8-byte groups, single bits set); that is
where the analysis points most likely come from. A match without it (Middlesbrough v Forest, 98 KB) still showed its shot
points on the analysis screen: their source is not found.
